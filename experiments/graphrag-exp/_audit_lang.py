"""A5 专项对照：同一篇英文语料，用「原版提示词」vs「掘金 tuned 提示词」各跑一遍，比什么？

重点：语言（实体/关系/报告）、类型分布、rank、规模。
用法：python _audit_lang.py 输出目录A 输出目录B
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u4e00-\u9fff]")


def zh_ratio(series) -> str:
    vals = [str(x) for x in series]
    if not vals:
        return "n/a"
    n = sum(1 for v in vals if CJK.search(v))
    return f"{n}/{len(vals)}（{n/len(vals):.0%}）"


def report(name: str, out: Path) -> None:
    t = {p.stem: pd.read_parquet(p) for p in out.glob("*.parquet")}
    ent, rel, rep, com = t["entities"], t["relationships"], t["community_reports"], t["communities"]
    print(f"\n===== {name}  ({out.name})")
    print(f"  规模: entities={len(ent)} relationships={len(rel)} communities={len(com)} reports={len(rep)}")
    missing = sorted(set(com["community"]) - set(rep["community"]))
    print(f"  没报告的社区: {len(missing)}/{len(com)}  {missing[:15]}")
    print(f"  实体名含中文    : {zh_ratio(ent['title'])}")
    print(f"  实体描述含中文  : {zh_ratio(ent['description'].fillna(''))}")
    print(f"  关系描述含中文  : {zh_ratio(rel['description'].fillna(''))}")
    print(f"  报告标题含中文  : {zh_ratio(rep['title'])}")
    print(f"  报告正文含中文  : {zh_ratio(rep['full_content'].fillna(''))}")
    print(f"  rank 中位/范围  : {rep['rank'].median()}  ({rep['rank'].min()}–{rep['rank'].max()})")
    print("  类型分布:")
    for ty, n in ent["type"].value_counts().items():
        print(f"     {ty:<26} {n:>4} ({n/len(ent):.0%})")
    print("  实体名样例:", ", ".join(str(x)[:24] for x in ent["title"].head(10)))
    if len(rep):
        print("  报告标题样例:", " | ".join(str(x)[:40] for x in rep["title"].head(3)))


def main() -> None:
    names = sys.argv[1:] or ["output-default", "output"]
    for nm in names:
        report(nm, ROOT / nm)


if __name__ == "__main__":
    main()
