"""QdrantVectorStore 单元测试：入库、语义检索、payload 过滤、按文档删除。

设计角度：不依赖 embedding 模型（用假向量），保证测试可离线、可重复；
embedding 模型的真实效果由集成冒烟与 12 条用例跑批验证。
"""

import uuid

from app.storage.qdrant_store import QdrantVectorStore


def _make_store(tmp_path):
    return QdrantVectorStore(path=tmp_path / "qdrant", dim=8)


def _point(chunk_id: str, vector: list[float], source_id: str, text: str) -> dict:
    return {
        "id": chunk_id,
        "vector": vector,
        "payload": {
            "text": text,
            "source_id": source_id,
            "decay_class": "fast",
            "document_id": source_id,
        },
    }


def test_upsert_search_filter_delete(tmp_path):
    store = _make_store(tmp_path)
    # 三个方向差异明显的 8 维向量
    store.upsert(
        "kb1",
        [
            _point("c1", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "O2", "harness 环境设计"),
            _point("c2", [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "A5", "安全半径与沙箱"),
            _point("c3", [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], "OWN", "本地向量库"),
        ],
    )

    # 语义检索：查 c1 方向应优先返回 c1
    hits = store.search("kb1", [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=3)
    assert [h["id"] for h in hits] == ["c1", "c2", "c3"]
    assert hits[0]["text"] == "harness 环境设计"

    # payload 过滤：只留 A5
    hits = store.search(
        "kb1",
        [0.0, 0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0],
        top_k=3,
        filters={"source_id": ["A5"]},
    )
    assert [h["id"] for h in hits] == ["c2"]

    # 跨库隔离：kb2 查不到 kb1 的内容
    assert store.search("kb2", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=3) == []

    # 按文档删除：O2 消失，A5/OWN 还在
    store.delete_by_document("kb1", "O2")
    hits = store.search("kb1", [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=3)
    assert [h["id"] for h in hits] == ["c2", "c3"]

    # 幂等：重复 upsert 同一 chunk 不产生重复（uuid5 稳定 id）
    store.upsert("kb1", [_point("c3", [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], "OWN", "本地向量库")])
    hits = store.search("kb1", [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], top_k=10)
    assert len([h for h in hits if h["id"] == "c3"]) == 1


def test_uuid_stable_mapping():
    """同一 (kb_id, chunk_id) 应映射到同一 Qdrant 点 id，保证重入库不重复。"""
    a = uuid.uuid5(uuid.NAMESPACE_URL, "kb1:c1")
    b = uuid.uuid5(uuid.NAMESPACE_URL, "kb1:c1")
    assert str(a) == str(b)


def test_list_by_document_sorted_by_seq(tmp_path):
    """chunk 预览应按原材料顺序返回，而不是 Qdrant 内部存储序。"""
    store = _make_store(tmp_path)
    # 故意乱序写入：c3 先写，c1 后写
    store.upsert("kb1", [_point("c3", [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], "X", "三")])
    store.upsert("kb1", [_point("c1", [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "X", "一")])
    store.upsert("kb1", [_point("c2", [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], "X", "二")])
    chunks = store.list_by_document("kb1", "X")
    assert [c["id"] for c in chunks] == ["c1", "c2", "c3"]
