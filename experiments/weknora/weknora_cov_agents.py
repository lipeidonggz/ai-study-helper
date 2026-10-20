"""建 B 臂 agent：预设系统提示词原文 ＋ 覆盖透明契约（单变量：只加这一条）。

依据（源码）：internal/agent/prompts.go:455-457 —— "Custom templates replace only the base
section; tool scope and runtime contracts always come from the active engine"，所以把预设
模板原文复制过来再追加一节，除多出这一条契约外，与 A 臂完全一致（工具白名单、轮次预算、
温度、rerank 都保持不变）。

用法（仓库根目录）：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_cov_agents.py create
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_cov_agents.py show
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_cov_agents.py delete
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import weknora_qa_eval as W  # noqa: E402

OUT = ROOT / "data" / "tmp" / "weknora-ourcases"
CONTRACT = OUT / "coverage-contract.txt"
BUILTIN = ROOT / "data" / "tmp" / "weknora-musique" / "_builtin-agents.json"

# B 臂：arm -> (agent 名, A 臂的内置模板键, 预设模板 id)
VARIANTS = {
    "wiki": ("exp-ourcases-covB-wiki", "builtin-wiki-researcher", "wiki_researcher"),
    "smart": ("exp-ourcases-covB-smart", "builtin-smart-reasoning", "progressive_rag_agent"),
    "hybrid": ("exp-ourcases-covB-hybrid", "preset:hybrid-rag-wiki", "hybrid_rag_wiki_agent"),
}


def prompt_text(template_id: str) -> str:
    p = OUT / f"prompt-{template_id}.txt"
    if not p.exists():
        raise SystemExit(f"缺模板原文 {p}，先跑 data/tmp/_kn_templates.py")
    return p.read_text(encoding="utf-8").strip()


def cmd_create() -> None:
    contract = CONTRACT.read_text(encoding="utf-8").strip()
    builtin = json.loads(BUILTIN.read_text(encoding="utf-8"))
    _st, existing = W.http_json("GET", "/agents?page=1&page_size=200")
    have = {a.get("name") for a in (existing.get("data") or [])}
    made = {}
    for arm, (name, builtin_key, tpl_id) in VARIANTS.items():
        if name in have:
            print(f"[skip] {arm}: 已存在 {name}")
            made[arm] = {"name": name, "reused": True}
            continue
        cfg = dict(W.HYBRID_PRESET) if builtin_key.startswith("preset:") else dict(
            (builtin[builtin_key].get("config") or {})
        )
        cfg["model_id"] = W.CHAT_MODEL_ID
        cfg["rerank_model_id"] = W.RERANK_MODEL_ID
        cfg["use_custom_system_prompt"] = True
        cfg["system_prompt"] = prompt_text(tpl_id) + "\n\n" + contract
        payload = {
            "name": name,
            "description": f"cmp-002 覆盖透明 A/B 的 B 臂（{arm}）：预设原文 + 覆盖透明契约",
            "avatar": "",
            "config": cfg,
        }
        st, data = W.http_json("POST", "/agents", payload)
        print(f"[{st}] {arm} -> {name} {str(data)[:140]}")
        made[arm] = {"name": name, "status": st}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "covB-agents.json").write_text(json.dumps(made, ensure_ascii=False, indent=2),
                                          encoding="utf-8")


def cmd_show() -> None:
    for arm, (name, _b, tpl_id) in VARIANTS.items():
        cfg = None
        _st, data = W.http_json("GET", "/agents?page=1&page_size=200")
        for a in (data.get("data") or []):
            if a.get("name") == name:
                cfg = a.get("config") or {}
        n = len((cfg or {}).get("system_prompt") or "")
        print(f"{arm:6s} {name:26s} custom={ (cfg or {}).get('use_custom_system_prompt') } "
              f"prompt_chars={n} tools={len((cfg or {}).get('allowed_tools') or [])} "
              f"iters={(cfg or {}).get('max_iterations')} | 模板 {tpl_id}={len(prompt_text(tpl_id))}")


def cmd_delete() -> None:
    for arm, (name, _b, _t) in VARIANTS.items():
        _st, data = W.http_json("GET", "/agents?page=1&page_size=200")
        for a in (data.get("data") or []):
            if a.get("name") == name:
                st, _ = W.http_json("DELETE", f"/agents/{a['id']}")
                print(f"[{st}] deleted {name}")


def main() -> None:
    {"create": cmd_create, "show": cmd_show, "delete": cmd_delete}[sys.argv[1]]()


if __name__ == "__main__":
    main()
