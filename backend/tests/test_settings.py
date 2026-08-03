"""设置接口测试：验证 LLM 配置的存取、脱敏、校验与 SQLite 持久化。"""

import pytest
from fastapi.testclient import TestClient

from app.di import AppDeps
from app.main import app
from app.models import LLMSettings
from app.storage.memory import InMemoryLogStore, InMemorySessionStore, InMemorySettingStore
from app.storage.sqlite.settings_store import SqliteSettingStore


@pytest.fixture
def client():
    """与冒烟测试相同的隔离策略：内存存储 + 关闭清理。"""
    app.state.deps = AppDeps(
        session_store=InMemorySessionStore(),
        settings_store=InMemorySettingStore(),
        log_store=InMemoryLogStore(),
    )
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def test_get_default(client):
    """未配置时：provider 应为 deepseek、无 Key、模型列表完整。"""
    resp = client.get("/api/settings/llm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-chat"
    assert data["has_key"] is False
    assert data["api_key_masked"] == ""
    assert data["models"] == ["deepseek-chat", "deepseek-reasoner"]


def test_put_key_masked(client):
    """保存 Key 后：返回脱敏结果，且 GET 读到的也是脱敏结果（Key 绝不明文）。"""
    resp = client.put(
        "/api/settings/llm",
        json={"provider": "deepseek", "model": "deepseek-chat", "api_key": "sk-test12345678"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_key"] is True
    assert data["api_key_masked"] == "sk-****5678"
    assert client.get("/api/settings/llm").json()["api_key_masked"] == "sk-****5678"


def test_put_empty_key_keeps_existing(client):
    """api_key 留空时：模型可以更新，但原来的 Key 保留。"""
    client.put("/api/settings/llm", json={"model": "deepseek-chat", "api_key": "sk-abc123456789"})
    resp = client.put("/api/settings/llm", json={"model": "deepseek-reasoner", "api_key": ""})
    data = resp.json()
    assert data["model"] == "deepseek-reasoner"
    assert data["has_key"] is True
    assert data["api_key_masked"] == "sk-****6789"


def test_put_invalid_model(client):
    """不在白名单里的模型应被拒绝（422）。"""
    resp = client.put("/api/settings/llm", json={"model": "gpt-4", "api_key": "sk-x"})
    assert resp.status_code == 422


def test_put_invalid_provider(client):
    """非 deepseek 的 provider 应被拒绝（422）。"""
    resp = client.put(
        "/api/settings/llm",
        json={"provider": "openai", "model": "deepseek-chat", "api_key": "sk-x"},
    )
    assert resp.status_code == 422


def test_put_invalid_key_format(client):
    """不以 sk- 开头的 Key 应被拒绝（422）。"""
    resp = client.put("/api/settings/llm", json={"model": "deepseek-chat", "api_key": "bad-key"})
    assert resp.status_code == 422


def test_sqlite_persistence(tmp_path):
    """SQLite 持久化：写入后重新打开数据库，数据仍在（模拟重启）。"""
    db = tmp_path / "settings.db"
    SqliteSettingStore(db).save_llm_settings(
        LLMSettings(provider="deepseek", model="deepseek-chat", api_key="sk-keep123456")
    )
    again = SqliteSettingStore(db)  # 重新打开，模拟重启
    assert again.get_llm_settings().api_key == "sk-keep123456"
