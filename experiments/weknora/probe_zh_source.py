"""同语言对照：投一篇**中文**源文，量同样的指标，看"英文源→中文页"的低比值是不是语言造成的。

对照（英文源 P6，24,681 字符 → 中文页）：页数 33、Σcontent 21,278、Σ素材 61,955、
out/in 0.343、out/source 0.862，且源的 12-gram 几乎不进产物（2–6%，因为语言不同）。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_zh_source.py
产出：
    data/tmp/weknora-multisource/zh-source.json
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-multisource"

SRC = ROOT / "data" / "kb-src" / "tutorials" / "bojieli-ai-agent-book" / "book" / "chapter9.md"
TITLE = "ZH · 深入理解 AI Agent 第 9 章"
KB_NAME = "L · 中文源 chapter9"

EMBEDDING_MODEL_ID = "e9e03c3f-4afc-4084-a392-4751e9b83bfe"
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"
N = 12


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _req(req):
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


def get(path: str):
    return _req(urllib.request.Request(BASE + path, headers={"X-API-Key": api_key()}))


def post(path: str, payload: dict):
    return _req(
        urllib.request.Request(
            BASE + path,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"X-API-Key": api_key(), "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
    )


def find_kb(name: str):
    _, data = get("/knowledge-bases")
    for kb in (data or {}).get("data") or []:
        if kb.get("name") == name:
            return kb
    return None


def grams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    return {s[i : i + N] for i in range(len(s) - N + 1)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    kb = find_kb(KB_NAME)
    if not kb:
        status, _ = post(
            "/knowledge-bases",
            {
                "name": KB_NAME,
                "description": "同语言对照：中文源文",
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
        print("建库 HTTP", status)
        kb = find_kb(KB_NAME)
    kb_id = kb["id"]

    src = SRC.read_text(encoding="utf-8", errors="ignore")
    _, pages_probe = get(f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=300")
    if not (pages_probe or {}).get("pages"):
        status, data = post(
            f"/knowledge-bases/{kb_id}/knowledge/manual",
            {"title": TITLE, "content": src, "status": "publish"},
        )
        kid = ((data or {}).get("data") or {}).get("id")
        print(f"投喂 HTTP {status} id={kid}（{len(src)} 字符）")
        deadline = time.time() + 20 * 60
        stable, last = 0, -1
        while time.time() < deadline:
            _, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
            _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
            rows = (know or {}).get("data") or []
            total = (stats or {}).get("total_pages")
            busy = any((r.get("parse_status") in {"pending", "processing", "finalizing"}) for r in rows)
            print(f"  pages={total} busy={busy}", flush=True)
            if isinstance(total, int) and total == last and not busy:
                stable += 1
                if stable >= 3:
                    break
            else:
                stable = 0
            last = total if isinstance(total, int) else last
            time.sleep(20)
    else:
        print("已有页面，跳过投喂")

    _, data = get(f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=300")
    pages = (data or {}).get("pages") or []
    _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
    kid = ((know or {}).get("data") or [{}])[0].get("id")
    chunks: dict[str, int] = {}
    page = 1
    while kid:
        _, cd = get(f"/chunks/{kid}?page={page}&page_size=100")
        rows = (cd or {}).get("data") or []
        for r in rows:
            chunks[r["id"]] = len(r.get("content") or "")
        total = (cd or {}).get("total")
        if not rows or (isinstance(total, int) and len(chunks) >= total):
            break
        page += 1

    content_pages = [p for p in pages if p.get("page_type") in {"entity", "concept"}]
    out_chars = sum(len(p.get("content") or "") for p in content_pages)
    in_chars = 0
    for p in content_pages:
        for cid in set(p.get("chunk_refs") or []):
            in_chars += chunks.get(cid, 0)
    product = "\n".join(p.get("content") or "" for p in pages)
    g_src, g_prod = grams(src), grams(product)
    cov = len(g_src & g_prod) / len(g_src) if g_src else 0.0

    res = {
        "kb_id": kb_id,
        "knowledge_id": kid,
        "source_chars": len(src),
        "source_cjk": round(sum(1 for ch in src if "\u4e00" <= ch <= "\u9fff") / max(len(src), 1), 3),
        "pages": len(pages),
        "content_pages": len(content_pages),
        "out_chars": out_chars,
        "in_chars": in_chars,
        "out_over_in": round(out_chars / in_chars, 3) if in_chars else None,
        "out_over_source": round(out_chars / len(src), 3),
        "chunk_count": len(chunks),
        "chunk_chars": sum(chunks.values()),
        "ngram_coverage_of_source": round(cov, 3),
        "sample_titles": [p.get("title") for p in content_pages[:8]],
    }
    print(json.dumps(res, ensure_ascii=False, indent=2))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "zh-source.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写 {OUT_DIR / 'zh-source.json'}")


if __name__ == "__main__":
    main()
