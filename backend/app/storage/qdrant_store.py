"""VectorStore 的 Qdrant 本地实现：单集合 + kb_id payload 过滤。

设计角度：
- Qdrant 本地（embedded）模式无需起服务，数据落在 backend/data/qdrant/（已 gitignore）
- 一个集合装所有知识库 chunk，用 kb_id / document_id 等 payload 字段隔离与过滤
- 向量以命名向量 "dense" 存储；本地模式不支持 payload index（警告已确认无效果），
  本阶段规模小，过滤在内存完成即可
"""

import re
import threading
import uuid
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from app.storage.ports import VectorStore

# Qdrant 本地模式会对存储目录加锁：同一进程内按路径复用同一个客户端，
# 避免 build_deps 被多次调用（测试里 create_app 反复创建）时撞锁。
_CLIENT_REGISTRY: dict[str, QdrantClient] = {}
_REGISTRY_LOCK = threading.Lock()


class QdrantVectorStore(VectorStore):
    """把 chunk 向量写进 Qdrant 本地的适配器（懒加载：首次使用才打开客户端）。"""

    def __init__(self, path: str | Path, collection: str = "kb_chunks", dim: int = 1024) -> None:
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._collection = collection
        self._dim = dim
        self._lock = threading.Lock()

    def _get_client(self) -> QdrantClient:
        """懒加载 + 同路径单例：聊天/评测等不使用 KB 的流程不会占用 Qdrant 锁。"""
        key = str(self._path.resolve())
        with _REGISTRY_LOCK:
            client = _CLIENT_REGISTRY.get(key)
            if client is None:
                client = QdrantClient(path=key)
                _CLIENT_REGISTRY[key] = client
            return client

    def _ensure_collection(self) -> None:
        """集合不存在则创建（幂等）。"""
        client = self._get_client()
        if client.collection_exists(self._collection):
            return
        with self._lock:
            if client.collection_exists(self._collection):
                return
            client.create_collection(
                collection_name=self._collection,
                vectors_config={"dense": VectorParams(size=self._dim, distance=Distance.COSINE)},
            )

    def upsert(self, kb_id: str, points: list[dict]) -> None:
        """写入 chunk；point 结构：{"id", "vector", "payload"}。id 用 uuid5 稳定映射。"""
        self._ensure_collection()
        client = self._get_client()
        qpoints: list[PointStruct] = []
        for p in points:
            chunk_id = str(p["id"])
            pid = uuid.uuid5(uuid.NAMESPACE_URL, f"{kb_id}:{chunk_id}")
            payload = dict(p.get("payload") or {})
            payload.update(
                kb_id=kb_id,
                document_id=str(payload.get("document_id", "")),
                chunk_id=chunk_id,
            )
            qpoints.append(
                PointStruct(id=str(pid), vector={"dense": p["vector"]}, payload=payload)
            )
        client.upsert(self._collection, points=qpoints)

    def search(
        self,
        kb_id: str,
        vector: list[float],
        top_k: int,
        filters: dict | None = None,
    ) -> list[dict]:
        """稠密向量检索；filters 支持等值 / 列表任一等值 / gte-lte 范围。"""
        self._ensure_collection()
        client = self._get_client()
        query_filter = self._build_filter(kb_id, filters)
        hits = client.query_points(
            collection_name=self._collection,
            query=vector,
            using="dense",
            query_filter=query_filter,
            limit=top_k,
            with_payload=True,
        ).points
        results: list[dict] = []
        for hit in hits:
            payload = dict(hit.payload or {})
            results.append(
                {
                    "id": payload.get("chunk_id", ""),
                    "score": hit.score,
                    "payload": payload,
                    "text": payload.get("text", ""),
                }
            )
        return results

    def list_by_document(self, kb_id: str, document_id: str, limit: int = 1000) -> list[dict]:
        """按文档列出全部 chunk（chunk 预览用）；按原材料顺序（seq）排序。"""
        self._ensure_collection()
        client = self._get_client()
        hits, _ = client.scroll(
            collection_name=self._collection,
            scroll_filter=Filter(
                must=[
                    FieldCondition(key="kb_id", match=MatchValue(value=kb_id)),
                    FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                ]
            ),
            limit=limit,
            with_payload=True,
        )
        results: list[dict] = []
        for hit in hits:
            payload = dict(hit.payload or {})
            results.append(
                {
                    "id": payload.get("chunk_id", ""),
                    "payload": payload,
                    "text": payload.get("text", ""),
                }
            )
        # Qdrant scroll 返回的是内部存储序，不是入库序；chunk_id 尾号/seq 才是原材料顺序
        return sorted(results, key=lambda c: _chunk_seq(c["payload"], c["id"]))

    def list_all(self, kb_id: str, limit: int = 100000) -> list[dict]:
        """列出该知识库全部 chunk（BM25 内存索引等整体加载用）。"""
        self._ensure_collection()
        client = self._get_client()
        hits, _ = client.scroll(
            collection_name=self._collection,
            scroll_filter=Filter(
                must=[FieldCondition(key="kb_id", match=MatchValue(value=kb_id))]
            ),
            limit=limit,
            with_payload=True,
        )
        return [
            {
                "id": payload.get("chunk_id", ""),
                "payload": payload,
                "text": payload.get("text", ""),
            }
            for hit in hits
            for payload in [dict(hit.payload or {})]
        ]

    def delete_by_document(self, kb_id: str, document_id: str) -> None:
        """按文档删除全部 chunk（文档重入库前调用）。"""
        self._ensure_collection()
        client = self._get_client()
        client.delete(
            collection_name=self._collection,
            points_selector=Filter(
                must=[
                    FieldCondition(key="kb_id", match=MatchValue(value=kb_id)),
                    FieldCondition(key="document_id", match=MatchValue(value=document_id)),
                ]
            ),
        )

    def _build_filter(self, kb_id: str, filters: dict | None) -> Filter:
        must: list[Any] = [FieldCondition(key="kb_id", match=MatchValue(value=kb_id))]
        for key, value in (filters or {}).items():
            if isinstance(value, list):
                must.append(
                    FieldCondition(
                        key=key,
                        match=MatchAny(any=list(value)),
                    )
                )
            elif isinstance(value, dict) and any(k in value for k in ("gte", "lte", "gt", "lt")):
                must.append(FieldCondition(key=key, range=Range(**value)))
            else:
                must.append(FieldCondition(key=key, match=MatchValue(value=value)))
        return Filter(must=must)


def _chunk_seq(payload: dict, chunk_id: str) -> int:
    """取 chunk 序号：优先 payload.seq（新入库），回退解析 chunk_id 尾号（存量数据）。"""
    seq = payload.get("seq")
    if seq is not None:
        try:
            return int(seq)
        except (TypeError, ValueError):
            pass
    # 兼容 "source:13"（入库格式）与 "c1"（测试等简单 id）两种尾部数字
    m = re.search(r"(\d+)$", str(chunk_id))
    return int(m.group(1)) if m else 0
