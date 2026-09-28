"""按"社区自己的材料 vs 它那份报告"算压缩比：这一版才是决定"摘要是否成立"的东西。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    tu = pd.read_parquet(OUT / "text_units.parquet")
    comms = pd.read_parquet(OUT / "communities.parquet")
    reps = pd.read_parquet(OUT / "community_reports.parquet")

    tok_of_tu = {r["id"]: len(tk.encode(str(r["text"]))) for _, r in tu.iterrows()}
    tok_of_rep = {int(r["community"]): len(tk.encode(str(r["full_content"]))) for _, r in reps.iterrows()}

    rows = []
    for _, c in comms.iterrows():
        cid = int(c["community"])
        if cid not in tok_of_rep:
            continue
        ids = [] if c["text_unit_ids"] is None else list(c["text_unit_ids"])
        src = sum(tok_of_tu.get(t, 0) for t in ids)
        rep = tok_of_rep[cid]
        rows.append({
            "cid": cid, "level": int(c["level"]),
            "n_tu": len(ids), "src_tok": src, "rep_tok": rep,
            "ratio": (src / rep) if rep else float("nan"),
        })
    df = pd.DataFrame(rows)
    print("每份报告：它的材料有多大 vs 报告自己多大（ratio = 材料/报告，>1 才是压缩）")
    print(df.groupby("level")[["n_tu", "src_tok", "rep_tok", "ratio"]].agg(["count", "median", "mean"]).round(2).to_string())
    print()
    print("=== L0 根层 17 个逐个 ===")
    for _, r in df[df.level == 0].sort_values("cid").iterrows():
        flag = "压缩" if r["ratio"] > 1.5 else ("持平" if r["ratio"] > 0.8 else "膨胀")
        print(f"  c{r['cid']:<3.0f} 材料 {r['n_tu']:2.0f} 块 / {r['src_tok']:6.0f} token → 报告 {r['rep_tok']:5.0f} token   ratio {r['ratio']:4.2f}  ← {flag}")
    print()
    print(f"全库：{len(df)} 份报告；ratio>1.5（真压缩）的 {int((df.ratio > 1.5).sum())} 份；ratio<0.8（膨胀）的 {int((df.ratio < 0.8).sum())} 份")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
