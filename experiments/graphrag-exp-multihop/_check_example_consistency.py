"""For each tuned prompt, check whether the few-shot examples' OUTPUT entities
actually appear in their own INPUT text (i.e. whether input/output are consistent).

Heuristic: for each example block, take entity names from the tuple-format output
and count how many appear (case-insensitive) in the input text of the same block.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGETS = [
    "prompts-tuned",              # B arm: hand-specified domain (used for the index)
    "prompts-tuned-v1-auto",      # A1/A2 arm: auto-inferred
    "prompts-auto5-1",
    "prompts-auto5-2",
    "prompts-auto5-3",
    "prompts-auto5-4",
    "prompts-auto5-5",
]
SEP = "------------------------"


def main() -> int:
    for d in TARGETS:
        f = ROOT / d / "extract_graph.txt"
        if not f.exists():
            print(f"{d:26s} missing")
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        blocks = text.split("Example ")[1:]
        stats = []
        for i, b in enumerate(blocks, 1):
            b = b.strip()
            if SEP not in b:
                continue
            before, after = b.split(SEP, 1)
            inp = before
            names = re.findall(r'\("entity"<\|>\s*([^<|]+?)\s*<\|>', after)
            if not names:
                continue
            low = inp.lower()
            hit = sum(1 for n in names if n.strip().lower() in low)
            stats.append((i, len(names), hit))
        if not stats:
            print(f"{d:26s} no parseable examples")
            continue
        tot_n = sum(s[1] for s in stats)
        tot_h = sum(s[2] for s in stats)
        detail = " ".join(f"#{i}:{h}/{n}" for i, n, h in stats)
        print(f"{d:26s} examples={len(stats):2d}  entities={tot_n:3d}  "
              f"appear-in-input={tot_h:3d} ({tot_h / tot_n:.0%})   {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
