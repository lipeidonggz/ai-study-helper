"""合并后每个条目的描述条数构成：到底哪些条目会真的调 LLM。

关键：描述条数只取决于"这个条目出现在几批里"，与新旧无关——
  只在旧批次 ⇒ 1 条；只在新批次 ⇒ 1 条；两批都有 ⇒ 2 条 ⇒ 才会调模型。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
TS = "20260926-120449"  # 第一次（成功的那次）增量


def main() -> None:
    prev = ROOT / "update_output" / TS / "previous"
    delta = ROOT / "update_output" / TS / "delta"
    out = ROOT / "output"

    pe = pd.read_parquet(prev / "entities.parquet")
    de = pd.read_parquet(delta / "entities.parquet")
    pr = pd.read_parquet(prev / "relationships.parquet")
    dr = pd.read_parquet(delta / "relationships.parquet")
    me = pd.read_parquet(out / "entities.parquet")
    mr = pd.read_parquet(out / "relationships.parquet")

    e_prev, e_delta = set(pe["title"]), set(de["title"])
    r_prev = set(zip(pr["source"], pr["target"]))
    r_delta = set(zip(dr["source"], dr["target"]))

    rows = []
    for label, prevset, deltaset, total in [
        ("实体", e_prev, e_delta, len(me)),
        ("关系", r_prev, r_delta, len(mr)),
    ]:
        both = prevset & deltaset
        old_only = prevset - deltaset
        new_only = deltaset - prevset
        rows.append(
            {
                "类别": label,
                "合并后条目数": total,
                "只在旧批次（1 条描述）": len(old_only),
                "只在新批次（1 条描述）": len(new_only),
                "两批重合（2 条描述⇒调模型）": len(both),
            }
        )

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()
    walked = int(df["合并后条目数"].sum())
    calls = int(df["两批重合（2 条描述⇒调模型）"].sum())
    print(f"进度条要走一遍的条目总数 = {walked}（实体 {len(me)} + 关系 {len(mr)}）")
    print(f"其中描述条数 ≥ 2、会真的调模型的 = {calls}")
    print(f"⇒ 只有 1 条描述的一共 {walked - calls} 个（旧独有 + 新独有，两类都不调模型）")
    print()
    print("两批重合的实体：", sorted(e_prev & e_delta))
    print("两批重合的关系对：", sorted(r_prev & r_delta) or "（无）")


if __name__ == "__main__":
    main()
