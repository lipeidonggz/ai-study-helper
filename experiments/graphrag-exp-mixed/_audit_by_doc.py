"""按源文档分别统计语言：实体名/实体描述/关系描述/报告正文。

归属链：entity.text_unit_ids → text_units.document_id → documents.title
（实体可能命中多篇，按"命中的第一篇"归属，并单独报跨文档实体数。）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "output")
CJK = re.compile(r"[\u4e00-\u9fff]")


def as_list(v) -> list:
    if v is None:
        return []
    return list(v)


def zh(series) -> str:
    vals = [str(x) for x in series]
    if not vals:
        return "n/a"
    n = sum(1 for v in vals if CJK.search(v))
    return f"{n}/{len(vals)}（{n/len(vals):.0%}）"


def main() -> None:
    t = {p.stem: pd.read_parquet(p) for p in OUT.glob("*.parquet")}
    ent, rel, tu, docs, rep = t["entities"], t["relationships"], t["text_units"], t["documents"], t["community_reports"]

    tu2doc = dict(zip(tu["id"], tu["document_id"], strict=False))
    doc_title = dict(zip(docs["id"], docs["title"], strict=False))

    def owner(tu_ids) -> str:
        counts: dict[str, int] = {}
        for i in as_list(tu_ids):
            d = tu2doc.get(i)
            if d:
                counts[d] = counts.get(d, 0) + 1
        if not counts:
            return "(无)"
        top = max(counts, key=lambda k: counts[k])
        return doc_title.get(top, top) + ("（跨篇）" if len(counts) > 1 else "")

    ent = ent.copy()
    ent["owner"] = ent["text_unit_ids"].map(owner)

    print(f"输出目录 {OUT.name}：{len(docs)} 篇文档 · {len(ent)} 实体 · {len(rel)} 关系 · {len(t['communities'])} 社区 · {len(rep)} 报告\n")
    for name, grp in ent.groupby("owner"):
        print(f"【{name}】实体 {len(grp)}")
        print(f"    实体名含中文   : {zh(grp['title'])}")
        print(f"    实体描述含中文 : {zh(grp['description'].fillna(''))}")
        print(f"    类型分布       : {dict(grp['type'].value_counts().head(6))}")
    print(f"\n全体关系描述含中文: {zh(rel['description'].fillna(''))}")
    print(f"报告标题含中文    : {zh(rep['title'])}")
    print(f"报告正文含中文    : {zh(rep['full_content'].fillna(''))}")
    print("\n实体名样例（按文档）:")
    for name, grp in ent.groupby("owner"):
        print(f"  【{name}】{', '.join(str(x)[:26] for x in grp['title'].head(8))}")


if __name__ == "__main__":
    main()
