"""Provisional check while embeddings still run: per-root coverage + entity density."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

OUT = Path(__file__).resolve().parent / "output"
SRC = 122_387


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    ent = pd.read_parquet(OUT / "entities.parquet")
    tu = pd.read_parquet(OUT / "text_units.parquet")
    comms = pd.read_parquet(OUT / "communities.parquet")
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    tok_of_tu = {r["id"]: len(tk.encode(str(r["text"]))) for _, r in tu.iterrows()}

    n_ent = len(ent)
    print(f"corpus tokens      : {SRC:,}")
    print(f"entities           : {n_ent:,}  -> {SRC / n_ent:.1f} token/entity")
    print(f"communities total  : {len(comms)}")
    for lv in sorted(comms["level"].unique()):
        sub = comms[comms["level"] == lv]
        covered: set[str] = set()
        for _, c in sub.iterrows():
            covered.update([] if c["text_unit_ids"] is None else list(c["text_unit_ids"]))
        mat = sum(tok_of_tu.get(t, 0) for t in covered)
        subr = reps[reps["level"] == lv]
        rep_tok = int(sum(len(tk.encode(str(x))) for x in subr["full_content"]))
        avg_find = subr["findings"].apply(lambda x: 0 if x is None else len(x)).mean() if len(subr) else 0
        print(f"  L{lv}: comms {len(sub):4d} | reports {len(subr):4d} | covered {mat:8,d} tok"
              f" | per-root {mat / len(sub):7,.0f} tok | report total {rep_tok:8,d}"
              f" | report/src {rep_tok / SRC:6.1%} | avg findings {avg_find:4.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
