"""增量前后对账：三份产物（previous / delta / 合并后 output）逐表比对。只读。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
TABLES = [
    "documents",
    "text_units",
    "entities",
    "relationships",
    "communities",
    "community_reports",
]


def load(d: Path, name: str) -> pd.DataFrame | None:
    f = d / f"{name}.parquet"
    return pd.read_parquet(f) if f.exists() else None


def main() -> None:
    ts = sorted(p.name for p in (ROOT / "update_output").iterdir() if p.is_dir())[-1]
    prev = ROOT / "update_output" / ts / "previous"
    delta = ROOT / "update_output" / ts / "delta"
    out = ROOT / "output"
    print(f"时间戳 {ts}\nprevious={prev.name}  delta={delta.name}\n")

    print("=" * 78)
    print("一、规模对账（previous / delta / 合并后）")
    print("=" * 78)
    print(f"{'表':<20}{'previous':>10}{'delta':>8}{'合并后':>10}{'是否=相加':>12}")
    for name in TABLES:
        a, b, c = load(prev, name), load(delta, name), load(out, name)
        na, nb, nc = (0 if x is None else len(x) for x in (a, b, c))
        print(f"{name:<20}{na:>10}{nb:>8}{nc:>10}{('是' if nc == na + nb else '否'):>12}")

    print()
    print("=" * 78)
    print("二、documents：合并后有哪些文档")
    print("=" * 78)
    d = load(out, "documents")
    print(d[["human_readable_id", "title"]].to_string(index=False))

    print()
    print("=" * 78)
    print("三、communities：编号 / 层级 / 标题 / 父指针")
    print("=" * 78)
    pc, dc, mc = load(prev, "communities"), load(delta, "communities"), load(out, "communities")
    print(f"previous 社区 {len(pc)} 个：编号 {sorted(pc['community'].tolist())}")
    print(f"  标题举例：{pc['title'].head(3).tolist()}")
    print(f"delta    社区 {len(dc)} 个：编号 {sorted(dc['community'].tolist())}")
    print(f"  标题举例：{dc['title'].head(3).tolist()}")
    print(f"合并后   社区 {len(mc)} 个：编号 {sorted(mc['community'].tolist())}")
    print(f"  level 分布 {mc['level'].value_counts().sort_index().to_dict()}")
    print(f"  根节点(parent<0) {int((mc['parent'] < 0).sum())} 个")
    all_renamed = bool(mc["title"].astype(str).str.match(r"^Community \d+$").all())
    print(f"  标题全部变成 Community N 了吗：{all_renamed}")
    print(f"  标题举例：{mc['title'].head(4).tolist()} … {mc['title'].tail(3).tolist()}")
    print("\n  合并后社区明细（按 level, community）：")
    print(
        mc.sort_values(["level", "community"])[
            ["community", "level", "parent", "size", "title"]
        ].to_string(index=False)
    )

    print()
    print("=" * 78)
    print("四、community_reports：编号映射与旧报告是否被改写")
    print("=" * 78)
    pr, dr, mr = (
        load(prev, "community_reports"),
        load(delta, "community_reports"),
        load(out, "community_reports"),
    )
    for tag, r in (("previous", pr), ("delta", dr), ("合并后", mr)):
        print(f"{tag:<10} {len(r)} 份，community={sorted(r['community'].tolist())}")
    rep_renamed = bool(mr["title"].astype(str).str.match(r"^Community \d+$").all())
    print(f"\n合并后报告标题是否也是 Community N：{rep_renamed}")
    print("合并后报告标题举例：", mr["title"].head(3).tolist())
    # 旧报告的 id 是否保持
    if "id" in pr.columns and "id" in mr.columns:
        keep = set(pr["id"]) & set(mr["id"])
        print(f"旧报告 id 保留下来的数量：{len(keep)} / {len(pr)}")

    print()
    print("=" * 78)
    print("五、entities：degree 口径（这一节是重点）")
    print("=" * 78)
    pe, de, me = load(prev, "entities"), load(delta, "entities"), load(out, "entities")
    print(f"previous 实体 {len(pe)}   delta 实体 {len(de)}   合并后 {len(me)}")
    # 跨批都出现的实体
    common = set(pe["title"]) & set(de["title"])
    print(f"两批都出现的实体（按 title）：{len(common)} 个")
    print(f"  举例：{sorted(common)[:8]}")
    merged_deg = dict(zip(me["title"], me["degree"]))
    prev_deg = dict(zip(pe["title"], pe["degree"]))
    delta_deg = dict(zip(de["title"], de["degree"]))

    print("\n  A. 跨批实体：合并后 degree 是否等于旧值（源码说取 first）")
    same = sum(1 for t in common if merged_deg.get(t) == prev_deg.get(t))
    print(f"    与旧值相同：{same} / {len(common)}")
    sample = sorted(common)[:6]
    for t in sample:
        print(f"      {t[:28]:<30} 旧={prev_deg.get(t)} 新批={delta_deg.get(t)} 合并后={merged_deg.get(t)}")

    print("\n  B. 只在新批次里的实体：degree 是「小图内」的度数吗")
    only_new = [t for t in de["title"] if t not in set(pe["title"])]
    print(f"    只在新批次的实体：{len(only_new)} 个")
    fact_deg = {}
    for _, r in me.iterrows():
        pass
    # 用合并后的边表实际数一遍，作为"真值"
    for _, r in me.iterrows():
        fact_deg[r["title"]] = 0
    for _, r in load(out, "relationships").iterrows():
        lo, hi = sorted((r["source"], r["target"]))
        if lo in fact_deg:
            fact_deg[lo] += 1
        if hi in fact_deg:
            fact_deg[hi] += 1
    for t in only_new[:8]:
        print(
            f"      {t[:28]:<30} 表里={int(merged_deg.get(t, -1))}  "
            f"按合并后边表实数={fact_deg.get(t, -1)}  delta小图内={int(delta_deg.get(t, -1))}"
        )
    # 全量统计：表里的 degree 与真值不一致的实体数
    mism = [t for t in me["title"] if int(merged_deg.get(t, 0)) != fact_deg.get(t, 0) and t in fact_deg]
    print(f"\n  C. 合并后表里 degree 与「按合并后边表实数」不一致的实体：{len(mism)} / {len(me)}")
    for t in mism[:8]:
        print(f"      {t[:30]:<32} 表里={int(merged_deg.get(t))} 实数={fact_deg.get(t)}")

    print()
    print("=" * 78)
    print("六、某几个实体的证据块是否被新批补全")
    print("=" * 78)
    for t in sorted(common)[:5]:
        a = pe.loc[pe["title"] == t, "text_unit_ids"].iloc[0]
        b = de.loc[de["title"] == t, "text_unit_ids"].iloc[0]
        c = me.loc[me["title"] == t, "text_unit_ids"].iloc[0]
        print(f"  {t[:26]:<28} 旧={len(a)} 新批={len(b)} 合并后={len(c)}")

    print()
    print("=" * 78)
    print("七、stats.json 里的用量（若有）")
    print("=" * 78)
    for tag, d in (("delta", delta), ("output", out)):
        f = d / "stats.json"
        if f.exists():
            print(f"--- {tag}: {f.read_text(encoding='utf-8')[:600]}")


if __name__ == "__main__":
    main()
