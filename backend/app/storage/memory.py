"""内存适配器：把数据放在进程内存里（字典），不落盘。

设计角度：为什么需要内存版？
- 骨架阶段跑通链路、写测试时不需要真实数据库，用内存版最轻
- 和 SQLite 版实现同一个接口（Port），业务代码无感知
注意：进程重启数据就没了；多 worker 部署时各进程内存互相独立。
"""

import uuid  # 生成唯一 ID
from datetime import datetime

from app.models import GlobalSettings, LLMSettings, Message, Session, SessionSettings
from app.storage.ports import LogStore, SessionStore, SettingStore


class InMemorySessionStore(SessionStore):
    """会话的内存实现：字典，key = 会话 id。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create_session(self, mode: str) -> Session:
        session = Session(id=uuid.uuid4().hex, mode=mode)  # 随机生成 32 位十六进制 id
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
        return list(session.messages) if session else []  # 返回副本，避免外部改坏内部


class InMemorySettingStore(SettingStore):
    """设置的内存实现。"""

    def __init__(self) -> None:
        self._global = GlobalSettings()
        self._sessions: dict[str, SessionSettings] = {}
        self._llm = LLMSettings()

    def get_global(self) -> GlobalSettings:
        return self._global

    def save_global(self, settings: GlobalSettings) -> None:
        self._global = settings

    def get_session(self, session_id: str) -> SessionSettings:
        return self._sessions.setdefault(session_id, SessionSettings())  # 没有则建默认

    def save_session(self, session_id: str, settings: SessionSettings) -> None:
        self._sessions[session_id] = settings

    def get_llm_settings(self) -> LLMSettings:
        return self._llm

    def save_llm_settings(self, settings: LLMSettings) -> None:
        self._llm = settings


class InMemoryLogStore(LogStore):
    """日志的内存实现：一个简单的列表。"""

    def __init__(self) -> None:
        self._entries: list[dict] = []

    def append(self, entry_type: str, payload: dict) -> None:
        self._entries.append(
            {"type": entry_type, "payload": payload, "ts": datetime.now().isoformat()}
        )

    def query(self, entry_type: str | None = None, limit: int = 100) -> list[dict]:
        entries = [e for e in self._entries if entry_type is None or e["type"] == entry_type]
        return entries[-limit:]  # 只取最近 limit 条
