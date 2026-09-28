"""语言无关的串味判据：实体名是否命中 **GraphRAG 提示词里 few-shot 示例** 的专名。

为什么换判据（2026-09-25 教训）：
  前一版用"实体名是否出现在输入文档里"来判断幻觉。A5 是英文、文档也是英文，
  还勉强能用；本篇文档是**中文**、图是**英文**（模型在抽取时做了翻译与抽象），
  于是大量合法实体（如 JOHNS HOPKINS UNIVERSITY ← "约翰霍普金斯大学"）被判成幻觉。
  ⇒ 改成：只把"命中提示词示例专名"的算串味（语言无关，且提示词里那些名字
  与本文档主题毫无关系，命中即证明是模型抄了示例）。

只读，不联网。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
PROMPT = (
    REPO
    / "data/tmp/graphrag-exp/.venv/Lib/site-packages"
    / "graphrag/prompts/index/extract_graph.py"
)
DOC = (ROOT / "input" / "doc.txt").read_text(encoding="utf-8").lower()


def main() -> None:
    prompt_text = PROMPT.read_text(encoding="utf-8", errors="ignore").lower()
    ents = pd.read_parquet(ROOT / "output" / "entities.parquet")

    rows = []
    for _, e in ents.iterrows():
        name = str(e["title"]).strip()
        low = name.lower()
        if not low:
            continue
        # 2026-09-25 修：中文名不含拉丁词，split 出来的词表是空的，
        # 而 all([]) == True ⇒ 会把**所有中文实体**误判成"命中提示词"。
        # 所以先要求词表非空，再谈"逐词命中"。
        words = [w for w in re.split(r"[^a-z0-9]+", low) if len(w) > 3]
        hit = low in prompt_text or (bool(words) and all(w in prompt_text for w in words))
        if hit:
            rows.append(
                {
                    "title": name,
                    "type": e.get("type"),
                    "degree": e.get("degree"),
                    "in_doc": low in DOC,
                }
            )
    df = pd.DataFrame(rows)
    print(f"实体总数 {len(ents)}；命中提示词示例专名的 {len(df)} 个")
    if len(df):
        print(df.sort_values("degree", ascending=False).to_string(index=False))
        print(
            f"\n其中同时出现在本文档里的: {int(df['in_doc'].sum())} 个"
            "（应为 0 或极少；若多则该判据也有噪声）"
        )


if __name__ == "__main__":
    main()
