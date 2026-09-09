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
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS chunk_offsets ("
            "chunk_id TEXT PRIMARY KEY,"
            "source_id TEXT NOT NULL,"
            "section_path TEXT NOT NULL DEFAULT '',"
            "file_idx INTEGER NOT NULL DEFAULT 0,"
            "start_pos INTEGER NOT NULL,"
            "end_pos INTEGER NOT NULL"
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

    def reset_offsets(self, source_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM chunk_offsets WHERE source_id = ?", (source_id,))
            self._conn.commit()

    def set_chunk_offset(
        self,
        chunk_id: str,
        source_id: str,
        section_path: str,
        file_idx: int,
        start: int,
        end: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO chunk_offsets(chunk_id, source_id, section_path, file_idx, start_pos, end_pos) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(chunk_id) DO UPDATE SET "
                "source_id=excluded.source_id, section_path=excluded.section_path, "
                "file_idx=excluded.file_idx, start_pos=excluded.start_pos, end_pos=excluded.end_pos",
                (chunk_id, source_id, section_path, file_idx, start, end),
            )
            self._conn.commit()

    def get_chunk_offset(self, chunk_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT chunk_id, source_id, section_path, file_idx, start_pos, end_pos "
                "FROM chunk_offsets WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        if not row:
            return None
        keys = ("chunk_id", "source_id", "section_path", "file_idx", "start_pos", "end_pos")
        return dict(zip(keys, row))

    def list_chunk_offsets(self, source_id: str | None = None) -> list[dict]:
        with self._lock:
            if source_id:
                rows = self._conn.execute(
                    "SELECT chunk_id, source_id, section_path, file_idx, start_pos, end_pos "
                    "FROM chunk_offsets WHERE source_id = ? ORDER BY rowid",
                    (source_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT chunk_id, source_id, section_path, file_idx, start_pos, end_pos "
                    "FROM chunk_offsets ORDER BY rowid"
                ).fetchall()
        keys = ("chunk_id", "source_id", "section_path", "file_idx", "start_pos", "end_pos")
        return [dict(zip(keys, row)) for row in rows]
