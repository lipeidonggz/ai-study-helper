"""为 cmp 家族用例（O2×A5 ×6 条、A1×O1 ×1 条）建一个跨源对照库。

用法（仓库根目录）：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_cmp_kb.py create
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_cmp_kb.py upload
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_cmp_kb.py wait
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_cmp_kb.py dump

口径：
  · 四篇源文（A5／O2 供 6 条 O2×A5 用例，A1／O1 供 cmp-005）放进**同一个库**，
    目的是让 wiki 层有机会做跨源连接；副作用是 O2×A5 用例可能被 A1／O1 干扰，
    判读时按答案的引用归属核对（引用落在哪一篇）。
  · wiki 开着、graph 关着（本轮只比 wiki／smart／hybrid 三臂）；
  · 库 id 落 data/tmp/weknora-ourcases/cmp-kb.json，供 weknora_ourcases_eval.py 读。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import weknora_qa_eval as W  # noqa: E402
import weknora_round1 as R  # noqa: E402  (multipart 上传助手)

OUT_DIR = ROOT / "data" / "tmp" / "weknora-ourcases"
KB_NAME = "cmp · 跨源对照（O2×A5×A1×O1）"
EMBEDDING_MODEL_ID = "e9e03c3f-4afc-4084-a392-4751e9b83bfe"   # qwen3.7-text-embedding
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"        # deepseek-flash

SRC = ROOT / "data" / "kb-src" / "articles"
# 注意：上传时 fileName 必须**带扩展名**——服务端按这个名字判文件类型（handler/knowledge.go
# 用 customFileName 覆盖原始名），不带扩展名会直接 500 unsupported file type。
DOCS: list[tuple[str, Path]] = [
    ("A5 · How we contain Claude across products.html", SRC / "anthropic" / "05-how-we-contain-claude.html"),
    ("O2 · Harness Engineering.html", SRC / "openai" / "02-harness-engineering.html"),
    ("A1 · Building Effective Agents.html", SRC / "anthropic" / "01-building-effective-agents.html"),
    ("O1 · A Practical Guide to Building Agents.pdf", SRC / "openai" / "01-a-practical-guide-to-building-agents.pdf"),
]


def write(name: str, obj) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def find_kb() -> dict | None:
    _st, d = W.http_json("GET", "/knowledge-bases")
    for kb in (d.get("data") or []):
        if kb.get("name") == KB_NAME:
            return kb
    return None


def cmd_create() -> None:
    kb = find_kb()
    if kb:
        print("已存在，复用：", kb["id"])
    else:
        payload = {
            "name": KB_NAME,
            "description": "cmp 家族跨源对照：A5／O2（O2×A5 六条）＋ A1／O1（cmp-005）",
            "type": "wiki",
            "embedding_model_id": EMBEDDING_MODEL_ID,
            "summary_model_id": CHAT_MODEL_ID,
            "indexing_strategy": {
                "vector_enabled": True,
                "keyword_enabled": True,
                "wiki_enabled": True,
                "graph_enabled": False,
            },
            "wiki_config": {"synthesis_model_id": CHAT_MODEL_ID},
        }
        st, data = W.http_json("POST", "/knowledge-bases", payload)
        print("HTTP", st, str(data)[:200])
        write("cmp-kb-create.json", {"status": st, "response": data})
        kb = find_kb()
    assert kb, "建库失败"
    write("cmp-kb.json", {"kb": kb})
    print("kb_id =", kb["id"], "| wiki =", (kb.get("capabilities") or {}).get("wiki"))


def cmd_upload() -> None:
    kb = find_kb()
    if not kb:
        raise SystemExit("先跑 create")
    kb_id = kb["id"]
    _st, d = W.http_json("GET", f"/knowledge-bases/{kb_id}/knowledge")
    have = {(r.get("title") or "").strip() for r in (d.get("data") or [])}
    for title, path in DOCS:
        if title in have:
            print(f"[skip] {title}")
            continue
        print(f"[upload] {title} <- {path.name} ({path.stat().st_size} bytes)", flush=True)
        st, data = R.post_file(f"/knowledge-bases/{kb_id}/knowledge/file", path, {"fileName": title})
        print(f"   HTTP {st} {str(data)[:160]}", flush=True)
    write("cmp-kb-upload.json", {"docs": [t for t, _ in DOCS]})


def cmd_wait() -> None:
    kb = find_kb()
    kb_id = kb["id"]
    deadline = time.time() + 120 * 60
    last = None
    stable = 0
    while time.time() < deadline:
        _st, d = W.http_json("GET", f"/knowledge-bases/{kb_id}/knowledge")
        rows = d.get("data") or []
        snap = [(r.get("title"), r.get("parse_status")) for r in rows]
        _st2, stats = W.http_json("GET", f"/knowledgebase/{kb_id}/wiki/stats")
        pages = (stats or {}).get("total_pages") if isinstance(stats, dict) else None
        if snap != last:
            print(time.strftime("%H:%M:%S"), pages, snap, flush=True)
            last = snap
        done = rows and all((r.get("parse_status") or "") in ("completed", "success", "failed") for r in rows)
        if done and pages:
            stable = stable + 1 if pages == getattr(cmd_wait, "_last_pages", None) else 0
            cmd_wait._last_pages = pages
            if stable >= 3:
                print("稳定：", pages, "页", flush=True)
                write("cmp-kb-wait.json", {"pages": pages, "snapshot": snap})
                return
        time.sleep(30)
    print("⚠️ 等超时，最后快照：", last)
    write("cmp-kb-wait.json", {"timeout": True, "snapshot": last})


def cmd_dump() -> None:
    kb = find_kb()
    kb_id = kb["id"]
    for name, path in {
        "cmp-wiki-stats.json": "/knowledgebase/%s/wiki/stats" % kb_id,
        "cmp-wiki-graph.json": "/knowledgebase/%s/wiki/graph?mode=overview&limit=2000" % kb_id,
        "cmp-wiki-pages.json": "/knowledgebase/%s/wiki/pages?page=1&page_size=200" % kb_id,
    }.items():
        _st, data = W.http_json("GET", path)
        (OUT_DIR / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", name)


def main() -> None:
    {"create": cmd_create, "upload": cmd_upload, "wait": cmd_wait, "dump": cmd_dump}[sys.argv[1]]()


if __name__ == "__main__":
    main()
