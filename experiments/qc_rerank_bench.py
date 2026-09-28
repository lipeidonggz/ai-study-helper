"""rerank 成本基准：本地 jina-reranker-v2-base-multilingual（ONNX CPU）。

测不同候选对数的单次调用延迟（预热后多次取中位数）与吞吐。
"""

import statistics
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from app.rag.reranker import FastEmbedReranker
from app.storage.qdrant_store import QdrantVectorStore

RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"
CACHE = r"backend/data/models"


def main() -> None:
    store = QdrantVectorStore(path=r"data/tmp/qdrant-qc", dim=1024)
    chunks = store.list_all("kb-main")
    docs = [c["text"] for c in chunks]
    reranker = FastEmbedReranker.shared(model_name=RERANK_MODEL, cache_dir=CACHE)

    queries = {
        "cmp-001（中英长对比）": (
            "OpenAI《Harness engineering》和 Anthropic《How we contain Claude》对 harness 的理解有什么异同？",
            44,
        ),
        "own-002（中文细节）": (
            "咱们项目之前做主动调校时，温度最终定档结论是什么？为什么？",
            44,
        ),
    }
    sizes = [10, 25, 50, 100]

    for qname, (q, cap) in queries.items():
        print(f"\n##### query: {qname} #####")
        _ = reranker.rerank(q, docs[:10])  # 预热（含模型首次推理/线程池初始化）
        for n in sizes:
            sample = docs[: min(n, cap)]
            laps = []
            for _ in range(3):
                t0 = time.perf_counter()
                list(reranker.rerank(q, sample))
                laps.append((time.perf_counter() - t0) * 1000)
            med = statistics.median(laps)
            print(
                f"  n={len(sample):3d} -> median {med:7.1f} ms "
                f"({len(sample) / (med / 1000):6.0f} pairs/s)"
            )


if __name__ == "__main__":
    main()
