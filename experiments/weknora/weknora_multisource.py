"""WeKnora「多源页」实验：把 P6 与 P7 投进同一个 wiki 库，看跨文档合并后的页面读感。

用法（在仓库根目录跑）：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_multisource.py <子命令>

子命令：
    create   建 wiki 型知识库（幂等：同名库已存在则复用）—— P6+P7 · 多源页读感
    manual   把 P6.md / P7.md 用「手工 Markdown 知识」接口投进去（一篇一条知识）
    status   查知识库 / 知识的解析与入库状态
    wait     轮询到页面数稳定（最多 40 分钟）
    dump     把 wiki 的 stats / lint / graph / index / pages 落盘
    multi    挑出 source_refs >= 2 的页，把整页正文单独落盘（多源页读感的原料）
    kb       打印知识库关键开关

设计约束（同 weknora_round1.py）：
    * API key 从 D:\\lida-data\\vscode\\WeKnora\\.api-key 读，**不打印明文**；
    * 所有原始响应落盘留档；只走本地 8080，不经代理。
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-multisource"

KB_NAME = "P6+P7 · 多源页读感"
KB_DESC = "多源页读感实验：Hamel「Your AI Product Needs Evals」(P6) + 「AI Evals FAQ」(P7)"
EMBEDDING_MODEL_ID = "e9e03c3f-4afc-4084-a392-4751e9b83bfe"  # qwen3.7-text-embedding
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"  # deepseek-flash

TITLES = {
    "P6": "P6 · Your AI Product Needs Evals",
    "P7": "P7 · AI Evals FAQ",
}


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _request(req: urllib.request.Request) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
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


def write(name: str, obj: object) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(
        json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def find_kb() -> dict | None:
    status, data = get("/knowledge-bases")
    if status != 200 or not isinstance(data, dict):
        return None
    for kb in data.get("data") or []:
        if kb.get("name") == KB_NAME:
            return kb
    return None


def cmd_create() -> None:
    existing = find_kb()
    if existing:
        print("已存在同名库，复用：", existing.get("id"))
        write("kb-create.json", {"status": 200, "response": existing, "reused": True})
        return
    payload = {
        "name": KB_NAME,
        "description": KB_DESC,
        "type": "wiki",
        "embedding_model_id": EMBEDDING_MODEL_ID,
        "summary_model_id": CHAT_MODEL_ID,
        # wiki 开关在 indexing_strategy 上（type=wiki 不会自动打开）
        "indexing_strategy": {
            "vector_enabled": True,
            "keyword_enabled": True,
            "wiki_enabled": True,
            "graph_enabled": False,
        },
        "wiki_config": {
            "synthesis_model_id": CHAT_MODEL_ID,
            "extraction_granularity": "standard",  # 与 A5 首跑同档，便于对照
        },
    }
    status, data = post_json("/knowledge-bases", payload)
    print("HTTP", status)
    print(json.dumps(data, ensure_ascii=False, indent=2)[:1500])
    write("kb-create.json", {"status": status, "response": data})


def cmd_manual() -> None:
    kb = find_kb()
    if not kb:
        print("找不到知识库，先跑 create")
        return
    codes = [c.upper() for c in sys.argv[2:]] or list(TITLES)
    results = {}
    for code in codes:
        md_file = OUT_DIR / f"{code}.md"
        if not md_file.exists():
            print(f"缺 {md_file}，先跑 prep_multisource.py {code}")
            continue
        content = md_file.read_text(encoding="utf-8")
        print(f"投喂 {code}：{len(content)} 字符 → {TITLES[code]}")
        status, data = post_json(
            f"/knowledge-bases/{kb['id']}/knowledge/manual",
            {"title": TITLES[code], "content": content, "status": "publish"},
        )
        print("  HTTP", status, json.dumps(data, ensure_ascii=False)[:300])
        results[code] = {"status": status, "response": data}
    write("kb-manual.json", results)


def cmd_status() -> None:
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    kb_id = kb["id"]
    _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
    rows = (know or {}).get("data") if isinstance(know, dict) else None
    for r in rows or []:
        print(
            f"  {r.get('title')!r:<48} parse={r.get('parse_status')} "
            f"subtasks={r.get('pending_subtasks_count')} id={r.get('id')}"
        )
    st, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
    print("  wiki stats:", json.dumps(stats, ensure_ascii=True)[:400])


def cmd_wait() -> None:
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    kb_id = kb["id"]
    deadline = time.time() + 40 * 60
    stable = 0
    last_pages = -1
    while time.time() < deadline:
        _, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
        _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
        rows = (know or {}).get("data") if isinstance(know, dict) else []
        pages = (stats or {}).get("total_pages") if isinstance(stats, dict) else None
        if pages is None and isinstance(stats, dict):
            pages = stats.get("total")
        states = ",".join(sorted({(r.get("parse_status") or "?") for r in rows or []}))
        print(
            f"[{time.strftime('%H:%M:%S')}] pages={pages} doc_states={states}",
            flush=True,
        )
        done = all((r.get("parse_status") or "") in {"completed", "success"} for r in rows or [])
        if isinstance(pages, int) and pages == last_pages and (done or pages > 0):
            stable += 1
            if stable >= 3:
                print("[done] 页面数连续三轮不变", flush=True)
                return
        else:
            stable = 0
        last_pages = pages if isinstance(pages, int) else last_pages
        time.sleep(20)
    print("[timeout] 40 分钟未稳定", flush=True)


def cmd_dump() -> None:
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    kb_id = kb["id"]
    endpoints = {
        "wiki-stats.json": f"/knowledgebase/{kb_id}/wiki/stats",
        "wiki-lint.json": f"/knowledgebase/{kb_id}/wiki/lint",
        "wiki-graph.json": f"/knowledgebase/{kb_id}/wiki/graph?mode=overview&limit=2000",
        "wiki-index.json": f"/knowledgebase/{kb_id}/wiki/index?limit=300",
        "wiki-pages.json": f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=300",
        "wiki-folders.json": f"/knowledgebase/{kb_id}/wiki/folders",
    }
    for name, path in endpoints.items():
        status, data = get(path)
        write(name, {"status": status, "endpoint": path, "response": data})
        size = len(json.dumps(data, ensure_ascii=False))
        print(f"{name:22s} HTTP {status}  {size} chars")


def load_response(name: str):
    obj = json.loads((OUT_DIR / name).read_text(encoding="utf-8"))
    return obj.get("response", obj)


def cmd_multi() -> None:
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    kb_id = kb["id"]
    pages_obj = load_response("wiki-pages.json")
    pages = (pages_obj or {}).get("pages") or []
    multi = [p for p in pages if len(p.get("source_refs") or []) >= 2]
    print(f"总页数 {len(pages)}，其中 source_refs>=2 的 {len(multi)} 页")
    bundle: list[str] = []
    detail_dir = OUT_DIR / "pages"
    detail_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(multi, key=lambda x: -(len(x.get("content") or ""))):
        slug = p.get("slug") or ""
        status, data = get(
            f"/knowledgebase/{kb_id}/wiki/pages/{urllib.parse.quote(slug)}"
        )
        page = data if isinstance(data, dict) else {}
        safe = slug.replace("/", "__") or "root"
        (detail_dir / f"{safe}.json").write_text(
            json.dumps(
                {"status": status, "slug": slug, "page": page}, ensure_ascii=False, indent=2
            ),
            encoding="utf-8",
        )
        bundle.append(
            "\n\n" + "=" * 78
            + f"\n# {page.get('title') or p.get('title')}   [{slug}]\n"
            + f"source_refs={len(page.get('source_refs') or [])} "
            + f"chunk_refs={len(page.get('chunk_refs') or [])} "
            + f"content_chars={len(page.get('content') or '')}\n"
            + "=" * 78
            + "\n"
            + (page.get("content") or "(空)")
        )
        print(
            f"  {slug:<44} refs={len(page.get('source_refs') or [])} "
            f"chars={len(page.get('content') or '')} HTTP {status}"
        )
    (OUT_DIR / "multi-source-pages.md").write_text(
        "\n".join(bundle).lstrip(), encoding="utf-8"
    )
    print(f"\n已写 {OUT_DIR / 'multi-source-pages.md'}")


def cmd_kb() -> None:
    kb = find_kb()
    if not kb:
        print("找不到知识库")
        return
    keys = [
        "id", "name", "type", "indexing_strategy", "embedding_model_id",
        "summary_model_id", "wiki_config",
    ]
    print(json.dumps({k: kb.get(k) for k in keys}, ensure_ascii=True, indent=2))


def cmd_delete() -> None:
    kb = find_kb()
    if not kb:
        print("找不到知识库，无需删")
        return
    req = urllib.request.Request(
        f"{BASE}/knowledge-bases/{kb['id']}",
        headers={"X-API-Key": api_key()},
        method="DELETE",
    )
    status, data = _request(req)
    print("DELETE HTTP", status, json.dumps(data, ensure_ascii=False)[:300])


COMMANDS = {
    "create": cmd_create,
    "manual": cmd_manual,
    "status": cmd_status,
    "wait": cmd_wait,
    "dump": cmd_dump,
    "multi": cmd_multi,
    "kb": cmd_kb,
    "delete": cmd_delete,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法：weknora_multisource.py [create|manual|status|wait|dump|multi|kb|delete]")
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
