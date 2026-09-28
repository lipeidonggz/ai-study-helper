"""列出某个输出目录里的社区报告清单（社区号/层级/size/rank/findings/标题）。只读。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "output")

com = pd.read_parquet(OUT / "communities.parquet")
rep = pd.read_parquet(OUT / "community_reports.parquet").sort_values(["level", "community"])

have = set(rep["community"])
missing = sorted(set(com["community"]) - have)
print(f"输出目录 {OUT.name}：社区 {len(com)} 个 → 报告 {len(rep)} 份；缺 {len(missing)} 个 {missing}")
print("rank 中位 %.1f · 范围 %.1f–%.1f" % (rep["rank"].median(), rep["rank"].min(), rep["rank"].max()))
print()
for _, x in rep.iterrows():
    n = len(x["findings"]) if hasattr(x["findings"], "__len__") else 0
    pa = int(x["parent"])
    print(
        f"  #{int(x['community']):<3} L{int(x['level'])} "
        f"parent={'—' if pa < 0 else '#' + str(pa):<4} size={int(x['size']):<3} "
        f"rank={x['rank']:<4} findings={n:<3} {str(x['title'])[:56]}"
    )
