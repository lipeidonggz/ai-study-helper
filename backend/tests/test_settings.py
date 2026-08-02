import pytest
from fastapi.testclient import TestClient

from app.di import AppDeps
from app.main import app
from app.models import LLMSettings
from app.storage.memory import InMemoryLogStore, InMemorySessionStore, InMemorySettingStore
from app.storage.sqlite.settings_store import SqliteSettingStore


@pytest.fixture
def client():
    app.state.deps = AppDeps(
        session_store=InMemorySessionStore(),
        settings_store=InMemorySettingStore(),
        log_store=InMemoryLogStore(),
    )
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def test_get_default(client):
    resp = client.get("/api/settings/llm")
    assert resp.status_code == 200
    data = resp.json()
    assert data["provider"] == "deepseek"
    assert data["model"] == "deepseek-chat"
    assert data["has_key"] is False
    assert data["api_key_masked"] == ""
    assert data["models"] == ["deepseek-chat", "deepseek-reasoner"]


def test_put_key_masked(client):
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
    client.put("/api/settings/llm", json={"model": "deepseek-chat", "api_key": "sk-abc123456789"})
    resp = client.put("/api/settings/llm", json={"model": "deepseek-reasoner", "api_key": ""})
    data = resp.json()
    assert data["model"] == "deepseek-reasoner"
    assert data["has_key"] is True
    assert data["api_key_masked"] == "sk-****6789"


def test_put_invalid_model(client):
    resp = client.put("/api/settings/llm", json={"model": "gpt-4", "api_key": "sk-x"})
    assert resp.status_code == 422


def test_put_invalid_provider(client):
    resp = client.put(
        "/api/settings/llm",
        json={"provider": "openai", "model": "deepseek-chat", "api_key": "sk-x"},
    )
    assert resp.status_code == 422


def test_put_invalid_key_format(client):
    resp = client.put("/api/settings/llm", json={"model": "deepseek-chat", "api_key": "bad-key"})
    assert resp.status_code == 422


def test_sqlite_persistence(tmp_path):
    db = tmp_path / "settings.db"
    SqliteSettingStore(db).save_llm_settings(
        LLMSettings(provider="deepseek", model="deepseek-chat", api_key="sk-keep123456")
    )
    again = SqliteSettingStore(db)  # 重新打开，模拟重启
    assert again.get_llm_settings().api_key == "sk-keep123456"
