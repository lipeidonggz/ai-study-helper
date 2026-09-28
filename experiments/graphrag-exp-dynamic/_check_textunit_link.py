"""直接看社区 0/14 的 text_unit_ids 到底指向哪篇文档、原文长什么样。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def main() -> int:
    communities = pd.read_parquet(OUT / "communities.parquet")
    text_units = pd.read_parquet(OUT / "text_units.parquet")
    documents = pd.read_parquet(OUT / "documents.parquet")
    reports = pd.read_parquet(OUT / "community_reports.parquet")

    print("text_units 列:", list(text_units.columns))
    print("documents 列:", list(documents.columns))
    print()
    print("text_units 每篇的块数:")
    print(text_units.groupby("document_id").size().to_string())
    print()
    print("documents:")
    for _, d in documents.iterrows():
        print(f"  {d['id'][:12]}…  {d['title']}  len={len(str(d.get('text','')))}")
    print()

    by_tu = text_units.set_index("id")
    for cid in (0, 1, 14, 178):
        row = communities[communities["community"] == cid].iloc[0]
        ids = [] if row["text_unit_ids"] is None else list(row["text_unit_ids"])
        rep = reports[reports["community"] == cid]
        title = rep.iloc[0]["title"] if len(rep) else "(无报告)"
        print(f"=== 社区 {cid}（L{int(row.level)}）报告标题: {title[:60]}")
        print(f"    text_unit_ids({len(ids)}): {[str(i)[:10] for i in ids]}")
        for tu_id in ids[:3]:
            if tu_id not in by_tu.index:
                print(f"      - {str(tu_id)[:12]}… 不在 text_units 表里")
                continue
            tu = by_tu.loc[tu_id]
            doc = documents[documents["id"] == tu["document_id"]]
            doc_title = doc.iloc[0]["title"] if len(doc) else "?"
            print(f"      - 文档 {doc_title} | 原文前 120 字: {str(tu['text'])[:120]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
