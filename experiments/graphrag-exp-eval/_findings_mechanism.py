"""Verify three pieces of evidence for the 'findings count = level granularity controller' claim.

A. real format of findings column + per-level count distribution (hit cap 10 / hit floor 5)
B. avg material token vs avg report token per community (check sublinearity)
C. report length floor: how long is the report of the smallest communities
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def parse_findings(x) -> list:
    if x is None:
        return []
    if isinstance(x, list):
        return x
    if hasattr(x, "tolist"):  # numpy ndarray
        try:
            return list(x.tolist())
        except Exception:  # noqa: BLE001
            pass
    if hasattr(x, "__len__") and not isinstance(x, (str, bytes, bytearray, dict)):
        try:
            return list(x)
        except Exception:  # noqa: BLE001
            pass
    if isinstance(x, (bytes, bytearray)):
        x = x.decode("utf-8", "ignore")
    s = str(x).strip()
    if not s or s in {"[]", "nan", "None"}:
        return []
    for loader in (lambda t: ast.literal_eval(t), lambda t: json.loads(t)):
        try:
            v = loader(s)
            if isinstance(v, list):
                return v
            if isinstance(v, dict) and "findings" in v:
                return v["findings"]
        except Exception:  # noqa: BLE001
            continue
    return []


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    comms = pd.read_parquet(OUT / "communities.parquet")

    print("=== A0. raw shape of findings column ===")
    v = reps["findings"].iloc[0]
    print("  python type:", type(v).__name__, "| pandas dtype:", reps["findings"].dtype)
    raw = v if isinstance(v, str) else str(v)
    print("  head:", raw[:220].replace("\n", " "))
    print()

    reps = reps.copy()
    reps["n_find"] = reps["findings"].apply(lambda x: len(parse_findings(x)))
    reps["rep_tok"] = reps["full_content"].apply(lambda s: len(tk.encode(str(s))))

    print("=== A. findings count distribution by level ===")
    g = reps.groupby("level")["n_find"].agg(
        n="count", mn="min", q1=lambda s: s.quantile(0.25), med="median",
        mean="mean", q3=lambda s: s.quantile(0.75), mx="max")
    print(g.round(1).to_string())
    print()
    print("=== A2. hit cap 10 / floor 5 (spec says 5-10) ===")
    for lv in sorted(reps["level"].unique()):
        s = reps[reps["level"] == lv]["n_find"]
        print(f"  L{lv}: n={len(s):4d} | ==0 {int((s==0).sum()):3d} ({((s==0).mean()):5.1%})"
              f" | <5 {int((s<5).sum()):3d} ({((s<5).mean()):5.1%})"
              f" | ==5 {int((s==5).sum()):3d} ({((s==5).mean()):5.1%})"
              f" | ==10 {int((s==10).sum()):3d} ({((s==10).mean()):5.1%})"
              f" | >10 {int((s>10).sum()):3d} | max {int(s.max())}")
    print()

    tu = pd.read_parquet(OUT / "text_units.parquet")
    tok_of_tu = {r["id"]: len(tk.encode(str(r["text"]))) for _, r in tu.iterrows()}
    rep_by_c = {int(r["community"]): (int(r["rep_tok"]), int(r["n_find"])) for _, r in reps.iterrows()}
    comms = comms.copy()
    comms["n_tu"] = comms["text_unit_ids"].apply(lambda x: 0 if x is None else len(x))
    comms["n_ent"] = comms["entity_ids"].apply(lambda x: 0 if x is None else len(x))
    comms["mat_tok"] = comms["text_unit_ids"].apply(
        lambda x: 0 if x is None else sum(tok_of_tu.get(t, 0) for t in list(x)))
    comms["rep_tok"] = comms["community"].apply(lambda c: rep_by_c.get(int(c), (0, 0))[0])
    comms["n_find"] = comms["community"].apply(lambda c: rep_by_c.get(int(c), (0, 0))[1])

    print("=== B. per-community: avg material token vs avg report token ===")
    print(f"{'lv':>4} {'n':>6} {'avg_ent':>8} {'avg_tu':>8} {'avg_mat_tok':>12} {'avg_rep_tok':>12} {'rep/mat':>8}")
    for lv in sorted(comms["level"].unique()):
        s = comms[comms["level"] == lv]
        mt, rt = s["mat_tok"].mean(), s["rep_tok"].mean()
        print(f"{'L'+str(lv):>4} {len(s):6d} {s['n_ent'].mean():8.1f} {s['n_tu'].mean():8.2f} "
              f"{mt:12.0f} {rt:12.0f} {rt/mt if mt else 0:8.2f}")
    print()

    print("=== C. report length floor: smallest communities ===")
    for lv in sorted(comms["level"].unique()):
        s = comms[comms["level"] == lv]
        one = s[s["n_tu"] <= 1]
        if not len(one):
            continue
        print(f"  L{lv}: <=1 block: {len(one):3d}/{len(s):3d} | avg report {one['rep_tok'].mean():6.0f} tok"
              f" | avg findings {one['n_find'].mean():4.1f} | avg material {one['mat_tok'].mean():5.0f} tok")
    print()

    print("=== D. corr(findings count, report token) by level ===")
    for lv in sorted(reps["level"].unique()):
        s = reps[reps["level"] == lv]
        if len(s) < 8:
            continue
        print(f"  L{lv}: n={len(s):4d} | corr = {s['n_find'].corr(s['rep_tok']):.3f} | median report tok {int(s['rep_tok'].median())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
