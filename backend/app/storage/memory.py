"""内存适配器：骨架阶段用于启动与测试，后续由 SQLite / 向量库适配器替换。"""

import uuid
from datetime import datetime

from app.models import GlobalSettings, Message, Session, SessionSettings
from app.storage.ports import LogStore, SessionStore, SettingStore


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create_session(self, mode: str) -> Session:
        session = Session(id=uuid.uuid4().hex, mode=mode)
        self._sessions[session.id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def append_message(self, session_id: str, message: Message) -> None:
        session = self._sessions.get(session_id)
        if session:
            session.messages.append(message)

    def list_messages(self, session_id: str) -> list[Message]:
        session = self._sessions.get(session_id)
        return list(session.messages) if session else []


class InMemorySettingStore(SettingStore):
    def __init__(self) -> None:
        self._global = GlobalSettings()
        self._sessions: dict[str, SessionSettings] = {}

    def get_global(self) -> GlobalSettings:
        return self._global

    def save_global(self, settings: GlobalSettings) -> None:
        self._global = settings

    def get_session(self, session_id: str) -> SessionSettings:
        return self._sessions.setdefault(session_id, SessionSettings())

    def save_session(self, session_id: str, settings: SessionSettings) -> None:
        self._sessions[session_id] = settings


class InMemoryLogStore(LogStore):
    def __init__(self) -> None:
        self._entries: list[dict] = []

    def append(self, entry_type: str, payload: dict) -> None:
        self._entries.append(
            {"type": entry_type, "payload": payload, "ts": datetime.now().isoformat()}
        )

    def query(self, entry_type: str | None = None, limit: int = 100) -> list[dict]:
        entries = [e for e in self._entries if entry_type is None or e["type"] == entry_type]
        return entries[-limit:]
