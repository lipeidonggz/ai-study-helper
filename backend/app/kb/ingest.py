"""入库服务：素材 → 清洗切块 → 向量化 → 写入向量库 → 记录状态。

骨架硬化原则：流程顺序写死在代码里（清 → 切 → 嵌 → 写 → 记），
模型只出现在 embedding 这一确定节点；格式复杂度（编码/PDF/文件选择）在 extract 层消化。
"""

from app.kb.chunker import chunk_sections, extract_sections_html, extract_sections_md
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
                extract_sections_html(raw)
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


def index_source(
    source: SourceDoc,
    vector_store: VectorStore,
    embedder: Embedder,
    kb_store: KbStore,
) -> int:
    """单篇入库（重入库 = 先删后写）。返回 chunk 数；失败抛异常由调用方记状态。"""
    kb_store.set_status(source.source_id, "indexing")
    tc = lambda t: embedder.token_count([t])[0]  # noqa: E731

    texts: list[str] = []
    metas: list[dict] = []
    seq = 0
    for file_title, sections in _file_units(source):
        line = _source_line(source, file_title)
        # 预留来源行 token：最终入库文本 ≤ 450 tokens（e5 硬上限 512）
        target = max(320, _MAX_FINAL_TOKENS - tc(line) - 10)
        for c in chunk_sections(sections, tc, max_tokens=target):
            texts.append(line + "\n\n" + c["text"])
            metas.append(
                {
                    "section_path": c["section_path"],
                    "seq": seq,
                }
            )
            seq += 1

    if not texts:
        raise ValueError(f"{source.source_id}: 未切出任何 chunk（文件缺失或为空？）")

    vector_store.delete_by_document(KB_ID, source.source_id)
    vecs = embedder.embed(texts, is_query=False)
    token_counts = embedder.token_count(texts)
    points = [
        {
            "id": f"{source.source_id}:{i}",
            "vector": v,
            "payload": {
                "text": t,
                "source_id": source.source_id,
                "document_id": source.source_id,
                "section_path": m["section_path"],
                "knowledge_date": source.knowledge_date,
                "decay_class": source.decay_class,
                "tokens": int(token_counts[i]),
                "seq": m["seq"],
            },
        }
        for i, (m, v, t) in enumerate(zip(metas, vecs, texts))
    ]
    vector_store.upsert(KB_ID, points)
    kb_store.set_status(source.source_id, "ready", chunk_count=len(points))
    return len(points)


def delete_source(source_id: str, vector_store: VectorStore, kb_store: KbStore) -> None:
    """删除入库结果：向量库 chunk + 状态复位（素材文件不动）。"""
    vector_store.delete_by_document(KB_ID, source_id)
    kb_store.reset(source_id)
