"""依赖组装（Dependency Injection，DI）：集中创建应用需要的所有依赖对象。

设计角度：为什么需要这一层？
- 业务模块不自己 new 存储对象，而是"被注入"——换实现（内存→SQLite→PostgreSQL）
  时业务代码一行都不用改
- 所有依赖的创建逻辑集中在一个文件，一眼能看到整个应用用了哪些组件
当前状态：设置用 SQLite 持久化；会话/日志暂用内存适配器，后续阶段替换。
"""

from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.storage.fastembed_store import FastEmbedEmbedder
from app.storage.memory import InMemoryLogStore, InMemorySessionStore, InMemorySettingStore
from app.storage.ports import Embedder, LogStore, SessionStore, SettingStore, VectorStore
from app.storage.qdrant_store import QdrantVectorStore
from app.storage.sqlite.settings_store import SqliteSettingStore


@dataclass
class AppDeps:
    """应用依赖的"容器"：把各个 Store 打包在一起，方便整体传递。

    类型注解用接口（Port）而不是具体实现，这就是"面向接口编程"：
    调用方只依赖接口定义的方法，不关心底层是 SQLite 还是内存。
    """

    session_store: SessionStore
    settings_store: SettingStore
    log_store: LogStore
    vector_store: VectorStore | None = None
    embedder: Embedder | None = None


def build_deps() -> AppDeps:
    """创建默认依赖组合（开发/生产用：设置持久化到 SQLite）。"""
    base = Path(__file__).resolve().parent.parent
    db_path = settings.db_path
    if not db_path:
        # 用 __file__ 定位，保证无论从哪个目录启动，路径都指向 backend/data/app.db
        db_path = str(base / "data" / "app.db")
    qdrant_path = settings.qdrant_path or str(base / "data" / "qdrant")
    embed_cache = settings.embedding_cache_dir or str(base / "data" / "models")
    return AppDeps(
        session_store=InMemorySessionStore(),  # 会话暂存内存（阶段 4 换 SQLite 支持断点）
        settings_store=SqliteSettingStore(db_path),  # 设置持久化（含 LLM Key）
        log_store=InMemoryLogStore(),  # 日志暂存内存
        vector_store=QdrantVectorStore(path=qdrant_path, dim=settings.embedding_dim),
        embedder=FastEmbedEmbedder(model_name=settings.embedding_model, cache_dir=embed_cache),
    )
