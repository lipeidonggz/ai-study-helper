"""Side-by-side root-community size comparison, ONE consistent definition set.

Per-level: community count, avg entities, avg text-unit blocks, avg material token
(sum of a community's own blocks, no cross-community dedup), avg report token.
Root level: source tokens / #roots, documents / #roots.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RUNS = {
    "MultiHop news (51)": (Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-multihop/output"), 122_387, 51),
    "Tech notes (14)": (Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-eval/output"), 108_786, 14),
}


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    for tag, (out, src_tok, n_docs) in RUNS.items():
        tu = pd.read_parquet(out / "text_units.parquet")
        comms = pd.read_parquet(out / "communities.parquet")
        reps = pd.read_parquet(out / "community_reports.parquet")
        tok_of_tu = {r["id"]: len(tk.encode(str(r["text"]))) for _, r in tu.iterrows()}
        rep_tok = {int(r["community"]): len(tk.encode(str(r["full_content"]))) for _, r in reps.iterrows()}

        c = comms.copy()
        c["n_ent"] = c["entity_ids"].apply(lambda x: 0 if x is None else len(x))
        c["n_tu"] = c["text_unit_ids"].apply(lambda x: 0 if x is None else len(x))
        c["mat"] = c["text_unit_ids"].apply(
            lambda x: 0 if x is None else sum(tok_of_tu.get(t, 0) for t in list(x)))
        c["rep"] = c["community"].apply(lambda x: rep_tok.get(int(x), 0))

        print("=" * 100)
        print(f"### {tag} | src {src_tok:,} tok | docs {n_docs} | avg doc {src_tok / n_docs:,.0f} tok")
        print(f"{'lv':>4} {'comms':>6} {'ent/comm':>9} {'tu/comm':>8} {'mat tok/comm':>13} {'rep tok/comm':>13} {'rep/mat':>8}")
        for lv in sorted(c["level"].unique()):
            s = c[c["level"] == lv]
            print(f"{'L'+str(lv):>4} {len(s):6d} {s['n_ent'].mean():9.1f} {s['n_tu'].mean():8.2f} "
                  f"{s['mat'].mean():13,.0f} {s['rep'].mean():13,.0f} {s['rep'].sum()/s['mat'].sum() if s['mat'].sum() else 0:8.2f}")
        roots = int((c["level"] == 0).sum())
        print(f"  ROOT LEVEL: {roots} roots | src/root {src_tok/roots:,.0f} tok | docs/root {n_docs/roots:.2f}"
              f" | ent/root {c[c['level']==0]['n_ent'].mean():.1f}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
