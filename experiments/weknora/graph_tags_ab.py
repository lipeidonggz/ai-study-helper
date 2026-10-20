"""A/B：KB 的 ExtractConfig 里配不配 `tags`（关系类型白名单）+ 示例，关系会不会被抽出来。

背景（代码）：
  - 实际提示词 = 全局 config.yaml `extract_graph.description`（Step 1 实体 / Step 2 关系，
    其中 Step 2 写 "Allowed relationship types are: %s."）+ KB 的 ExtractConfig.Tags/Examples。
  - QAPromptGenerator.System(): Tags 为空时**不做 %s 替换**（`%s` 原样留在提示词里），
    且 Examples 只要 len>0 就会渲染——我们从 API 建库时 Examples 是 [{空}]，
    所以模型拿到的是"关系类型列表 = %s" + 一个空示例。
预期：tags 配好后，relations_added > 0。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import graph_perfile_probe as P  # noqa: E402

KB = "exp-graph-tags"
DOC = P.ROOT / "docs" / "publish" / "0042-llmwiki-overview.md"
ROMEO = (
    '"Romeo and Juliet" is a tragedy written by William Shakespeare early in his career. '
    'It was among Shakespeare\'s most popular plays during his lifetime. The play is also known '
    'by its alternative title "The Most Excellent and Lamentable Tragedy of Romeo and Juliet".'
)
EXAMPLE = {
    "text": ROMEO,
    "nodes": [
        {"name": "Romeo and Juliet", "attributes": ["A tragedy by William Shakespeare"]},
        {"name": "William Shakespeare", "attributes": ["Playwright", "Author of Romeo and Juliet"]},
        {"name": "The Most Excellent and Lamentable Tragedy of Romeo and Juliet",
         "attributes": ["Alternative title for Romeo and Juliet"]},
    ],
    "relations": [
        {"node1": "Romeo and Juliet", "node2": "William Shakespeare", "type": "Author"},
        {"node1": "Romeo and Juliet",
         "node2": "The Most Excellent and Lamentable Tragedy of Romeo and Juliet", "type": "Alias"},
    ],
}


def main() -> None:
    kb = P.find_kb_named(KB) if hasattr(P, "find_kb_named") else None
    if kb is None:
        status, data = P.post("/knowledge-bases", {
            "name": KB,
            "description": "图谱 tags A/B：配了关系类型白名单与示例",
            "type": "wiki",
            "embedding_model_id": P.EMBEDDING_MODEL_ID,
            "summary_model_id": P.CHAT_MODEL_ID,
            "indexing_strategy": {"vector_enabled": True, "keyword_enabled": True,
                                  "wiki_enabled": False, "graph_enabled": True},
            "extract_config": {"enabled": True, "tags": ["Author", "Alias"], **EXAMPLE},
        })
        print("create HTTP", status, str(data)[:200])
        _, lst = P.get("/knowledge-bases")
        rows = (lst or {}).get("data") if isinstance(lst, dict) else []
        kb = next((r for r in rows or [] if r.get("name") == KB), None)
    print("kb:", kb and kb.get("id"),
          "| capabilities:", json.dumps((kb or {}).get("capabilities"), ensure_ascii=False),
          "| extract_config:", json.dumps((kb or {}).get("extract_config"), ensure_ascii=False)[:120])
    kb_id = kb["id"]

    _, data = P.get(f"/knowledge-bases/{kb_id}/knowledge")
    rows = (data or {}).get("data") if isinstance(data, dict) else []
    if not rows:
        status, resp = P.post(f"/knowledge-bases/{kb_id}/knowledge/manual",
                              {"title": DOC.stem, "content": DOC.read_text(encoding="utf-8"),
                               "status": "publish"})
        print("upload HTTP", status, str(resp)[:160])
    deadline = time.time() + 20 * 60
    while time.time() < deadline:
        _, data = P.get(f"/knowledge-bases/{kb_id}/knowledge")
        rows = (data or {}).get("data") if isinstance(data, dict) else []
        st = [(r.get("title"), r.get("parse_status")) for r in rows or []]
        if rows and all(x[1] == "completed" for x in st):
            print("parse done:", st)
            break
        time.sleep(10)
    time.sleep(20)
    print("=== 该库的关系数（Neo4j）===")
    print(P.cypher(f"MATCH (n) WHERE n.kg = '{kb_id}' RETURN count(n) AS nodes"))
    print(P.cypher(f"MATCH (a)-[r]->(b) WHERE a.kg = '{kb_id}' RETURN count(r) AS rels, "
                   "collect(DISTINCT type(r))[0..10] AS types"))


if __name__ == "__main__":
    main()
