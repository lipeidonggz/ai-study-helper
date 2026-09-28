"""定位"报告语言由什么驱动"：对每个社区，分别统计
实体名 / 实体描述 / 关系描述 / 报告正文 的中文占比，横向对照。

用途：沛东指出"社区里出现几个核心英文术语 ⇒ 整份报告变英文"，
需要判断到底哪个信号在带节奏（名字？描述？），才能选对修法。
只读 parquet。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u4e00-\u9fff]")


def frac(texts: list[str]) -> float:
    texts = [t for t in texts if str(t).strip()]
    if not texts:
        return 0.0
    return sum(1 for t in texts if CJK.search(str(t))) / len(texts)


def as_list(v) -> list:
    if v is None:
        return []
    return list(v)


def main() -> None:
    ent = pd.read_parquet(ROOT / "output" / "entities.parquet")
    rel = pd.read_parquet(ROOT / "output" / "relationships.parquet")
    com = pd.read_parquet(ROOT / "output" / "communities.parquet")
    rep = pd.read_parquet(ROOT / "output" / "community_reports.parquet")

    ent_meta = {
        r["id"]: (r["title"], r.get("description", "")) for _, r in ent.iterrows()
    }
    rel_meta = {
        r["id"]: (r.get("source"), r.get("target"), r.get("description", ""))
        for _, r in rel.iterrows()
    }

    rows = []
    for _, c in com.iterrows():
        cid = int(c["community"])
        match = rep[rep["community"] == cid]
        if match.empty:
            continue
        r = match.iloc[0]
        ids = as_list(c["entity_ids"])
        rids = as_list(c["relationship_ids"])
        names = [ent_meta.get(i, ("", ""))[0] for i in ids]
        descs = [ent_meta.get(i, ("", ""))[1] for i in ids]
        rdescs = [rel_meta.get(i, ("", "", ""))[2] for i in rids]
        rows.append(
            {
                "社区": cid,
                "L": int(c["level"]),
                "size": int(c["size"]),
                "实体名中文": round(frac(names), 2),
                "实体描述中文": round(frac(descs), 2),
                "关系描述中文": round(frac(rdescs), 2),
                "报告正文中文": round(
                    len(CJK.findall(str(r["full_content"]))) / max(1, len(str(r["full_content"]))), 3
                ),
                "标题": str(r["title"])[:34],
            }
        )
    df = pd.DataFrame(rows).sort_values("报告正文中文")
    print(df.to_string(index=False))

    print("\n=== 只挑「报告正文几乎全英文」的社区，看它的原料 ===")
    for _, x in df[df["报告正文中文"] < 0.05].iterrows():
        c = com[com["community"] == x["社区"]].iloc[0]
        names = [ent_meta.get(i, ("", ""))[0] for i in as_list(c["entity_ids"])]
        print(f"\n# {x['社区']}  L{int(x['L'])}  size={int(x['size'])}  · {x['标题']}")
        print(f"   实体名（{len(names)}）: {names}")


if __name__ == "__main__":
    main()
