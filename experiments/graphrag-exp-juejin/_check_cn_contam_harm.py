"""中文示例串味实体的危害面：有没有进社区？有没有进报告？

只读。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
NAMES = [
    "陈立诚", "市场策略委员会", "北岭联合储备委员会",
    "华芯科技", "远景控股", "远景交易所",
    "青屿市", "岚港", "白丘国", "云溪", "明珠市", "碧石监狱",
    "林柏舟", "顾丽娟", "苏小满",
]


def as_list(v) -> list:
    if v is None:
        return []
    return list(v)


def main() -> None:
    ent = pd.read_parquet(ROOT / "output" / "entities.parquet")
    com = pd.read_parquet(ROOT / "output" / "communities.parquet")
    rep = pd.read_parquet(ROOT / "output" / "community_reports.parquet")
    rel = pd.read_parquet(ROOT / "output" / "relationships.parquet")

    id2title = dict(zip(ent["id"], ent["title"], strict=False))
    bad_ids = set(ent[ent["title"].isin(NAMES)]["id"])
    print(f"目标实体 {len(bad_ids)} 个（名字命中的）")

    # 落在哪些社区
    hit = []
    for _, c in com.iterrows():
        ids = as_list(c["entity_ids"])
        inter = [i for i in ids if i in bad_ids]
        if inter:
            hit.append((int(c["community"]), int(c["level"]), len(inter),
                        ", ".join(id2title[i] for i in inter[:3])))
    print(f"\n=== 落入的社区（{len(hit)} 个）===")
    for cid, lv, n, ex in sorted(hit):
        print(f"  社区 #{cid} L{lv}：{n} 个 · {ex}")

    # 有无报告、报告里有没有提到
    has_rep = set(rep["community"])
    print("\n=== 报告里是否出现这些名字 ===")
    for name in NAMES:
        n = sum(str(x).count(name) for x in rep["full_content"])
        print(f"  {name:<14} 报告中出现 {n} 次")

    # 涉及它们的关系数（注意：relationships 的 source/target 是**实体名**，不是 id）
    nrel = int(rel["source"].isin(NAMES).sum() + rel["target"].isin(NAMES).sum())
    print(f"\n涉及这些实体的关系（按端点计，按名字匹配）：{nrel} / {len(rel)}")
    sub = rel[rel["source"].isin(NAMES) | rel["target"].isin(NAMES)]
    for _, x in sub.head(12).iterrows():
        print(f"    {x['source']} --[{x['weight']}]--> {x['target']}")


if __name__ == "__main__":
    main()
