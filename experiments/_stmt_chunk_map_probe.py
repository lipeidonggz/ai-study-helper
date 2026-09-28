"""核对"断言 ↔ chunk 的引用映射"到底是一对一还是多对多（2026-09-21）。

用法：backend\\.venv\\Scripts\\python.exe data\\tmp\\_stmt_chunk_map_probe.py
只读。
"""

import json
import sqlite3
from collections import Counter

conn = sqlite3.connect("backend/data/compile.db")

rows = list(
    conn.execute(
        "select id, subject_id, object_id, predicate, evidence_chunks_json "
        "from compile_statement"
    )
)

chunks_per_stmt = []
chunk_to_stmts = {}
missing = 0
for stmt_id, _s, _o, _p, raw in rows:
    try:
        cids = json.loads(raw) if raw else []
    except Exception:
        cids = []
    cids = [c for c in cids if c]
    if not cids:
        missing += 1
    chunks_per_stmt.append(len(cids))
    for c in set(cids):
        chunk_to_stmts.setdefault(c, set()).add(stmt_id)

print("== 每条断言覆盖的 chunk 数分布 ==")
dist = Counter(chunks_per_stmt)
total = len(chunks_per_stmt)
for n in sorted(dist):
    print(f"   {n} chunk(s): {dist[n]:>4} 条  ({dist[n] / total:.1%})")
print(f"   合计 {total} 条；无 chunk 的 {missing} 条")

multi = [r for r, n in zip(rows, chunks_per_stmt) if n > 1]
print(f"\n== 跨多个 chunk 的断言：{len(multi)} 条（占 {len(multi) / total:.1%}）==")
for stmt_id, _s, _o, pred, raw in multi[:12]:
    cids = json.loads(raw)
    print(f"   {stmt_id} [{pred}] -> {len(cids)} 个块: {cids[:4]}")

print(f"\n== 反向：一个 chunk 承载多少条断言 ==")
per_chunk = Counter(len(v) for v in chunk_to_stmts.values())
print(f"   有断言的 chunk 数: {len(chunk_to_stmts)}")
for n in sorted(per_chunk)[:8]:
    print(f"   {n:>3} 条断言/块: {per_chunk[n]:>3} 个块")
top = sorted(chunk_to_stmts.items(), key=lambda kv: -len(kv[1]))[:5]
print("   最高负载的块:")
for cid, stmts in top:
    print(f"     {cid}: {len(stmts)} 条")
