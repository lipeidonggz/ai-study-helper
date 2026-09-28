"""Executable checks for the two suspected prompt-tune defects (no API calls)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from graphrag.prompt_tune.loader.input import _sample_chunks_from_embeddings
from graphrag_llm.utils import CompletionMessagesBuilder


def check_message_builder() -> None:
    print("=== TEST 1: CompletionMessagesBuilder.build() identity ===")
    b = CompletionMessagesBuilder().add_system_message("SYS")
    m1 = b.add_user_message("MSG-1").build()
    m2 = b.add_user_message("MSG-2").build()
    print(f"  m1 is m2 ............ {m1 is m2}")
    print(f"  len(m1)={len(m1)}  len(m2)={len(m2)}")
    print(f"  contents of m1 ...... {[str(x.get('content'))[:12] for x in m1]}")
    print("  -> if m1 is m2 and len==3, every caller sees system + ALL user msgs")
    print()


def check_chunk_selection(trials: int = 6) -> None:
    print("=== TEST 2: _sample_chunks_from_embeddings index alignment ===")
    df = pd.DataFrame({"text": [f"row{i}" for i in range(10)]})
    rng = np.random.default_rng(0)
    for t in range(1, trials + 1):
        sampled_idx = sorted(rng.choice(len(df), size=3, replace=False).tolist())
        embeddings = rng.random((3, 4))
        out = _sample_chunks_from_embeddings(df, embeddings, k=2)
        picked = [int(s.replace("row", "")) for s in out["text"]]
        print(f"  trial {t}: sampled rows={sampled_idx}  embeddings=3x4  "
              f"-> selected rows {picked}   in-sampled={[p in sampled_idx for p in picked]}")
    print("  -> selection should be a subset of the sampled rows; if it always lands")
    print("     in the first `len(embeddings)` rows of the FULL frame, indices are misaligned")
    print()


def check_sample_seed() -> None:
    print("=== TEST 3: pandas sample() without random_state ===")
    df = pd.DataFrame({"text": [f"row{i}" for i in range(50)]})
    a = df.sample(n=5)["text"].tolist()
    b = df.sample(n=5)["text"].tolist()
    print(f"  draw A = {a}")
    print(f"  draw B = {b}")
    print(f"  identical? {a == b}")
    print()


if __name__ == "__main__":
    check_message_builder()
    check_chunk_selection()
    check_sample_seed()
