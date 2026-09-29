"""检查"拆不动"的社区在实测数据里到底存不存在。

口径：叶子 = 没有任何子社区（children 为空）——按官方实现，递归拆到"再拆不出子集"就停，
所以叶子若仍大于 max_cluster_size（默认 10 个实体），就是"算法拆不动"留下的证据。

只读各实验目录的 communities.parquet。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RUNS = {
    "14 篇技术笔记（评测语料）": Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-eval/output"),
    "7 篇小语料": Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-dynamic/output"),
    "51 篇英文新闻": Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-multihop/output"),
}


def main() -> int:
    for tag, out in RUNS.items():
        c = pd.read_parquet(out / "communities.parquet")
        c = c.copy()
        c["n_ent"] = c["entity_ids"].apply(lambda x: 0 if x is None else len(x))
        c["n_child"] = c["children"].apply(lambda x: 0 if x is None else len(x))
        leaves = c[c["n_child"] == 0]
        big = leaves[leaves["n_ent"] > 10]
        print("=" * 90)
        print(f"### {tag}：社区 {len(c)}，叶子 {len(leaves)}（{len(leaves)/len(c):.0%}），"
              f"其中实体数 > 10 的叶子 {len(big)}")
        print(f"  叶子实体数分布：{leaves['n_ent'].describe()[['min','50%','max']].to_dict()}")
        if len(big):
            print("  「拆不动」的叶子（实体数, 社区号）：" +
                  " ".join(f"({r.n_ent},{int(r.human_readable_id)})" for r in big.sort_values('n_ent', ascending=False).head(15).itertuples()))
        else:
            print("  没有实体数 > 10 的叶子")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
