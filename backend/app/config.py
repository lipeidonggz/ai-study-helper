"""应用配置：环境变量前缀 ASH_（避免与系统变量冲突）。"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "ai-study-helper"
    llm_provider: str = "fake"  # fake | deepseek | openai（阶段 1 接入真实客户端时启用）
    default_mode: str = "general"

    model_config = {"env_prefix": "ASH_"}


settings = Settings()
