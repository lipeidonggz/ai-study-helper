"""冒烟测试：验证应用能启动、核心接口能响应。

设计角度：冒烟测试是最轻量的"健康检查"，不测细节，只确认链路没断。
这里用内存存储替换真实 SQLite，保证测试可重复、不污染用户数据。
"""

import json  # 解析 SSE 事件里的 JSON

import pytest
from fastapi.testclient import TestClient  # 模拟 HTTP 客户端（不需要真起服务）

from app.di import AppDeps
from app.main import app
from app.storage.memory import InMemoryLogStore, InMemorySessionStore, InMemorySettingStore


@pytest.fixture
def client():
    """每个测试开始前：换成内存存储；结束后关闭客户端。"""
    app.state.deps = AppDeps(  # 覆盖 main.py 注入的依赖（SQLite → 内存）
        session_store=InMemorySessionStore(),
        settings_store=InMemorySettingStore(),
        log_store=InMemoryLogStore(),
    )
    test_client = TestClient(app)
    yield test_client
    test_client.close()  # 关闭后台线程，避免测试结束挂起


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析成 (事件名, 数据) 列表，模拟前端 client.ts 的解析逻辑。"""
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):  # SSE 事件块之间用空行分隔
        event_name = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event_name = line[len("event: ") :]  # 取事件名
            elif line.startswith("data: "):
                data = json.loads(line[len("data: ") :])  # 取数据并解析 JSON
        if event_name and data is not None:
            events.append((event_name, data))
    return events


def test_health(client):
    """健康检查接口应返回 ok。"""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_chat_sse_without_key(client):
    """未配置 API Key 时，对话接口应返回 error 事件而不是崩溃。"""
    with client.stream(
        "POST", "/api/chat", json={"message": "你好", "mode": "general"}
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())  # 收集完整流文本

    events = _parse_sse(body)
    names = [name for name, _ in events]
    assert names[0] == "start"  # 第一个事件是 start
    assert names[-1] == "done"  # 最后一个事件是 done
    assert any(name == "error" for name, _ in events)  # 中间有 error 事件
    assert any("API Key" in data.get("message", "") for _, data in events)  # 提示要配 Key
