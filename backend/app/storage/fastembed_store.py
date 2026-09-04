"""Embedder 的 fastembed 本地实现（ONNX，多语言 e5-large）。

设计角度：
- 懒加载：首次 embed 时才下载/加载模型，应用启动不背 2GB 下载的成本
- e5 系列要求 query/passage 前缀，这里显式区分，避免检索质量打折扣
- 缓存目录落在 backend/data/models/（已 gitignore），避开 C 盘空间问题
"""

import threading
from pathlib import Path

from fastembed import TextEmbedding

from app.storage.ports import Embedder

_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


class FastEmbedEmbedder(Embedder):
    """本地多语言 embedding 适配器。"""

    def __init__(
        self,
        model_name: str = "intfloat/multilingual-e5-large",
        cache_dir: str | Path | None = None,
        threads: int | None = None,
    ) -> None:
        self._model_name = model_name
        self._cache_dir = str(cache_dir) if cache_dir else None
        self._threads = threads
        self._model: TextEmbedding | None = None
        self._lock = threading.Lock()

    def _get_model(self) -> TextEmbedding:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = TextEmbedding(
                        model_name=self._model_name,
                        cache_dir=self._cache_dir,
                        threads=self._threads,
                    )
        return self._model

    def embed(
        self,
        texts: list[str],
        *,
        is_query: bool = False,
        batch_size: int = 32,
    ) -> list[list[float]]:
        prefix = _QUERY_PREFIX if is_query else _PASSAGE_PREFIX
        model = self._get_model()
        # batch_size 默认 256 时，e5-large 单批 256×512 token 的中间激活可达十几 GB（CPU）；
        # 全量入库实测内存失控（T1 17GB+，2026-09-04）→ 降批到 32，峰值降一个量级
        return [
            v.tolist()
            for v in model.embed([prefix + t for t in texts], batch_size=batch_size)
        ]

    def token_count(self, texts: list[str]) -> list[int]:
        """逐条统计 token 数（切块长度控制用，e5 硬上限 512）。"""
        model = self._get_model()
        return [model.token_count([t]) for t in texts]
