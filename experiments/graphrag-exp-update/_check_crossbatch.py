"""跨批结构核查：合并后的社区到底有没有"跨批"的东西。

三个问题：
  A. 有没有任何一个社区，其成员实体来自两篇不同的文档？（＝有没有跨批社区）
  B. 社区表里的 entity_ids 还指得到实体吗？（实体按 title 合并后，新批次那些 id 已被旧 id 取代）
  C. 那 4 个跨批实体，最后落在哪些社区里？它新增的证据块归到了哪个社区？
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
TS = "20260926-120449"  # 第一次（成功的那次）增量


def main() -> None:
    prev = ROOT / "update_output" / TS / "previous"
    delta = ROOT / "update_output" / TS / "delta"
    out = ROOT / "output"

    me = pd.read_parquet(out / "entities.parquet")
    mc = pd.read_parquet(out / "communities.parquet")
    mtu = pd.read_parquet(out / "text_units.parquet")
    md = pd.read_parquet(out / "documents.parquet")
    pe = pd.read_parquet(prev / "entities.parquet")
    de = pd.read_parquet(delta / "entities.parquet")

    id2title = dict(zip(me["id"], me["title"]))
    tu2doc = dict(zip(mtu["id"], mtu["document_id"]))
    doc2title = dict(zip(md["id"], md["title"]))

    def entity_docs(eid: str) -> set[str]:
        """这个实体的证据块落在哪些文档里。"""
        row = me.loc[me["id"] == eid]
        if row.empty:
            return set()
        docs = set()
        for t in row["text_unit_ids"].iloc[0]:
            d = tu2doc.get(t)
            if d:
                docs.add(doc2title.get(d, str(d)))
        return docs

    print("=" * 78)
    print("A. 有没有任何一个社区跨了两篇文档")
    print("=" * 78)
    spanning = []
    for _, c in mc.iterrows():
        docs: set[str] = set()
        for eid in c["entity_ids"]:
            docs |= entity_docs(eid)
        if len(docs) > 1:
            spanning.append((int(c["community"]), int(c["level"]), sorted(docs)))
    print(f"跨两篇文档的社区：{len(spanning)} 个")
    for cid, lvl, docs in spanning:
        print(f"  #{cid} (L{lvl}) 覆盖文档 {docs}")

    print()
    print("=" * 78)
    print("B. 社区引用的实体 id 还指得到实体吗")
    print("=" * 78)
    known = set(me["id"])
    total_refs = 0
    dangling = 0
    per_comm = []
    for _, c in mc.iterrows():
        refs = list(c["entity_ids"])
        bad = [r for r in refs if r not in known]
        total_refs += len(refs)
        dangling += len(bad)
        per_comm.append((int(c["community"]), len(refs), len(bad)))
    print(f"社区表里 entity_ids 引用总数 {total_refs}，指不到实体的 {dangling} 条 ({dangling/max(1,total_refs):.1%})")
    bad_comms = [x for x in per_comm if x[2]]
    print(f"有悬空引用的社区 {len(bad_comms)} 个（前 10）：{bad_comms[:10]}")

    # 新批次（社区编号 >=14）的引用情况
    print("\n  按批次看（新批次社区编号 ≥ 14）：")
    for cid, n, badn in per_comm:
        batch = "新" if cid >= 14 else "旧"
        if badn:
            print(f"    #{cid:<3} {batch}  引用 {n:>3} 条，悬空 {badn}")

    print()
    print("=" * 78)
    print("C. 那 4 个跨批实体落在哪些社区 / 证据跨了几篇文档")
    print("=" * 78)
    common = sorted(set(pe["title"]) & set(de["title"]))
    for t in common:
        row = me.loc[me["title"] == t]
        eid = row["id"].iloc[0]
        docs = sorted(entity_docs(eid))
        comms = sorted(
            int(c["community"]) for _, c in mc.iterrows() if eid in list(c["entity_ids"])
        )
        # 它在新批次里的旧 id 是否仍被引用
        stale = de.loc[de["title"] == t, "id"]
        stale_refs = []
        if len(stale):
            sid = stale.iloc[0]
            stale_refs = sorted(
                int(c["community"]) for _, c in mc.iterrows() if sid in list(c["entity_ids"])
            )
        print(f"  {t}")
        print(f"    证据块跨文档 {docs}；当前实体 id 出现在社区 {comms}")
        print(f"    它在新批次里的 id 是否仍被社区引用：{stale_refs or '否（已失效）'}")


if __name__ == "__main__":
    main()
