"""定位"社区有、报告没有"的 47 个社区是在哪一层被丢掉的（纯离线，不调 LLM）。

思路：把 create_community_reports 的**上下文构建**部分原样复算一遍
（explode_communities → _prep_nodes/_prep_edges → build_local_context → build_level_context），
数一数每个 level 的上下文里到底有多少个社区：
  - 上下文里就是 231 个 ⇒ 丢失发生在上下文构建层（可精确定位到哪个函数）；
  - 上下文里有 278 个 ⇒ 丢失发生在 LLM 抽取/落表层（再看 derive_from_rows 的静默吞异常）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import graphrag.data_model.schemas as schemas

ROOT = Path(__file__).resolve().parent

from graphrag.index.operations.summarize_communities.explode_communities import (  # noqa: E402
    explode_communities,
)
from graphrag.index.operations.summarize_communities.graph_context.context_builder import (  # noqa: E402
    build_level_context,
    build_local_context,
)
from graphrag.index.workflows.create_community_reports import (  # noqa: E402
    _prep_edges,
    _prep_nodes,
)
from graphrag.index.operations.summarize_communities.utils import get_levels  # noqa: E402
from graphrag.tokenizer.get_tokenizer import get_tokenizer  # noqa: E402


class NoopProgress:
    """progress_iterable 需要一个有 __call__ 的对象。"""

    def __call__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class NoopCallbacks:
    progress = NoopProgress()


def main() -> int:
    out = ROOT / "output"
    communities = pd.read_parquet(out / "communities.parquet")
    entities = pd.read_parquet(out / "entities.parquet")
    relationships = pd.read_parquet(out / "relationships.parquet")
    reports = pd.read_parquet(out / "community_reports.parquet")

    max_input_length = 4000  # 与 settings 默认一致；如有覆盖可改
    tokenizer = get_tokenizer()

    nodes = _prep_nodes(explode_communities(communities, entities))
    edges = _prep_edges(relationships)
    local_contexts = build_local_context(
        nodes, edges, None, tokenizer, NoopCallbacks(), max_context_tokens=max_input_length
    )
    print(f"local_contexts 行数（= 有本地上下文的社区数）: {len(local_contexts)}")
    print("local_contexts 列:", list(local_contexts.columns))
    flag_col = schemas.CONTEXT_EXCEED_FLAG
    print(f"其中 {flag_col}=True 的: {int(local_contexts[flag_col].sum())}")

    hierarchy = (
        communities
        .explode("children")
        .rename({"children": "sub_community"}, axis=1)
        .loc[:, ["community", "level", "sub_community"]]
        .dropna()
    )

    levels = get_levels(nodes)
    print("levels:", levels)

    accumulated: list[pd.DataFrame] = []
    seen: list[int] = []
    for level in levels:
        df = build_level_context(
            pd.DataFrame(accumulated) if accumulated else pd.DataFrame(),
            community_hierarchy_df=hierarchy,
            local_context_df=local_contexts,
            tokenizer=tokenizer,
            level=level,
            max_context_tokens=max_input_length,
        )
        ids = sorted(int(x) for x in df[schemas.COMMUNITY_ID].tolist())
        seen.extend(ids)
        print(f"  level {level}: 上下文社区 {len(ids)} 个 → {ids[:12]}{'…' if len(ids) > 12 else ''}")
        # 模拟"这一层生成完的报告"：报告表里已存在的该层社区
        level_reports = reports[reports["level"] == level]
        accumulated.extend(level_reports.to_dict("records"))

    in_context = set(seen)
    all_ids = set(int(x) for x in communities["community"])
    report_ids = set(int(x) for x in reports["community"])
    print()
    print(f"社区总数 {len(all_ids)} · 进入上下文 {len(in_context)} · 实际有报告 {len(report_ids)}")
    print(f"进了上下文但没报告（⇒ 丢在 LLM/落表层）: {len(in_context - report_ids)}")
    print(f"没进上下文（⇒ 丢在上下文构建层）: {len(all_ids - in_context)} → {sorted(all_ids - in_context)[:30]}")
    lvl = dict(zip(communities["community"], communities["level"]))
    print("  没进上下文的按层:", pd.Series([lvl[i] for i in sorted(all_ids - in_context)]).value_counts().to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
