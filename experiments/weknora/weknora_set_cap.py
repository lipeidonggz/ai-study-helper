"""改 agent 的 max_completion_tokens（每轮输出预算），用于"输出上限"对照实验。

用法：
    python experiments/weknora/weknora_set_cap.py <agent 名> <tokens>
    python experiments/weknora/weknora_set_cap.py exp-ourcases-covB2-wiki 8192
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import weknora_qa_eval as W  # noqa: E402


def find(name: str) -> dict | None:
    _st, data = W.http_json("GET", "/agents?page=1&page_size=200")
    for a in (data.get("data") or []):
        if a.get("name") == name:
            return a
    return None


def main() -> None:
    name, cap = sys.argv[1], int(sys.argv[2])
    agent = find(name)
    if not agent:
        raise SystemExit(f"找不到 agent: {name}")
    cfg = dict(agent.get("config") or {})
    old = cfg.get("max_completion_tokens")
    cfg["max_completion_tokens"] = cap
    payload = {
        "name": agent.get("name"),
        "description": agent.get("description") or "",
        "avatar": agent.get("avatar") or "",
        "config": cfg,
    }
    st, resp = W.http_json("PUT", f"/agents/{agent['id']}", payload)
    print(f"[{st}] {name}: max_completion_tokens {old} → {cap}")
    again = find(name) or {}
    print("  复查:", (again.get("config") or {}).get("max_completion_tokens"))


if __name__ == "__main__":
    main()
