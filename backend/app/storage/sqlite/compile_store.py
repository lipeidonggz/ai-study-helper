"""编译产物落库（S9）：doc / concept / statement / edge 四张表 + 按角色的朴素查询。

为什么单独一个库（`backend/data/compile.db`）：编译产物可以**重跑**（导入是幂等的先删后插），
与 KB 台账（`kb.db`，记入库状态与 chunk 偏移）生命周期不同；两者靠 `chunk_id` 字符串关联即可
（chunk 文本在 Qdrant / 原文里，不需要跨库 join）。

骨架边命名统一为 **snake_case**（`asserts / has_statement / grounded_in`）——趁落库前改代价最小。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SKELETON_EDGES = ("asserts", "has_statement", "grounded_in")

SCHEMA = """
CREATE TABLE IF NOT EXISTS compile_doc (
  source_id TEXT PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  publisher TEXT NOT NULL DEFAULT '',
  file_idx INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS compile_concept (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  canonical TEXT NOT NULL,
  type TEXT NOT NULL DEFAULT 'concept',
  aliases_json TEXT NOT NULL DEFAULT '[]',
  cross_unit INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS compile_statement (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  claim_idx INTEGER NOT NULL,
  subject_id TEXT,
  object_id TEXT,
  predicate TEXT NOT NULL DEFAULT '',
  predicate_surface TEXT NOT NULL DEFAULT '',
  claim_type TEXT,
  roles_json TEXT,
  polarity TEXT,
  clean_start INTEGER,
  clean_end INTEGER,
  anchor TEXT,
  evidence_texts_json TEXT NOT NULL DEFAULT '[]',
  evidence_chunks_json TEXT NOT NULL DEFAULT '[]',
  asserted_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS compile_edge (
  source_id TEXT NOT NULL,
  from_id TEXT NOT NULL,
  to_id TEXT NOT NULL,
  predicate TEXT NOT NULL,
  produced_by TEXT NOT NULL DEFAULT 'compile',
  PRIMARY KEY (from_id, to_id, predicate)
);
CREATE INDEX IF NOT EXISTS ix_stmt_type ON compile_statement(source_id, claim_type);
CREATE INDEX IF NOT EXISTS ix_stmt_subj ON compile_statement(subject_id);
CREATE INDEX IF NOT EXISTS ix_stmt_obj ON compile_statement(object_id);
CREATE INDEX IF NOT EXISTS ix_edge_from ON compile_edge(from_id);
CREATE INDEX IF NOT EXISTS ix_edge_to ON compile_edge(to_id);
"""


class CompileStore:
    """编译产物的 SQLite 落地与查询（读多写少；导入按 source_id 幂等）。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ---------- 写入 ----------

    def import_source(
        self,
        *,
        source_id: str,
        statements_payload: dict,
        merged: dict,
        doc_meta: dict | None = None,
    ) -> dict:
        """把一个源的编译产物落库（幂等：先删该源，再插）。返回对账用的计数。"""
        stmts = statements_payload.get("statements") or []
        concepts = merged.get("entities") or []
        edges = statements_payload.get("edges") or []
        meta = doc_meta or {}
        with self._lock:
            cur = self._conn
            cur.execute("DELETE FROM compile_statement WHERE source_id = ?", (source_id,))
            cur.execute("DELETE FROM compile_concept WHERE source_id = ?", (source_id,))
            cur.execute("DELETE FROM compile_edge WHERE source_id = ?", (source_id,))
            cur.execute(
                "INSERT INTO compile_doc(source_id, title, publisher, file_idx) VALUES(?,?,?,?) "
                "ON CONFLICT(source_id) DO UPDATE SET title = excluded.title, "
                "publisher = excluded.publisher",
                (source_id, meta.get("title", ""), meta.get("publisher", ""), int(meta.get("file_idx", 0))),
            )
            for c in concepts:
                cur.execute(
                    "INSERT OR REPLACE INTO compile_concept(id, source_id, canonical, type, aliases_json, cross_unit) "
                    "VALUES(?,?,?,?,?,?)",
                    (c.get("id"), source_id, c.get("name") or "", c.get("type") or "concept",
                     json.dumps(c.get("aliases") or [], ensure_ascii=False),
                     1 if c.get("cross_unit") else 0),
                )
            for s in stmts:
                ref = s.get("clean_ref") or {}
                cur.execute(
                    "INSERT OR REPLACE INTO compile_statement("
                    "id, source_id, claim_idx, subject_id, object_id, predicate, predicate_surface,"
                    "claim_type, roles_json, polarity, clean_start, clean_end, anchor,"
                    "evidence_texts_json, evidence_chunks_json, asserted_by) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s.get("id"), source_id, int(s.get("claim_idx", -1)), s.get("subject"), s.get("object"),
                     s.get("predicate") or "", s.get("predicate_surface") or "", s.get("claim_type"),
                     json.dumps(s.get("roles"), ensure_ascii=False) if s.get("roles") else None,
                     s.get("polarity"), ref.get("start_pos"), ref.get("end_pos"), s.get("anchor"),
                     json.dumps(s.get("evidence_texts") or [], ensure_ascii=False),
                     json.dumps(s.get("evidence_chunks") or [], ensure_ascii=False),
                     s.get("asserted_by") or f"doc:{source_id}"),
                )
            for e in edges:
                cur.execute(
                    "INSERT OR REPLACE INTO compile_edge(source_id, from_id, to_id, predicate, produced_by) "
                    "VALUES(?,?,?,?,?)",
                    (source_id, e.get("from"), e.get("to"), e.get("predicate"),
                     e.get("produced_by") or "compile"),
                )
            cur.commit()
        return self.counts(source_id)

    # ---------- 查询（检索侧用） ----------

    def counts(self, source_id: str) -> dict:
        with self._lock:
            c = self._conn
            one = lambda sql: c.execute(sql, (source_id,)).fetchone()[0]  # noqa: E731
            return {
                "statements": one("SELECT COUNT(*) FROM compile_statement WHERE source_id = ?"),
                "concepts": one("SELECT COUNT(*) FROM compile_concept WHERE source_id = ?"),
                "edges": one("SELECT COUNT(*) FROM compile_edge WHERE source_id = ?"),
            }

    def sources(self) -> list[str]:
        """已落库的源代号（图路由判断"用户是不是在点某个源"用）。"""
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT source_id FROM compile_doc ORDER BY source_id")]

    def role_statements(self, source_id: str, claim_type: str) -> list[dict]:
        """**按角色过滤**（第 3 步最小闭环用）：某文档下某类论述角色的全部 statement。

        这是"枚举"语义——返回全集，不做 top-k（纯向量做不到这一点）。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM compile_statement WHERE source_id = ? AND claim_type = ? "
                "ORDER BY claim_idx",
                (source_id, claim_type),
            ).fetchall()
        return [self._row_to_stmt(r) for r in rows]

    def doc_statements(self, source_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM compile_statement WHERE source_id = ? ORDER BY claim_idx",
                (source_id,),
            ).fetchall()
        return [self._row_to_stmt(r) for r in rows]

    def statements_of_concept(self, concept_id: str) -> list[dict]:
        """概念路由（第 4 步用）：某概念的所有 statement（subject 或 object 命中）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM compile_statement WHERE subject_id = ? OR object_id = ? "
                "ORDER BY source_id, claim_idx",
                (concept_id, concept_id),
            ).fetchall()
        return [self._row_to_stmt(r) for r in rows]

    def concepts(self, source_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM compile_concept WHERE source_id = ? ORDER BY canonical", (source_id,)
            ).fetchall()
        return [
            {"id": r["id"], "source_id": r["source_id"], "canonical": r["canonical"], "type": r["type"],
             "aliases": json.loads(r["aliases_json"] or "[]"), "cross_unit": bool(r["cross_unit"])}
            for r in rows
        ]

    @staticmethod
    def _row_to_stmt(r: sqlite3.Row) -> dict:
        return {
            "id": r["id"], "source_id": r["source_id"], "claim_idx": r["claim_idx"],
            "subject_id": r["subject_id"], "object_id": r["object_id"],
            "predicate": r["predicate"], "predicate_surface": r["predicate_surface"],
            "claim_type": r["claim_type"],
            "roles": json.loads(r["roles_json"]) if r["roles_json"] else None,
            "polarity": r["polarity"],
            "clean_ref": {"start_pos": r["clean_start"], "end_pos": r["clean_end"]},
            "anchor": r["anchor"],
            "evidence_texts": json.loads(r["evidence_texts_json"] or "[]"),
            "evidence_chunks": json.loads(r["evidence_chunks_json"] or "[]"),
            "asserted_by": r["asserted_by"],
        }
