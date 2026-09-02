"""知识库管理 API：素材列表 / 入库 / 全量入库 / 删除 / chunk 预览 / 检索调试。"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import settings
from app.kb.ingest import KB_ID, delete_source, index_source
from app.kb.manifest import SourceDoc, parse_manifest
from app.storage.sqlite.kb_store import KbStore

router = APIRouter(prefix="/api/kb", tags=["kb"])


def _manifest_path() -> Path:
    # api/kb.py -> app -> backend -> 仓库根目录；素材台账在根目录 data/kb-src
    return Path(__file__).resolve().parents[3] / "data" / "kb-src" / "MANIFEST.md"


def _deps(request: Request):
    return request.app.state.deps, request.app.state.kb_store


def _merge(manifest: list[SourceDoc], statuses: dict[str, dict]) -> list[dict]:
    rows = []
    for src in manifest:
        st = statuses.get(src.source_id, {})
        rows.append(
            {
                "source_id": src.source_id,
                "name": src.name,
                "category": src.category,
                "carrier": src.carrier,
                "collected": src.collected,
                "knowledge_date": src.knowledge_date,
                "decay_class": src.decay_class,
                "status": st.get("status", "未入库"),
                "chunk_count": st.get("chunk_count", 0),
                "error": st.get("error", ""),
                "indexed_at": st.get("indexed_at", ""),
            }
        )
    return rows


@router.get("/documents")
def list_documents(request: Request):
    """素材台账 + 入库状态合并列表。"""
    _, kb_store = _deps(request)
    manifest = parse_manifest(_manifest_path())
    statuses = {s["source_id"]: s for s in kb_store.list()}
    return _merge(manifest, statuses)


@router.post("/documents/{source_id}/index")
def index_one(source_id: str, request: Request):
    """单篇入库（重入库 = 先删后写）。"""
    deps, kb_store = _deps(request)
    manifest = parse_manifest(_manifest_path())
    source = next((s for s in manifest if s.source_id == source_id), None)
    if source is None:
        raise HTTPException(404, f"素材 {source_id} 不在台账中")
    if not source.collected:
        raise HTTPException(400, f"素材 {source_id} 尚未采集（状态非 ✅）")
    try:
        count = index_source(source, deps.vector_store, deps.embedder, kb_store)
    except Exception as exc:
        kb_store.set_status(source_id, "failed", error=str(exc)[:500])
        raise HTTPException(500, f"入库失败：{exc}")
    return {"source_id": source_id, "status": "ready", "chunk_count": count}


async def _index_all(manifest: list[SourceDoc], deps, kb_store: KbStore) -> None:
    """后台全量入库：只处理已采集素材，逐篇更新状态。"""
    for source in manifest:
        if not source.collected:
            continue
        try:
            await asyncio.to_thread(index_source, source, deps.vector_store, deps.embedder, kb_store)
        except Exception as exc:
            kb_store.set_status(source.source_id, "failed", error=str(exc)[:500])


@router.post("/index-all")
async def index_all(request: Request):
    """一键全量入库（后台任务，逐篇更新状态，前端轮询列表）。"""
    deps, kb_store = _deps(request)
    manifest = parse_manifest(_manifest_path())
    pending = [s for s in manifest if s.collected]
    asyncio.create_task(_index_all(pending, deps, kb_store))
    return {"accepted": len(pending)}


@router.delete("/documents/{source_id}")
def remove(source_id: str, request: Request):
    """删除该素材的入库结果（素材文件不动）。"""
    deps, kb_store = _deps(request)
    delete_source(source_id, deps.vector_store, kb_store)
    return {"ok": True}


@router.get("/documents/{source_id}/chunks")
def list_chunks(source_id: str, request: Request):
    """chunk 预览：按文档列出全部切块。"""
    deps, _ = _deps(request)
    chunks = deps.vector_store.list_by_document(KB_ID, source_id, limit=1000)
    if not chunks:
        raise HTTPException(404, f"{source_id} 尚未入库")
    return [
        {
            "id": c["id"],
            "section_path": c["payload"].get("section_path", ""),
            "tokens": c["payload"].get("tokens") or len(c["text"]),
            "text": c["text"],
        }
        for c in chunks
    ]


class SearchBody(BaseModel):
    query: str
    top_k: int = 8
    filters: dict | None = None


@router.post("/search")
def search(request: Request, body: SearchBody):
    """检索调试台：query → 稠密检索 top-k（混合检索在问答侧步骤 3 接入）。"""
    deps, _ = _deps(request)
    if not deps.embedder or not deps.vector_store:
        raise HTTPException(503, "向量库/embedding 未就绪")
    query_vec = deps.embedder.embed([body.query], is_query=True)[0]
    hits = deps.vector_store.search(KB_ID, query_vec, top_k=body.top_k, filters=body.filters)
    return [
        {
            "score": round(h["score"], 4),
            "source_id": h["payload"].get("source_id", ""),
            "section_path": h["payload"].get("section_path", ""),
            "decay_class": h["payload"].get("decay_class", ""),
            "text": h["text"],
        }
        for h in hits
    ]
