"""查：哪些社区没有报告、它们的共同特征是什么。只读。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
com = pd.read_parquet(ROOT / "output" / "communities.parquet")
rep = pd.read_parquet(ROOT / "output" / "community_reports.parquet")

have = set(rep["community"])
print(f"社区 {len(com)} 个，报告 {len(rep)} 份")
missing = com[~com["community"].isin(have)]
print("\n没有报告的社区：")
cols = [c for c in ("community", "level", "parent", "children", "size", "entity_ids") if c in missing.columns]
if len(missing):
    m = missing[cols].copy()
    if "entity_ids" in m.columns:
        m["n_entities"] = m["entity_ids"].apply(len)
        m = m.drop(columns=["entity_ids"])
    print(m.sort_values(["level", "community"]).to_string(index=False))
print("\n对照：有报告的社区的 size 分布")
have_df = com[com["community"].isin(have)]
print(f"  size: min={have_df['size'].min()} median={int(have_df['size'].median())} max={have_df['size'].max()}")
print("\n各 level 的社区数 vs 报告数:")
print(pd.DataFrame({"communities": com.groupby("level").size(), "reports": rep.groupby("level").size()}).to_string())
