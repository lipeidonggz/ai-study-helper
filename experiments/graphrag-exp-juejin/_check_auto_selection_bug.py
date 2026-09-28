"""验证 AUTO 选法的"行号错位"问题。

源码（prompt_tune/loader/input.py）：
    sampled_text_chunks = chunks_df.sample(n=min(n_subset_max, len(chunks_df)))["text"].tolist()
    embeddings = run_embed_text(sampled_text_chunks, ...).embeddings      # 顺序 = 采样后的顺序
    chunks_df = _sample_chunks_from_embeddings(chunks_df, embeddings, k=k)

而 _sample_chunks_from_embeddings 里：
    nearest_indices = argsort(distances)[:k]        # 位置是"采样后"的下标
    return text_chunks.iloc[nearest_indices]        # 却拿去索引"原始"整表的位置

⇒ 只要 sample 打乱了顺序（一定），取回的行就不是"离质心最近"的那些行。
用纯 pandas 复现，不调 LLM。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def main() -> None:
    n = 10
    df = pd.DataFrame({"text": [f"chunk{i}" for i in range(n)]})
    # 让每个 chunk 的"语义"= 它的下标，便于判断"谁最接近质心"
    emb_by_text = {f"chunk{i}": np.array([float(i), 0.0]) for i in range(n)}

    sampled = df.sample(n=n)["text"].tolist()          # 关键：sample 会打乱顺序
    print("原始顺序 :", df["text"].tolist())
    print("采样后顺序:", sampled)

    emb = np.array([emb_by_text[t] for t in sampled])   # 顺序 = 采样后顺序
    center = emb.mean(axis=0)
    nearest = np.argsort(np.linalg.norm(emb - center, axis=1))[:3]

    should = [sampled[i] for i in nearest]              # 语义上"应该"选这些
    actual = df.iloc[nearest]["text"].tolist()          # 源码实际会取到这些
    print(f"\n最近质心的 3 个（按采样后下标 {list(nearest)}）")
    print("  语义上应该取:", should)
    print("  源码实际取到:", actual)
    print("  一致吗:", should == actual)
    print("\n（n_subset_max=300 时同理：只要原表行数 > k 且顺序被打乱，取回的行就会错）")


if __name__ == "__main__":
    main()
