"""核对抽取幻觉：把实体名回原文做字面匹配，找出"图里有、文里没有"的实体。

只读，不联网。判定很保守：实体名（或其主要词）在输入文本里**一次都不出现** ⇒ 标记可疑。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
TEXT = (ROOT / "input" / "a5.txt").read_text(encoding="utf-8")
LOWER = TEXT.lower()


def appears(name: str) -> bool:
    n = name.strip().lower()
    if not n:
        return False
    if n in LOWER:
        return True
    # 放宽一点：去掉常见后缀/前缀词后仍在文中出现
    core = re.sub(r"\b(the|a|an)\b", " ", n)
    words = [w for w in re.split(r"[^a-z0-9.]+", core) if len(w) > 3]
    return bool(words) and all(w in LOWER for w in words)


def main() -> None:
    ents = pd.read_parquet(ROOT / "output" / "entities.parquet")
    ents["in_text"] = ents["title"].map(appears)
    bad = ents[~ents["in_text"]]
    print(f"实体总数 {len(ents)}；名字在原文中找不到的 {len(bad)} 个（{len(bad)/len(ents):.0%}）")
    print("\n可疑实体（title / type / degree / frequency）：")
    cols = [c for c in ("title", "type", "degree", "frequency") if c in bad.columns]
    print(bad[cols].sort_values("degree", ascending=False).to_string(index=False))
    print("\n其中前 8 个的描述（看它是否编得像真的）：")
    for _, r in bad.head(8).iterrows():
        print(f"  · {r['title']} [{r.get('type')}] text_units={r.get('text_unit_ids')}")
        print(f"    {str(r.get('description'))[:260]}")

    rel = pd.read_parquet(ROOT / "output" / "relationships.parquet")
    marked = set(bad["title"])
    flagged = rel[rel["source"].isin(marked) | rel["target"].isin(marked)]
    print(f"\n涉及可疑实体的关系: {len(flagged)} / {len(rel)}")
    print(flagged[["source", "target", "description"]].head(8).to_string(index=False))


if __name__ == "__main__":
    main()
