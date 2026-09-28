"""算清这次更新到底会触发多少次「重汇总」LLM 调用，以及度数陈旧面有多大。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ts = sorted(p.name for p in (ROOT / "update_output").iterdir() if p.is_dir())[-1]
    prev, delta, out = (
        ROOT / "update_output" / ts / "previous",
        ROOT / "update_output" / ts / "delta",
        ROOT / "output",
    )
    pe = pd.read_parquet(prev / "entities.parquet")
    de = pd.read_parquet(delta / "entities.parquet")
    pr = pd.read_parquet(prev / "relationships.parquet")
    dr = pd.read_parquet(delta / "relationships.parquet")
    me = pd.read_parquet(out / "entities.parquet")
    mr = pd.read_parquet(out / "relationships.parquet")

    common_e = set(pe["title"]) & set(de["title"])
    prev_pairs = set(zip(pr["source"], pr["target"]))
    delta_pairs = set(zip(dr["source"], dr["target"]))
    common_r = prev_pairs & delta_pairs

    print("=" * 78)
    print("A. 合并后「描述条数 ≥ 2」= 会真的调 LLM 的条目数")
    print("=" * 78)
    print(f"  实体：两批同 title 的 {len(common_e)} 个 ⇒ 描述 1(旧) + 1(新) = 2 条 ⇒ 调 LLM")
    print(f"  关系：两批同 (source,target) 的 {len(common_r)} 对 ⇒ 调 LLM")
    print(f"  ⇒ update_entities_relationships 这一步实际 LLM 调用 ≈ {len(common_e) + len(common_r)} 次")
    print(f"  （进度条总刻度 = 实体 {len(me)} + 关系 {len(mr)} = {len(me) + len(mr)}，这是「逐个过一遍」的刻度，不等于调用数）")
    print("\n  两批都出现的关系对（前 12）：")
    for s, t in sorted(common_r)[:12]:
        print(f"    {s[:30]:<32} → {t[:30]}")

    print()
    print("=" * 78)
    print("B. 度数陈旧面：谁被新边连上、却没被重算 degree")
    print("=" * 78)
    fact: dict[str, int] = {t: 0 for t in me["title"]}
    for _, r in mr.iterrows():
        lo, hi = sorted((r["source"], r["target"]))
        if lo in fact:
            fact[lo] += 1
        if hi in fact:
            fact[hi] += 1
    deg = dict(zip(me["title"], me["degree"]))
    mism = [t for t in me["title"] if t in fact and int(deg[t]) != fact[t]]
    print(f"  度数陈旧（表里 ≠ 合并后边表实数）：{len(mism)} 个实体")
    print(f"  其中「被 title 合并过」的（会同时被重写描述）：{len(set(mism) & common_e)} 个")
    print(f"  其中「没被合并、只是被新边连上」的：{len(set(mism) - common_e)} 个")
    print("\n  delta 关系里，端点是旧实体 title、但 delta 自己没把它当实体的：")
    delta_titles = set(de["title"])
    prev_titles = set(pe["title"])
    odd = sorted(
        {
            x
            for s, t in delta_pairs
            for x in (s, t)
            if x in prev_titles and x not in delta_titles
        }
    )
    print(f"    共 {len(odd)} 个：{odd[:12]}")


if __name__ == "__main__":
    main()
