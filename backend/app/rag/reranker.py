"""本地 rerank 封装：fastembed cross-encoder（ONNX）。

选型（0025 阶段 6 量化后定案）：jinaai/jina-reranker-v2-base-multilingual——
fastembed 0.8 支持列表中唯一的多语言 reranker，适配中英混合素材；
进程内按 (model, cache_dir) 单例复用模型，避免 chat/runner 每次重建。
"""

import threading
from pathlib import Path

from app.storage.ports import Reranker

_INSTANCES: dict[tuple[str, str | None], "FastEmbedReranker"] = {}
_INSTANCES_LOCK = threading.Lock()


class FastEmbedReranker(Reranker):
    def __init__(
        self,
        model_name: str = "jinaai/jina-reranker-v2-base-multilingual",
        cache_dir: str | Path | None = None,
        threads: int | None = None,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._threads = threads
        self._model = None
        self._lock = threading.Lock()

    @classmethod
    def shared(
        cls,
        model_name: str = "jinaai/jina-reranker-v2-base-multilingual",
        cache_dir: str | Path | None = None,
        threads: int | None = None,
    ) -> "FastEmbedReranker":
        """进程内单例：同一模型/缓存目录只加载一次。"""
        key = (model_name, str(cache_dir) if cache_dir else None)
        with _INSTANCES_LOCK:
            inst = _INSTANCES.get(key)
            if inst is None:
                inst = cls(model_name=model_name, cache_dir=cache_dir, threads=threads)
                _INSTANCES[key] = inst
            return inst

    def _get_model(self):
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed.rerank.cross_encoder.text_cross_encoder import (
                        TextCrossEncoder,
                    )

                    self._model = TextCrossEncoder(
                        model_name=self._model_name,
                        cache_dir=self._cache_dir,
                        threads=self._threads,
                    )
        return self._model

    def rerank(self, query: str, documents: list[str]) -> list[float]:
        """query × documents 打分，返回与 documents 同序的分数列表。"""
        if not documents:
            return []
        model = self._get_model()
        with self._lock:
            return [float(s) for s in model.rerank(query, documents)]
