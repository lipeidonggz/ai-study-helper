"""依赖组装（简单工厂）。

结构化设置使用 SQLite 持久化；会话/日志暂用内存适配器，后续阶段替换。
"""

from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.storage.memory import InMemoryLogStore, InMemorySessionStore, InMemorySettingStore
from app.storage.ports import LogStore, SessionStore, SettingStore
from app.storage.sqlite.settings_store import SqliteSettingStore


@dataclass
class AppDeps:
    session_store: SessionStore
    settings_store: SettingStore
    log_store: LogStore


def build_deps() -> AppDeps:
    db_path = settings.db_path
    if not db_path:
        db_path = str(Path(__file__).resolve().parent.parent / "data" / "app.db")
    return AppDeps(
        session_store=InMemorySessionStore(),
        settings_store=SqliteSettingStore(db_path),
        log_store=InMemoryLogStore(),
    )
