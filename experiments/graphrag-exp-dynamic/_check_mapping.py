"""核对：community_reports 的标题 与 communities.text_unit_ids 的文档来源 是否对得上。

如果对不上，说明"选中集合按文档"这个判据本身有问题，必须查清。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def main() -> int:
    communities = pd.read_parquet(OUT / "communities.parquet")
    reports = pd.read_parquet(OUT / "community_reports.parquet")
    text_units = pd.read_parquet(OUT / "text_units.parquet")
    documents = pd.read_parquet(OUT / "documents.parquet")
    doc_of_tu = dict(zip(text_units["id"], text_units["document_id"]))
    title_of_doc = {i: t.replace(".txt", "") for i, t in zip(documents["id"], documents["title"])}

    def dom(ids) -> list[tuple[str, int]]:
        cnt = Counter(title_of_doc.get(doc_of_tu.get(t)) for t in ([] if ids is None else list(ids)))
        cnt.pop(None, None)
        return cnt.most_common(2)

    by_comm = reports.set_index("community")
    print("cid | lvl | 报告标题 | 社区表 text_unit 文档来源")
    for cid in [0, 1, 2, 3, 4, 5, 9, 10, 14, 15, 16, 17, 178, 273, 276]:
        row = communities[communities["community"] == cid]
        if row.empty:
            continue
        r0 = row.iloc[0]
        title = str(by_comm.loc[cid, "title"]) if cid in by_comm.index else "(无报告)"
        print(f"{cid:>4} | L{int(r0.level)} | {title[:52]:54s} | {str(dom(r0.text_unit_ids))[:60]}")

    # 全量对账：按"报告标题里出现的文档关键词"粗判该报告属于哪个域
    KEYS = {
        "01": ["判官", "复盘", "排查实录"],
        "A5": ["contain", "容器", "sandbox", "Claude"],
        "04": ["单 Agent", "ReAct", "Plan-And-Execute", "ReWOO"],
        "05": ["多 Agent", "通信", "拓扑"],
        "06": ["Prompt 工程", "Harness", "Loop", "演进阶段"],
    }
    print()
    print("=== 全量粗对账（报告标题命中域关键词 vs 社区表主导文档）===")
    mismatch = 0
    for _, r in communities.iterrows():
        cid = int(r["community"])
        if cid not in by_comm.index:
            continue
        title = str(by_comm.loc[cid, "title"])
        hits = [d for d, kws in KEYS.items() if any(k in title for k in kws)]
        if not hits:
            continue
        d_top = dom(r["text_unit_ids"])
        top = d_top[0][0] if d_top else "?"
        if top not in hits and not any(h in top for h in hits):
            mismatch += 1
            if mismatch <= 12:
                print(f"  不一致 c{cid} L{int(r['level'])}: 标题域 {hits} / 社区表主导 {d_top} | {title[:40]}")
    print(f"  共 {mismatch} 个不一致（只统计标题能判出域的）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
