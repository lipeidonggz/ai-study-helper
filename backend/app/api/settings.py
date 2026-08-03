"""设置 API：大模型配置（provider 当前仅支持 deepseek）。

设计角度：为什么单独一个文件？
- 设置是"范围控制"产品灵魂的一部分：用户在前端管理 Key、模型、知识库/工具开关
- 目前只实现了 LLM 配置；后续全局设置、会话设置都扩展到这个文件

接口：
GET /api/settings/llm  → 返回脱敏配置（Key 绝不明文返回）
PUT /api/settings/llm  → 保存配置（api_key 留空表示保持不变）
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator

from app.agent.llm import DEEPSEEK_MODELS  # 可选的模型列表
from app.models import LLMSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _mask(key: str) -> str:
    """把 API Key 脱敏：只保留前 3 位和后 4 位，中间用星号代替。

    为什么？Key 是敏感信息，绝不能在接口响应里明文传输；
    前端只需要知道"是否已配置、大概长什么样"。
    """
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * 4 + key[-2:]  # 很短的 Key 也至少遮住大部分
    return key[:3] + "****" + key[-4:]


class LLMSettingsIn(BaseModel):
    """前端提交的 LLM 配置（带字段校验）。"""

    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        """当前只允许 deepseek；以后扩展厂商时在这里加白名单。"""
        if v != "deepseek":
            raise ValueError("当前仅支持 deepseek")
        return v

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        """模型必须在白名单里，防止拼错模型名浪费请求。"""
        if v not in DEEPSEEK_MODELS:
            raise ValueError(f"model 必须是 {DEEPSEEK_MODELS}")
        return v

    @field_validator("api_key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        """Key 非空时必须符合 sk- 开头的基本格式（浅校验，真实有效性由调用时发现）。"""
        v = v.strip()  # 去掉首尾空格（复制粘贴常见）
        if v and not v.startswith("sk-"):
            raise ValueError("api_key 格式不正确（应以 sk- 开头）")
        return v


def _view(s: LLMSettings) -> dict:
    """把内部配置转成对外返回的字典（Key 脱敏，附可选模型列表）。"""
    return {
        "provider": s.provider,
        "model": s.model,
        "api_key_masked": _mask(s.api_key),  # 只给脱敏后的 Key
        "has_key": bool(s.api_key),  # 前端据此决定占位文案
        "models": DEEPSEEK_MODELS,  # 前端下拉框选项
    }


@router.get("/llm")
def get_llm(request: Request) -> dict:
    """读取当前 LLM 配置（前端打开设置面板时调用）。"""
    store = request.app.state.deps.settings_store
    return _view(store.get_llm_settings())


@router.put("/llm")
def put_llm(body: LLMSettingsIn, request: Request) -> dict:
    """保存 LLM 配置。

    细节：api_key 留空表示"保留原来的 Key"——这样用户只改模型时不用重新输入 Key。
    """
    store = request.app.state.deps.settings_store
    current = store.get_llm_settings()  # 拿旧配置（为了保留 Key）
    api_key = body.api_key if body.api_key else current.api_key  # 空则沿用旧 Key
    saved = LLMSettings(provider=body.provider, model=body.model, api_key=api_key)
    store.save_llm_settings(saved)  # 持久化
    request.app.state.deps.log_store.append(
        "settings", {"action": "update_llm", "provider": body.provider, "model": body.model}
    )  # 记录设置变更日志（注意不记录 Key 本身）
    return _view(saved)
