"""Inspect the few-shot example blocks of the prompt actually used for the index."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENT = re.compile(r'\("entity"<\|>\s*([^<|]+?)\s*<\|>')


def show(folder: str, label: str) -> None:
    t = (ROOT / folder / "extract_graph.txt").read_text(encoding="utf-8", errors="ignore")
    print(f"========== {label} ({folder}) ==========")
    for n in (1, 2):
        i = t.find(f"Example {n}:")
        if i < 0:
            continue
        j = t.find("------------------------", i)
        k = t.find("#############################", j)
        inp = t[i:j]
        out = t[j:k if k > 0 else j + 6000]
        ents = [e.strip() for e in ENT.findall(out)]
        low = inp.lower()
        present = [e for e in ents if e.lower() in low]
        body = inp.split("text:", 1)[-1].strip()
        print(f"--- Example {n}: input {len(inp)} chars, output {len(ents)} entities")
        print(f"    input head : {body[:95].replace(chr(10), ' ')}")
        print(f"    input tail : {body[-95:].replace(chr(10), ' ')}")
        print(f"    entities   : {ents[:6]}")
        print(f"    in-input   : {present[:6]}  ({len(present)}/{len(ents)})")
    print()


def main() -> int:
    show("prompts-tuned", "B arm · 人工给域（真正用于建索引的那份）")
    show("prompts-tuned-v1-auto", "A1/A2 arm · 自动推断")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
