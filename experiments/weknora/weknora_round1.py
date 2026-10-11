"""WeKnora 第一轮：看 wiki 编译的产物形状。

用法（在仓库根目录跑）：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_round1.py <子命令>

子命令：
    create   建 wiki 型知识库（幂等：同名库已存在则复用）
    upload   上传 A5 原文到该库
    status   查知识库 / 知识的解析与入库状态
    dump     把 wiki 的 stats / lint / graph / index / pages 落到 data/tmp/weknora-a5/

设计约束：
    * API key 从 D:\\lida-data\\vscode\\WeKnora\\.api-key 读，**不打印明文**；
    * 所有原始响应都落盘留档（对账的前提）；
    * 只走本地 8080，不经代理。
"""

from __future__ import annotations

import json
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-a5"

KB_NAME = "A5 · How we contain Claude across products"
EMBEDDING_MODEL_ID = "e9e03c3f-4afc-4084-a392-4751e9b83bfe"  # qwen3.7-text-embedding
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"  # deepseek-flash
SOURCE_FILE = ROOT / "data" / "kb-src" / "articles" / "anthropic" / "05-how-we-contain-claude.html"


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _request(req: urllib.request.Request) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要看到 body
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def get(path: str) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, headers={"X-API-Key": api_key()})
    return _request(req)


def post_json(path: str, payload: dict) -> tuple[int, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"X-API-Key": api_key(), "Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    return _request(req)


def post_file(path: str, file_path: Path, fields: dict[str, str] | None = None) -> tuple[int, object]:
    boundary = "----weknora" + uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in (fields or {}).items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
            ).encode("utf-8")
        )
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{file_path.name}"\r\nContent-Type: {ctype}\r\n\r\n'
        ).encode("utf-8")
    )
    parts.append(file_path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    req = urllib.request.Request(
        BASE + path,
        data=body,
        headers={"X-API-Key": api_key(), "Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    return _request(req)


def find_kb() -> dict | None:
    return find_kb_by_name(KB_NAME)


def find_kb_by_name(name: str) -> dict | None:
    status, data = get("/knowledge-bases")
    if status != 200 or not isinstance(data, dict):
        return None
    for kb in data.get("data") or []:
        if kb.get("name") == name:
            return kb
    return None


# 可选覆盖：argv[2] = 知识库名，argv[3] = 粒度（focused/standard/exhaustive）
NAME_OVERRIDE = sys.argv[2] if len(sys.argv) > 2 else None
GRAN_OVERRIDE = sys.argv[3] if len(sys.argv) > 3 else None


def pick_name() -> None:
    """把 argv 给的名字写回全局 KB_NAME（各子命令开头调用）。"""
    global KB_NAME
    if NAME_OVERRIDE:
        KB_NAME = NAME_OVERRIDE


def cmd_create() -> None:
    pick_name()
    existing = find_kb()
    if existing:
        print("已存在同名库，复用：", existing.get("id"))
        print(json.dumps(existing, ensure_ascii=False, indent=2)[:1200])
        return
    payload = {
        "name": KB_NAME,
        "description": "LLM-Wiki 验证第一轮：单篇 A5（Anthropic），与 GraphRAG 线同源对照",
        "type": "wiki",
        "embedding_model_id": EMBEDDING_MODEL_ID,
        "summary_model_id": CHAT_MODEL_ID,
        # wiki 的开关在 indexing_strategy 上（IsWikiEnabled 只认它），type=wiki 不会自动打开
        "indexing_strategy": {
            "vector_enabled": True,
            "keyword_enabled": True,
            "wiki_enabled": True,
            "graph_enabled": False,
        },
        # wiki 生成用哪个模型；content/extraction instructions 是"视角"旋钮（第二轮要动的）
        "wiki_config": {
            "synthesis_model_id": CHAT_MODEL_ID,
            **({"extraction_granularity": GRAN_OVERRIDE} if GRAN_OVERRIDE else {}),
        },
    }
    status, data = post_json("/knowledge-bases", payload)
    print("HTTP", status)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "kb-create.json").write_text(
        json.dumps({"status": status, "response": data}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cmd_upload() -> None:
    pick_name()
    kb = find_kb()
    if not kb:
        print("找不到知识库，先跑 create")
        return
    if not SOURCE_FILE.exists():
        print("源文件不存在：", SOURCE_FILE)
        return
    print("上传：", SOURCE_FILE.name, f"({SOURCE_FILE.stat().st_size} bytes)")
    status, data = post_file(
        f"/knowledge-bases/{kb['id']}/knowledge/file",
        SOURCE_FILE,
        {"fileName": "A5 · How we contain Claude across products"},
    )
    print("HTTP", status)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:2000])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "kb-upload.json").write_text(
        json.dumps({"status": status, "response": data}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cmd_status() -> None:
    pick_name()
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    status, data = get(f"/knowledge-bases/{kb['id']}/knowledge")
    print("HTTP", status)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])


def cmd_kb() -> None:
    pick_name()
    """打印知识库的关键开关（wiki 是否真的开着）。"""
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    keys = ["id", "name", "type", "indexing_strategy", "capabilities", "embedding_model_id",
            "summary_model_id", "wiki_config"]
    print(json.dumps({k: kb.get(k) for k in keys}, ensure_ascii=True, indent=2))


def cmd_manual() -> None:
    pick_name()
    """用「手工 Markdown 知识」接口投喂 A5（HTML 不在它接受的文件类型里）。"""
    kb = find_kb()
    if not kb:
        print("找不到知识库，先跑 create")
        return
    md_file = OUT_DIR / "A5.md"
    if not md_file.exists():
        print("先跑 extract_a5.py 生成 A5.md")
        return
    content = md_file.read_text(encoding="utf-8")
    print(f"投喂 Markdown：{len(content)} 字符")
    status, data = post_json(
        f"/knowledge-bases/{kb['id']}/knowledge/manual",
        {"title": "A5 · How we contain Claude across products", "content": content, "status": "publish"},
    )
    print("HTTP", status)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
    (OUT_DIR / "kb-manual.json").write_text(
        json.dumps({"status": status, "response": data}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def cmd_delete() -> None:
    pick_name()
    kb = find_kb()
    if not kb:
        print("找不到知识库，无需删")
        return
    req = urllib.request.Request(
        f"{BASE}/knowledge-bases/{kb['id']}", headers={"X-API-Key": api_key()}, method="DELETE"
    )
    status, data = _request(req)
    print("DELETE HTTP", status, json.dumps(data, ensure_ascii=False)[:300] if isinstance(data, (dict, list)) else data)


def cmd_wait() -> None:
    pick_name()
    """轮询 wiki 编译进度：等 stats 里出现页面，或解析结束。最多等 20 分钟。"""
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    kb_id = kb["id"]
    deadline = time.time() + 20 * 60
    last = None
    while time.time() < deadline:
        st, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
        kt, know = get(f"/knowledge-bases/{kb_id}/knowledge")
        pages = stats.get("total_pages") if isinstance(stats, dict) else None
        if pages is None and isinstance(stats, dict):
            pages = stats.get("total")
        parse = ""
        if isinstance(know, dict):
            rows = know.get("data") or []
            if rows:
                parse = f"{rows[0].get('parse_status')}/sub={rows[0].get('pending_subtasks_count')}"
        snapshot = (pages, parse)
        if snapshot != last:
            print(f"[{time.strftime('%H:%M:%S')}] wiki_stats={json.dumps(stats, ensure_ascii=True)[:200]}  parse={parse}", flush=True)
            last = snapshot
        if isinstance(pages, int) and pages > 0:
            print("[done] 已出现页面", flush=True)
            return
        time.sleep(15)
    print("[timeout] 20 分钟仍未出现页面", flush=True)


def cmd_dump() -> None:
    pick_name()
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    kb_id = kb["id"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    endpoints = {
        "wiki-stats.json": f"/knowledgebase/{kb_id}/wiki/stats",
        "wiki-lint.json": f"/knowledgebase/{kb_id}/wiki/lint",
        "wiki-graph.json": f"/knowledgebase/{kb_id}/wiki/graph?mode=overview&limit=2000",
        "wiki-index.json": f"/knowledgebase/{kb_id}/wiki/index?limit=200",
        "wiki-pages.json": f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=200",
        "wiki-folders.json": f"/knowledgebase/{kb_id}/wiki/folders",
        "wiki-issues.json": f"/knowledgebase/{kb_id}/wiki/issues",
    }
    for name, path in endpoints.items():
        status, data = get(path)
        (OUT_DIR / name).write_text(
            json.dumps({"status": status, "endpoint": path, "response": data}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        size = len(json.dumps(data, ensure_ascii=False))
        print(f"{name:22s} HTTP {status}  {size} chars")


COMMANDS = {
    "create": cmd_create,
    "upload": cmd_upload,
    "status": cmd_status,
    "dump": cmd_dump,
    "kb": cmd_kb,
    "delete": cmd_delete,
    "manual": cmd_manual,
    "wait": cmd_wait,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法：weknora_round1.py [create|upload|status|dump]")
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
