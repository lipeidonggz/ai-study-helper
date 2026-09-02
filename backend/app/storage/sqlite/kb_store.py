"""知识库入库状态存储：source_id → status / chunk_count / error / indexed_at。"""

import sqlite3
import threading
from pathlib import Path


class KbStore:
    """SQLite 单表状态库；素材元数据以 MANIFEST.md 为权威，这里只记入库状态。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kb_documents ("
            "source_id TEXT PRIMARY KEY,"
            "status TEXT NOT NULL,"
            "chunk_count INTEGER NOT NULL DEFAULT 0,"
            "error TEXT NOT NULL DEFAULT '',"
            "indexed_at TEXT"
            ")"
        )
        self._conn.commit()

    def set_status(
        self, source_id: str, status: str, chunk_count: int = 0, error: str = ""
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kb_documents(source_id, status, chunk_count, error, indexed_at) "
                "VALUES(?, ?, ?, ?, datetime('now')) "
                "ON CONFLICT(source_id) DO UPDATE SET "
                "status=excluded.status, chunk_count=excluded.chunk_count, "
                "error=excluded.error, indexed_at=excluded.indexed_at",
                (source_id, status, chunk_count, error),
            )
            self._conn.commit()

    def get(self, source_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT source_id, status, chunk_count, error, indexed_at "
                "FROM kb_documents WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        if not row:
            return None
        keys = ("source_id", "status", "chunk_count", "error", "indexed_at")
        return dict(zip(keys, row))

    def list(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT source_id, status, chunk_count, error, indexed_at FROM kb_documents"
            ).fetchall()
        keys = ("source_id", "status", "chunk_count", "error", "indexed_at")
        return [dict(zip(keys, row)) for row in rows]

    def reset(self, source_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM kb_documents WHERE source_id = ?", (source_id,))
            self._conn.commit()
