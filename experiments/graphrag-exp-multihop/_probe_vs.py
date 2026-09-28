"""默认路径 vs Dynamic Community Selection —— 同一个问题跑两遍，对账选择行为与成本。

用法（在本目录下跑，用 graphrag-exp 那个 venv）：
    python _probe_vs.py --level 2 --query "……"
    python _probe_vs.py --level 2 --query "……" --paths default,dynamic

输出：终端一张对照表 + probe-out/<时间戳>.json（含逐社区评分明细）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
APP_DB = REPO / "backend" / "data" / "app.db"
OUT_DIR = ROOT / "probe-out"

# DeepSeek 的 shim 必须在本进程 import litellm 之前装上；sitecustomize 在解释器启动时不会自动加载，
# 所以这里显式把它 import 进来（它模块级就装好补丁）。
sys.path.insert(0, str(ROOT))
import sitecustomize  # noqa: E402,F401

import pandas as pd  # noqa: E402

from graphrag.config.load_config import load_config  # noqa: E402
from graphrag.query.factory import get_global_search_engine  # noqa: E402
from graphrag.query.indexer_adapters import (  # noqa: E402
    read_indexer_communities,
    read_indexer_entities,
    read_indexer_reports,
)


def load_api_key() -> str:
    con = sqlite3.connect(f"file:{APP_DB}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    return json.loads(row[0])["api_key"]


def load_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    out = ROOT / "output"
    reports = pd.read_parquet(out / "community_reports.parquet")
    communities = pd.read_parquet(out / "communities.parquet")
    entities = pd.read_parquet(out / "entities.parquet")
    return reports, communities, entities


DOC_LABEL = {
    "juejin-01-judge-troubleshooting.txt": "01",
    "juejin-02-switch-article.txt": "02",
    "juejin-03-gold-standard.txt": "03",
    "juejin-04-single-agent.txt": "04",
    "juejin-05-multi-agent.txt": "05",
    "juejin-06-prompt-to-loop.txt": "06",
    "a5.txt": "A5",
}


def load_doc_map(communities_df: pd.DataFrame) -> tuple[dict[int, str], dict[int, set[str]]]:
    """社区 → 它覆盖的文档（按 text unit 归属），以及主导文档。"""
    out = ROOT / "output"
    text_units = pd.read_parquet(out / "text_units.parquet")
    documents = pd.read_parquet(out / "documents.parquet")
    doc_of_tu = dict(zip(text_units["id"], text_units["document_id"]))
    label_of_doc = {
        doc_id: DOC_LABEL.get(str(title).strip(), str(title)[:6])
        for doc_id, title in zip(documents["id"], documents["title"])
    }
    docs_of: dict[int, set[str]] = {}
    dominant: dict[int, str] = {}
    for _, row in communities_df.iterrows():
        ids = [] if row["text_unit_ids"] is None else list(row["text_unit_ids"])
        labels = Counter(
            label_of_doc.get(doc_of_tu.get(tu)) for tu in ids
        )
        labels.pop(None, None)
        cid = int(row["community"])
        docs_of[cid] = set(labels)
        dominant[cid] = labels.most_common(1)[0][0] if labels else "?"
    return dominant, docs_of


def citation_docs(answer: str, dominant: dict[int, str]) -> Counter:
    """从答案里的 [Data: Reports (id, id, …)] 反推它引到了哪些文档。"""
    counter: Counter = Counter()
    for group in re.findall(r"\[Data: Reports \(([^)]*)\)\]", answer or ""):
        for token in re.split(r"[,\s]+", group.strip()):
            token = token.strip().lstrip("+")
            if token.isdigit():
                counter[dominant.get(int(token), "?")] += 1
    return counter


async def run_one(
    config,
    reports_df: pd.DataFrame,
    communities_df: pd.DataFrame,
    entities_df: pd.DataFrame,
    level: int,
    query: str,
    dynamic: bool,
    dominant: dict[int, str],
) -> dict:
    reports = read_indexer_reports(
        reports_df, communities_df, level, dynamic_community_selection=dynamic
    )
    entities = read_indexer_entities(entities_df, communities_df, level)
    communities = read_indexer_communities(communities_df, reports_df)

    engine = get_global_search_engine(
        config=config,
        reports=reports,
        entities=entities,
        communities=communities,
        response_type="Multiple Paragraphs",
        dynamic_community_selection=dynamic,
    )

    spy: dict = {}
    if dynamic:
        selector = engine.context_builder.dynamic_community_selection
        original = selector.select

        async def select_and_record(q: str):
            selected, info = await original(q)
            spy["ratings"] = {k: v for k, v in info.get("ratings", {}).items()}
            spy["selected"] = [r.short_id for r in selected]
            spy["selection_tokens"] = {
                "llm_calls": info.get("llm_calls"),
                "prompt_tokens": info.get("prompt_tokens"),
                "output_tokens": info.get("output_tokens"),
            }
            return selected, info

        selector.select = select_and_record  # type: ignore[method-assign]

    started = time.time()
    result = await engine.search(query)
    elapsed = time.time() - started

    # 进 map 的报告数 = 各批记录的行数之和；批数 = context_chunks 的个数
    rows = sum(len(df) for df in result.context_data.values()) if isinstance(result.context_data, dict) else 0
    level_of = dict(zip(communities_df["community"], communities_df["level"]))

    payload = {
        "path": "dynamic" if dynamic else "default",
        "query": query,
        "community_level": level,
        "reports_fed": len(reports),
        "fed_by_doc": dict(Counter(dominant.get(int(r.short_id), "?") for r in reports).most_common()),
        "batches": len(result.context_text) if isinstance(result.context_text, list) else 1,
        "context_rows": rows,
        "elapsed_s": round(elapsed, 1),
        "llm_calls": result.llm_calls_categories,
        "prompt_tokens": result.prompt_tokens_categories,
        "output_tokens": result.output_tokens_categories,
        "answer_chars": len(result.response or ""),
        "answer_is_canned": (result.response or "").strip().startswith("I am sorry but I am unable to answer"),
        "cited_by_doc": dict(citation_docs(result.response or "", dominant).most_common()),
        # 说明：cited_by_doc 反映的是"批次里有什么"而不是"结论取材"——
        # 已验证引用的 id 恰好等于那一批读到的报告 id（打散切批 ⇒ 天生跨域）。
        "answer": result.response or "",
        "answer_head": (result.response or "")[:400],
    }
    if dynamic:
        ratings = spy.get("ratings", {})
        selected = spy.get("selected", [])
        selected_ids = [int(s) for s in selected]
        payload["selection"] = {
            "rated": len(ratings),
            "rating_hist": dict(sorted(Counter(ratings.values()).items())),
            "selected_count": len(selected),
            "selected_by_level": dict(sorted(Counter(level_of.get(int(s), "?") for s in selected).items(), key=str)),
            "selected_by_doc": dict(Counter(dominant.get(i, "?") for i in selected_ids).most_common()),
            "selected": selected,
            "ratings": ratings,
            "tokens": spy.get("selection_tokens", {}),
        }
    return payload


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--paths", default="default,dynamic")
    ap.add_argument("--expect-docs", default="", help="该问题真正需要的文档代号，逗号分隔（用于覆盖率对账）")
    ap.add_argument("--tag", default="", help="结果文件后缀标签")
    args = ap.parse_args()

    os.environ["DEEPSEEK_API_KEY"] = load_api_key()
    config = load_config(root_dir=ROOT)
    reports_df, communities_df, entities_df = load_tables()
    dominant, _ = load_doc_map(communities_df)
    expect = [x.strip() for x in args.expect_docs.split(",") if x.strip()]

    results = []
    for path in [p.strip() for p in args.paths.split(",") if p.strip()]:
        dynamic = path == "dynamic"
        print(f"\n>>> 跑 {path}（community_level={args.level}）…", flush=True)
        payload = await run_one(
            config, reports_df, communities_df, entities_df, args.level, args.query, dynamic, dominant
        )
        if expect:
            selected_docs = set((payload.get("selection") or {}).get("selected_by_doc", {}) or payload["fed_by_doc"])
            payload["expect_docs"] = expect
            payload["expect_covered"] = sorted(d for d in expect if d in selected_docs)
            payload["expect_missing"] = sorted(d for d in expect if d not in selected_docs)
        results.append(payload)
        print(json.dumps({k: v for k, v in payload.items() if k not in {"answer_head", "selection"}}, ensure_ascii=False, indent=2, default=str), flush=True)
        if dynamic and "selection" in payload:
            sel = payload["selection"]
            print(f"  选中 {sel['selected_count']} 份（评了 {sel['rated']} 个社区，历史分布 {sel['rating_hist']}，按层 {sel['selected_by_level']}）", flush=True)
            print(f"  选中按文档: {sel['selected_by_doc']}   答案引用按文档: {payload['cited_by_doc']}", flush=True)
            print(f"  选择阶段 tokens: {sel['tokens']}", flush=True)
            if expect:
                print(f"  期望文档覆盖 {payload['expect_covered']} / 缺 {payload['expect_missing']}", flush=True)

    OUT_DIR.mkdir(exist_ok=True)
    suffix = f"-{args.tag}" if args.tag else ""
    dest = OUT_DIR / f"vs-{time.strftime('%m%d-%H%M%S')}{suffix}.json"
    dest.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n明细写入 {dest.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
