"""只读：核实 communities.parquet 到底是"一棵树"还是"森林"（几个根节点）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not (p / "communities.parquet").exists():
            print(f"{arg}: 没有 communities.parquet")
            continue
        c = pd.read_parquet(p / "communities.parquet")
        roots = c[c["parent"] < 0]
        print(f"== {p.name} ==")
        print(f"  社区总数 {len(c)}；level 分布 {c['level'].value_counts().sort_index().to_dict()}")
        print(f"  根节点（parent<0）共 {len(roots)} 个：community={sorted(roots['community'].tolist())}")
        # 每个根下面挂了多少后代
        by_parent = c.groupby("parent").size().to_dict()
        print("  每个根的子节点数:", {int(r): int(by_parent.get(r, 0)) for r in roots["community"]})
        print(f"  所有节点的 parent 都指向存在的社区吗: ", end="")
        known = set(c["community"])
        bad = [int(x) for x in c["parent"] if x >= 0 and x not in known]
        print("是" if not bad else f"否，悬空 parent={bad}")


if __name__ == "__main__":
    main()
