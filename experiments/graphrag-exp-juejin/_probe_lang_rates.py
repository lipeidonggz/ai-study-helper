"""只读：核实各轮的"中文率"与报告语言，供对外文章引用（避免串数）。"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u4e00-\u9fff]")


def has_cjk(s) -> bool:
    return bool(CJK.search(str(s or "")))


def rate(series: pd.Series, pred) -> str:
    n = len(series)
    if n == 0:
        return "0/0"
    k = int(series.apply(pred).sum())
    return f"{k}/{n} = {k / n:.0%}"


def main() -> None:
    for d in ["output-en", "output-samelang", "output-v3", "output-v4", "output"]:
        p = ROOT / d
        if not p.exists():
            continue
        e = pd.read_parquet(p / "entities.parquet")
        r = pd.read_parquet(p / "relationships.parquet")
        rep = pd.read_parquet(p / "community_reports.parquet")
        print(f"== {d} ==")
        print("  实体描述含中文 :", rate(e["description"], has_cjk))
        print("  关系描述含中文 :", rate(r["description"], has_cjk))
        print("  实体名含中文   :", rate(e["title"], has_cjk))
        print("  报告标题含中文 :", rate(rep["title"], has_cjk))
        print("  报告正文含中文 :", rate(rep["full_content"], has_cjk))
        # 正文里中文占比（字符级），用中位数避免被长尾带偏
        fracs = rep["full_content"].apply(
            lambda s: (len(CJK.findall(str(s))) / max(1, len(str(s))))
        )
        print(f"  报告正文字符级中文占比：中位 {fracs.median():.1%} 最小 {fracs.min():.1%}")
        print()


if __name__ == "__main__":
    main()
