"""补充对账：度数不一致的实体归属、报告缺口、日志里的 LLM 调用量。"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ts = sorted(p.name for p in (ROOT / "update_output").iterdir() if p.is_dir())[-1]
    prev = ROOT / "update_output" / ts / "previous"
    delta = ROOT / "update_output" / ts / "delta"
    out = ROOT / "output"
    pe = pd.read_parquet(prev / "entities.parquet")
    de = pd.read_parquet(delta / "entities.parquet")
    me = pd.read_parquet(out / "entities.parquet")
    mr = pd.read_parquet(out / "relationships.parquet")
    mc = pd.read_parquet(out / "communities.parquet")
    mrep = pd.read_parquet(out / "community_reports.parquet")

    print("=" * 78)
    print("A. 合并后 degree 与真值不符的实体：属于旧批次还是新批次？")
    print("=" * 78)
    fact: dict[str, int] = {t: 0 for t in me["title"]}
    for _, r in mr.iterrows():
        lo, hi = sorted((r["source"], r["target"]))
        if lo in fact:
            fact[lo] += 1
        if hi in fact:
            fact[hi] += 1
    merged_deg = dict(zip(me["title"], me["degree"]))
    prev_set, delta_set = set(pe["title"]), set(de["title"])
    mism = [t for t in me["title"] if t in fact and int(merged_deg[t]) != fact[t]]
    only_old = [t for t in mism if t in prev_set and t not in delta_set]
    both = [t for t in mism if t in prev_set and t in delta_set]
    only_new = [t for t in mism if t not in prev_set]
    print(f"不一致合计 {len(mism)} 个：只在旧批次 {len(only_old)} / 两批都有 {len(both)} / 只在新批次 {len(only_new)}")
    print("  按差值排序（表里 vs 真值）：")
    for t in sorted(mism, key=lambda x: fact[x] - int(merged_deg[x]), reverse=True)[:10]:
        print(f"    {t[:34]:<36} 表里={int(merged_deg[t]):>3} 真值={fact[t]:>3} 差={fact[t]-int(merged_deg[t]):>3}")

    print()
    print("=" * 78)
    print("B. 合并后哪些社区没有报告")
    print("=" * 78)
    have = set(mrep["community"])
    missing = sorted(set(mc["community"]) - have)
    print(f"社区 {len(mc)} 个，报告 {len(mrep)} 份，缺 {len(missing)} 个：{missing}")
    sub = mc[mc["community"].isin(missing)][["community", "level", "size"]]
    print(sub.to_string(index=False))

    print()
    print("=" * 78)
    print("C. 更新链的 LLM 调用量（从日志数）")
    print("=" * 78)
    logs = sorted(ROOT.glob("update-*.out.log"))
    if not logs:
        print("(无日志)")
        return
    text = logs[-1].read_text(encoding="utf-8", errors="ignore")
    print(f"日志 {logs[-1].name}，{len(text)} 字符")
    for pat, label in [
        (r"Workflow started: (\w+)", "工作流启动"),
        (r"summariz\w+", "含 summarize 字样"),
        (r"\[Data:\s*GraphRAG", "报告里的数据引用"),
    ]:
        hits = re.findall(pat, text)
        print(f"  {label}: {len(hits)}")
    # 进度条出现处（每个 LLM 循环通常带一个进度条）
    for m in re.finditer(r"(?m)^\s*(\w[\w \-/]{4,40}progress|[A-Z][^\n]{0,60}progress[^\n]{0,20})", text):
        print("   进度标题:", m.group(1).strip()[:70])
    # 汇总 workflow 个数
    started = re.findall(r"Workflow started: (\w+)", text)
    print("  启动的工作流顺序:", started)


if __name__ == "__main__":
    main()
