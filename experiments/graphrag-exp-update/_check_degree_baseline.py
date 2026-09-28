"""关键对照：degree 与「按边表实数」的不一致，是增量引入的，还是基线索引自己就有？"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / "graphrag-exp-update-base" / "output"


def report(tag: str, d: Path) -> tuple[int, int, list[str]]:
    e = pd.read_parquet(d / "entities.parquet")
    r = pd.read_parquet(d / "relationships.parquet")
    fact: dict[str, int] = {t: 0 for t in e["title"]}
    for _, x in r.iterrows():
        lo, hi = sorted((x["source"], x["target"]))
        if lo in fact:
            fact[lo] += 1
        if hi in fact:
            fact[hi] += 1
    deg = dict(zip(e["title"], e["degree"]))
    mism = [t for t in e["title"] if int(deg[t]) != fact[t]]
    print(f"{tag:<12} 实体 {len(e):>4}  关系 {len(r):>4}  degree 与边表实数不符：{len(mism):>3} 个")
    for t in sorted(mism, key=lambda x: fact[x] - int(deg[x]), reverse=True)[:6]:
        print(f"    {t[:32]:<34} 表里={int(deg[t]):>3} 实数={fact[t]:>3}")
    return len(e), len(mism), mism


def main() -> None:
    ts = sorted(p.name for p in (ROOT / "update_output").iterdir() if p.is_dir())[-1]
    print("=== 三个产物分别量一遍 ===")
    report("基线索引", BASE)
    print()
    report("previous", ROOT / "update_output" / ts / "previous")
    print()
    report("delta", ROOT / "update_output" / ts / "delta")
    print()
    report("合并后", ROOT / "output")


if __name__ == "__main__":
    main()
