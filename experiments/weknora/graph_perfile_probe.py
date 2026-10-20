"""验证 WeKnora 知识图谱的「按文件切子图」问题。

用两篇真实文档（0042 LLM-Wiki 总论 / 0043 LLM-Wiki 知识编译）建一个**只开 graph**的 KB，
跑完抽取后直接查 Neo4j，回答三个问题：
  1) 同一实体出现在两个文件里时，图里是不是两个独立节点？（期望：是）
  2) 有没有跨文件的边？（期望：0 条）
  3) 图里一共几个节点/关系，分别挂在哪些 knowledge_id 上？

用法：
    python experiments/weknora/graph_perfile_probe.py create
    python experiments/weknora/graph_perfile_probe.py upload
    python experiments/weknora/graph_perfile_probe.py wait
    python experiments/weknora/graph_perfile_probe.py probe
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
BASE = "http://localhost:8080/api/v1"
OUT = ROOT / "data" / "tmp" / "weknora-graph-perfile"
KB_NAME = "exp-graph-perfile"
KB_DESC = "图谱 per-file 探针：0042 + 0043 两篇"
CORPUS = [
    ROOT / "docs" / "publish" / "0042-llmwiki-overview.md",
    ROOT / "docs" / "publish" / "0043-llmwiki-compile-pipeline.md",
]
EMBEDDING_MODEL_ID = "e9e03c3f-4afc-4084-a392-4751e9b83bfe"  # qwen3.7-text-embedding
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"       # deepseek-flash
NEO4J_CNAME = "WeKnora-neo4j"
OVERLAP_NAMES = ["WeKnora", "LLM-Wiki", "GraphRAG", "slug", "Error Book"]


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def req(method: str, path: str, payload: dict | None = None) -> tuple[int, object]:
    url = BASE + path
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    r = urllib.request.Request(url, data=body, method=method, headers={
        "X-API-Key": api_key(),
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400]


def get(path: str):
    return req("GET", path)


def post(path: str, payload: dict):
    return req("POST", path, payload)


def write(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def find_kb() -> dict | None:
    _, data = get("/knowledge-bases")
    rows = (data or {}).get("data") if isinstance(data, dict) else []
    for r in rows or []:
        if (r.get("name") or "") == KB_NAME:
            return r
    return None


def cypher(query: str) -> str:
    """跑一条 Cypher：优先用 cypher-shell（容器内），失败则回退到 docker exec + 驱动不可用提示。"""
    with_password = ["docker", "exec", NEO4J_CNAME, "cypher-shell",
                     "-u", "neo4j", "-p", "password", "--format", "plain", query]
    try:
        p = subprocess.run(with_password, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return "(docker 不可用)"
    out = (p.stdout or "") + (p.stderr or "")
    return out.strip()


def cmd_create() -> None:
    kb = find_kb()
    if kb:
        print("已存在同名库，复用：", kb.get("id"))
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
                "wiki_enabled": False,   # 只验图谱，wiki 关掉
                "graph_enabled": True,   # 关键开关
            },
        }
        status, data = post("/knowledge-bases", payload)
        print("HTTP", status)
        write("kb-create.json", {"status": status, "response": data})
        if status not in (200, 201):
            raise SystemExit(f"建库失败：{data}")
        kb = find_kb()
    write("kb.json", {"kb": kb})
    print("kb_id =", (kb or {}).get("id"))
    print("indexing_strategy =", json.dumps((kb or {}).get("indexing_strategy"), ensure_ascii=False))


def cmd_enable() -> None:
    """创建时不把 graph_enabled 同步成 ExtractConfig；更新（PUT）才会同步。

    源码：internal/application/service/knowledgebase.go:560-565
          types/knowledgebase.go:863 IsGraphEnabled() 要求 IndexingStrategy.GraphEnabled
          **且** ExtractConfig != nil && ExtractConfig.Enabled。
    """
    kb_id = load_kb_id()
    status, data = req("PUT", f"/knowledge-bases/{kb_id}", {
        "name": KB_NAME,
        "description": KB_DESC,
        "config": {
            "indexing_strategy": {
                "vector_enabled": True,
                "keyword_enabled": True,
                "wiki_enabled": False,
                "graph_enabled": True,
            },
        },
    })
    print("HTTP", status)
    write("kb-enable.json", {"status": status, "response": data})
    kb = find_kb() or {}
    print("extract_config =", json.dumps(kb.get("extract_config"), ensure_ascii=False))
    print("capabilities   =", json.dumps(kb.get("capabilities"), ensure_ascii=False))


def cmd_reupload() -> None:
    """删掉库里已有的知识再重投——图谱任务只在 post-process 时入队，必须重跑一次。"""
    kb_id = load_kb_id()
    _, data = get(f"/knowledge-bases/{kb_id}/knowledge")
    rows = (data or {}).get("data") if isinstance(data, dict) else []
    for r in rows or []:
        status, resp = req("DELETE", f"/knowledge-bases/{kb_id}/knowledge/{r['id']}")
        print(f"[del {status}] {r.get('title')} {str(resp)[:120]}")
    time.sleep(3)
    cmd_upload()


def cmd_reparse() -> None:
    """POST /knowledge/batch-reparse —— 重跑处理链路（图谱任务只在 post-process 入队）。"""
    kb_id = load_kb_id()
    _, data = get(f"/knowledge-bases/{kb_id}/knowledge")
    rows = (data or {}).get("data") if isinstance(data, dict) else []
    ids = [r["id"] for r in rows or []]
    if not ids:
        raise SystemExit("库里没有知识，先 upload")
    status, resp = post("/knowledge/batch-reparse", {"ids": ids, "kb_id": kb_id})
    print("HTTP", status, str(resp)[:300])
    write("reparse.json", {"status": status, "ids": ids, "response": resp})


def load_kb_id() -> str:
    kb = find_kb()
    if not kb:
        raise SystemExit("找不到知识库，先跑 create")
    return kb["id"]


def cmd_upload() -> None:
    kb_id = load_kb_id()
    _, data = get(f"/knowledge-bases/{kb_id}/knowledge")
    rows = (data or {}).get("data") if isinstance(data, dict) else []
    have = {(r.get("title") or "").strip() for r in rows or []}
    results = []
    for path in CORPUS:
        if path.stem in have:
            print(f"[skip] {path.stem} 已在库")
            continue
        status, resp = post(
            f"/knowledge-bases/{kb_id}/knowledge/manual",
            {"title": path.stem, "content": path.read_text(encoding="utf-8"), "status": "publish"},
        )
        print(f"[{status}] {path.stem}  {str(resp)[:180]}")
        results.append({"title": path.stem, "status": status})
    write("upload.json", results)


def cmd_wait() -> None:
    kb_id = load_kb_id()
    deadline = time.time() + 45 * 60
    last = None
    while time.time() < deadline:
        _, data = get(f"/knowledge-bases/{kb_id}/knowledge")
        rows = (data or {}).get("data") if isinstance(data, dict) else []
        snap = [(r.get("title"), r.get("parse_status")) for r in rows or []]
        if snap != last:
            print(time.strftime("%H:%M:%S"), snap, flush=True)
            last = snap
        if rows and all((r.get("parse_status") or "") in ("completed", "success", "failed")
                        for r in rows):
            print("解析结束：", snap)
            write("wait.json", snap)
            return
        time.sleep(10)
    print("⚠️ 等超时（45 分钟），最后一次快照：", last)
    write("wait.json", last)


def cmd_probe() -> None:
    checks = {
        "总节点数": "MATCH (n) RETURN count(n)",
        "总关系数": "MATCH ()-[r]->() RETURN count(r)",
        "节点按文件分布": "MATCH (n) RETURN n.kg AS kg, count(*) AS nodes ORDER BY nodes DESC",
        "跨文件同名实体（应当存在）":
            "MATCH (n) WITH n.name AS name, count(DISTINCT n.kg) AS k, collect(DISTINCT n.kg) AS ks "
            "WHERE k > 1 RETURN name, k ORDER BY k DESC, name LIMIT 30",
        "跨文件的边（应当为 0）":
            "MATCH (a)-[r]->(b) WHERE a.kg <> b.kg RETURN count(r)",
        "抽样实体在两文件里的节点":
            f"MATCH (n) WHERE n.name IN {json.dumps(OVERLAP_NAMES)} "
            "RETURN n.name, n.kg, size(coalesce(n.chunks, [])) AS chunks ORDER BY n.name, n.kg",
        "抽样实体的邻居（看是否只在同文件内）":
            f"MATCH (a)-[r]->(b) WHERE a.name IN {json.dumps(OVERLAP_NAMES)} "
            "RETURN a.name, a.kg = b.kg AS same_file, type(r), b.name ORDER BY a.name LIMIT 40",
    }
    out = {}
    for label, q in checks.items():
        res = cypher(q)
        out[label] = res
        print(f"===== {label}\n{q}\n{res}\n")
    write("probe.json", out)
    print("写出：", OUT / "probe.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["create", "enable", "upload", "reupload", "reparse",
                                    "wait", "probe", "kb"])
    args = ap.parse_args(sys.argv[1:])
    {"create": cmd_create, "enable": cmd_enable, "upload": cmd_upload, "reupload": cmd_reupload,
     "reparse": cmd_reparse, "wait": cmd_wait, "probe": cmd_probe,
     "kb": lambda: print(json.dumps(find_kb(), ensure_ascii=False, indent=2))}[args.cmd]()


if __name__ == "__main__":
    main()
