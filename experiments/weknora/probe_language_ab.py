"""A/B：同一篇英文文档，分别用 zh-CN / en-US 两种"输出语言"编译，看产物体积差多少。

背景：wiki 页面的语言由 middleware/language.go 决定——WEKNORA_LANGUAGE 环境变量 > 请求的
Accept-Language 头 > 硬编码 "zh-CN"。我们的部署没设环境变量，上传时也没带 Accept-Language，
于是**英文语料被编译成了中文页面**。这个脚本把语言当成唯一变量，量一量它值多少。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_language_ab.py
产出：
    data/tmp/weknora-multisource/language-ab.json
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-multisource"
SRC = OUT_DIR / "P6.md"          # Hamel《Your AI Product Needs Evals》，24,681 字符（英文）
TITLE = "P6 · Your AI Product Needs Evals"

EMBEDDING_MODEL_ID = "e9e03c3f-4afc-4084-a392-4751e9b83bfe"
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _req(req: urllib.request.Request):
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def get(path: str, lang: str | None = None):
    headers = {"X-API-Key": api_key()}
    if lang:
        headers["Accept-Language"] = lang
    return _req(urllib.request.Request(BASE + path, headers=headers))


def post(path: str, payload: dict, lang: str | None = None):
    headers = {"X-API-Key": api_key(), "Content-Type": "application/json; charset=utf-8"}
    if lang:
        headers["Accept-Language"] = lang
    return _req(
        urllib.request.Request(
            BASE + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
    )


def find_kb(name: str):
    _, data = get("/knowledge-bases")
    for kb in (data or {}).get("data") or []:
        if kb.get("name") == name:
            return kb
    return None


def create_kb(name: str):
    kb = find_kb(name)
    if kb:
        return kb
    status, data = post(
        "/knowledge-bases",
        {
            "name": name,
            "description": "语言 A/B：同一篇英文文档，不同输出语言",
            "type": "wiki",
            "embedding_model_id": EMBEDDING_MODEL_ID,
            "summary_model_id": CHAT_MODEL_ID,
            "indexing_strategy": {
                "vector_enabled": True,
                "keyword_enabled": True,
                "wiki_enabled": True,
                "graph_enabled": False,
            },
            "wiki_config": {"synthesis_model_id": CHAT_MODEL_ID, "extraction_granularity": "standard"},
        },
    )
    print(f"  建库 {name}: HTTP {status}")
    return find_kb(name)


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(text)


def fetch_pages(kb_id: str):
    _, data = get(f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=300")
    return (data or {}).get("pages") or []


def wait_stable(kb_id: str, timeout: int = 15 * 60, rounds: int = 3):
    deadline = time.time() + timeout
    stable, last = 0, -1
    while time.time() < deadline:
        _, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
        _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
        total = (stats or {}).get("total_pages")
        rows = (know or {}).get("data") or []
        busy = any((r.get("parse_status") in {"pending", "processing", "finalizing"}) for r in rows)
        print(f"    pages={total} busy={busy}", flush=True)
        if isinstance(total, int) and total == last and not busy:
            stable += 1
            if stable >= rounds:
                return total
        else:
            stable = 0
        last = total if isinstance(total, int) else last
        time.sleep(20)
    return last


def fetch_chunks(knowledge_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    page = 1
    while True:
        _, data = get(f"/chunks/{knowledge_id}?page={page}&page_size=100")
        rows = (data or {}).get("data") or []
        for r in rows:
            out[r["id"]] = len(r.get("content") or "")
        total = (data or {}).get("total")
        if not rows or (isinstance(total, int) and len(out) >= total):
            break
        page += 1
    return out


def measure(lang: str) -> dict:
    name = f"L · P6 {lang}"
    print(f"== {name} ==")
    kb = create_kb(name)
    kb_id = kb["id"]
    if not fetch_pages(kb_id):
        content = SRC.read_text(encoding="utf-8")
        status, data = post(
            f"/knowledge-bases/{kb_id}/knowledge/manual",
            {"title": TITLE, "content": content, "status": "publish"},
            lang=lang,
        )
        kid = ((data or {}).get("data") or {}).get("id")
        print(f"  投喂 HTTP {status} id={kid}（{len(content)} 字符）")
        wait_stable(kb_id)
    else:
        print("  已有页面，跳过投喂")
    pages = fetch_pages(kb_id)
    _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
    rows = (know or {}).get("data") or []
    kid = rows[0]["id"] if rows else None
    chunks = fetch_chunks(kid) if kid else {}

    content_pages = [p for p in pages if p.get("page_type") in {"entity", "concept"}]
    out_chars = sum(len(p.get("content") or "") for p in content_pages)
    in_chars = 0
    refs = 0
    for p in content_pages:
        seen = set()
        for cid in p.get("chunk_refs") or []:
            if cid in seen:
                continue
            seen.add(cid)
            in_chars += chunks.get(cid, 0)
            refs += 1
    all_content = "\n".join(p.get("content") or "" for p in pages)
    res = {
        "lang": lang,
        "kb_id": kb_id,
        "knowledge_id": kid,
        "pages": len(pages),
        "content_pages": len(content_pages),
        "by_type": {t: sum(1 for p in pages if p.get("page_type") == t) for t in {p.get("page_type") for p in pages}},
        "out_chars": out_chars,
        "in_chars": in_chars,
        "out_over_in": round(out_chars / in_chars, 3) if in_chars else None,
        "chunk_count": len(chunks),
        "chunk_chars": sum(chunks.values()),
        "source_chars": len(SRC.read_text(encoding="utf-8")),
        "out_over_source": round(out_chars / len(SRC.read_text(encoding="utf-8")), 3),
        "cjk_ratio_all_pages": round(cjk_ratio(all_content), 3),
        "sample_titles": [p.get("title") for p in content_pages[:6]],
    }
    print(
        f"  页数={res['pages']}  Σcontent={out_chars}  Σ素材={in_chars}"
        f"  out/in={res['out_over_in']}  out/source={res['out_over_source']}"
        f"  中文占比={res['cjk_ratio_all_pages']}"
    )
    print(f"  样例标题：{res['sample_titles']}")
    return res


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    results = {}
    for lang in ("zh-CN", "en-US"):
        results[lang] = measure(lang)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "language-ab.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n== 对照 ==")
    for lang, r in results.items():
        print(
            f"  {lang}: pages={r['pages']}  Σcontent={r['out_chars']}  out/in={r['out_over_in']}"
            f"  out/source={r['out_over_source']}  中文占比={r['cjk_ratio_all_pages']}"
        )


if __name__ == "__main__":
    main()
