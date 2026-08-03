"""SettingStore 的 SQLite 实现：把设置持久化到单文件数据库。

设计角度：为什么用"key -> JSON value"一张表？
- 各类设置（全局、会话、LLM）结构不同，但都不需要复杂查询，
  用统一的 key-value 表最省事；将来需要关系查询再拆表

文件位置：默认 backend/data/app.db（已 gitignore，API Key 不入库）
并发说明：sqlite3 连接默认不允许跨线程，这里用 check_same_thread=False +
threading.Lock 保证异步框架下的线程安全（单用户场景足够）。
"""

import json  # 序列化/反序列化设置对象
import sqlite3  # Python 内置的 SQLite 驱动，无需额外安装
import threading  # 加锁保护连接，避免多线程同时写
from pathlib import Path

from app.models import GlobalSettings, LLMSettings, SessionSettings
from app.storage.ports import SettingStore


class SqliteSettingStore(SettingStore):
    """把设置存进 SQLite 的适配器。"""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)  # 目录不存在则创建
        self._lock = threading.Lock()  # 所有读写操作共用一把锁
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        # 建表（IF NOT EXISTS：重复启动不报错）；value 存 JSON 字符串
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self._conn.commit()  # 提交建表

    def _get(self, key: str) -> str:
        """读一行；不存在返回空字符串。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else ""

    def _set(self, key: str, value: str) -> None:
        """写一行；key 已存在则覆盖（UPSERT 语法）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()  # 提交事务，确保写入落盘

    def get_global(self) -> GlobalSettings:
        raw = self._get("global")
        return GlobalSettings(**json.loads(raw)) if raw else GlobalSettings()

    def save_global(self, settings: GlobalSettings) -> None:
        self._set("global", json.dumps(settings.__dict__, ensure_ascii=False))

    def get_session(self, session_id: str) -> SessionSettings:
        raw = self._get(f"session:{session_id}")  # 会话 key 带前缀避免冲突
        return SessionSettings(**json.loads(raw)) if raw else SessionSettings()

    def save_session(self, session_id: str, settings: SessionSettings) -> None:
        self._set(f"session:{session_id}", json.dumps(settings.__dict__, ensure_ascii=False))

    def get_llm_settings(self) -> LLMSettings:
        raw = self._get("llm")
        return LLMSettings(**json.loads(raw)) if raw else LLMSettings()

    def save_llm_settings(self, settings: LLMSettings) -> None:
        self._set("llm", json.dumps(settings.__dict__, ensure_ascii=False))
