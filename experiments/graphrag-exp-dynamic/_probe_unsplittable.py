"""合成图实验：max_cluster_size=10 时，"拆不动"到底会不会发生？

三张图，全部用官方那条 native 调用（与 graphrag/graphs/hierarchical_leiden.py 同参）：
  ① 20 个节点的完全图（K20，处处等强）—— 预期：拆不动，留下一个 20 个实体的"叶子"
  ② 两个 K10 + 一条桥边 —— 预期：能拆成 2 个
  ③ 一个 K20 里塞一条明显的二分结构（8/12 两簇，簇内稠密、簇间稀疏）—— 预期：能拆

打印每张图的层级、簇大小、以及"最终簇"（is_final_cluster）。
"""

from __future__ import annotations

from itertools import combinations

import graspologic_native as gn

MAX_SIZE = 10


def edges_of(nodes: list[str], weight: float = 1.0):
    return [(a, b, weight) for a, b in combinations(nodes, 2)]


def run(tag: str, nodes: list[str], edges) -> None:
    hcs = gn.hierarchical_leiden(
        edges=edges,
        starting_communities=None,
        resolution=1.0,
        randomness=0.001,
        iterations=1,
        use_modularity=True,
        max_cluster_size=MAX_SIZE,
        seed=0xDEADBEEF,
    )
    by_level: dict[int, dict[int, int]] = {}
    for e in hcs:
        by_level.setdefault(e.level, {})
        by_level[e.level][e.cluster] = by_level[e.level].get(e.cluster, 0) + 1
    final = {}
    for e in hcs:
        if e.is_final_cluster:
            final[e.cluster] = final.get(e.cluster, 0) + 1
    print(f"\n### {tag}（{len(nodes)} 个节点，max_cluster_size={MAX_SIZE}）")
    for lv in sorted(by_level):
        sizes = sorted(by_level[lv].values(), reverse=True)
        print(f"   level {lv}: {len(sizes)} 个簇，大小 {sizes}")
    print(f"   最终簇（is_final_cluster）：{len(final)} 个，大小 {sorted(final.values(), reverse=True)}")


def main() -> int:
    # ① K20：完全图
    k20 = [f"n{i}" for i in range(20)]
    run("① 完全图 K20", k20, edges_of(k20))

    # ② 两个 K10 + 一条桥
    left = [f"L{i}" for i in range(10)]
    right = [f"R{i}" for i in range(10)]
    two = [f"L{i}" for i in range(10)] + [f"R{i}" for i in range(10)]
    run("② 两个 K10 + 一条桥", two, edges_of(left) + edges_of(right) + [("L0", "R0", 1.0)])

    # ③ K20 内部有 8/12 两簇（簇内稠密、簇间稀疏）
    a = [f"a{i}" for i in range(8)]
    b = [f"b{i}" for i in range(12)]
    mixed = [f"a{i}" for i in range(8)] + [f"b{i}" for i in range(12)]
    sparse = [(x, y, 0.1) for x in a for y in b][:3]
    run("③ 8/12 两簇（簇间稀疏）", mixed, edges_of(a) + edges_of(b) + sparse)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
