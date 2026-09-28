"""看 C0（根社区）报告的 findings 分布：是不是都顶到 10？以及有没有撞 max_length=2000 的上限。"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
MAX_LEN = 2000  # community_reports.max_length 默认


def n_findings(x) -> int:
    if x is None:
        return 0
    if isinstance(x, list):
        return len(x)
    try:
        v = ast.literal_eval(str(x))
        return len(v) if isinstance(v, list) else 0
    except Exception:  # noqa: BLE001
        return 0


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    comms = pd.read_parquet(OUT / "communities.parquet")
    ent_of = {int(c["community"]): (0 if c["entity_ids"] is None else len(c["entity_ids"])) for _, c in comms.iterrows()}
    tu_of = {int(c["community"]): (0 if c["text_unit_ids"] is None else len(c["text_unit_ids"])) for _, c in comms.iterrows()}

    reps = reps.copy()
    reps["n_find"] = reps["findings"].apply(n_findings)
    reps["rep_tok"] = reps["full_content"].apply(lambda s: len(tk.encode(str(s))))
    reps["n_ent"] = reps["community"].apply(lambda c: ent_of.get(int(c), 0))
    reps["n_tu"] = reps["community"].apply(lambda c: tu_of.get(int(c), 0))

    print("=== 每层 findings 条数分布 ===")
    g = reps.groupby("level")["n_find"].agg(["count", "min", lambda s: s.quantile(0.25), "median", "mean", "max"])
    g.columns = ["份数", "min", "Q1", "median", "mean", "max"]
    print(g.round(1).to_string())
    print()
    print("=== 命中下限/上限的比例（提示词写的是 5-10 条）===")
    for lv in sorted(reps["level"].unique()):
        s = reps[reps["level"] == lv]["n_find"]
        lt5 = int((s < 5).sum()); eq5 = int((s == 5).sum()); eq10 = int((s == 10).sum()); gt10 = int((s > 10).sum())
        print(f"  L{lv}: {len(s):4d} 份 · <5 条的 {lt5:3d}（{lt5/len(s):5.1%}） · 恰好 5 条 {eq5:3d}（{eq5/len(s):5.1%}） · "
              f"恰好 10 条 {eq10:3d}（{eq10/len(s):5.1%}） · >10 条 {gt10:3d}")
    print()
    print("=== C0（47 个根）逐条：实体数 / 材料块 / findings / 报告 token ===")
    l0 = reps[reps["level"] == 0].sort_values("n_find", ascending=False)
    for _, r in l0.iterrows():
        flag = " ←顶到 10" if r["n_find"] >= 10 else (" ←只有 5" if r["n_find"] <= 5 else "")
        near = " ⚠近上限" if r["rep_tok"] >= MAX_LEN * 0.95 else ""
        print(f"  c{int(r['community']):<3d} 实体 {r['n_ent']:3d} · 材料 {r['n_tu']:2.0f} 块 · findings {int(r['n_find']):2d} · "
              f"报告 {int(r['rep_tok']):5d} token{flag}{near}  {str(r['title'])[:34]}")
    print()
    print(f"=== 报告 token 撞上限（max_length={MAX_LEN}）的情况 ===")
    for lv in sorted(reps["level"].unique()):
        s = reps[reps["level"] == lv]["rep_tok"]
        print(f"  L{lv}: ≥{int(MAX_LEN*0.95)} token 的 {int((s >= MAX_LEN*0.95).sum()):3d}/{len(s):4d}（{((s >= MAX_LEN*0.95).mean()):5.1%}）· "
              f"median {int(s.median()):5d} · max {int(s.max()):5d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
