"""现场重生成一个社区的报告（不走缓存），和表里存的对比，判断问题出在生成阶段还是缓存阶段。

用法：python _regen_one_report.py --community 0
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

from graphrag.config.load_config import load_config  # noqa: E402
from graphrag.index.operations.summarize_communities.community_reports_extractor import (  # noqa: E402
    CommunityReportsExtractor,
)
from graphrag.index.operations.summarize_communities.explode_communities import (  # noqa: E402
    explode_communities,
)
from graphrag.index.operations.summarize_communities.graph_context.context_builder import (  # noqa: E402
    build_local_context,
)
from graphrag.index.workflows.create_community_reports import _prep_edges, _prep_nodes  # noqa: E402
from graphrag_llm.completion import create_completion  # noqa: E402
from graphrag.tokenizer.get_tokenizer import get_tokenizer  # noqa: E402


class NoopProgress:
    def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class NoopCallbacks:
    progress = NoopProgress()


def load_api_key() -> str:
    con = sqlite3.connect(f"file:{REPO / 'backend' / 'data' / 'app.db'}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    return json.loads(row[0])["api_key"]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--community", type=int, default=0)
    args = ap.parse_args()
    os.environ["DEEPSEEK_API_KEY"] = load_api_key()

    config = load_config(root_dir=ROOT)
    out = ROOT / "output"
    communities = pd.read_parquet(out / "communities.parquet")
    entities = pd.read_parquet(out / "entities.parquet")
    relationships = pd.read_parquet(out / "relationships.parquet")
    reports = pd.read_parquet(out / "community_reports.parquet")

    nodes = _prep_nodes(explode_communities(communities, entities))
    edges = _prep_edges(relationships)
    local = build_local_context(nodes, edges, None, get_tokenizer(), NoopCallbacks(),
                                max_context_tokens=config.community_reports.max_input_length)
    ctx = local[local["community"] == args.community].iloc[0]["context_string"]

    print("=== 表里存的报告 ===")
    old = reports[reports["community"] == args.community].iloc[0]
    print("标题:", old["title"])
    print("正文前 300:", str(old["full_content"])[:300])

    model = create_completion(config.get_completion_model_config(
        config.community_reports.completion_model_id))  # 不传 cache ⇒ 一定真打
    prompt = config.community_reports.resolved_prompts().graph_prompt
    extractor = CommunityReportsExtractor(model, extraction_prompt=prompt,
                                          max_report_length=config.community_reports.max_length)
    result = await extractor(ctx)
    print()
    print("=== 现场重生成（同一段上下文，不走缓存）===")
    if result.structured_output is None:
        print("!! 抽取失败（structured_output=None）")
        print(str(result.output)[:400])
    else:
        print("标题:", result.structured_output.title)
        print("正文前 300:", str(result.output)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
