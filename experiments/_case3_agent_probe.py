"""把细化的 Query3 套到 A5 上：'知识库里提到的 Agent 安全防护手段都有哪些，各自针对什么威胁？'

三条路各自能拿到什么：
  A 谓词族（protects_against / blocks / detects / isolates / enforces / monitors）
  B 概念锚 agent（hasStatement）
  C chunk 层（含 agent 的块）
并看 'Agent' 这个范围词是帮忙还是毁枚举。

用法：backend\\.venv\\Scripts\\python.exe data\\tmp\\_case3_agent_probe.py
只读。
"""

import json
import pickle
import re
import sqlite3

AGENT_RE = re.compile(r"\bagents?\b|agentic", re.I)
FAMILY = ("protects_against", "blocks", "detects", "isolates", "enforces", "monitors")

conn = sqlite3.connect("backend/data/compile.db")

concepts = {
    r[0]: r[1] for r in conn.execute("select id, canonical from compile_concept")
}
stmts = list(
    conn.execute(
        """
        select id, subject_id, object_id, predicate, evidence_chunks_json
        from compile_statement
        """
    )
)


def endpoints(row):
    _, sid, oid, _, _ = row
    return concepts.get(sid), concepts.get(oid)


def hits_agent(row):
    return any(v and AGENT_RE.search(v) for v in endpoints(row))


def chunk_ids(row):
    try:
        return json.loads(row[4]) if row[4] else []
    except Exception:
        return []


family = [r for r in stmts if r[3] in FAMILY]
family_agent = [r for r in family if hits_agent(r)]

print("== A 路：谓词族 ==")
print(f"   全库同族断言      : {len(family)} 条")
print(f"   其中涉及 agent    : {len(family_agent)} 条")
for r in family_agent:
    s, o = endpoints(r)
    print(f"     {r[0]}  {s} --{r[3]}--> {o}")

agent_stmts = [r for r in stmts if hits_agent(r)]
print("\n== B 路：端点含 agent 的全部断言（不只是防护族）==")
print(f"   {len(agent_stmts)} 条")
for r in agent_stmts:
    s, o = endpoints(r)
    print(f"     {r[0]}  {s} --{r[3]}--> {o}")

print("\n== 块数口径 ==")
def count_chunks(rows):
    return {c for r in rows for c in chunk_ids(r)}

print(f"   谓词族 {len(family)} 条 → {len(count_chunks(family))} 块")
print(f"   谓词族 ∩ agent {len(family_agent)} 条 → {len(count_chunks(family_agent))} 块")
print(f"   端点含 agent {len(agent_stmts)} 条 → {len(count_chunks(agent_stmts))} 块")
print(f"   全部 390 条 → {len(count_chunks(stmts))} 块")

print("\n== C 路：chunk 文本里 agent 的分布 ==")
q = sqlite3.connect("backend/data/qdrant/collection/kb_chunks/storage.sqlite")
aq = {}
for (blob,) in q.execute("select point from points"):
    pt = pickle.loads(blob)
    if pt.payload.get("source_id") == "A5":
        aq[pt.payload["chunk_id"]] = pt.payload["text"]
print(f"   A5 块总数: {len(aq)}")
agent_chunks = [cid for cid, t in aq.items() if AGENT_RE.search(t)]
print(f"   提到 agent 的块: {len(agent_chunks)}")
for cid in sorted(agent_chunks, key=lambda x: int(x.split(":")[1])):
    print(f"     {cid}  {aq[cid][:70].replace(chr(10), ' ')}")
