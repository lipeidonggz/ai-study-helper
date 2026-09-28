"""审读 GraphRAG 产出：社区层级 + 实体/关系规模 + **社区报告的形状**。

只读 parquet，不联网、不调 LLM。
"""

from __future__ import annotations

import pandas as pd

ROOT = __import__("pathlib").Path(__file__).resolve().parent
OUT = ROOT / "output"


def hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def main() -> None:
    tables = {p.stem: pd.read_parquet(p) for p in sorted(OUT.glob("*.parquet"))}

    hr("一、规模总览")
    for name in ("documents", "text_units", "entities", "relationships", "communities", "community_reports"):
        df = tables.get(name)
        print(f"  {name:<18} rows={0 if df is None else len(df):>6}   cols={list(df.columns) if df is not None else '-'}")

    ents = tables["entities"]
    hr("二、实体类型分布（默认 entity_types = organization/person/geo/event）")
    if "type" in ents.columns:
        print(ents["type"].value_counts().to_string())
    print("\n  实体样例（title / type / degree）:")
    cols = [c for c in ("title", "type", "degree", "frequency") if c in ents.columns]
    print(ents[cols].sort_values(cols[-1], ascending=False).head(15).to_string(index=False))
    print("\n  实体描述样例:")
    for _, r in ents.head(3).iterrows():
        desc = str(r.get("description", ""))[:220].replace("\n", " ")
        print(f"    · {r.get('title')}: {desc}")

    hr("三、社区层级（communities.parquet）")
    com = tables["communities"]
    keep = [c for c in ("community", "level", "parent", "children", "size", "title") if c in com.columns]
    print("  level 分布:\n" + com["level"].value_counts().sort_index().to_string())
    print(f"\n  size 统计: min={com['size'].min()} median={int(com['size'].median())} max={com['size'].max()}")
    print("\n  全部社区（按 level, community）:")
    print(com[keep].sort_values(["level", "community"]).to_string(index=False))

    hr("四、社区报告的形状（community_reports.parquet）")
    rep = tables["community_reports"]
    print("  列:", list(rep.columns))
    if "rank" in rep.columns:
        print(f"\n  rank 分布: min={rep['rank'].min()} median={rep['rank'].median()} max={rep['rank'].max()}")
    if "findings" in rep.columns:
        counts = rep["findings"].apply(lambda x: len(x) if hasattr(x, "__len__") else 0)
        print(f"  findings 条数: min={counts.min()} median={int(counts.median())} max={counts.max()}")
    if "full_content" in rep.columns:
        lens = rep["full_content"].str.len()
        print(f"  full_content 字符数: min={lens.min()} median={int(lens.median())} max={lens.max()}")
    if "summary" in rep.columns:
        lens = rep["summary"].str.len()
        print(f"  summary 字符数: min={lens.min()} median={int(lens.median())} max={lens.max()}")

    hr("五、样例报告全文（各挑一份）")
    order = rep.sort_values("community") if "community" in rep.columns else rep
    for idx in {0, len(order) // 2, len(order) - 1}:
        if idx >= len(order):
            continue
        r = order.iloc[idx]
        print(f"\n---------- 报告 idx={idx}  community={r.get('community')}  rank={r.get('rank')} ----------")
        print(f"TITLE: {r.get('title')}")
        print(f"SUMMARY: {str(r.get('summary'))[:600]}")
        print(f"RATING EXPLANATION: {str(r.get('rating_explanation'))[:300]}")
        findings = r.get("findings")
        if hasattr(findings, "__len__"):
            print(f"FINDINGS ({len(findings)} 条):")
            for i, f in enumerate(findings[:3]):
                if isinstance(f, dict):
                    print(f"  [{i}] summary: {str(f.get('summary'))[:160]}")
                    print(f"      explanation: {str(f.get('explanation'))[:400]}...")
        print("FULL_CONTENT（前 1800 字符）:")
        print(str(r.get("full_content"))[:1800])


if __name__ == "__main__":
    main()
