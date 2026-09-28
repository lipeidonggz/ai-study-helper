"""量化"报告提示词把主题写死"造成的污染，并对照官方默认提示词。"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"
JUDGE = re.compile("Judge|judge|判官|评测|评估")


def find_default_prompt() -> str:
    import graphrag

    base = Path(graphrag.__file__).parent
    for p in base.rglob("*community_report*graph*"):
        if p.suffix in {".txt", ".py"} and "prompt" in str(p):
            text = p.read_text(encoding="utf-8")
            if "---Role---" in text or "Role" in text:
                return f"{p}\n\n{text[:900]}"
    return "(没找到默认提示词文件)"


def main() -> int:
    reports = pd.read_parquet(OUT / "community_reports.parquet")
    title_hit = reports["title"].fillna("").str.contains(JUDGE)
    body_hit = reports["full_content"].fillna("").str.contains(re.compile("LLM-as-Judge|判官"))
    print(f"278 份报告里：")
    print(f"  标题带 judge/判官/评测 字样: {int(title_hit.sum())}")
    print(f"  正文提到 LLM-as-Judge/判官: {int(body_hit.sum())}")
    print()
    print("按层看'标题带 judge 类字样'：")
    tmp = reports.assign(hit=title_hit)
    print(tmp.groupby("level")["hit"].agg(["count", "sum", "mean"]).round(2).to_string())
    print()
    print("我们用的提示词里那几句把主题写死的话：")
    ours = (ROOT / "prompts-tuned" / "community_report_graph.txt").read_text(encoding="utf-8")
    for line in ours.splitlines():
        if JUDGE.search(line) and len(line) > 40:
            print("   ·", line.strip()[:170])
    print()
    print("=== 官方默认提示词（开头）===")
    print(find_default_prompt()[:900])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
