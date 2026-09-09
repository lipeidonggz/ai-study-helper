"""入库服务：素材 → 清洗切块 → 向量化 → 写入向量库 → 记录状态。

骨架硬化原则：流程顺序写死在代码里（清 → 切 → 嵌 → 写 → 记），
模型只出现在 embedding 这一确定节点；格式复杂度（编码/PDF/文件选择）在 extract 层消化。
"""

from app.kb.chunker import (
    chunk_sections,
    extract_sections_html_auto,
    extract_sections_md,
)
from app.kb.extract import extract_sections_pdf, read_text_auto
from app.kb.ingest_rules import collect_source_files, source_label
from app.kb.manifest import SourceDoc
from app.storage.ports import Embedder, VectorStore
from app.storage.sqlite.kb_store import KbStore

KB_ID = "kb-main"
_MAX_FINAL_TOKENS = 450


def _file_units(source: SourceDoc) -> list[tuple[str | None, list]]:
    """逐文件抽取 → [(文件级标题 | None, sections)]；单文件源 None，多文件源取文件首个标题。"""
    from app.kb.chunker import Section

    files = collect_source_files(source)
    units: list[tuple[str | None, list[Section]]] = []
    for f in files:
        if f.suffix.lower() == ".pdf":
            secs = extract_sections_pdf(f)
        else:
            raw = read_text_auto(f)
            secs = (
                extract_sections_html_auto(raw)
                if f.suffix.lower() == ".html"
                else extract_sections_md(raw)
            )
        if not secs:
            continue
        title: str | None = None
        if len(files) > 1:
            first = next((s.path for s in secs if s.path and s.path != "引言"), None)
            title = first if first else (f.stem or f.name)
        units.append((title, secs))
    return units


def _source_line(source: SourceDoc, file_title: str | None) -> str:
    """来源行：单文件源带作者/平台·标题；多文件源再拼文件级标题（如 0022/章节名）。"""
    label = source_label(source)
    if file_title:
        return f"来源：{label} › {file_title}"
    return f"来源：{label}"


def _leaf_title(section_path: str) -> str | None:
    """取叶子节标题（section_path 最后一段）参与检索（own-002 教训，2026-09-04）。

    规则：只拼叶子不拼全路径（token 成本）；路径为空或只有一段（顶层节/引言，等于
    文档标题）不拼，避免与来源行重复文档标题。
    """
    path = (section_path or "").strip()
    if not path or " / " not in path:
        return None
    leaf = path.split(" / ")[-1].strip()
    if not leaf:
        return None
    return leaf


def _build_source_chunks(source: SourceDoc, embedder: Embedder) -> list[dict]:
    """复算某源的 chunk 序列（与 index_source 完全一致），返回含偏移的记录（供入库与 backfill 共用）。

    每条记录：{chunk_id, section_path, text(裸块), stored_text(含来源行/叶子标题头), start, end, file_idx, seq}。
    start/end 是裸块 text 在"清洗后全文 T"中的字符偏移（T = 各节段落按序以 \\n 连接）。
    """
    tc = lambda t: embedder.token_count([t])[0]  # noqa: E731
    records: list[dict] = []
    seq = 0
    for file_idx, (file_title, sections) in enumerate(_file_units(source)):
        line = _source_line(source, file_title)
        leaves = [_leaf_title(s.path) for s in sections]
        max_leaf_tokens = max((tc(l) for l in leaves if l), default=0)
        target = max(320, _MAX_FINAL_TOKENS - tc(line) - max_leaf_tokens - 10)
        for c in chunk_sections(sections, tc, max_tokens=target):
            leaf = _leaf_title(c["section_path"])
            head = line + ("\n\n" + leaf if leaf else "")
            stored_text = head + "\n\n" + c["text"]
            records.append(
                {
                    "chunk_id": f"{source.source_id}:{seq}",
                    "section_path": c["section_path"],
                    "text": c["text"],
                    "stored_text": stored_text,
                    "start": c.get("start"),
                    "end": c.get("end"),
                    "file_idx": file_idx,
                    "seq": seq,
                }
            )
            seq += 1
    return records


def index_source(
    source: SourceDoc,
    vector_store: VectorStore,
    embedder: Embedder,
    kb_store: KbStore,
) -> int:
    """单篇入库（重入库 = 先删后写）。返回 chunk 数；失败抛异常由调用方记状态。"""
    kb_store.set_status(source.source_id, "indexing")
    records = _build_source_chunks(source, embedder)
    if not records:
        raise ValueError(f"{source.source_id}: 未切出任何 chunk（文件缺失或为空？）")

    texts = [r["stored_text"] for r in records]
    vector_store.delete_by_document(KB_ID, source.source_id)
    vecs = embedder.embed(texts, is_query=False)
    token_counts = embedder.token_count(texts)
    points = [
        {
            "id": r["chunk_id"],
            "vector": v,
            "payload": {
                "text": r["stored_text"],
                "source_id": source.source_id,
                "document_id": source.source_id,
                "section_path": r["section_path"],
                "knowledge_date": source.knowledge_date,
                "decay_class": source.decay_class,
                "tokens": int(token_counts[i]),
                "seq": r["seq"],
            },
        }
        for i, (r, v) in enumerate(zip(records, vecs))
    ]
    vector_store.upsert(KB_ID, points)
    kb_store.reset_offsets(source.source_id)
    for r in records:
        if r["start"] is not None and r["end"] is not None:
            kb_store.set_chunk_offset(
                r["chunk_id"], source.source_id, r["section_path"], r["file_idx"], r["start"], r["end"]
            )
    kb_store.set_status(source.source_id, "ready", chunk_count=len(points))
    return len(points)


def delete_source(source_id: str, vector_store: VectorStore, kb_store: KbStore) -> None:
    """删除入库结果：向量库 chunk + 状态复位（素材文件不动）。"""
    vector_store.delete_by_document(KB_ID, source_id)
    kb_store.reset(source_id)
