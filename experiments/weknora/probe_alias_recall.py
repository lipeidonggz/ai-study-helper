"""实测：页面的 aliases 到底参不参与去重召回？

做法（在已跑完的 P6+P7 库里）：
    1. 记下当前页集合；
    2. 投一篇只讲 "Retrieval-Augmented Generation" 的英文小文（不用中文名）；
    3. 等 wiki 编译完，看页集合里是否多出一个 RAG 页。

背景：该库里已有 `concept/rag`，title=检索增强生成，aliases=["RAG","Retrieval-Augmented Generation"]。
如果 aliases 参与召回 ⇒ 这篇新文应当并入 `concept/rag`（页数不变）。
如果只有 title 参与召回 ⇒ trigram 相似度 0（英文 vs 中文），新文会另建一页（孪生页）。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_alias_recall.py [--pages-only]
输出：
    data/tmp/weknora-multisource/alias-recall-probe.json
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-multisource"

KB_NAME = "P6+P7 · 多源页读感"
TITLE = "Z1 · RAG in practice"

CONTENT = """# Retrieval-Augmented Generation in practice

Retrieval-Augmented Generation (RAG) combines a retrieval step with a generative model. A typical pipeline has three parts: an index of document chunks, a retriever that returns the top-k chunks for a query, and a generator that conditions its answer on those chunks.

Common failure modes of Retrieval-Augmented Generation:

- The retriever returns chunks that are semantically close to the query but do not contain the answer.
- The generator ignores the retrieved context and answers from parametric memory instead.

Evaluation for Retrieval-Augmented Generation usually separates retrieval metrics (recall@k, MRR) from generation metrics (faithfulness, answer relevance). Teams commonly annotate a small set of query/chunk pairs and track retrieval recall before tuning the generator.
"""

# 变体：全文只写全称、绝不出现缩写 "RAG" ⇒ 让 Pass 0 只能把条目命名成全称，
# 从而 slug 与已有页 concept/rag 不同（不靠 slug 撞车），单独检验"别名能不能召回"。
CONTENT_LONG = """# Retrieval-Augmented Generation in production

Retrieval-Augmented Generation is a design pattern that pairs a retriever with a text generator. The retriever selects passages from an index; the generator conditions its answer on those passages instead of relying only on parametric memory.

Two failure modes dominate field reports. First, the retriever returns passages that are topically close but do not contain the answer. Second, the generator ignores the retrieved passages and answers from parametric memory anyway.

Evaluation for Retrieval-Augmented Generation separates retrieval quality (recall at k, mean reciprocal rank) from generation quality (faithfulness to the retrieved passages, answer relevance). A small annotated set of query/passage pairs is usually enough to track retrieval quality before tuning the generator.
"""


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _request(req: urllib.request.Request):
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")


def get(path: str):
    return _request(urllib.request.Request(BASE + path, headers={"X-API-Key": api_key()}))


def post_json(path: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request(
        urllib.request.Request(
            BASE + path,
            data=body,
            headers={"X-API-Key": api_key(), "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
    )


def find_kb() -> dict | None:
    status, data = get("/knowledge-bases")
    for kb in (data or {}).get("data") or []:
        if kb.get("name") == KB_NAME:
            return kb
    return None


def pages_snapshot(kb_id: str) -> dict[str, dict]:
    status, data = get(f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=300")
    pages = (data or {}).get("pages") or []
    return {
        p["slug"]: {
            "title": p.get("title"),
            "source_refs": len(p.get("source_refs") or []),
            "chars": len(p.get("content") or ""),
        }
        for p in pages
    }


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    kb = find_kb()
    if not kb:
        raise SystemExit("找不到目标知识库，先确认 WeKnora 在跑")
    kb_id = kb["id"]
    content = CONTENT_LONG if "--long" in sys.argv else CONTENT
    title = "Z2 · RAG in production（只写全称）" if "--long" in sys.argv else TITLE

    before = pages_snapshot(kb_id)
    print(f"投喂前的页数：{len(before)}")
    rag_before = {s: v for s, v in before.items() if "rag" in s.lower() or "检索增强" in (v.get("title") or "")}
    for s, v in rag_before.items():
        print(f"  已有 RAG 相关页：{s}  title={v['title']}  refs={v['source_refs']}")

    if "--pages-only" in sys.argv:
        return

    status, data = post_json(
        f"/knowledge-bases/{kb_id}/knowledge/manual",
        {"title": title, "content": content, "status": "publish"},
    )
    kid = ((data or {}).get("data") or {}).get("id")
    print(f"投喂 {title}：HTTP {status} knowledge_id={kid}（{len(content)} 字符）")

    deadline = time.time() + 20 * 60
    stable = 0
    last = -1
    while time.time() < deadline:
        _, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
        _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
        rows = [r for r in ((know or {}).get("data") or []) if r.get("id") == kid]
        state = rows[0].get("parse_status") if rows else "?"
        total = (stats or {}).get("total_pages")
        print(f"[{time.strftime('%H:%M:%S')}] pages={total} new_doc_state={state}", flush=True)
        if isinstance(total, int) and total == last and state in {"completed", "success"}:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last = total if isinstance(total, int) else last
        time.sleep(20)

    after = pages_snapshot(kb_id)
    new_slugs = sorted(set(after) - set(before))
    changed = {
        s: {"before": before[s], "after": after[s]}
        for s in set(after) & set(before)
        if before[s] != after[s]
    }
    rag_after = {s: v for s, v in after.items() if "rag" in s.lower() or "检索增强" in (v.get("title") or "")}

    print(f"\n投喂后页数：{len(after)}（+{len(after) - len(before)}）")
    print("新增页：")
    for s in new_slugs:
        print(f"  + {s}  title={after[s]['title']}  refs={after[s]['source_refs']}  chars={after[s]['chars']}")
    print("被改动的页：")
    for s, v in changed.items():
        print(f"  ~ {s}: refs {v['before']['source_refs']}→{v['after']['source_refs']}, chars {v['before']['chars']}→{v['after']['chars']}")
    print("RAG 相关页（投喂后）：")
    for s, v in rag_after.items():
        print(f"  * {s}  title={v['title']}  refs={v['source_refs']}  chars={v['chars']}")

    # ⚠️ 端到端探针的判读要人工做：本库输出语言是中文，模型会把英文文档里的概念也命名成
    # 中文（例："检索增强生成"），于是与旧页**标题完全相同** ⇒ 走精确身份层就地更新。
    # 这会把"别名不参与召回"这个缺口掩盖掉，所以这里的自动判读只报事实，不下结论。
    rag_touched = changed.get("concept/rag") or (after.get("concept/rag") != before.get("concept/rag"))
    verdict = (
        "concept/rag 被就地更新（命中精确身份层或 slug 同名），未产生孪生页"
        if rag_touched
        else "concept/rag 未动；需人工看新增页是否与它同指（自动判读不可靠）"
    )
    print(f"\n结论：{verdict}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "alias-recall-probe.json").write_text(
        json.dumps(
            {
                "kb": kb_id,
                "knowledge_id": kid,
                "pages_before": len(before),
                "pages_after": len(after),
                "new_slugs": new_slugs,
                "changed": changed,
                "rag_pages_after": rag_after,
                "verdict": verdict,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"已写 {OUT_DIR / 'alias-recall-probe.json'}")


if __name__ == "__main__":
    main()
