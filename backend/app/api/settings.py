"""设置 API：大模型配置（provider 当前仅支持 deepseek）。

GET /api/settings/llm  → 返回脱敏配置
PUT /api/settings/llm  → 保存配置（api_key 留空表示保持不变）
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator

from app.agent.llm import DEEPSEEK_MODELS
from app.models import LLMSettings

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * 4 + key[-2:]
    return key[:3] + "****" + key[-4:]


class LLMSettingsIn(BaseModel):
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str) -> str:
        if v != "deepseek":
            raise ValueError("当前仅支持 deepseek")
        return v

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if v not in DEEPSEEK_MODELS:
            raise ValueError(f"model 必须是 {DEEPSEEK_MODELS}")
        return v

    @field_validator("api_key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        v = v.strip()
        if v and not v.startswith("sk-"):
            raise ValueError("api_key 格式不正确（应以 sk- 开头）")
        return v


def _view(s: LLMSettings) -> dict:
    return {
        "provider": s.provider,
        "model": s.model,
        "api_key_masked": _mask(s.api_key),
        "has_key": bool(s.api_key),
        "models": DEEPSEEK_MODELS,
    }


@router.get("/llm")
def get_llm(request: Request) -> dict:
    store = request.app.state.deps.settings_store
    return _view(store.get_llm_settings())


@router.put("/llm")
def put_llm(body: LLMSettingsIn, request: Request) -> dict:
    store = request.app.state.deps.settings_store
    current = store.get_llm_settings()
    api_key = body.api_key if body.api_key else current.api_key
    saved = LLMSettings(provider=body.provider, model=body.model, api_key=api_key)
    store.save_llm_settings(saved)
    request.app.state.deps.log_store.append(
        "settings", {"action": "update_llm", "provider": body.provider, "model": body.model}
    )
    return _view(saved)
