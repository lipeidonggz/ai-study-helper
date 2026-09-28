"""只读：核实 relationships 表里 description / weight 的实际类型与合并痕迹。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "output-v4"
    df = pd.read_parquet(ROOT / which / "relationships.parquet")
    print("dtypes:\n", df.dtypes.to_string())
    print("\ndescription 类型抽样:")
    for v in df["description"].head(4):
        print(f"  {type(v).__name__}: {str(v)[:120]}")
    print("\nweight 统计:", df["weight"].describe().to_dict())
    print("\n同一对 (source,target) 出现次数最大值:",
          df.groupby(["source", "target"]).size().max())
    multi = df[df["text_unit_ids"].apply(lambda x: len(x) > 1)]
    print(f"\ntext_unit_ids 多于 1 个的关系数: {len(multi)}")
    for _, r in multi.head(4).iterrows():
        print(f"  {r['source']} → {r['target']}  weight={r['weight']}  units={len(r['text_unit_ids'])}")
        print(f"    desc: {str(r['description'])[:200]}")


if __name__ == "__main__":
    main()
