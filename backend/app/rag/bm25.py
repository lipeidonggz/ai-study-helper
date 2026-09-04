"""内存 BM25 索引：向量检索的词匹配补充（混合检索的 BM25 侧）。

设计（0025 阶段 6 量化后定案）：
- own-002 类"术语/标题强匹配"场景纯稠密排序会漏（答案块 rank23），
  BM25 恰擅长该类词匹配；入库侧早已定案"向量 + BM25"但从未接入实现，此处补缺口；
- rank_bm25 + jieba（中英混合切词），语料从 VectorStore 整体加载（list_all），
  进程内懒加载缓存；入库/删除后由 KB API 显式 invalidate()。
"""

import re
import threading
from typing import Any

import jieba
from rank_bm25 import BM25Okapi

_STOPWORDS = {
    "的",
    "了",
    "和",
    "与",
    "是",
    "在",
    "上",
    "下",
    "中",
    "这",
    "那",
    "我们",
    "你们",
    "它们",
    "一个",
    "什么",
    "怎么",
    "为什么",
    "如何",
    "吗",
    "呢",
    "吧",
    "啊",
}

_ASCII_TOKEN = re.compile(r"[a-z0-9]+(?:[._\-][a-z0-9]+)*")
_ASCII_SPLIT = re.compile(r"[a-z0-9]+(?:[._\-][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    """中英混合切词：英文/数字按连续串保留，中文走 jieba 搜索模式。"""
    text = (text or "").lower()
    toks: list[str] = []
    # 英文/数字连续串保留；其余片段交 jieba
    for m in _ASCII_TOKEN.finditer(text):
        w = m.group(0).strip("._-")
        if len(w) >= 2:
            toks.append(w)
    zh_parts = _ASCII_SPLIT.split(text)
    for part in zh_parts:
        for w in jieba.cut_for_search(part):
            w = w.strip()
            if w and w not in _STOPWORDS and len(w) >= 2:
                toks.append(w)
    return toks


class Bm25Index:
    """知识库 chunk 的内存 BM25 索引（懒加载 + 显式失效）。"""

    def __init__(self, vector_store: Any, kb_id: str = "kb-main") -> None:
        self._vector_store = vector_store
        self._kb_id = kb_id
        self._docs: list[dict] | None = None
        self._corpus_tokens: list[list[str]] | None = None
        self._bm25: BM25Okapi | None = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        """入库/删除后调用，强制下次重建。"""
        with self._lock:
            self._docs = None
            self._corpus_tokens = None
            self._bm25 = None

    def _ensure(self) -> tuple[list[dict], BM25Okapi]:
        if self._docs is None:
            with self._lock:
                if self._docs is None:
                    raw = self._vector_store.list_all(self._kb_id)
                    docs = [
                        {
                            "id": c["id"],
                            "source_id": (c["payload"] or {}).get("source_id", ""),
                            "section_path": (c["payload"] or {}).get("section_path", ""),
                            "tokens": (c["payload"] or {}).get("tokens"),
                            "text": c["text"],
                            "payload": c["payload"],
                        }
                        for c in raw
                    ]
                    # 节标题（section_path）是强检索信号但未入 chunk 文本（own-002 教训）：
                    # BM25 侧把节标题拼进可检索文本，不改变存储与向量索引
                    corpus = [
                        tokenize(d["text"] + "\n" + d["section_path"]) for d in docs
                    ]
                    self._docs = docs
                    self._corpus_tokens = corpus
                    self._bm25 = BM25Okapi(corpus) if corpus else None
        return self._docs, self._bm25  # type: ignore[return-value]

    def search(
        self,
        query: str,
        *,
        top_k: int = 25,
        source_id: str | None = None,
    ) -> list[dict]:
        """BM25 检索；结果结构与 VectorStore.search 对齐（score 为 BM25 原始分）。"""
        docs, bm25 = self._ensure()
        if not docs or bm25 is None:
            return []
        scores = bm25.get_scores(tokenize(query))
        idxs = [
            i
            for i, d in enumerate(docs)
            if (source_id is None or d["source_id"] == source_id) and scores[i] > 0
        ]
        ranked = sorted(idxs, key=lambda i: scores[i], reverse=True)[:top_k]
        out: list[dict] = []
        for i in ranked:
            d = docs[i]
            out.append(
                {
                    "id": d["id"],
                    "score": round(float(scores[i]), 4),
                    "payload": d["payload"],
                    "text": d["text"],
                }
            )
        return out
