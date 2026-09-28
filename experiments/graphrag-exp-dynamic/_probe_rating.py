"""把评分调用单独拿出来看：拿几个指定社区的**报告全文**，用 GraphRAG 原样的 RATE_QUERY 打一次分，
把模型给的 reason 也打出来（正式跑里的评分调用没有缓存，日志只有分数，没有理由）。

用法：python _probe_rating.py --query "……" --communities 0,1,2 [--repeat 2] [--prompt variant]
  --prompt full   原样（默认）
  --prompt anchor 加一版"档位有定义"的对照提示词（看分数会不会分开）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
sys.path.insert(0, str(ROOT))
import sitecustomize  # noqa: E402,F401

from graphrag.query.context_builder.rate_prompt import RATE_QUERY  # noqa: E402

ANCHOR_PROMPT = """
---Role---
You decide whether a piece of information is on the same subject as a question. Being in the same
broad field (e.g. "AI", "LLM", "evaluation") is NOT enough: the information must help answer THIS question.
---Goal---
Rate how relevant the provided information is for answering the question, using these anchors:
- 0 = 讲的是完全不同的主题（哪怕都跟 AI 有关）
- 1 = 只在词汇层面沾边，实际回答不了这个问题
- 2 = 提供了背景，但核心内容对回答没帮助
- 3 = 部分内容能直接用上
- 4 = 大部分内容能直接用上
- 5 = 就是直接回答这个问题的材料
---Information---
{description}
---Question---
{question}
---Target response length and format---
Please response in the following JSON format with two entries:
- "reason": the reasoning of your rating, please include information that you have considered.
- "rating": the relevancy rating from 0 to 5.
{{
    "reason": str,
    "rating": int.
}}
"""


def load_api_key() -> str:
    con = sqlite3.connect(f"file:{REPO / 'backend' / 'data' / 'app.db'}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    return json.loads(row[0])["api_key"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--communities", default="0,1,2")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--prompt", default="full", choices=["full", "anchor"])
    ap.add_argument("--use-summary", action="store_true")
    args = ap.parse_args()

    os.environ["DEEPSEEK_API_KEY"] = load_api_key()
    reports = pd.read_parquet(ROOT / "output" / "community_reports.parquet")
    communities = pd.read_parquet(ROOT / "output" / "communities.parquet")
    level_of = dict(zip(communities["community"], communities["level"]))
    prompt_tpl = RATE_QUERY if args.prompt == "full" else ANCHOR_PROMPT

    import litellm

    for cid in [int(x) for x in args.communities.split(",")]:
        row = reports[reports["community"] == cid].iloc[0]
        material = row["summary"] if args.use_summary else row["full_content"]
        print(f"\n=== 社区 {cid}（L{level_of.get(cid)}）title: {row['title']} ===")
        print(f"材料：{'summary' if args.use_summary else 'full_content'}，{len(material)} 字符 | 提示词：{args.prompt}")
        for i in range(args.repeat):
            resp = litellm.completion(
                model="deepseek/deepseek-chat",
                api_key=os.environ["DEEPSEEK_API_KEY"],
                messages=[
                    {"role": "system", "content": prompt_tpl.format(description=material, question=args.query)},
                    {"role": "user", "content": args.query},
                ],
                response_format={"type": "json_object"},
                timeout=180,
            )
            content = resp.choices[0].message.content
            try:
                parsed = json.loads(content)
                print(f"  第{i+1}次 → rating={parsed.get('rating')}  reason: {str(parsed.get('reason'))[:260]}")
            except Exception as e:  # noqa: BLE001
                print(f"  第{i+1}次 → 解析失败 {e}: {content[:160]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
