"""只读探测：核实 `use_lcc` 的实际后果——有多少实体在聚类前被排除、之后进了几个社区。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "output-v4"
    out = ROOT / which
    ents = pd.read_parquet(out / "entities.parquet")
    rels = pd.read_parquet(out / "relationships.parquet")
    coms = pd.read_parquet(out / "communities.parquet")

    in_edges = set(rels["source"]) | set(rels["target"])
    print(f"[{which}] entities={len(ents)}  relationships={len(rels)}  communities={len(coms)}")
    print(f"  关系表中出现的实体名: {len(in_edges)}")
    print(f"  entities 表里但没出现在任何关系里: {len(ents) - len(set(ents['title']) & in_edges)}")
    no_edge = sorted(set(ents["title"]) - in_edges)
    print("    —— 其中前 20 个:", no_edge[:20])

    print("\n  communities 的列:", list(coms.columns))
    if "entity_ids" in coms.columns:
        member = set()
        for ids in coms["entity_ids"]:
            if hasattr(ids, "__len__"):
                member |= set(ids)
        print(f"  被任何社区收录的实体数: {len(member)}")
        print(f"  entities 表里、但不在任何社区里的实体数: {len(ents) - len(member)}")

        if "title" in ents.columns:
            # entities 表的 id 字段
            title_by_id = dict(zip(ents["id"], ents["title"]))
            off = [title_by_id.get(i, i) for i in set(ents["id"]) - member]
            print("    —— 这些实体（前 25）:")
            for t in sorted(off)[:25]:
                print("       ·", t)

    print("\n  最大连通片检查（用连通分量工具直接算）:")
    from graphrag.graphs.connected_components import largest_connected_component

    lcc = largest_connected_component(rels)
    print(f"  关系图的节点数: {len(in_edges)}   最大连通片节点数: {len(lcc)}")
    print(f"  ⇒ 被 use_lcc 排除的节点数: {len(in_edges) - len(lcc)}")


if __name__ == "__main__":
    main()
