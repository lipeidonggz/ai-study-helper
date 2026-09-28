"""Does the bug survive when n_subset_max >= corpus size (i.e. sample == whole frame)?

Build it so the truth is unambiguous: 8 rows, embedding of row i == i (1-D).
The sampled order is a permutation of ALL rows (the `n_subset_max >= N` case),
so the set of embeddings is complete — only the ORDER differs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from graphrag.prompt_tune.loader.input import _sample_chunks_from_embeddings


def main() -> int:
    df = pd.DataFrame({"text": [f"row{i}" for i in range(8)]})
    perm = np.array([0, 6, 7, 2, 4, 5, 1, 3])          # sample(n=8) 的结果（乱序）
    emb = perm.astype(float).reshape(-1, 1)             # embeddings 按【采样顺序】排列
    k = 3

    center = emb.mean(axis=0)
    nearest_j = np.argsort(np.abs(emb - center).ravel())[:k]   # 采样子集里的位置
    should = [df["text"].iloc[perm[j]] for j in nearest_j]     # 正确：按采样顺序回映
    got = _sample_chunks_from_embeddings(df, emb, k=k)["text"].tolist()

    print("=== n_subset_max >= N（抽样抽满全量，只是顺序被打乱）===")
    print(f"  原始顺序      : {df['text'].tolist()}")
    print(f"  采样返回的顺序: {[f'row{i}' for i in perm.tolist()]}")
    print(f"  embeddings    : {emb.ravel().tolist()}   (按采样顺序)")
    print(f"  质心          : {center.item():.2f}")
    print(f"  离质心最近的 {k} 个（采样序列里的位置）: {nearest_j.tolist()}")
    print(f"  应该返回      : {should}")
    print(f"  实际返回      : {got}")
    print(f"  一致? {should == got}")
    print()
    print("  注：两种情况下 index 的取值范围都合法（都是 0..7），")
    print("      所以这层错误【不会报错】，只会静默取错行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
