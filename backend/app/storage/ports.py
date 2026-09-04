"""存储接口（Port）：定义"存储层必须提供什么能力"，不关心怎么实现。

设计角度：为什么单独定义接口？
- 这是 0005 架构里"接口先行、实现可换"的核心：业务只依赖这里的抽象
- 企业级演进：SQLite → PostgreSQL、Qdrant 本地 → Qdrant 服务，只需替换适配器

命名约定：接口名以 Store 结尾，方法名描述"做什么"而非"怎么做"。
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
    """会话存储：会话的创建、查询、消息读写（多轮对话的基础）。"""

    @abstractmethod
    def create_session(self, mode: str) -> Session: ...

    @abstractmethod
    def get_session(self, session_id: str) -> Session | None: ...

    @abstractmethod
    def append_message(self, session_id: str, message: Message) -> None: ...

    @abstractmethod
    def list_messages(self, session_id: str) -> list[Message]: ...


class SettingStore(ABC):
    """设置存储：全局/会话级范围控制配置 + LLM 配置。"""

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
    """文档存储：知识库文档元数据（阶段 2 落地）。"""

    @abstractmethod
    def create(self, document: Document) -> Document: ...

    @abstractmethod
    def get(self, document_id: str) -> Document | None: ...

    @abstractmethod
    def list_by_kb(self, kb_id: str) -> list[Document]: ...

    @abstractmethod
    def delete(self, document_id: str) -> None: ...


class ToolStore(ABC):
    """工具定义存储：L2 表单注册的工具（阶段 4 落地）。"""

    @abstractmethod
    def create(self, definition: ToolDefinition) -> ToolDefinition: ...

    @abstractmethod
    def list(self) -> list[ToolDefinition]: ...

    @abstractmethod
    def get(self, name: str) -> ToolDefinition | None: ...

    @abstractmethod
    def delete(self, name: str) -> None: ...


class LogStore(ABC):
    """日志存储：记录对话、工具调用、错误（可观测性的基础）。"""

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
    def list_by_document(self, kb_id: str, document_id: str, limit: int = 1000) -> list[dict]: ...

    @abstractmethod
    def list_all(self, kb_id: str, limit: int = 100000) -> list[dict]: ...

    @abstractmethod
    def delete_by_document(self, kb_id: str, document_id: str) -> None: ...


class Embedder(ABC):
    """文本向量化：索引侧用文档前缀，检索侧用 query 前缀（e5 系模型依赖前缀区分）。"""

    @abstractmethod
    def embed(self, texts: list[str], *, is_query: bool = False) -> list[list[float]]: ...

    @abstractmethod
    def token_count(self, texts: list[str]) -> list[int]: ...


class Reranker(ABC):
    """query-document 重排打分（分数质量层，弥补稠密排序的平带/词匹配短板）。"""

    @abstractmethod
    def rerank(self, query: str, documents: list[str]) -> list[float]: ...
