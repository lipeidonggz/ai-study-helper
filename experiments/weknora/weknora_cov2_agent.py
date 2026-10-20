"""建 v2 契约的 wiki agent：预设原文 ＋ 「列举里带出未展开项」契约（不含清单表槽）。

用法：
    python experiments/weknora/weknora_cov2_agent.py create|show|delete
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import weknora_qa_eval as W  # noqa: E402

OUT = ROOT / "data" / "tmp" / "weknora-ourcases"
CONTRACT = OUT / "coverage-contract-v2.txt"
BUILTIN = ROOT / "data" / "tmp" / "weknora-musique" / "_builtin-agents.json"
NAME = "exp-ourcases-covB2-wiki"
TPL = "wiki_researcher"
BUILTIN_KEY = "builtin-wiki-researcher"


def cmd_create() -> None:
    contract = CONTRACT.read_text(encoding="utf-8").strip()
    tpl = (OUT / f"prompt-{TPL}.txt").read_text(encoding="utf-8").strip()
    builtin = json.loads(BUILTIN.read_text(encoding="utf-8"))
    cfg = dict((builtin[BUILTIN_KEY].get("config") or {}))
    cfg["model_id"] = W.CHAT_MODEL_ID
    cfg["rerank_model_id"] = W.RERANK_MODEL_ID
    cfg["use_custom_system_prompt"] = True
    cfg["system_prompt"] = tpl + "\n\n" + contract
    _st, data = W.http_json("GET", "/agents?page=1&page_size=200")
    if any(a.get("name") == NAME for a in (data.get("data") or [])):
        print("[skip] 已存在", NAME)
        return
    st, resp = W.http_json("POST", "/agents", {
        "name": NAME,
        "description": "cmp-002 覆盖披露 v2（列举里带出未展开项）：预设原文 + 契约，无清单表槽",
        "avatar": "",
        "config": cfg,
    })
    print(f"[{st}] {NAME} {str(resp)[:160]}")
    print("system_prompt 长度 =", len(cfg["system_prompt"]),
          "| tools =", len(cfg.get("allowed_tools") or []), "| iters =", cfg.get("max_iterations"))


def cmd_show() -> None:
    _st, data = W.http_json("GET", "/agents?page=1&page_size=200")
    for a in (data.get("data") or []):
        if a.get("name") == NAME:
            cfg = a.get("config") or {}
            print(NAME, "| system_prompt 长度 =", len(cfg.get("system_prompt") or ""),
                  "| tools =", len(cfg.get("allowed_tools") or []),
                  "| iters =", cfg.get("max_iterations"))
            return
    print("未找到", NAME)


def cmd_delete() -> None:
    _st, data = W.http_json("GET", "/agents?page=1&page_size=200")
    for a in (data.get("data") or []):
        if a.get("name") == NAME:
            st, _ = W.http_json("DELETE", f"/agents/{a['id']}")
            print(f"[{st}] deleted {NAME}")


def main() -> None:
    {"create": cmd_create, "show": cmd_show, "delete": cmd_delete}[sys.argv[1]]()


if __name__ == "__main__":
    main()
