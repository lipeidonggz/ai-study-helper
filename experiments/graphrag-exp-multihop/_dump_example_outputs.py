"""Dump the raw output sections of each example block, to check for duplication."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEP = "------------------------"
FENCE = "#############################"


def main() -> int:
    for folder in ("prompts-tuned", "prompts-tuned-v1-auto"):
        t = (ROOT / folder / "extract_graph.txt").read_text(encoding="utf-8", errors="ignore")
        print(f"########## {folder} ##########")
        outs = []
        pos = 0
        while True:
            j = t.find(SEP, pos)
            if j < 0:
                break
            k = t.find(FENCE, j)
            block = t[j + len(SEP):k if k > 0 else j + 8000].strip()
            outs.append(block)
            pos = k if k > 0 else j + 8000
        for i, o in enumerate(outs, 1):
            print(f"--- output block {i} (len={len(o)}) first 220 chars:")
            print("   ", o[:220].replace("\n", " "))
        if len(outs) >= 2:
            print(f"  >>> identical? {outs[0] == outs[1]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
