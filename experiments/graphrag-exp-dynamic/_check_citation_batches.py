"""验证一个猜想：答案里引用的报告 id 是不是"就是它那一批读到的 id"。

做法：不调 LLM，纯本地重建 default 路径的切批（打散用固定种子 86 ⇒ 可复现），
把每批的社区 id 解出来，再看答案里引用的 id 落在哪些批、各批的域构成是什么。

用法：python _check_citation_batches.py <probe json> [--path default]
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
sys.path.insert(0, str(ROOT))
import sitecustomize  # noqa: E402,F401

from graphrag.config.load_config import load_config  # noqa: E402
from graphrag.query.factory import get_global_search_engine  # noqa: E402
from graphrag.query.indexer_adapters import (  # noqa: E402
    read_indexer_communities,
    read_indexer_entities,
    read_indexer_reports,
)


def load_api_key() -> str:
    con = sqlite3.connect(f"file:{REPO / 'backend' / 'data' / 'app.db'}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    return json.loads(row[0])["api_key"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file")
    ap.add_argument("--path", default="default")
    ap.add_argument("--level", type=int, default=2)
    args = ap.parse_args()

    os.environ["DEEPSEEK_API_KEY"] = load_api_key()
    rows = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    target = next(r for r in rows if r["path"] == args.path)
    query = target["query"]
    cited_ids = []
    for group in re.findall(r"\[Data: Reports \(([^)]*)\)\]", target["answer_head"]):
        cited_ids += [int(t) for t in re.split(r"[,\s]+", group) if t.strip().isdigit()]

    out = ROOT / "output"
    reports_df = pd.read_parquet(out / "community_reports.parquet")
    communities_df = pd.read_parquet(out / "communities.parquet")
    entities_df = pd.read_parquet(out / "entities.parquet")
    text_units = pd.read_parquet(out / "text_units.parquet")
    documents = pd.read_parquet(out / "documents.parquet")
    doc_of_tu = dict(zip(text_units["id"], text_units["document_id"]))
    label_of_doc = {i: t.replace("juejin-", "").replace(".txt", "")[:10]
                    for i, t in zip(documents["id"], documents["title"])}
    dom = {}
    for _, r in communities_df.iterrows():
        ids = [] if r["text_unit_ids"] is None else list(r["text_unit_ids"])
        cnt = Counter(label_of_doc.get(doc_of_tu.get(t)) for t in ids)
        cnt.pop(None, None)
        dom[int(r["community"])] = cnt.most_common(1)[0][0] if cnt else "?"

    reports = read_indexer_reports(reports_df, communities_df, args.level, dynamic_community_selection=False)
    entities = read_indexer_entities(entities_df, communities_df, args.level)
    communities = read_indexer_communities(communities_df, reports_df)
    engine = get_global_search_engine(
        config=load_config(root_dir=ROOT), reports=reports, entities=entities,
        communities=communities, response_type="Multiple Paragraphs",
        dynamic_community_selection=False,
    )
    ctx = asyncio.run(engine.context_builder.build_context(query=query, **engine.context_builder_params))
    batches = ctx.context_chunks
    print(f"重建 default 切批：{len(batches)} 批，共 {sum(len(b.splitlines())-1 for b in batches)} 行")

    batch_of: dict[int, int] = {}
    batch_docs: dict[int, Counter] = {}
    for bi, text in enumerate(batches):
        lines = text.splitlines()
        start = next((k for k, ln in enumerate(lines) if ln.startswith("id|")), None)
        if start is None:
            continue
        df = pd.read_csv(io.StringIO("\n".join(lines[start:])), sep="|")
        ids = [int(x) for x in df["id"].tolist()]
        for i in ids:
            batch_of[i] = bi
        batch_docs[bi] = Counter(dom.get(i, "?") for i in ids)

    print()
    uniq = sorted(set(cited_ids))
    print(f"答案（头部截取）里引用的唯一 id 共 {len(uniq)} 个")
    hit = [i for i in uniq if i in batch_of]
    print(f"其中能在重建批次里定位到的: {len(hit)}/{len(uniq)}")
    per_batch = Counter(batch_of[i] for i in hit)
    print(f"它们落在 {len(per_batch)} 个不同批次里（答复只截取了前 400 字，所以样本偏小）")
    print("  每批引用数:", dict(sorted(per_batch.items())))
    print()
    print("被引用的 id 各自所属批次里，那一批的域构成：")
    for bi in sorted(per_batch):
        cited_here = [i for i in hit if batch_of[i] == bi]
        print(f"  批 {bi:2d}: 批内域 {dict(batch_docs[bi].most_common())}")
        print(f"          该批被引用的 id {cited_here} → 它们的域 {[dom.get(i) for i in cited_here]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
