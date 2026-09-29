"""按"来源文档的类别"给实体分域：B vs A2 在 体育/技术 与 其余四域 各抽到多少实体。

用途：0039 §2.3 原来只说"A2 的类型表只覆盖两个域，所以全局实体少了 20%"，
但没有回答"在它覆盖的那两个域里，它到底抽得更多还是更少"。这个脚本补那一格。

口径：
  · 文档类别来自 subset_manifest.json（file → category）
  · 实体 → text_unit_ids → document_id → 类别；一个实体可能跨多类，
    这里给两种口径：主导类别（出现最多的那类）与"是否触到体育/技术"。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

REPO = Path("D:/lida-data/vscode/ai-study-helper")
ARMS = {
    "B（手工给域）": REPO / "data/tmp/graphrag-exp-multihop/output",
    "A2（prompt-tune 自动域）": REPO / "data/tmp/graphrag-exp-multihop-autodomain-selfconsistent/output",
}
MANIFEST = REPO / "data/tmp/graphrag-exp-multihop/subset_manifest.json"
TARGET = {"sports", "technology"}


def main() -> int:
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cat_of_file = {a["file"]: a["category"] for a in man["articles"]}
    print("源文档：", len(cat_of_file), "篇 |", dict(Counter(cat_of_file.values())), "\n")

    for tag, out in ARMS.items():
        docs = pd.read_parquet(out / "documents.parquet")
        tu = pd.read_parquet(out / "text_units.parquet")
        ent = pd.read_parquet(out / "entities.parquet")
        file_of_doc = dict(zip(docs["id"], docs["title"]))
        cat_of_doc = {d: cat_of_file.get(str(f).strip(), "?") for d, f in file_of_doc.items()}
        doc_of_tu = dict(zip(tu["id"], tu["document_id"]))

        dominant = Counter()
        touches = Counter()
        for r in ent.itertuples():
            cats = Counter()
            for t in ([] if r.text_unit_ids is None else list(r.text_unit_ids)):
                c = cat_of_doc.get(doc_of_tu.get(t))
                if c and c != "?":
                    cats[c] += 1
            if cats:
                dominant[cats.most_common(1)[0][0]] += 1
                for c in cats:
                    touches[c] += 1
        tot = len(ent)
        in_target = sum(dominant[c] for c in TARGET)
        print(f"### {tag}（实体 {tot}）")
        print("   主导类别：", dict(dominant.most_common()))
        print(f"   体育+技术（主导）：{in_target}（{in_target/tot:.0%}） | 其余四域：{tot-in_target}（{(tot-in_target)/tot:.0%}）")
        print("   触到（≥1 篇该类的块）：", dict(touches.most_common()))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
