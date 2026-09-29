"""两臂的类型体系对比 + 基于该对比的实体数量比较（0039 §2.3 的底稿）。

三块：
  ① 两张类型表并置（B 的 10 类 vs A2 的 10 类），标出同名类型；
  ② 数据驱动的对齐：用**实体名**把两臂的实体配对（同名即同一实体），
     得到"B 的类 → A2 的类"交叉表（不靠我先验映射）；
  ③ 能等口径比的三个量：实体名对账（共有 / 仅 B / 仅 A2）、同名类型各自的数量、
     以及"落在对方没有的类型里"的占比。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

REPO = Path("D:/lida-data/vscode/ai-study-helper")
B = REPO / "data/tmp/graphrag-exp-multihop/output/entities.parquet"
A2 = REPO / "data/tmp/graphrag-exp-multihop-autodomain-selfconsistent/output/entities.parquet"


def main() -> int:
    eb, ea = pd.read_parquet(B), pd.read_parquet(A2)
    cb, ca = Counter(eb.type), Counter(ea.type)
    tb, ta = dict(zip(eb.title, eb.type)), dict(zip(ea.title, ea.type))

    print("=== ① 两张类型表 ===")
    print(f"{'B（手工给域）':<34}{'数量':>6}{'占比':>8}   |  {'A2（自动域）':<30}{'数量':>6}{'占比':>8}")
    rows = max(len(cb), len(ca))
    left, right = cb.most_common(), ca.most_common()
    for i in range(rows):
        lb = f"{left[i][0]:<34}{left[i][1]:>6}{left[i][1]/len(eb):>7.1%}" if i < len(left) else " " * 48
        lr = f"{right[i][0]:<30}{right[i][1]:>6}{right[i][1]/len(ea):>7.1%}" if i < len(right) else ""
        print(f"{lb}   |  {lr}")
    shared_types = set(cb) & set(ca)
    print(f"\n同名类型：{sorted(shared_types)}")
    print(f"B 独有类型里的实体：{sum(v for k, v in cb.items() if k not in shared_types):,} / {len(eb):,}"
          f"（{sum(v for k, v in cb.items() if k not in shared_types)/len(eb):.0%}）")
    print(f"A2 独有类型里的实体：{sum(v for k, v in ca.items() if k not in shared_types):,} / {len(ea):,}"
          f"（{sum(v for k, v in ca.items() if k not in shared_types)/len(ea):.0%}）")

    print("\n=== ② 按实体名配对后的交叉表（B 类 → A2 类，前 12 条） ===")
    shared = set(tb) & set(ta)
    ct = Counter((tb[t], ta[t]) for t in shared)
    for (b, a), n in ct.most_common(12):
        print(f"   {b:<26} → {a:<28} {n:>5}")

    print("\n=== ③ 数量对比（等口径的三块） ===")
    print(f"实体名对账：共有 {len(shared):,}（占 B {len(shared)/len(tb):.0%}、占 A2 {len(shared)/len(ta):.0%}）"
          f" | 仅 B {len(set(tb)-set(ta)):,} | 仅 A2 {len(set(ta)-set(tb)):,}")
    for t in sorted(shared_types):
        print(f"同名类型 {t}：B {cb[t]:,} → A2 {ca[t]:,}（{(ca[t]/cb[t]-1):+.0%}）")
    only_a2 = set(ta) - set(tb)
    print("仅 A2 才有的实体，按 A2 类型分布：", dict(Counter(ta[t] for t in only_a2).most_common()))
    only_b = set(tb) - set(ta)
    print("仅 B 才有的实体，按 B 类型分布：", dict(Counter(tb[t] for t in only_b).most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
