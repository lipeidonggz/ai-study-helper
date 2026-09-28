"""L2 报告量按"社区材料块数"分桶，看膨胀到底集中在哪一类社区。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    comms = pd.read_parquet(OUT / "communities.parquet")
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    rep_tok = {int(r["community"]): len(tk.encode(str(r["full_content"]))) for _, r in reps.iterrows()}

    sub = comms[comms["level"] == 2].copy()
    sub["n_tu"] = sub["text_unit_ids"].apply(lambda x: 0 if x is None else len(x))
    sub["rep_tok"] = sub["community"].apply(lambda c: rep_tok.get(int(c), 0))
    total = sub["rep_tok"].sum()

    def bucket(n: int) -> str:
        return "1 块" if n <= 1 else ("2 块" if n == 2 else ("3-4 块" if n <= 4 else "5+ 块"))

    sub["bucket"] = sub["n_tu"].apply(bucket)
    g = sub.groupby("bucket").agg(社区数=("community", "count"), 报告token=("rep_tok", "sum"),
                                  平均报告token=("rep_tok", "mean")).round(0)
    g["报告token占比"] = (g["报告token"] / total).map(lambda x: f"{x:.1%}")
    order = ["1 块", "2 块", "3-4 块", "5+ 块"]
    print("=== L2（747 个社区）按材料块数分桶 ===")
    print(g.loc[[b for b in order if b in g.index]].to_string())
    print(f"\nL2 报告 token 合计 {int(total):,}")
    print()
    print("=== 对照：L0 同样看 ===")
    sub0 = comms[comms["level"] == 0].copy()
    sub0["n_tu"] = sub0["text_unit_ids"].apply(lambda x: 0 if x is None else len(x))
    sub0["rep_tok"] = sub0["community"].apply(lambda c: rep_tok.get(int(c), 0))
    t0 = sub0["rep_tok"].sum()
    sub0["bucket"] = sub0["n_tu"].apply(bucket)
    g0 = sub0.groupby("bucket").agg(社区数=("community", "count"), 报告token=("rep_tok", "sum"),
                                    平均报告token=("rep_tok", "mean")).round(0)
    g0["报告token占比"] = (g0["报告token"] / t0).map(lambda x: f"{x:.1%}")
    print(g0.loc[[b for b in order if b in g0.index]].to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
