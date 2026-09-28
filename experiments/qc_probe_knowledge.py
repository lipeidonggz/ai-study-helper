"""探测模型是否已内化 harness 术语史（general 模式、不注入知识库）。"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")


def chat(message: str) -> str:
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/chat",
        data=json.dumps(
            {"message": message, "mode": "general", "session_id": "probe-harness"}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    text = ""
    events: list[str] = []
    with urllib.request.urlopen(req, timeout=180) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data: "):
                continue
            payload = json.loads(line[6:])
            events.append(payload.get("type", "?"))
            if payload.get("type") == "delta":
                text += payload.get("text", "")
            elif payload.get("type") == "event":
                ev = (payload.get("data") or {}).get("event") or {}
                if ev.get("type") == "text":
                    text += ev.get("text", "")
            elif payload.get("type") == "error":
                text += f"\n[ERROR] {payload.get('message','')}"
    print(f"[events: {events}]")
    return text
    return text


def main() -> None:
    q1 = (
        "harness engineering 这个术语在业界是什么时候、被谁确立为统一概念的？"
        "OpenAI《Harness engineering》（2026-02）和 Anthropic《How we contain Claude》"
        "（2026-05）在概念史上是什么关系？A5 那篇文章里用了 harness 这个词吗？"
    )
    print("===== Q1 =====")
    print(chat(q1))


if __name__ == "__main__":
    main()
