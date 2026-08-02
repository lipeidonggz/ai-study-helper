import json

import pytest
from fastapi.testclient import TestClient

from app.di import AppDeps
from app.main import app
from app.storage.memory import InMemoryLogStore, InMemorySessionStore, InMemorySettingStore


@pytest.fixture
def client():
    # 测试一律使用内存存储，避免读写真实 SQLite 数据库
    app.state.deps = AppDeps(
        session_store=InMemorySessionStore(),
        settings_store=InMemorySettingStore(),
        log_store=InMemoryLogStore(),
    )
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        event_name = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])
        if event_name and data is not None:
            events.append((event_name, data))
    return events


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_chat_sse_without_key(client):
    with client.stream(
        "POST", "/api/chat", json={"message": "你好", "mode": "general"}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    assert any(name == "error" for name, _ in events)
    assert any("API Key" in data.get("message", "") for _, data in events)
