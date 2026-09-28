"""v4 产出体检：把实体分成三桶，另看报告的引用密度。

三桶：
  A 示例串味——名字命中 GraphRAG 抽取提示词的 few-shot 示例，且不在原文里
  B 结构垃圾——章节引用（第N节）、产物编号（0号输出）、单字母、纯枚举/字段名（全大写、下划线、斜杠）
  C 真幻觉候选——既不在原文、也不在提示词、也不属于 B
只读。
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
doc = (ROOT / "input" / "doc.txt").read_text(encoding="utf-8").lower()
prompt = (ROOT / "prompts" / "extract_graph.txt").read_text(encoding="utf-8").lower()

STRUCT = [
    (re.compile(r"^第\s*\d+\s*节"), "章节引用"),
    (re.compile(r"^\d+\s*号"), "产物编号"),
    (re.compile(r"^[A-Za-z]$"), "单字母"),
    (re.compile(r"^[A-Z0-9_/\-\.]+$"), "全大写枚举/字段名"),
    (re.compile(r"^\d+([.,]\d+)?%?$"), "纯数字"),
]


def in_text(name: str) -> bool:
    n = name.strip().lower()
    if not n:
        return True
    if n in doc:
        return True
    if re.search(r"[\u4e00-\u9fff]", n):  # 中文名：只做整串匹配，避免误判
        return False
    words = [w for w in re.split(r"[^a-z0-9]+", n) if len(w) > 2]
    return bool(words) and all(w in doc for w in words)


def struct_kind(name: str) -> str | None:
    for rx, label in STRUCT:
        if rx.match(name.strip()):
            return label
    return None


def main() -> None:
    ent = pd.read_parquet(ROOT / "output" / "entities.parquet")
    rel = pd.read_parquet(ROOT / "output" / "relationships.parquet")
    rep = pd.read_parquet(ROOT / "output" / "community_reports.parquet")

    a: list[dict] = []
    b: list[dict] = []
    c: list[dict] = []
    for _, e in ent.iterrows():
        name = str(e["title"])
        row = {"name": name, "type": e.get("type"), "deg": e.get("degree")}
        in_doc = in_text(name)
        if not in_doc and name.strip().lower() in prompt:
            a.append(row)
        elif struct_kind(name):
            row["kind"] = struct_kind(name)
            b.append(row)
        elif not in_doc:
            c.append(row)

    print(f"实体总数 {len(ent)}")
    print(f"\n【A 示例串味】{len(a)} 个"); [print("   ", r) for r in a]
    print(f"\n【B 结构垃圾】{len(b)} 个")
    for r in sorted(b, key=lambda x: (x["kind"], x["name"])):
        print(f"    {r['kind']:<14} {r['name']:<28} [{r['type']}] deg={r['deg']}")
    print(f"\n【C 真幻觉候选】{len(c)} 个"); [print("   ", r) for r in c]

    print("\n=== 报告引用密度 ===")
    rows = []
    for _, r in rep.iterrows():
        fc = str(r["full_content"])
        rows.append(
            {
                "社区": int(r["community"]),
                "字符": len(fc),
                "引用标记数": len(re.findall(r"\[Data:", fc)),
                "findings": len(r["findings"]) if hasattr(r["findings"], "__len__") else 0,
            }
        )
    d = pd.DataFrame(rows).sort_values("引用标记数")
    print(d.to_string(index=False))
    print(f"\n无引用标记的报告: {int((d['引用标记数'] == 0).sum())} / {len(d)}")

    print("\n=== 旧版出现过的幻觉在 v4 里还在吗 ===")
    for w in ("Autonomous Driving", "自动驾驶", "Johns Hopkins", "约翰霍普金斯"):
        n_ent = sum(1 for x in ent["title"] if w.lower() in str(x).lower())
        n_rep = sum(str(x).lower().count(w.lower()) for x in rep["full_content"])
        print(f"  {w:<20} 实体中 {n_ent} · 报告中 {n_rep} 次 · 原文中 {doc.count(w.lower())} 次")


if __name__ == "__main__":
    main()
