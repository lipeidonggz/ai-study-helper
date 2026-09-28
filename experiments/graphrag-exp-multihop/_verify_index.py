"""索引建完的验收检查（一把跑完，不调 LLM）。

检查项：
  1. 表的规模与层级分布
  2. 报告齐全度：哪些社区没有报告？里面有没有根节点？（剪枝断点）
  3. 报告是否被某个域带偏：四域关键词在标题/正文里的命中比例
  4. 报告与社区是否对齐：报告自带的 [Data: Entities (id)] 是否属于该社区
  5. 日志里有没有 No report found
  6. 向量库三张表的行数（从日志里读）

用法：python _verify_index.py
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"

DOMAIN_KW = {
    "判官评测": r"判官|LLM-as-Judge|翻转率|flip-rate|金标准|判分",
    "Agent 架构": r"Agent 架构|ReAct|Plan-And-Execute|ReWOO|LLMCompiler|多 Agent",
    "应用方法论": r"Prompt 工程|Context 工程|Harness|Loop 工程|四层演进",
    "Agent 安全": r"containment|sandbox|沙箱|prompt injection|提示注入|遏制|egress",
}


def main() -> int:
    docs = pd.read_parquet(OUT / "documents.parquet")
    tus = pd.read_parquet(OUT / "text_units.parquet")
    ents = pd.read_parquet(OUT / "entities.parquet")
    rels = pd.read_parquet(OUT / "relationships.parquet")
    comms = pd.read_parquet(OUT / "communities.parquet")
    reps = pd.read_parquet(OUT / "community_reports.parquet")

    print("=" * 78)
    print("① 规模")
    print(f"  文档 {len(docs)} · text unit {len(tus)} · 实体 {len(ents)} · 关系 {len(rels)} · 社区 {len(comms)} · 报告 {len(reps)}")
    print("  层级分布（社区 / 报告）:")
    for lv in sorted(comms["level"].unique()):
        c = int((comms["level"] == lv).sum())
        r = int((reps["level"] == lv).sum())
        print(f"    L{lv}: {c:4d} / {r:4d}{'   ← 有缺口' if c != r else ''}")

    print()
    print("② 报告齐全度")
    miss = set(comms["community"]) - set(reps["community"])
    roots = set(comms[comms["parent"].astype(str).isin(["-1", "-1.0"])]["community"])
    print(f"  缺报告的社区: {len(miss)} 个" + (f" → {sorted(miss)[:20]}" if miss else "（齐全）"))
    if miss:
        print(f"  其中是根节点的: {sorted(miss & roots)}  ← 这些子树在 dynamic 里连评分资格都没有")

    print()
    print("③ 报告是否被某个域带偏（标题 / 正文命中各域关键词的报告数）")
    for name, pat in DOMAIN_KW.items():
        rx = re.compile(pat, re.IGNORECASE)
        t = int(reps["title"].fillna("").str.contains(rx).sum())
        b = int(reps["full_content"].fillna("").str.contains(rx).sum())
        print(f"  {name:12s} 标题 {t:4d}/{len(reps)} = {t / len(reps):5.1%}   正文 {b:4d}/{len(reps)} = {b / len(reps):5.1%}")
    print("  （期望：命中比例大致与各域文档占比相当——judge 3/7 篇、架构 2/7、方法论 1/7、安全 1/7；")
    print("    若某个域接近 100%，就是又被打分/报告提示词带偏了）")

    print()
    print("④ 报告与社区是否对齐（语言无关：看报告自带的实体 id）")
    hr_of_entity = dict(zip(ents["id"], ents["human_readable_id"]))
    own: dict[int, set[int]] = {}
    for _, c in comms.iterrows():
        own[int(c["community"])] = {
            int(hr_of_entity[e])
            for e in ([] if c["entity_ids"] is None else list(c["entity_ids"]))
            if e in hr_of_entity
        }
    ratios = []
    for _, r in reps.iterrows():
        cited = []
        for grp in re.findall(r"Entities \(([^)]*)\)", str(r["full_content"])):
            cited += [int(x) for x in re.split(r"[,\s]+", grp) if x.strip().isdigit()]
        if cited:
            ratios.append((int(r["community"]), sum(1 for h in cited if h in own[int(r["community"])]) / len(cited)))
    if ratios:
        bad = [c for c, v in ratios if v < 0.5]
        avg = sum(v for _, v in ratios) / len(ratios)
        print(f"  有引用的报告 {len(ratios)} 份，平均'引用属于自己'的比例 {avg:.3f}")
        print(f"  低于 50% 的: {bad if bad else '无'}")

    print()
    print("⑤ 日志里的失败信号")
    logs = list(ROOT.glob("logs/indexing-engine.log"))
    if logs:
        text = logs[0].read_text(encoding="utf-8", errors="ignore")
        print(f"  No report found: {text.count('No report found for community')} 条")
        print(f"  JSON 解析失败: {text.count('JSONDecodeError')} 条")
        print(f"  json_repair 兜底成功: {text.count('json_repair')} 条（我们的 shim 打的）")
        embedded = re.findall(r"Embedded (\d+) rows for (\w+)", text)
        print(f"  Embedded N rows: {embedded}")
    else:
        print("  （还没跑）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
