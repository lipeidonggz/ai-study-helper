"""Compare index arms on the same 51-article corpus.

Arms:
  B  = multi_hop baseline (hand-specified 6-domain extraction prompt)
  A1 = auto-inferred extraction prompt + B's summarize/report prompts
  A2 = auto-inferred prompts for all three (added later)
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

BASE = Path("D:/lida-data/vscode/ai-study-helper/data/tmp")
SRC = 122_387
ARMS = {
    "B  (手给域)": BASE / "graphrag-exp-multihop/output",
    "A1 (自动抽取)": BASE / "graphrag-exp-multihop-autodomain/output",
    "A2 (全自动)": BASE / "graphrag-exp-multihop-autodomain-selfconsistent/output",
}


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    rows = []
    for tag, out in ARMS.items():
        if not (out / "entities.parquet").exists():
            rows.append({"arm": tag, "note": "not available"})
            continue
        ent = pd.read_parquet(out / "entities.parquet")
        rel = pd.read_parquet(out / "relationships.parquet")
        tu = pd.read_parquet(out / "text_units.parquet")
        comms = pd.read_parquet(out / "communities.parquet")
        reps = pd.read_parquet(out / "community_reports.parquet")

        tok_of_tu = {r["id"]: len(tk.encode(str(r["text"]))) for _, r in tu.iterrows()}
        rep_tok = {int(r["community"]): len(tk.encode(str(r["full_content"]))) for _, r in reps.iterrows()}
        n_find = {int(r["community"]): (0 if r["findings"] is None else len(r["findings"])) for _, r in reps.iterrows()}

        c = comms.copy()
        c["n_ent"] = c["entity_ids"].apply(lambda x: 0 if x is None else len(x))
        c["mat"] = c["text_unit_ids"].apply(
            lambda x: 0 if x is None else sum(tok_of_tu.get(t, 0) for t in list(x)))
        c["rep"] = c["community"].apply(lambda x: rep_tok.get(int(x), 0))
        c["nf"] = c["community"].apply(lambda x: n_find.get(int(x), 0))

        l0 = c[c["level"] == 0]
        rows.append({
            "arm": tag,
            "entities": len(ent),
            "token/ent": round(SRC / len(ent), 1),
            "rels": len(rel),
            "communities": len(c),
            "levels": "/".join(str(int(x)) for x in sorted(c["level"].unique())),
            "roots": len(l0),
            "src/root": round(SRC / len(l0)),
            "L0 report/src": f"{l0['rep'].sum() / SRC:.1%}",
            "L0 rep/comm": round(l0["rep"].mean()),
            "L0 mat/comm": round(l0["mat"].mean()),
            "L0 find": round(l0["nf"].mean(), 1),
            "all report/src": f"{c['rep'].sum() / SRC:.1%}",
        })
        if len(rows) and tag.startswith("A1"):
            tcol = "type" if "type" in ent.columns else ("entity_type" if "entity_type" in ent.columns else None)
            if tcol:
                cnt = Counter(str(x).strip().lower() for x in ent[tcol])
                print("A1 entity type distribution (top 12):")
                for t, n in cnt.most_common(12):
                    print(f"    {t:34s} {n:5d}  ({n / len(ent):.1%})")
                print()

    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    print()
    print("per-level report/src:")
    for tag, out in ARMS.items():
        if not (out / "community_reports.parquet").exists():
            continue
        reps = pd.read_parquet(out / "community_reports.parquet")
        comms = pd.read_parquet(out / "communities.parquet")
        parts = []
        for lv in sorted(comms["level"].unique()):
            sub = reps[reps["level"] == lv]
            tok = sum(len(tk.encode(str(x))) for x in sub["full_content"])
            nf = sub["findings"].apply(lambda x: 0 if x is None else len(x)).mean() if len(sub) else 0
            parts.append(f"L{lv} {len(sub):4d}份 {tok / SRC:7.1%} tok均{int(tok / len(sub)) if len(sub) else 0:5d} find{nf:4.1f}")
        print(f"  {tag}: " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
