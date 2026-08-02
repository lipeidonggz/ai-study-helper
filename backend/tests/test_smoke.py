import json

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    test_client = TestClient(app)
    yield test_client
    test_client.close()


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_chat_sse(client):
    with client.stream(
        "POST", "/api/chat", json={"message": "你好", "mode": "general"}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    # 解析 SSE 块：每个块含 event: <name> 与 data: <json>
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

    names = [name for name, _ in events]
    assert names[0] == "start"
    assert names[-1] == "done"
    text = "".join(d.get("text", "") for name, d in events if name == "delta")
    assert "骨架占位回复" in text
    assert "你好" in text
