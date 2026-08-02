"""SettingStore 的 SQLite 实现。

单文件数据库（默认 backend/data/app.db），表结构简单：key -> JSON value。
会话、全局范围控制、LLM 配置共用一张 settings 表，按 key 区分。
"""

import json
import sqlite3
import threading
from pathlib import Path

from app.models import GlobalSettings, LLMSettings, SessionSettings
from app.storage.ports import SettingStore


class SqliteSettingStore(SettingStore):
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.commit()

    def _get(self, key: str) -> str:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else ""

    def _set(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def get_global(self) -> GlobalSettings:
        raw = self._get("global")
        return GlobalSettings(**json.loads(raw)) if raw else GlobalSettings()

    def save_global(self, settings: GlobalSettings) -> None:
        self._set("global", json.dumps(settings.__dict__, ensure_ascii=False))

    def get_session(self, session_id: str) -> SessionSettings:
        raw = self._get(f"session:{session_id}")
        return SessionSettings(**json.loads(raw)) if raw else SessionSettings()

    def save_session(self, session_id: str, settings: SessionSettings) -> None:
        self._set(f"session:{session_id}", json.dumps(settings.__dict__, ensure_ascii=False))

    def get_llm_settings(self) -> LLMSettings:
        raw = self._get("llm")
        return LLMSettings(**json.loads(raw)) if raw else LLMSettings()

    def save_llm_settings(self, settings: LLMSettings) -> None:
        self._set("llm", json.dumps(settings.__dict__, ensure_ascii=False))
