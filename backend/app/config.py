"""应用配置：集中管理所有可调参数。

设计角度：为什么单独一个文件？
- 一处修改、全局生效，而不是把参数散落在各个模块里
- 支持用环境变量覆盖（前缀 ASH_），方便以后部署，也方便测试隔离
这里使用 pydantic-settings 读取配置。
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """配置模型：字段的默认值就是"未额外配置时"使用的值。"""

    app_name: str = "ai-study-helper"
    # LLM 提供方：fake=占位实现（不联网）；deepseek=真实调用（前端配置 Key 后生效）
    llm_provider: str = "fake"
    # 默认会话模式：general 通用 / kb_priority 知识库优先 / tool_enhanced 工具增强
    default_mode: str = "general"
    # 数据库文件路径；为空时由 di.py 计算默认值 backend/data/app.db
    db_path: str = ""

    # RAG 基础设施：向量库本地路径 / embedding 模型与缓存目录（为空时由 di.py 计算默认值）
    qdrant_path: str = ""
    embedding_model: str = "intfloat/multilingual-e5-large"
    embedding_cache_dir: str = ""
    embedding_dim: int = 1024
    # RAG rerank：本地 cross-encoder（fastembed）；为空则跳过 rerank
    rerank_model: str = "jinaai/jina-reranker-v2-base-multilingual"
    # 知识库入库状态库（SQLite）；为空时由 main.py 计算默认值 backend/data/kb.db
    kb_db_path: str = ""

    # 告诉 pydantic-settings：所有环境变量必须以 ASH_ 开头，
    # 例如 ASH_DB_PATH=xxx 会覆盖 db_path 字段
    model_config = {"env_prefix": "ASH_"}


# 模块级单例：整个进程只加载一次配置，任何地方 import 都是同一份
settings = Settings()
