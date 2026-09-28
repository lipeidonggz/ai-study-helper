"""v5 专项体检：类型桶分布 / 结构垃圾 / rank 分布 / 串味（对照 tuned 提示词）。

用法：python _audit_v5.py [输出目录名] [提示词目录名]   默认 output prompts-tuned
只读。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "output")
PROMPT = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "prompts-tuned") / "extract_graph.txt"

doc = (ROOT / "input" / "doc.txt").read_text(encoding="utf-8").lower()
prompt = PROMPT.read_text(encoding="utf-8").lower()

STRUCT = [
    (re.compile(r"^第\s*\d+\s*节"), "章节引用"),
    (re.compile(r"^\d+\s*号"), "产物编号"),
    (re.compile(r"^[A-Za-z]$"), "单字母"),
    (re.compile(r"^[A-Z0-9_/\-\.]+$"), "全大写枚举/字段名"),
    (re.compile(r"^\d+([.,]\d+)?%?$"), "纯数字"),
]


def in_doc(name: str) -> bool:
    n = name.strip().lower()
    if not n:
        return True
    if n in doc:
        return True
    if re.search(r"[\u4e00-\u9fff]", n):
        return False
    words = [w for w in re.split(r"[^a-z0-9]+", n) if len(w) > 2]
    return bool(words) and all(w in doc for w in words)


def main() -> None:
    print(f"输出目录 {OUT.name} · 对照提示词 {PROMPT.parent.name}/{PROMPT.name}\n")
    ent = pd.read_parquet(OUT / "entities.parquet")
    rep = pd.read_parquet(OUT / "community_reports.parquet")
    com = pd.read_parquet(OUT / "communities.parquet")
    rel = pd.read_parquet(OUT / "relationships.parquet")

    print(f"规模: entities={len(ent)} relationships={len(rel)} communities={len(com)} reports={len(rep)}")
    print("\n=== 类型分布 ===")
    vc = ent["type"].value_counts()
    for t, n in vc.items():
        print(f"  {t:<26} {n:>4}  ({n/len(ent):.0%})")

    print("\n=== 结构垃圾（章节引用/编号/单字母/全大写枚举）===")
    junk = []
    for _, e in ent.iterrows():
        name = str(e["title"])
        for rx, label in STRUCT:
            if rx.match(name.strip()):
                junk.append((label, name, e.get("type")))
                break
    if junk:
        for label, name, t in sorted(junk):
            print(f"  {label:<16} {name:<34} [{t}]")
    else:
        print("  （0 个）")

    print("\n=== 串味（名字命中 tuned 提示词、且不在原文）===")
    hit = [str(e["title"]) for _, e in ent.iterrows()
           if not in_doc(str(e["title"])) and str(e["title"]).strip().lower() in prompt]
    print(f"  {len(hit)} 个 {hit[:10]}")

    print("\n=== rank 分布 ===")
    r = rep["rank"].astype(float)
    print(f"  n={len(r)} min={r.min()} 中位={r.median()} max={r.max()} 标准差={r.std():.2f}")
    bins = pd.cut(r, [0, 2, 4, 6, 8, 10], include_lowest=True).value_counts().sort_index()
    for k, v in bins.items():
        print(f"  {str(k):<14} {v:>3}")

    print("\n=== 引用密度 ===")
    cites = [len(re.findall(r"\[Data:", str(x))) for x in rep["full_content"]]
    print(f"  每份报告引用标记数: min={min(cites)} 中位={int(pd.Series(cites).median())} max={max(cites)}")
    print(f"  无引用的报告: {sum(1 for c in cites if c == 0)} / {len(cites)}")


if __name__ == "__main__":
    main()
