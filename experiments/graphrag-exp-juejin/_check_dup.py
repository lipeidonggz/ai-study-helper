"""查报告字段是否天然重复：summary / findings 是否就是 full_content 的内容。

只读 parquet。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
rep = pd.read_parquet(ROOT / "output" / "community_reports.parquet")

print(f"报告数 {len(rep)}；列 = {list(rep.columns)}\n")

summary_in = 0
findings_in = 0
for _, r in rep.iterrows():
    fc = str(r["full_content"])
    s = str(r["summary"]).strip()
    if s and s[:120] in fc:
        summary_in += 1
    fs = r["findings"]
    if hasattr(fs, "__len__") and len(fs) and isinstance(fs[0], dict):
        ok = sum(
            1 for f in fs if str(f.get("explanation", ""))[:120] in fc
        )
        if ok == len(fs):
            findings_in += 1

print(f"summary 出现在 full_content 里：{summary_in}/{len(rep)}")
print(f"findings 全部出现在 full_content 里：{findings_in}/{len(rep)}")

r = rep.iloc[0]
print("\n===== 样例（community %s）" % r["community"])
print("--- title:\n", r["title"])
print("--- summary（前 200）:\n", str(r["summary"])[:200])
print("--- full_content（前 400）:\n", str(r["full_content"])[:400])
print("--- findings[0]:\n", str(r["findings"][0])[:300] if hasattr(r["findings"], "__len__") else "-")
print("--- full_content_json 前 300:\n", str(r["full_content_json"])[:300])
