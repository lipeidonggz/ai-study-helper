"""决定性检验：重建 create_community_reports 的上下文，看社区 0/1/14 实际被喂了什么内容。

如果上下文是 A5 的内容、而报告却是判官评测 ⇒ 报告与社区的关联错位（上游 bug）。
如果上下文本身就是判官评测 ⇒ 是 communities 表的 text_unit_ids 与聚类对不上。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from graphrag.index.operations.summarize_communities.explode_communities import (
    explode_communities,
)
from graphrag.index.operations.summarize_communities.graph_context.context_builder import (
    build_local_context,
)
from graphrag.index.workflows.create_community_reports import _prep_edges, _prep_nodes
from graphrag.tokenizer.get_tokenizer import get_tokenizer

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


class NoopProgress:
    def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class NoopCallbacks:
    progress = NoopProgress()


def main() -> int:
    communities = pd.read_parquet(OUT / "communities.parquet")
    entities = pd.read_parquet(OUT / "entities.parquet")
    relationships = pd.read_parquet(OUT / "relationships.parquet")

    nodes = _prep_nodes(explode_communities(communities, entities))
    edges = _prep_edges(relationships)
    local = build_local_context(nodes, edges, None, get_tokenizer(), NoopCallbacks(), max_context_tokens=8000)
    print("local_context 的列:", list(local.columns))

    for cid in (0, 1, 14, 178):
        row = local[local["community"] == cid]
        if row.empty:
            print(f"社区 {cid}: 不在 local_context 里")
            continue
        row = row.iloc[0]
        ctx = str(row["context_string"])
        ent_titles = [e.get("title") for e in str(row.get("all_context", ""))[:0]] if False else None
        print(f"\n=== 社区 {cid}（L{int(row['level'])}）context_size={row['context_size']} 超限={row['context_exceed_limit']}")
        print("  上下文前 500 字:", repr(ctx[:500]))
        # 该社区包含的实体名（从 nodes 里取）
        names = nodes[nodes["community"] == cid]["title"].tolist()
        print(f"  该社区的实体（{len(names)} 个，前 12）: {names[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
