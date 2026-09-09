"""存量 chunk 补"清洗后原文偏移"：复算 → 校验 → 写 chunk_offsets 表。

不重新入库、不重嵌向量、不改 chunk 文本/id。对每个 source：
  1) 用与 index_source 完全一致的逻辑复算 chunk（含 start/end 偏移）；
  2) 与现存向量 payload 的 chunk 校验（数量 + section_path + 文本一致）；
  3) 一致才写 chunk_offsets；不一致则跳过并告警（防 chunker 漂移造成基线偏移）。

用法（在 backend/ 下）：
  .venv\\Scripts\\python.exe scripts\\backfill_chunk_offsets.py            # 全部 ready 源
  .venv\\Scripts\\python.exe scripts\\backfill_chunk_offsets.py --source O2
  .venv\\Scripts\\python.exe scripts\\backfill_chunk_offsets.py --dry-run  # 只校验不写
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本从 backend/ 任意位置运行时都能 import app.*
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.di import build_deps  # noqa: E402
from app.kb.ingest import KB_ID, _build_source_chunks  # noqa: E402
from app.kb.manifest import parse_manifest  # noqa: E402
from app.storage.sqlite.kb_store import KbStore  # noqa: E402


def _manifest_path() -> Path:
    # 素材台账在仓库根目录 data/kb-src（见 app/kb/manifest.py 注释）；qdrant/kb.db 在 backend/data
    return BACKEND_ROOT.parent / "data" / "kb-src" / "MANIFEST.md"


def _kb_db_path() -> Path:
    from app.config import settings

    kb_path = settings.kb_db_path
    return Path(kb_path) if kb_path else BACKEND_ROOT / "data" / "kb.db"


def _validate(existing: list[dict], expected: list[dict]) -> tuple[bool, list[str]]:
    """校验复算 chunk 与现存向量 chunk 是否一致（数量 + section_path + 文本）。"""
    notes: list[str] = []
    if len(existing) != len(expected):
        return False, [f"chunk 数不一致：现存 {len(existing)} vs 复算 {len(expected)}"]
    for i, (ex, exp) in enumerate(zip(existing, expected)):
        ex_path = str((ex.get("payload") or {}).get("section_path", ""))
        ex_text = str(ex.get("text", ""))
        if ex_path != exp["section_path"]:
            notes.append(f"#{i} section_path 不一致：{ex_path!r} vs {exp['section_path']!r}")
        elif not ex_text.endswith(exp["text"]):
            notes.append(f"#{i} 文本不一致（裸块非现存文本后缀）")
        if len(notes) > 20:
            break
    return (not notes), notes


def main() -> int:
    parser = argparse.ArgumentParser(description="存量 chunk 补清洗后原文偏移")
    parser.add_argument("--source", action="append", default=[], help="只处理指定 source_id（可多次）")
    parser.add_argument("--dry-run", action="store_true", help="只校验不写")
    args = parser.parse_args()

    deps = build_deps()
    kb_store = KbStore(_kb_db_path())
    manifest = parse_manifest(_manifest_path())
    sources = [s for s in manifest if s.collected]
    if args.source:
        sources = [s for s in sources if s.source_id in args.source]

    ok, skip, fail = 0, 0, 0
    for src in sources:
        st = kb_store.get(src.source_id)
        if not st or st.get("status") != "ready":
            print(f"[跳过] {src.source_id}: 未入库或非 ready（status={st.get('status') if st else '无记录'}）")
            skip += 1
            continue
        existing = deps.vector_store.list_by_document(KB_ID, src.source_id, limit=100000)
        expected = _build_source_chunks(src, deps.embedder)
        valid, notes = _validate(existing, expected)
        if not valid:
            print(f"[错配] {src.source_id}: 复算与现存不一致，未写入。\n  " + "\n  ".join(notes[:8]))
            fail += 1
            continue
        if args.dry_run:
            print(f"[校验通过] {src.source_id}: {len(expected)} 块，dry-run 不写。")
            ok += 1
            continue
        kb_store.reset_offsets(src.source_id)
        for r in expected:
            if r.get("start") is not None and r.get("end") is not None:
                kb_store.set_chunk_offset(
                    r["chunk_id"], src.source_id, r["section_path"], r["file_idx"], r["start"], r["end"]
                )
        print(f"[写入] {src.source_id}: {len(expected)} 块偏移。")
        ok += 1

    print(f"\n完成：通过/写入 {ok}，跳过 {skip}，错配 {fail}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
