"""查：串味（幻觉）实体落在哪些社区、为什么没进报告。只读。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"

TEXT = (ROOT / "input" / "a5.txt").read_text(encoding="utf-8").lower()

ent = pd.read_parquet(OUT / "entities.parquet")
com = pd.read_parquet(OUT / "communities.parquet")
rep = pd.read_parquet(OUT / "community_reports.parquet")

id2title = dict(zip(ent["id"], ent["title"], strict=False))
contaminated = {
    t for t in ent["title"] if t.strip().lower() not in TEXT
}

print(f"名字不在原文中的实体：{len(contaminated)} / {len(ent)}")
print("\n它们分布在哪些社区（community / level / size / 有无报告）：")
have_report = set(rep["community"])
rows = []
for _, c in com.iterrows():
    ids = c["entity_ids"]
    ids = list(ids) if ids is not None and len(ids) > 0 else []
    members = [id2title.get(i, "?") for i in ids]
    hits = [m for m in members if m in contaminated]
    if hits:
        rows.append(
            {
                "community": int(c["community"]),
                "level": int(c["level"]),
                "size": int(c["size"]),
                "n_contaminated": len(hits),
                "has_report": int(c["community"]) in have_report,
                "examples": ", ".join(hits[:4]),
            }
        )
print(pd.DataFrame(rows).sort_values(["level", "community"]).to_string(index=False) if rows else "（没有）")

print("\n这些实体的 degree 分布（孤立点不会被社区上下文优先带上）：")
d = ent[ent["title"].isin(contaminated)]["degree"]
print(d.value_counts().sort_index().to_string())
