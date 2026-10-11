"""增量与删除的对账实验（对外稿 0043 的证据）。

用法：
    # 增量：往 P6+P7 库里投一篇新文档（默认 A4 harness），做前后对账
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_incr_del.py inc [源代号]

    # 删除：把指定知识从库里删掉，记录页面/目录/lint/索引的变化
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_incr_del.py del [源代号]

产出：
    data/tmp/weknora-multisource/incr-<code>.json
    data/tmp/weknora-multisource/del-<code>.json
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.kb.chunker import extract_sections_html  # noqa: E402

KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-multisource"
SRC_DIR = ROOT / "data" / "kb-src" / "articles"

KB_NAME = "P6+P7 · 多源页读感"

SOURCES = {
    "A4": ("anthropic/04-effective-harnesses-long-running.html", "A4 · Effective harnesses for long-running agents"),
    "O3": ("openai/03-new-tools-for-building-agents.html", "O3 · New tools for building agents"),
    "P6": ("personal/06-hamel-your-ai-product-needs-evals.html", "P6 · Your AI Product Needs Evals"),
    "P7": ("personal/07-hamel-ai-evals-faq.html", "P7 · AI Evals FAQ"),
}


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _request(req: urllib.request.Request):
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def get(path: str):
    return _request(urllib.request.Request(BASE + path, headers={"X-API-Key": api_key()}))


def post_json(path: str, payload: dict):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request(
        urllib.request.Request(
            BASE + path,
            data=body,
            headers={"X-API-Key": api_key(), "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
    )


def delete(path: str):
    return _request(
        urllib.request.Request(BASE + path, headers={"X-API-Key": api_key()}, method="DELETE")
    )


def find_kb() -> dict:
    _, data = get("/knowledge-bases")
    for kb in (data or {}).get("data") or []:
        if kb.get("name") == KB_NAME:
            return kb
    raise SystemExit("找不到目标知识库")


def snapshot(kb_id: str):
    _, data = get(f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=300")
    pages = (data or {}).get("pages") or []
    _, lint = get(f"/knowledgebase/{kb_id}/wiki/lint")
    _, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
    _, folders = get(f"/knowledgebase/{kb_id}/wiki/folders")
    _, index = get(f"/knowledgebase/{kb_id}/wiki/index")
    _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
    page_map = {
        p["slug"]: {
            "title": p.get("title"),
            "refs": list(p.get("source_refs") or []),
            "chunk_refs": len(p.get("chunk_refs") or []),
            "chars": len(p.get("content") or ""),
            "version": p.get("version"),
            "folder": " / ".join(p.get("category_path") or []),
        }
        for p in pages
    }
    lint_types = Counter(i.get("type") for i in ((lint or {}).get("issues") or []))
    return {
        "pages": page_map,
        "page_count": len(pages),
        "lint": dict(lint_types),
        "stats": {k: v for k, v in (stats or {}).items() if k != "recent_updates"},
        "folders": len(((folders or {}).get("folders") or [])),
        "folder_paths": [f.get("path") for f in ((folders or {}).get("folders") or [])],
        "index_content": ((index or {}).get("page") or {}).get("content")
        or ((index or {}).get("content") or ""),
        "knowledge": [
            {"id": r.get("id"), "title": r.get("title"), "state": r.get("parse_status")}
            for r in ((know or {}).get("data") or [])
        ],
    }


def wait_stable(kb_id: str, expect_total: int | None = None, timeout_sec: int = 20 * 60, rounds: int = 3):
    deadline = time.time() + timeout_sec
    stable, last = 0, -1
    while time.time() < deadline:
        _, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
        total = (stats or {}).get("total_pages")
        _, know = get(f"/knowledge-bases/{kb_id}/knowledge")
        states = Counter((r.get("parse_status") or "?") for r in ((know or {}).get("data") or []))
        print(f"[{time.strftime('%H:%M:%S')}] pages={total} docs={dict(states)}", flush=True)
        busy = any(s in {"pending", "processing", "finalizing", "parsing"} for s in states)
        if isinstance(total, int) and total == last and not busy:
            stable += 1
            if stable >= rounds and (expect_total is None or total == expect_total):
                return total
        else:
            stable = 0
        last = total if isinstance(total, int) else last
        time.sleep(20)
    return last


def cmd_increment() -> None:
    code = (sys.argv[2] if len(sys.argv) > 2 else "A4").upper()
    rel, title = SOURCES[code]
    sections = extract_sections_html((SRC_DIR / rel).read_text(encoding="utf-8"))
    lines = [f"# {title}", ""]
    for sec in sections:
        lines += [f"## {sec.path}", ""]
        for para in sec.paragraphs:
            lines += [para.strip(), ""]
    content = "\n".join(lines).strip() + "\n"

    kb = find_kb()
    kb_id = kb["id"]
    before = snapshot(kb_id)
    print(f"投喂前：{before['page_count']} 页；知识 {len(before['knowledge'])} 条")

    status, data = post_json(
        f"/knowledge-bases/{kb_id}/knowledge/manual",
        {"title": title, "content": content, "status": "publish"},
    )
    kid = ((data or {}).get("data") or {}).get("id")
    print(f"投喂 {title}：HTTP {status} id={kid}（{len(content)} 字符）")
    wait_stable(kb_id)

    after = snapshot(kb_id)
    new = sorted(set(after["pages"]) - set(before["pages"]))
    updated, untouched = [], 0
    for slug, a in after["pages"].items():
        b = before["pages"].get(slug)
        if b is None:
            continue
        if (b["refs"], b["chars"], b["version"]) != (a["refs"], a["chars"], a["version"]):
            updated.append(
                {
                    "slug": slug,
                    "refs": f"{len(b['refs'])}→{len(a['refs'])}",
                    "chars": f"{b['chars']}→{a['chars']}",
                    "version": f"{b['version']}→{a['version']}",
                    "gained_new_doc": kid in a["refs"] and kid not in b["refs"],
                }
            )
        else:
            untouched += 1
    gone = sorted(set(before["pages"]) - set(after["pages"]))

    out = {
        "experiment": "increment",
        "code": code,
        "knowledge_id": kid,
        "title": title,
        "chars": len(content),
        "pages_before": before["page_count"],
        "pages_after": after["page_count"],
        "new_pages": [(s, after["pages"][s]["title"]) for s in new],
        "updated_pages": updated,
        "unchanged_pages": untouched,
        "removed_pages": gone,
        "lint_before": before["lint"],
        "lint_after": after["lint"],
        "folders_before": before["folders"],
        "folders_after": after["folders"],
    }
    print(f"\n投喂后：{after['page_count']} 页（+{len(new)}；更新 {len(updated)}；未动 {untouched}；消失 {len(gone)}）")
    for s, t in out["new_pages"]:
        print(f"  + {s}  {t}")
    for u in updated:
        mark = "（挂上本文档 ref）" if u["gained_new_doc"] else ""
        print(f"  ~ {u['slug']}  refs {u['refs']}  chars {u['chars']}  ver {u['version']} {mark}")
    print(f"lint: {before['lint']} → {after['lint']}")
    print(f"目录数: {before['folders']} → {after['folders']}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"incr-{code.lower()}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已写 {OUT_DIR / f'incr-{code.lower()}.json'}")


def cmd_delete() -> None:
    code = (sys.argv[2] if len(sys.argv) > 2 else "P7").upper()
    rel, title = SOURCES[code]
    kb = find_kb()
    kb_id = kb["id"]
    before = snapshot(kb_id)
    target = next((k for k in before["knowledge"] if title.split(" · ")[1] in (k.get("title") or "")), None)
    if not target:
        print("现有知识：")
        for k in before["knowledge"]:
            print("   ", k)
        raise SystemExit(f"找不到要删的知识：{title}")
    kid = target["id"]
    print(f"投喂前：{before['page_count']} 页；将删除 {target['title']}（{kid}）")

    status, data = delete(f"/knowledge/{kid}")
    print(f"DELETE /knowledge/{kid} → HTTP {status} {json.dumps(data, ensure_ascii=False)[:200]}")
    wait_stable(kb_id)

    after = snapshot(kb_id)
    gone = sorted(set(before["pages"]) - set(after["pages"]))
    stripped, untouched = [], 0
    for slug, a in after["pages"].items():
        b = before["pages"].get(slug)
        if b is None:
            continue
        if kid in b["refs"] and kid not in a["refs"]:
            stripped.append(
                {
                    "slug": slug,
                    "refs": f"{len(b['refs'])}→{len(a['refs'])}",
                    "chars": f"{b['chars']}→{a['chars']}",
                    "version": f"{b['version']}→{a['version']}",
                    "still_live": len(a["refs"]) > 0,
                }
            )
        elif (b["refs"], b["chars"], b["version"]) != (a["refs"], a["chars"], a["version"]):
            stripped.append(
                {
                    "slug": slug,
                    "refs": f"{len(b['refs'])}→{len(a['refs'])}",
                    "chars": f"{b['chars']}→{a['chars']}",
                    "version": f"{b['version']}→{a['version']}",
                    "still_live": len(a["refs"]) > 0,
                }
            )
        else:
            untouched += 1

    out = {
        "experiment": "delete",
        "code": code,
        "knowledge_id": kid,
        "title": target["title"],
        "pages_before": before["page_count"],
        "pages_after": after["page_count"],
        "removed_pages": gone,
        "stripped_pages": stripped,
        "unchanged_pages": untouched,
        "lint_before": before["lint"],
        "lint_after": after["lint"],
        "folders_before": before["folders"],
        "folders_after": after["folders"],
        "index_before": before["index_content"],
        "index_after": after["index_content"],
    }
    print(f"\n删除后：{after['page_count']} 页（删掉 {len(gone)} 页；被剥 ref {len(stripped)} 页；未动 {untouched}）")
    print("被删的页：")
    for s in gone:
        print(f"  - {s}  {before['pages'][s]['title']}")
    print("被剥/改动的页：")
    for u in stripped:
        keep = "留存" if u["still_live"] else "变无源"
        print(f"  ~ {u['slug']}  refs {u['refs']}  chars {u['chars']}  ver {u['version']}  {keep}")
    print(f"lint: {before['lint']} → {after['lint']}")
    print(f"目录数: {before['folders']} → {after['folders']}")
    if before["index_content"] != after["index_content"]:
        print(f"索引引言变化：{len(before['index_content'])} → {len(after['index_content'])} 字符")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"del-{code.lower()}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已写 {OUT_DIR / f'del-{code.lower()}.json'}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if len(sys.argv) < 2 or sys.argv[1] not in {"inc", "del"}:
        print("用法：probe_incr_del.py [inc|del] [源代号]")
        raise SystemExit(2)
    {"inc": cmd_increment, "del": cmd_delete}[sys.argv[1]]()
