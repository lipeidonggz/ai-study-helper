"""MuSiQue 语料入 WeKnora：建新库 → 批量投喂 → 计时到编译稳定。

用法（仓库根目录）：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_musique.py <子命令>

子命令：
    create   建 MuSiQue 专用库（幂等：同名库存在则复用）
    upload   把 slice 目录下 corpus/*.md 逐条投成「手工 Markdown 知识」，并发投
    wait     轮询到全部知识解析完成且 wiki 页数连续稳定，打印耗时
    dump     落盘 wiki stats / lint / graph / index / pages / folders
    status   当前进度快照
    summary  打印计时结论（上传起止、编译耗时、页面数、逐次调用 token）

口径说明：
    * 知识条目 = 一个 .md 文件 = MuSiQue 的一段（标题来自文件名）；
    * 计时以「第一条 POST 成功」为起点，以「全部知识 completed 且 wiki 页数连续 3 轮不变」为终点；
    * API key 从 D:\\lida-data\\vscode\\WeKnora\\.api-key 读，不打印明文；
    * 逐次 LLM 用量不在本脚本取，另跑 usage_report.py（日志口径）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"

SLICE_DIR = ROOT / "data" / "tmp" / "musique" / "slice-30-strat"
CORPUS_DIR = SLICE_DIR / "corpus"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-musique"

KB_NAME = "MuSiQue · 30题分层 · 检索对照"
KB_DESC = "MuSiQue full dev 可答子集（2/3/4 跳各 10 题）的 context 段落并集，572 段一条一文件"
EMBEDDING_MODEL_ID = "e9e03c3f-4afc-4084-a392-4751e9b83bfe"  # qwen3.7-text-embedding
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"  # deepseek-flash


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def _request(req: urllib.request.Request, timeout: int = 180) -> tuple[int, object]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except Exception as exc:  # 网络层异常也返回，方便脚本继续
        return 0, {"error": repr(exc)}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def get(path: str) -> tuple[int, object]:
    return _request(urllib.request.Request(BASE + path, headers={"X-API-Key": api_key()}))


def post_json(path: str, payload: dict) -> tuple[int, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return _request(
        urllib.request.Request(
            BASE + path,
            data=body,
            headers={"X-API-Key": api_key(), "Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
    )


def write(name: str, obj: object) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def read_state() -> dict:
    p = OUT_DIR / "state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    write("state.json", state)


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
    else:
        payload = {
            "name": KB_NAME,
            "description": KB_DESC,
            "type": "wiki",
            "embedding_model_id": EMBEDDING_MODEL_ID,
            "summary_model_id": CHAT_MODEL_ID,
            "indexing_strategy": {
                "vector_enabled": True,
                "keyword_enabled": True,
                "wiki_enabled": True,
                "graph_enabled": False,
            },
            "wiki_config": {
                "synthesis_model_id": CHAT_MODEL_ID,
                "extraction_granularity": "standard",  # 与 A5/P6P7 同档，便于对照
            },
        }
        status, data = post_json("/knowledge-bases", payload)
        print("HTTP", status)
        write("kb-create.json", {"status": status, "response": data})
    kb = find_kb()
    if not kb:
        raise SystemExit("建库失败")
    state = read_state()
    state["kb_id"] = kb["id"]
    save_state(state)
    print("kb_id =", kb["id"])


def corpus_files() -> list[Path]:
    files = sorted(CORPUS_DIR.glob("*.md"))
    if not files:
        raise SystemExit(f"没有语料：{CORPUS_DIR}")
    return files


def existing_titles(kb_id: str) -> set[str]:
    _, data = get(f"/knowledge-bases/{kb_id}/knowledge")
    rows = (data or {}).get("data") if isinstance(data, dict) else []
    return {(r.get("title") or "").strip() for r in rows or []}


def cmd_upload() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args(sys.argv[2:])

    kb = find_kb()
    if not kb:
        raise SystemExit("找不到知识库，先跑 create")
    kb_id = kb["id"]
    files = corpus_files()
    done = existing_titles(kb_id)
    todo = [f for f in files if f.stem not in done]
    print(f"语料 {len(files)} 条，库里已有 {len(done)} 条，本次投 {len(todo)} 条，并发 {args.workers}")

    lock = threading.Lock()
    results: list[dict] = []
    t0 = time.time()

    def one(path: Path) -> None:
        content = path.read_text(encoding="utf-8")
        status, data = post_json(
            f"/knowledge-bases/{kb_id}/knowledge/manual",
            {"title": path.stem, "content": content, "status": "publish"},
        )
        with lock:
            results.append({"title": path.stem, "status": status, "ok": status in (200, 201)})
            n = len(results)
            if n % 25 == 0 or n == len(todo):
                el = time.time() - t0
                print(f"  已投 {n}/{len(todo)}  用时 {el/60:.1f} 分钟", flush=True)

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(one, todo))

    t1 = time.time()
    bad = [r for r in results if not r["ok"]]
    state = read_state()
    state["upload"] = {
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1)),
        "seconds": round(t1 - t0, 1),
        "submitted": len(todo),
        "failed": len(bad),
        "titles_total": len(files),
    }
    save_state(state)
    write("upload-results.json", results)
    print(f"投喂完成：{len(todo)} 条，失败 {len(bad)}，耗时 {(t1-t0)/60:.1f} 分钟")
    if bad:
        print("失败样例：", json.dumps(bad[:5], ensure_ascii=False))


def snapshot(kb_id: str) -> dict:
    _, stats = get(f"/knowledgebase/{kb_id}/wiki/stats")
    # 列表接口默认 page_size=20，必须显式放大，否则文档计数被截断
    _, know = get(f"/knowledge-bases/{kb_id}/knowledge?page=1&page_size=1000")
    rows = (know or {}).get("data") if isinstance(know, dict) else []
    rows = rows or []
    total = (know or {}).get("total") if isinstance(know, dict) else None
    pages = None
    if isinstance(stats, dict):
        pages = stats.get("total_pages", stats.get("total"))
    states: dict[str, int] = {}
    for r in rows:
        key = r.get("parse_status") or "?"
        states[key] = states.get(key, 0) + 1
    return {
        "pages": pages,
        "docs": len(rows),
        "total": total,
        "doc_states": states,
        "stats": stats,
    }


def cmd_status() -> None:
    kb = find_kb()
    if not kb:
        raise SystemExit("找不到知识库")
    snap = snapshot(kb["id"])
    print(json.dumps({k: v for k, v in snap.items() if k != "stats"}, ensure_ascii=False))
    print("wiki stats:", json.dumps(snap["stats"], ensure_ascii=True)[:300])


def cmd_wait() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout-min", type=float, default=720.0)
    ap.add_argument("--interval-sec", type=float, default=30.0)
    args = ap.parse_args(sys.argv[2:])

    kb = find_kb()
    if not kb:
        raise SystemExit("找不到知识库")
    kb_id = kb["id"]

    deadline = time.time() + args.timeout_min * 60
    stable = 0
    last_pages = -1
    trace: list[dict] = []
    t_start = time.time()
    while time.time() < deadline:
        snap = snapshot(kb_id)
        now = time.time()
        trace.append({"t": round(now - t_start, 1), "at": time.strftime("%H:%M:%S"), **{k: v for k, v in snap.items() if k != "stats"}})
        print(
            f"[{time.strftime('%H:%M:%S')}] +{(now-t_start)/60:5.1f}min pages={snap['pages']} "
            f"docs={snap['docs']} states={snap['doc_states']}",
            flush=True,
        )
        write("wait-trace.json", trace)
        done = snap["docs"] > 0 and all(k in {"completed", "success"} for k in snap["doc_states"])
        pages = snap["pages"] if isinstance(snap["pages"], int) else None
        if pages is not None and pages == last_pages and done:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        if pages is not None:
            last_pages = pages
        time.sleep(args.interval_sec)

    t_end = time.time()
    state = read_state()
    state["compile"] = {
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t_end)),
        "elapsed_min": round((t_end - t_start) / 60, 1),
        "pages": last_pages,
        "trace_points": len(trace),
        "stopped_by": "stable" if stable >= 3 else "timeout",
    }
    save_state(state)
    print(json.dumps(state["compile"], ensure_ascii=False))


def cmd_dump() -> None:
    kb = find_kb()
    if not kb:
        raise SystemExit("找不到知识库")
    kb_id = kb["id"]
    endpoints = {
        "wiki-stats.json": f"/knowledgebase/{kb_id}/wiki/stats",
        "wiki-lint.json": f"/knowledgebase/{kb_id}/wiki/lint",
        "wiki-graph.json": f"/knowledgebase/{kb_id}/wiki/graph?mode=overview&limit=2000",
        "wiki-index.json": f"/knowledgebase/{kb_id}/wiki/index?limit=300",
        "wiki-folders.json": f"/knowledgebase/{kb_id}/wiki/folders",
    }
    for name, path in endpoints.items():
        status, data = get(path)
        write(name, {"status": status, "endpoint": path, "response": data})
        print(f"{name:22s} HTTP {status}  {len(json.dumps(data, ensure_ascii=False))} chars")
    # 页面正文单独落盘（可能很大）
    status, pages = get(f"/knowledgebase/{kb_id}/wiki/pages?page=1&page_size=500")
    write("wiki-pages.json", {"status": status, "response": pages})
    n = len(((pages or {}).get("pages") or [])) if isinstance(pages, dict) else 0
    print(f"wiki-pages.json        HTTP {status}  {n} pages")


def cmd_summary() -> None:
    state = read_state()
    out = dict(state)
    kb = find_kb()
    if kb:
        out["kb"] = {"id": kb.get("id"), "name": kb.get("name"), "indexing_strategy": kb.get("indexing_strategy")}
        out["now"] = snapshot(kb["id"])
    print(json.dumps(out, ensure_ascii=False, indent=2))


COMMANDS = {
    "create": cmd_create,
    "upload": cmd_upload,
    "wait": cmd_wait,
    "status": cmd_status,
    "dump": cmd_dump,
    "summary": cmd_summary,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法：weknora_musique.py [create|upload|wait|status|dump|summary]")
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
