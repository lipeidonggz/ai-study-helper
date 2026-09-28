"""量化"报告内容 ↔ 社区"的错位：用社区自己的实体名在报告正文里的出现比例来判断。

对齐的社区：它的实体名应该大量出现在自己的报告里。
用法：python _check_report_alignment.py [--top 15]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    communities = pd.read_parquet(OUT / "communities.parquet")
    reports = pd.read_parquet(OUT / "community_reports.parquet")
    entities = pd.read_parquet(OUT / "entities.parquet")
    title_of_entity = dict(zip(entities["id"], entities["title"]))
    report_by_comm = reports.set_index("community")

    rows = []
    for _, c in communities.iterrows():
        cid = int(c["community"])
        if cid not in report_by_comm.index:
            continue
        ent_ids = [] if c["entity_ids"] is None else list(c["entity_ids"])
        names = [str(title_of_entity.get(e, "")) for e in ent_ids]
        names = [n for n in names if len(n) >= 3]
        body = str(report_by_comm.loc[cid, "title"]) + "\n" + str(report_by_comm.loc[cid, "full_content"])
        up = body.upper()
        hit = sum(1 for n in names if n.upper() in up)
        ratio = hit / len(names) if names else float("nan")
        rows.append({
            "cid": cid, "level": int(c["level"]), "n_ent": len(names),
            "hit": hit, "ratio": ratio,
            "title": str(report_by_comm.loc[cid, "title"])[:38],
        })
    df = pd.DataFrame(rows)
    print("按层看'实体名出现在自己报告里'的比例：")
    print(df.groupby("level")["ratio"].agg(["count", "mean", "median", "min", "max"]).round(3).to_string())
    print()
    low = df[df["ratio"] < 0.2].sort_values("ratio")
    print(f"疑似错位的社区（比例 < 20%）：{len(low)} / {len(df)}")
    print(low.groupby("level").size().to_string())
    print()
    print("最错位的前若干个：")
    for _, r in low.head(args.top).iterrows():
        print(f"  c{r['cid']:<3d} L{r['level']}  命中 {r['hit']}/{r['n_ent']}  报告标题 {r['title']}")
    aligned = df[df["ratio"] >= 0.8]
    print()
    print(f"明显对齐的（比例 >= 80%）：{len(aligned)} / {len(df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
