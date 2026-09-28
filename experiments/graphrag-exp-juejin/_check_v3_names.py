"""v3 里"不含中文的实体名"到底是漏改，还是原文本来就是英文术语（应当保留）。

判据：实体名（小写）能否在原文里找到 → 能找到＝合法保留；找不到＝仍可疑。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u4e00-\u9fff]")
doc = (ROOT / "input" / "doc.txt").read_text(encoding="utf-8").lower()
prod = (ROOT / "prompts" / "extract_graph.txt").read_text(encoding="utf-8").lower()

ent = pd.read_parquet(ROOT / "output" / "entities.parquet")
# 别用 .str.contains(r"[\u4e00-\u9fff]")：pyarrow 后端不接受 \u 转义，改在 Python 层过滤
en = ent[[not CJK.search(str(t)) for t in ent["title"]]]


def in_doc(name: str) -> bool:
    n = name.strip().lower()
    if n in doc:
        return True
    words = [w for w in re.split(r"[^a-z0-9.]+", n) if len(w) > 2]
    return bool(words) and all(w in doc for w in words)


rows = [
    {
        "title": t,
        "degree": d,
        "in_doc": in_doc(str(t)),
        "in_prompt_examples": str(t).strip().lower() in prod,
    }
    for t, d in zip(en["title"], en["degree"], strict=False)
]
df = pd.DataFrame(rows)
print(f"总实体 {len(ent)}；不含中文的实体名 {len(df)} 个")
if len(df):
    print(f"  其中能在原文里找到（属合法保留）: {int(df['in_doc'].sum())}")
    print(f"  原文里找不到（仍可疑）          : {int((~df['in_doc']).sum())}")
    print(f"  命中提示词示例（串味）          : {int(df['in_prompt_examples'].sum())}")
    print("\n明细：")
    print(df.sort_values(["in_doc", "degree"], ascending=[True, False]).to_string(index=False))
