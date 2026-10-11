"""多源页的块级溯源：把每个多源页的 chunk_refs 还原成"哪篇文档的第几块、原文是什么"。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_multisource_chunks.py
输出：
    data/tmp/weknora-multisource/chunk-trace.json ＋ 控制台摘要
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
D = ROOT / "data" / "tmp" / "weknora-multisource"

KNOWLEDGE = {
    "P6": "2478eb26-2d7e-4aa9-8e08-60a4350ecbb2",
    "P7": "488c2d3c-0bf0-431c-baf8-a34b40cb7302",
}


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def get(path: str):
    req = urllib.request.Request(BASE + path, headers={"X-API-Key": api_key()})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def load(name: str):
    obj = json.loads((D / name).read_text(encoding="utf-8"))
    return obj.get("response", obj)


def fetch_chunks(knowledge_id: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        data = get(f"/chunks/{knowledge_id}?page={page}&page_size=100")
        rows = (data or {}).get("data") or []
        out.extend(rows)
        total = (data or {}).get("total")
        if not rows or (isinstance(total, int) and len(out) >= total):
            break
        page += 1
    return out


def main() -> None:
    try:  # Windows 控制台默认 GBK，chunk 原文里有 emoji 会炸
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    index: dict[str, dict] = {}
    per_doc_counts: dict[str, int] = {}
    for code, kid in KNOWLEDGE.items():
        rows = fetch_chunks(kid)
        per_doc_counts[code] = len(rows)
        for r in rows:
            index[r["id"]] = {
                "doc": code,
                "chunk_index": r.get("chunk_index"),
                "chars": len(r.get("content") or ""),
                "preview": (r.get("content") or "")[:120].replace("\n", " "),
            }
    print("chunks:", json.dumps(per_doc_counts, ensure_ascii=True))

    pages = (load("wiki-pages.json") or {}).get("pages") or []
    multi = [p for p in pages if len(p.get("source_refs") or []) >= 2]

    trace = []
    for p in multi:
        slug = p.get("slug") or ""
        detail = D / "pages" / (slug.replace("/", "__") + ".json")
        page = json.loads(detail.read_text(encoding="utf-8"))["page"] if detail.exists() else p
        refs = page.get("chunk_refs") or []
        by_doc = Counter(index.get(c, {}).get("doc", "?") for c in refs)
        rows = [
            {**index.get(c, {"doc": "?", "chunk_index": None, "preview": ""}), "chunk_id": c}
            for c in refs
        ]
        rows.sort(key=lambda r: (r["doc"], r["chunk_index"] or 0))
        trace.append(
            {
                "slug": slug,
                "title": page.get("title"),
                "refs_total": len(refs),
                "by_doc": dict(by_doc),
                "content_chars": len(page.get("content") or ""),
                "chunks": rows,
            }
        )
        print(f"\n== {slug}  refs={len(refs)}  by_doc={dict(by_doc)}")
        for r in rows:
            print(f"   [{r['doc']}#{r['chunk_index']:>3}] {r['preview']}")

    (D / "chunk-trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已写 {D / 'chunk-trace.json'}")


if __name__ == "__main__":
    sys.exit(main())
