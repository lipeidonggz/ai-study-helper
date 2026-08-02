"""存储接口（Port）：业务层只依赖这里的抽象，具体实现见 memory / sqlite / vector。

原则：按业务聚合划分接口，不按表划分；目标是“换实现不换接口”。
企业级演进：SQLite → PostgreSQL、Qdrant 本地 → Qdrant 服务，只需替换适配器。
"""

from abc import ABC, abstractmethod

from app.models import (
    Document,
    GlobalSettings,
    LLMSettings,
    Message,
    Session,
    SessionSettings,
    ToolDefinition,
)


class SessionStore(ABC):
    @abstractmethod
    def create_session(self, mode: str) -> Session: ...

    @abstractmethod
    def get_session(self, session_id: str) -> Session | None: ...

    @abstractmethod
    def append_message(self, session_id: str, message: Message) -> None: ...

    @abstractmethod
    def list_messages(self, session_id: str) -> list[Message]: ...


class SettingStore(ABC):
    @abstractmethod
    def get_global(self) -> GlobalSettings: ...

    @abstractmethod
    def save_global(self, settings: GlobalSettings) -> None: ...

    @abstractmethod
    def get_session(self, session_id: str) -> SessionSettings: ...

    @abstractmethod
    def save_session(self, session_id: str, settings: SessionSettings) -> None: ...

    @abstractmethod
    def get_llm_settings(self) -> LLMSettings: ...

    @abstractmethod
    def save_llm_settings(self, settings: LLMSettings) -> None: ...


class DocumentStore(ABC):
    @abstractmethod
    def create(self, document: Document) -> Document: ...

    @abstractmethod
    def get(self, document_id: str) -> Document | None: ...

    @abstractmethod
    def list_by_kb(self, kb_id: str) -> list[Document]: ...

    @abstractmethod
    def delete(self, document_id: str) -> None: ...


class ToolStore(ABC):
    @abstractmethod
    def create(self, definition: ToolDefinition) -> ToolDefinition: ...

    @abstractmethod
    def list(self) -> list[ToolDefinition]: ...

    @abstractmethod
    def get(self, name: str) -> ToolDefinition | None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...


class LogStore(ABC):
    @abstractmethod
    def append(self, entry_type: str, payload: dict) -> None: ...

    @abstractmethod
    def query(self, entry_type: str | None = None, limit: int = 100) -> list[dict]: ...


class VectorStore(ABC):
    """知识库向量检索（阶段 2 落地）。"""

    @abstractmethod
    def upsert(self, kb_id: str, points: list[dict]) -> None: ...

    @abstractmethod
    def search(
        self, kb_id: str, vector: list[float], top_k: int, filters: dict | None = None
    ) -> list[dict]: ...

    @abstractmethod
    def delete_by_document(self, kb_id: str, document_id: str) -> None: ...


class Embedder(ABC):
    """文本向量化（阶段 2 落地，v1 建议本地 BGE）。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]: ...
