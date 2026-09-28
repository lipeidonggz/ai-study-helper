"""本地 OpenAI 兼容 embedding 服务 —— 给 GraphRAG 全量索引的 generate_text_embeddings 用。

为什么需要它：
  - 全量流水线的最后一步要写向量库，必须有 embedding 模型；
  - 我们只有 DeepSeek（不提供 embedding），也没有 OpenAI key；
  - 但项目自己已经用 fastembed + intfloat/multilingual-e5-large（1024 维）做本地嵌入，
    模型权重已在 backend/data/models 缓存（离线可用）。

所以这里把它包成一个 OpenAI 形态的 /v1/embeddings，GraphRAG 侧用
  model_provider: openai / api_base: http://127.0.0.1:8123/v1 / api_key: 任意
接进去（graphrag_llm 里 litellm 调用形如 model="openai/<model>" + api_base）。

只服务本实验，不写任何密钥。用 backend 那个 venv 跑（有 fastembed + fastapi + uvicorn）。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from fastapi import FastAPI
from fastembed import TextEmbedding
from pydantic import BaseModel

# _embed_server.py 在 data/tmp/graphrag-exp-dynamic/ 下：parents[0]=本目录, [1]=tmp, [2]=data, [3]=仓库根
REPO = Path(__file__).resolve().parents[3]
MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-large")
CACHE_DIR = os.environ.get("EMBED_CACHE", str(REPO / "backend" / "data" / "models"))

app = FastAPI(title="local-e5-embeddings")
_lock = threading.Lock()
_model: TextEmbedding | None = None


def get_model() -> TextEmbedding:
    global _model
    if _model is None:
        print(f"[embed] loading {MODEL_NAME} (cache={CACHE_DIR})", flush=True)
        _model = TextEmbedding(model_name=MODEL_NAME, cache_dir=CACHE_DIR)
        print("[embed] model ready", flush=True)
    return _model


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None


@app.get("/health")
def health() -> dict[str, object]:
    return {"ok": True, "model": MODEL_NAME, "cache": CACHE_DIR, "loaded": _model is not None}


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest) -> dict[str, object]:
    texts = [req.input] if isinstance(req.input, str) else list(req.input)
    # fastembed 的 ONNX session 不是线程安全的，串行化最稳（本地嵌入很快）
    with _lock:
        vectors = list(get_model().embed(texts))
    return {
        "object": "list",
        "model": req.model or MODEL_NAME,
        "data": [
            {"object": "embedding", "index": i, "embedding": [float(x) for x in vec]}
            for i, vec in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": 0, "total_tokens": 0},
    }


@app.get("/v1/models")
def models() -> dict[str, object]:
    return {
        "object": "list",
        "data": [{"id": MODEL_NAME, "object": "model", "owned_by": "local"}],
    }
