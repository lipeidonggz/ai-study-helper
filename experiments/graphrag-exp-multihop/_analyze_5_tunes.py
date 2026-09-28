"""Analyze the 5 identical-args prompt-tune runs."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOCS = {f.name: f.read_text(encoding="utf-8", errors="ignore")
        for f in sorted((ROOT / "input").glob("*.txt"))}


def probes(body: str, n: int = 12, width: int = 70) -> list[str]:
    """Distinctive substrings spread across a document."""
    step = max(1, len(body) // n)
    out = []
    for i in range(0, len(body) - width, step):
        s = body[i:i + width].replace("\n", " ").strip()
        if len(s) == width and s.isprintable():
            out.append(s)
    return out[:n]


PROBES = {name: probes(body) for name, body in DOCS.items()}


def docs_present(text: str) -> list[str]:
    hits = []
    for name, ps in PROBES.items():
        if sum(1 for p in ps if p in text) >= 2:
            hits.append(name)
    return hits


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def main() -> int:
    recs = json.loads((ROOT / "_runs_auto5.json").read_text(encoding="utf-8"))

    print("=== per-run summary ===")
    type_sets = []
    for r in recs:
        i = r["run"]
        ex = (ROOT / f"prompts-auto5-{i}" / "extract_graph.txt").read_text(encoding="utf-8", errors="ignore")
        rep = (ROOT / f"prompts-auto5-{i}" / "community_report_graph.txt").read_text(encoding="utf-8", errors="ignore")
        present = docs_present(ex)
        themes = docs_present(rep)
        type_sets.append(set(t.lower() for t in r["types"]))
        print(f"\n--- run {i} | {r['n_types']} types ---")
        print(f"  role     : {r['role'][:105]}")
        print(f"  types    : {', '.join(t.lower() for t in r['types'])}")
        print(f"  docs in extract prompt : {[d[:11] for d in present]}")
        print(f"  docs in report  prompt : {[d[:11] for d in themes]}")
        m = re.search(r"REPORT RATING:(.*)", rep)
        print(f"  rating   : {(m.group(1).strip()[:150] if m else '')}")

    print("\n=== pairwise Jaccard of entity-type sets ===")
    for a, b in combinations(range(len(type_sets)), 2):
        print(f"  run{a+1} vs run{b+1}: {jaccard(type_sets[a], type_sets[b]):.2f}")

    all_types = Counter()
    for s in type_sets:
        all_types.update(s)
    print(f"\n=== {len(all_types)} distinct entity types across 5 runs ===")
    for t, n in all_types.most_common():
        print(f"  {n}x  {t}")

    print("\n=== which docs appear in EACH run's extract prompt ===")
    per_doc = defaultdict(list)
    for r in recs:
        i = r["run"]
        ex = (ROOT / f"prompts-auto5-{i}" / "extract_graph.txt").read_text(encoding="utf-8", errors="ignore")
        for d in docs_present(ex):
            per_doc[d].append(i)
    for d, runs in sorted(per_doc.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(runs)}/5  runs {runs}  {d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
