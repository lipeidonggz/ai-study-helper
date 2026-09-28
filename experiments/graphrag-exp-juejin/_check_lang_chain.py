"""查语言在链上的传递：社区实体的语言 vs 该社区报告的语言。

假设：community_report 提示词没有任何语言要求 ⇒ 报告语言应当**跟随上下文（实体/关系描述）**。
若成立，则"报告一半英文"的根源就是"抽取出来的实体一半英文"。

只读 parquet。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u4e00-\u9fff]")


def zh_frac(texts: list[str]) -> float:
    if not texts:
        return 0.0
    return sum(1 for t in texts if CJK.search(str(t))) / len(texts)


def as_list(v) -> list:
    """parquet 里的 list 列可能是 numpy.ndarray / None —— 别用 `v or []`（数组判空会抛异常）。"""
    if v is None:
        return []
    return list(v)


def main() -> None:
    ent = pd.read_parquet(ROOT / "output" / "entities.parquet")
    com = pd.read_parquet(ROOT / "output" / "communities.parquet")
    rep = pd.read_parquet(ROOT / "output" / "community_reports.parquet")

    id2title = dict(zip(ent["id"], ent["title"], strict=False))
    com2ents = {
        int(c["community"]): [id2title.get(i, "") for i in as_list(c["entity_ids"])]
        for _, c in com.iterrows()
    }

    rows = []
    for _, r in rep.iterrows():
        cid = int(r["community"])
        e_titles = com2ents.get(cid, [])
        rows.append(
            {
                "community": cid,
                "level": int(r["level"]),
                "实体中文占比": round(zh_frac(e_titles), 2),
                "报告标题中文": bool(CJK.search(str(r["title"]))),
                "摘要中文占比": round(
                    len(CJK.findall(str(r["summary"]))) / max(1, len(str(r["summary"]))), 3
                ),
            }
        )
    df = pd.DataFrame(rows).sort_values("实体中文占比", ascending=False)
    print(df.to_string(index=False))

    print("\n=== 分组统计 ===")
    for label, sub in (
        ("实体全中文(占比=1.0)", df[df["实体中文占比"] == 1.0]),
        ("实体全英文(占比=0.0)", df[df["实体中文占比"] == 0.0]),
        ("混合(0<占比<1)", df[(df["实体中文占比"] > 0) & (df["实体中文占比"] < 1)]),
    ):
        if len(sub) == 0:
            continue
        print(
            f"  {label:<22} {len(sub):>2} 份 · 报告标题是中文的 "
            f"{int(sub['报告标题中文'].sum())} 份 · 摘要里中文占比中位 {sub['摘要中文占比'].median():.2f}"
        )

    print("\n=== 关系描述语言 ===")
    rel = pd.read_parquet(ROOT / "output" / "relationships.parquet")
    print(f"  含中文的关系描述: {zh_frac(list(rel['description'].fillna(''))):.0%}")


if __name__ == "__main__":
    main()
