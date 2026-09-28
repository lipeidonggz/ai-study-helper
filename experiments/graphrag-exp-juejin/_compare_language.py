"""对比"改语言要求"前后：规模 + 语言分布。只读两个 output 目录。"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u4e00-\u9fff]")


def zh_ratio(series: pd.Series) -> str:
    if len(series) == 0:
        return "n/a"
    n = sum(1 for v in series if CJK.search(str(v)))
    return f"{n}/{len(series)}（{n / len(series):.0%}）"


def report(name: str, out: Path) -> None:
    tables = {p.stem: pd.read_parquet(p) for p in out.glob("*.parquet")}
    ent, rel, rep = tables["entities"], tables["relationships"], tables["community_reports"]
    print(f"\n===== {name}  ({out.name})")
    print(
        f"  规模: text_units={len(tables['text_units'])} entities={len(ent)} "
        f"relationships={len(rel)} communities={len(tables['communities'])} reports={len(rep)}"
    )
    print(f"  实体名含中文      : {zh_ratio(ent['title'])}")
    print(f"  实体描述含中文    : {zh_ratio(ent['description'].fillna(''))}")
    print(f"  关系描述含中文    : {zh_ratio(rel['description'].fillna(''))}")
    print(f"  报告标题含中文    : {zh_ratio(rep['title'])}")
    print(f"  报告正文含中文    : {zh_ratio(rep['full_content'].fillna(''))}")
    print(f"  rank 中位/范围    : {rep['rank'].median()}  ({rep['rank'].min()}–{rep['rank'].max()})")
    lens = rep["full_content"].str.len()
    print(f"  正文长度 中位/最大: {int(lens.median()):,} / {lens.max():,}")
    print("  实体名样例:", ", ".join(ent["title"].head(8).tolist()))


def main() -> None:
    report("v1 默认提示词（要求 English）", ROOT / "output-en")
    report("v2 只改语言句（same language）", ROOT / "output-samelang")
    report("v3 抽取三刀（中文示例＋去 capitalized＋Do not translate）", ROOT / "output-v3")
    report("v4 = v3 ＋ 报告提示词翻译成中文", ROOT / "output-v4")
    report("v5 = prompt-tune 生成的三份提示词 + 14 类领域类型", ROOT / "output")


if __name__ == "__main__":
    main()
