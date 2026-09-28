"""打印某个社区的原料：实体（名/类型/描述）与关系（source/target/描述/权重）。

用法：python _dump_community.py 8
只读 parquet。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def as_list(v) -> list:
    if v is None:
        return []
    return list(v)


def main() -> None:
    cid = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    com = pd.read_parquet(ROOT / "output" / "communities.parquet")
    ent = pd.read_parquet(ROOT / "output" / "entities.parquet")
    rel = pd.read_parquet(ROOT / "output" / "relationships.parquet")
    rep = pd.read_parquet(ROOT / "output" / "community_reports.parquet")

    c = com[com["community"] == cid].iloc[0]
    eids = as_list(c["entity_ids"])
    rids = as_list(c["relationship_ids"])
    print(f"社区 #{cid} · level={int(c['level'])} · size={int(c['size'])}")
    print(f"  entity_ids={len(eids)}  relationship_ids={len(rids)}")
    r = rep[rep["community"] == cid]
    if len(r):
        r = r.iloc[0]
        print(f"  报告标题: {r['title']}")
        print(f"  报告正文前 120 字: {str(r['full_content'])[:120]!r}")

    print("\n=== 实体 ===")
    for _, e in ent[ent["id"].isin(eids)].iterrows():
        print(f"\n· {e['title']}   [{e.get('type')}]  degree={e.get('degree')}  freq={e.get('frequency')}")
        print(f"  描述: {str(e.get('description'))[:400]}")

    print("\n=== 关系 ===")
    for _, x in rel[rel["id"].isin(rids)].iterrows():
        print(f"\n· {x.get('source')}  --[{x.get('weight')}]-->  {x.get('target')}")
        print(f"  描述: {str(x.get('description'))[:300]}")


if __name__ == "__main__":
    main()
