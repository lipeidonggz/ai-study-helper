"""引用标记到底出现在哪些字段里：summary / rating_explanation / findings / full_content。

背景：我先前说"只有 full_content 带 [Data: …] 引用"，沛东看 reports.html 后指出
**findings 也有引用**。这里逐个字段实测，把口径定准。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

CITE = re.compile(r"\[Data:")


def scan(name: str, out: Path) -> None:
    rep = pd.read_parquet(out / "community_reports.parquet")
    n = len(rep)

    def has(series) -> int:
        return sum(1 for v in series if CITE.search(str(v)))

    f_expl = [
        " ".join(str(f.get("explanation", "")) for f in fs if isinstance(f, dict))
        for fs in rep["findings"]
    ]
    f_sum = [
        " ".join(str(f.get("summary", "")) for f in fs if isinstance(f, dict))
        for fs in rep["findings"]
    ]
    print(f"\n===== {name}（{n} 份报告）")
    print(f"  summary            含引用: {has(rep['summary'])}/{n}")
    print(f"  rating_explanation 含引用: {has(rep['rating_explanation'])}/{n}")
    print(f"  findings.summary   含引用: {has(pd.Series(f_sum))}/{n}")
    print(f"  findings.explanation 含引用: {has(pd.Series(f_expl))}/{n}")
    print(f"  full_content       含引用: {has(rep['full_content'])}/{n}")

    # 抽样看一条 findings.explanation 的引用形态
    for _, r in rep.iterrows():
        for f in r["findings"]:
            if isinstance(f, dict) and CITE.search(str(f.get("explanation", ""))):
                m = CITE.search(str(f["explanation"]))
                s = max(0, m.start() - 120)
                print("  样例 →", str(f["explanation"])[s : m.start() + 160].replace("\n", " "))
                return


def main() -> None:
    ROOT = Path(__file__).resolve().parent
    scan("掘金文章（中文）", ROOT / "output")
    scan("A5（英文）", ROOT.parent / "graphrag-exp" / "output")


if __name__ == "__main__":
    main()
