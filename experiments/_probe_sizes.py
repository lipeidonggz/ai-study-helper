"""只读：汇总各实验产出的规模（实体/关系/社区/报告）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def count(p: Path, name: str) -> int:
    f = p / f"{name}.parquet"
    return len(pd.read_parquet(f)) if f.exists() else -1


def main() -> None:
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(f"{arg}: (不存在)")
            continue
        print(
            f"{arg:<48} entities={count(p, 'entities'):>4}  "
            f"relationships={count(p, 'relationships'):>4}  "
            f"communities={count(p, 'communities'):>3}  "
            f"reports={count(p, 'community_reports'):>3}"
        )


if __name__ == "__main__":
    main()
