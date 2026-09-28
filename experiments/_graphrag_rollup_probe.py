"""探针：验证 GraphRAG read_indexer_reports 里的 "community level roll up" 到底做了什么。

做法：用合成数据精确复刻 graphrag/query/indexer_adapters.py::read_indexer_reports 的算法，
分别喂两种 title 语义：
  (A) 当前版本：communities.title = "Community N"（来自 create_communities.py）
  (B) 旧版本：  nodes.title = 实体名（旧 create_final_nodes 的输出）
看最终"报告集合"是什么。

不依赖 graphrag 包，只用 pandas；只读、无副作用。
"""

from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------- 合成语料
# 层级结构（社区 id 随深度递增）。
# 必须满足「可达性约束」：一个社区要么是叶子（不再细分），要么被它的子社区 **完整划分**
# —— 因为 hierarchical_leiden 是对超限社区在**其诱导子图内**再聚类，每个成员都会落到
# 恰好一个子社区里。所以「父社区只被拆走一部分、剩下的留在父社区」这种状态不存在。
#（2026-09-24 修正：初版写的是 C20{A,B} 只拆出 C77{A}，把 B 留在父社区 —— 不可达。）
#
#   L0: C5{A,B,C}                 C6{D}
#   L1: C20{A,B}(parent 5)   C21{C}(parent 5)     C22{D}(parent 6)
#   L2: C77{A}(parent 20)    C78{B}(parent 20)    ← C20 被 77/78 完整划分
COMMUNITIES = [
    # community, level, parent, members
    (5, 0, -1, ["A", "B", "C"]),
    (6, 0, -1, ["D"]),
    (20, 1, 5, ["A", "B"]),
    (21, 1, 5, ["C"]),
    (22, 1, 6, ["D"]),
    (77, 2, 20, ["A"]),
    (78, 2, 20, ["B"]),
]

communities_df = pd.DataFrame(
    [
        {
            "community": c,
            "level": lv,
            "parent": pa,
            "title": f"Community {c}",  # ← create_communities.py 写的就是这个
            "entity_ids": members,
        }
        for c, lv, pa, members in COMMUNITIES
    ]
)

reports_df = pd.DataFrame(
    [
        {"id": f"rep-{c}", "community": c, "level": lv, "title": f"Report of {c}"}
        for c, lv, _pa, _m in COMMUNITIES
    ]
)

# ------------------------------------------------- 旧版输入：实体级 "final_nodes"
# 旧版 final_nodes 一行 = (实体, 它所属的某个社区)，title = 实体名
legacy_nodes_df = pd.DataFrame(
    [
        {"title": m, "community": c, "level": lv}
        for c, lv, _pa, members in COMMUNITIES
        for m in members
    ]
)


def filter_under(df: pd.DataFrame, level: int | None) -> pd.DataFrame:
    """复刻 _filter_under_community_level。"""
    if level is None:
        return df
    return df[df.level <= level]


def read_indexer_reports_current(
    reports: pd.DataFrame, communities: pd.DataFrame, community_level: int | None
) -> pd.DataFrame:
    """当前版本（main）：nodes_df = final_communities.explode('entity_ids')。"""
    nodes_df = communities.explode("entity_ids")
    if community_level is not None:
        nodes_df = filter_under(nodes_df, community_level)
    # perform community level roll up
    nodes_df = nodes_df.copy()
    nodes_df["community"] = nodes_df["community"].fillna(-1).astype(int)
    nodes_df = nodes_df.groupby(["title"]).agg({"community": "max"}).reset_index()
    filtered_community_df = nodes_df["community"].drop_duplicates()
    reports_df = reports.copy()
    if community_level is not None:
        reports_df = filter_under(reports_df, community_level)
    return reports_df.merge(filtered_community_df, on="community", how="inner")


def read_indexer_reports_legacy(
    reports: pd.DataFrame, nodes: pd.DataFrame, community_level: int | None
) -> pd.DataFrame:
    """旧版本（<= v2.x）：nodes_df = final_nodes（title = 实体名）。"""
    nodes_df = filter_under(nodes.copy(), community_level)
    nodes_df["community"] = nodes_df["community"].fillna(-1).astype(int)
    nodes_df = nodes_df.groupby(["title"]).agg({"community": "max"}).reset_index()
    filtered_community_df = nodes_df["community"].drop_duplicates()
    reports_df = filter_under(reports.copy(), community_level)
    return reports_df.merge(filtered_community_df, on="community", how="inner")


def read_indexer_reports_fixed(
    reports: pd.DataFrame, communities: pd.DataFrame, community_level: int | None
) -> pd.DataFrame:
    """候选修复（1 行）：分组键从 communities.title 换成 explode 出来的 entity_ids。

    改动只有一行：
        - nodes_df.groupby(["title"]).agg({"community": "max"})
        + nodes_df.groupby("entity_ids").agg({"community": "max"})
    无需改函数签名、无需把 entities 表传进来 —— 因为 explode 后的 entity_ids
    本来就是一列"实体 id"，正好就是 docstring 说的"每个实体"。
    """
    nodes_df = communities.explode("entity_ids")
    if community_level is not None:
        nodes_df = filter_under(nodes_df, community_level)
    # perform community level roll up（按实体取最深社区）
    nodes_df = nodes_df.copy()
    nodes_df["community"] = nodes_df["community"].fillna(-1).astype(int)
    nodes_df = nodes_df.groupby("entity_ids").agg({"community": "max"}).reset_index()
    filtered_community_df = nodes_df["community"].drop_duplicates()
    reports_df = reports.copy()
    if community_level is not None:
        reports_df = filter_under(reports_df, community_level)
    return reports_df.merge(filtered_community_df, on="community", how="inner")


def show(tag: str, df: pd.DataFrame, level: int | None) -> None:
    got = sorted(df.community.tolist())
    print(f"  {tag:<26} community_level={level!s:<5} -> {len(got)} 份报告 {got}")


if __name__ == "__main__":
    print("合成结构（社区,层,成员）:", [(c, lv, m) for c, lv, _p, m in COMMUNITIES])
    print("communities.title 实测取值:", communities_df.title.tolist())
    print()
    for lv in (0, 1, 2, None):
        print(f"community_level = {lv}")
        show(
            "当前版本(title=Community N)",
            read_indexer_reports_current(reports_df, communities_df, lv),
            lv,
        )
        show(
            "旧版本(title=实体名)",
            read_indexer_reports_legacy(reports_df, legacy_nodes_df, lv),
            lv,
        )
        show(
            "候选修复(group by entity)",
            read_indexer_reports_fixed(reports_df, communities_df, lv),
            lv,
        )
        print()
    print("对照：全量报告数 =", len(reports_df))
