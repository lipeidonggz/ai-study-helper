"""只读探测：把 output-v4 的 relationships 表按需打印出来，供 0033 引用真实样例。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "output-v4"
    df = pd.read_parquet(ROOT / which / "relationships.parquet")
    print(f"[{which}] relationships rows={len(df)}")
    print("columns:", list(df.columns))
    print()

    def show(row: pd.Series) -> None:
        print(f"  {row['source']}  --[{row['predicate'] if 'predicate' in row else '?'}]-->  {row['target']}")
        print(f"    description : {str(row.get('description', ''))[:200]}")
        print(f"    weight      : {row.get('weight')}   combined_degree: {row.get('combined_degree')}")
        tu = row.get("text_unit_ids")
        print(f"    text_units  : {len(tu) if hasattr(tu, '__len__') else tu}")
        print()

    print("== 含 LLM-AS-JUDGE / judge 的关系 ==")
    mask = df["source"].str.contains("JUDGE", case=False, na=False) | df[
        "target"
    ].str.contains("JUDGE", case=False, na=False)
    for _, r in df[mask].head(10).iterrows():
        show(r)

    print("== 含 RAG 的关系 ==")
    mask = df["source"].str.contains("RAG", case=False, na=False) | df["target"].str.contains(
        "RAG", case=False, na=False
    )
    for _, r in df[mask].head(10).iterrows():
        show(r)

    print("== 同一实体对是否有多条关系（source/target 组合重复） ==")
    pairs = df.groupby(["source", "target"]).size().sort_values(ascending=False)
    print(pairs.head(8).to_string())

    print("\n== 度数最高的实体（按 source/target 出现次数） ==")
    deg = pd.concat([df["source"], df["target"]]).value_counts()
    print(deg.head(12).to_string())


if __name__ == "__main__":
    main()
