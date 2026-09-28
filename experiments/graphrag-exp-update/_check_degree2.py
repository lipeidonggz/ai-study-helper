"""按源码口径（无向去重的度数）重量：合并后 degree 是否被刷新。

源码口径（index/workflows/finalize_graph.py 的 _build_degree_map）：
    把每条边归一成无向对并去重，每个端点 +1。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
BASE = ROOT.parent / "graphrag-exp-update-base" / "output"


def undirected_degree(r: pd.DataFrame) -> Counter:
    seen: set[tuple[str, str]] = set()
    deg: Counter = Counter()
    for _, x in r.iterrows():
        lo, hi = sorted((str(x["source"]), str(x["target"])))
        if (lo, hi) in seen:
            continue
        seen.add((lo, hi))
        deg[lo] += 1
        deg[hi] += 1
    return deg


def report(tag: str, d: Path) -> tuple[int, int, list[str]]:
    e = pd.read_parquet(d / "entities.parquet")
    r = pd.read_parquet(d / "relationships.parquet")
    truth = undirected_degree(r)
    deg = dict(zip(e["title"], e["degree"]))
    mism = [t for t in e["title"] if int(deg[t]) != truth.get(t, 0)]
    print(f"{tag:<12} 实体 {len(e):>4} 关系 {len(r):>4}  |  degree 与「无向去重度数」不符：{len(mism):>3}")
    for t in sorted(mism, key=lambda x: truth.get(x, 0) - int(deg[x]), reverse=True)[:8]:
        print(f"    {t[:32]:<34} 表里={int(deg[t]):>3} 应为={truth.get(t,0):>3} 差={truth.get(t,0)-int(deg[t]):>3}")
    return len(e), len(mism), mism


def main() -> None:
    ts = sorted(p.name for p in (ROOT / "update_output").iterdir() if p.is_dir())[-1]
    prev = ROOT / "update_output" / ts / "previous"
    delta = ROOT / "update_output" / ts / "delta"
    out = ROOT / "output"

    print("=== 用源码口径量四份产物 ===")
    _, m_base, _ = report("基线索引", BASE)
    print()
    _, m_prev, prev_mism = report("previous", prev)
    print()
    report("delta", delta)
    print()
    _, m_out, out_mism = report("合并后", out)
    print()
    print(f"结论：基线 {m_base} 处不符 → 合并后 {m_out} 处；增量新增 {m_out - m_base} 处")
    new_bad = sorted(set(out_mism) - set(prev_mism))
    print(f"新出现的问题实体（{len(new_bad)} 个）：{new_bad}")

    # 逐个看这些实体在两批里的度数
    pe = pd.read_parquet(prev / "entities.parquet")
    de = pd.read_parquet(delta / "entities.parquet")
    me = pd.read_parquet(out / "entities.parquet")
    prev_deg = dict(zip(pe["title"], pe["degree"]))
    delta_deg = dict(zip(de["title"], de["degree"]))
    merged_deg = dict(zip(me["title"], me["degree"]))
    truth = undirected_degree(pd.read_parquet(out / "relationships.parquet"))
    print("\n=== 新增问题实体的明细 ===")
    for t in new_bad:
        print(
            f"  {t[:30]:<32} 基线={int(prev_deg.get(t,-1)):>3} "
            f"delta内={int(delta_deg.get(t,-1)):>3} 合并后表里={int(merged_deg.get(t,-1)):>3} "
            f"合并后应为={truth.get(t,0):>3}"
        )


if __name__ == "__main__":
    main()
