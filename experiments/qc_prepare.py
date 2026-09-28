"""离线检索侧验证（真数据快照）：cmp-001 对比机制 + own-002 fallback 混合。"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.rag.bm25 import Bm25Index
from app.rag.reranker import FastEmbedReranker
from app.rag.workflow import RagBackend
from app.storage.fastembed_store import FastEmbedEmbedder
from app.storage.qdrant_store import QdrantVectorStore

EMBED_MODEL = "intfloat/multilingual-e5-large"
RERANK_MODEL = "jinaai/jina-reranker-v2-base-multilingual"
CACHE = r"backend/data/models"


def main() -> None:
    store = QdrantVectorStore(path=r"data/tmp/qdrant-qc", dim=1024)
    embedder = FastEmbedEmbedder(model_name=EMBED_MODEL, cache_dir=CACHE)
    reranker = FastEmbedReranker.shared(model_name=RERANK_MODEL, cache_dir=CACHE)
    bm25 = Bm25Index(vector_store=store)
    backend = RagBackend(
        store,
        embedder,
        bm25=bm25,
        reranker=reranker,
        candidate_k=25,
        budget_tokens=3600,
    )

    cases = [
        ("cmp-001", "OpenAI《Harness engineering》和 Anthropic《How we contain Claude》对 harness 的理解有什么异同？"),
        ("own-002", "咱们项目之前做主动调校时，温度最终定档结论是什么？为什么？"),
    ]
    want = {
        "cmp-001": {"O2:1", "O2:6", "A5:1", "A5:0", "A5:5", "A5:20"},
        "own-002": {"OW1:173"},
    }
    for cid, q in cases:
        ctx = backend.prepare(q)
        print(f"\n########## {cid} ##########")
        print("gate:", ctx.gate)
        print("type:", ctx.query_type, "| rerank:", ctx.rerank_used)
        print("reason:", ctx.reason)
        print("groups:", ctx.groups)
        ids = [h["id"] for h in ctx.hits]
        print("injected ids:", ids)
        got = set(ids) & want[cid]
        print("WANTED evidence in injection:", sorted(got), "| missing:", sorted(want[cid] - got))
        for h in ctx.hits[:8]:
            payload = h["payload"]
            rr = h.get("_rerank")
            print(
                f"  {h['id']} dense={h.get('score', 0):.4f}"
                + (f" rerank={rr:.4f}" if rr is not None else "")
                + f" | {payload.get('section_path','')} | {h['text'][:70]}"
            )


if __name__ == "__main__":
    main()
