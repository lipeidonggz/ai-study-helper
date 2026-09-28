"""把各次索引实验的原始产物集中备份到 data/backup/graphrag-index/，并生成 MANIFEST。

备份内容（不动原目录）：
  output/*.parquet（六张表）+ stats.json + context.json · settings.yaml · prompts*/ · input/ ·
  run-*.out.log（已脱敏的 stdout 日志）· cache/（原始 LLM 响应，可复算）
不备份：output/lancedb（向量库，可由 parquet + 本地 embedding 重算，只在 MANIFEST 里记大小）

用法：python _backup_index_runs.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent.parent
SRC = REPO / "data" / "tmp"
DEST = REPO / "data" / "backup" / "graphrag-index"

RUNS = [
    ("graphrag-exp", "A5 英文安全文（单篇）"),
    ("graphrag-exp-juejin", "掘金单篇《判官排查实录》"),
    ("graphrag-exp-mixed", "早前混合实验"),
    ("graphrag-exp-update-base", "增量实验 · 基线"),
    ("graphrag-exp-update", "增量实验 · 增量后"),
    ("graphrag-exp-dynamic", "小语料 7 篇（评测 3 + 架构 2 + 方法论 1 + A5）"),
    ("graphrag-exp-eval", "评测语料 14 篇技术笔记"),
]


def copy_file(src: Path, dst: Path) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    DEST.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, desc in RUNS:
        src = SRC / name
        if not src.exists():
            print(f"跳过 {name}（不存在）")
            continue
        out = DEST / name
        out.mkdir(parents=True, exist_ok=True)
        # 1) 六张表 + 两个 json
        for p in sorted((src / "output").glob("*.parquet")) + sorted((src / "output").glob("*.json")):
            copy_file(p, out / "output" / p.name)
        # 2) 配置、提示词、输入、日志、缓存
        copy_file(src / "settings.yaml", out / "settings.yaml")
        for sub in ("prompts", "prompts-tuned", "input", "logs"):
            if (src / sub).exists():
                shutil.copytree(src / sub, out / sub, dirs_exist_ok=True)
        for p in sorted(src.glob("run-*.out.log")) + sorted(src.glob("prompt-tune-*.out.log")):
            copy_file(p, out / p.name)
        if (src / "cache").exists():
            shutil.copytree(src / "cache", out / "cache", dirs_exist_ok=True)

        # 3) 统计（用于 MANIFEST）
        o = src / "output"
        def n(f: str) -> int:
            p = o / f"{f}.parquet"
            return len(pd.read_parquet(p)) if p.exists() else 0

        src_tokens = 0
        for f in sorted((src / "input").glob("*")):
            if f.is_file():
                src_tokens += len(tk.encode(f.read_text(encoding="utf-8", errors="ignore")))
        lvl_desc = ""
        l0_ratio = ""
        comms_p = o / "communities.parquet"
        reps_p = o / "community_reports.parquet"
        if comms_p.exists():
            comms = pd.read_parquet(comms_p)
            lvl_desc = " · ".join(f"L{k}:{v}" for k, v in sorted(comms.groupby("level").size().items()))
            if reps_p.exists() and src_tokens:
                reps = pd.read_parquet(reps_p)
                l0 = reps[reps["level"] == 0]
                l0_tok = sum(len(tk.encode(str(x))) for x in l0["full_content"])
                l0_ratio = f"{l0_tok / src_tokens:.1%}（{len(l0)} 份）"
        vec_mb = 0.0
        for d in (o / "lancedb", o / "lance"):
            if d.exists():
                vec_mb += sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6
        cache_mb = 0.0
        if (src / "cache").exists():
            cache_mb = sum(f.stat().st_size for f in (src / "cache").rglob("*") if f.is_file()) / 1e6
        rows.append({
            "run": name, "corpus": desc, "src_tokens": src_tokens,
            "docs": n("documents"), "text_units": n("text_units"),
            "entities": n("entities"), "relationships": n("relationships"),
            "communities": n("communities"), "reports": n("community_reports"),
            "levels": lvl_desc, "L0/源": l0_ratio,
            "lancedb_MB": round(vec_mb, 1), "cache_MB": round(cache_mb, 1),
        })
        print(f"备份 {name}: {n('communities')} 社区 / {n('community_reports')} 报告 · cache {cache_mb:.0f} MB")

    lines = [
        "# GraphRAG 索引实验 · 原始产物备份",
        "",
        f"由 `data/tmp/_backup_index_runs.py` 生成；源目录在 `data/tmp/<run>/`，此处为**只读快照**。",
        "",
        "## 清单",
        "",
        "| run | 语料 | 源 token | 文档 | 块 | 实体 | 关系 | 社区 | 报告 | 层级分布 | L0 报告/源 | 向量库 | cache |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| `{r['run']}` | {r['corpus']} | {r['src_tokens']:,} | {r['docs']} | {r['text_units']} | "
            f"{r['entities']:,} | {r['relationships']:,} | {r['communities']:,} | {r['reports']:,} | "
            f"{r['levels']} | {r['L0/源']} | {r['lancedb_MB']} MB | {r['cache_MB']:.0f} MB |"
        )
    lines += [
        "",
        "## 每个 run 目录里有什么",
        "",
        "- `output/`：`documents` / `text_units` / `entities` / `relationships` / `communities` / `community_reports` 六张 parquet + `stats.json` + `context.json`",
        "- `input/`：该次索引的语料原文（可复算 token 数与来源归属）",
        "- `settings.yaml` + `prompts*/`：这次跑用的配置与提示词（**复盘时必须看，提示词换代际差异的记录**）",
        "- `run-*.out.log` / `prompt-tune-*.out.log`：子进程输出（已脱敏）",
        "- `cache/`：原始 LLM 响应（报告生成、抽取、摘要、嵌入），可用于重解析与复核",
        "- **没有** `output/lancedb`（向量库可由 parquet + 本地 embedding 重算，只在清单里记大小）",
        "",
        "## 已知缺失",
        "",
        "- **第一版 dynamic 实验的产物（278 社区 / 231 报告、提示词污染那版）已被删除**（2026-09-27 沛东定“整链从 0 重来”时删的，含 `cache-bad-json`）。只剩下 0034 笔记里的统计数字。",
        "",
    ]
    (DEST / "MANIFEST.md").write_text("\n".join(lines), encoding="utf-8")
    print()
    print("MANIFEST:", DEST / "MANIFEST.md")
    total = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file()) / 1e6
    print(f"备份总大小: {total:.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
