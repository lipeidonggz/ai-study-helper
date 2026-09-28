"""Does df.sample(n=len(df)) preserve the original row order?"""

from __future__ import annotations

import numpy as np
import pandas as pd

from graphrag.prompt_tune.loader.input import _sample_chunks_from_embeddings


def main() -> int:
    df = pd.DataFrame({"text": [f"row{i}" for i in range(8)]})

    s = df.sample(n=len(df))
    print("=== TEST: sample(n=len(df)) order ===")
    print(f"  original order : {df['text'].tolist()}")
    print(f"  sampled  order : {s['text'].tolist()}")
    print(f"  same order?    {s['text'].tolist() == df['text'].tolist()}")
    print()

    print("=== TEST: full-permutation case (n_subset_max >= N) ===")
    rng = np.random.default_rng(7)
    # fake embeddings keyed to the ROW VALUE so we can see which rows survive
    vals = np.arange(8, dtype=float).reshape(-1, 1)
    perm = rng.permutation(8)
    emb = vals[perm]                      # embeddings in sampled (shuffled) order
    out = _sample_chunks_from_embeddings(df, emb, k=3)
    nearest_j = np.argsort(np.abs(emb - emb.mean()))[:3]
    truth = [df["text"].iloc[perm[j]] for j in nearest_j]      # what SHOULD be returned
    print(f"  sampled order (row ids)   : {perm.tolist()}")
    print(f"  embeddings given to helper: {emb.ravel().tolist()}")
    print(f"  nearest-j (positions)     : {nearest_j.tolist()}")
    print(f"  SHOULD return             : {truth}")
    print(f"  ACTUALLY returned         : {out['text'].tolist()}")
    print(f"  match? {truth == out['text'].tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
