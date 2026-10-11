"""MuSiQue 30 题 × 三臂问答对照：建 agent → 逐题提问 → 打分。

三臂（都指向同一个 MuSiQue 库）：
    quick    = 经典 RAG 管线（agent_mode=quick-answer，无工具循环）
    smart    = Agentic RAG（agent_mode=smart-reasoning，agent_type=rag-qa，工具＝原始块一族）
    wiki     = Agentic RAG 的 wiki 路（agent_type=wiki-qa，工具＝wiki_search／wiki_read_page／read_document）

为什么自建 agent：内置 agent 的 model_id 为空，直接问答会报
`chat model is not configured: please set model_id on agent`。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_qa_eval.py agents
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_qa_eval.py ask --arm quick --limit 1
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_qa_eval.py score

口径：
    · 每题一个<b>新会话</b>（避免历史污染）；
    · 时延＝从 POST 到流里出现 done/error 的墙钟；
    · 答案取该会话最后一条 assistant 消息的 content；引用取它的 knowledge_references；
    · 打分照论文：SQuAD 式归一化后算 EM 与 F1（词级），并按 2/3/4 跳分层。
"""

from __future__ import annotations

import argparse
import collections
import glob
import http.client
import json
import re
import string
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(r"D:\lida-data\vscode\WeKnora\.api-key")
HOST, PORT = "localhost", 8080
BASE_PATH = "/api/v1"

KB_ID = "e1874078-71a8-4d7f-8d8a-95ca313334ed"
CHAT_MODEL_ID = "4b0ab03b-be54-4658-a3f0-5338db734402"  # deepseek-flash
RERANK_MODEL_ID = "fcbcf3a1-ea46-4011-8fda-2c07c2b8f7f6"  # qwen3-rerank (DashScope)

QUESTIONS = ROOT / "data" / "tmp" / "musique" / "slice-30-strat" / "questions.jsonl"
BUILTIN = ROOT / "data" / "tmp" / "weknora-musique" / "_builtin-agents.json"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-musique"

# arm -> (agent 名, 内置 agent 模板, 端点, 备注)
# 关键：/knowledge-chat 永远走经典管线（qaModeNormal），agent 循环只在 /agent-chat（qaModeAgent）
ARMS: dict[str, tuple[str, str, str, str]] = {
    "quick": ("exp-musique-quick", "builtin-quick-answer", "/knowledge-chat",
              "经典 RAG：query→检索→重排→一次生成"),
    "smart": ("exp-musique-smart", "builtin-smart-reasoning", "/agent-chat",
              "Agentic：search_knowledge/read_document/list_documents"),
    "wiki": ("exp-musique-wiki", "builtin-wiki-researcher", "/agent-chat",
             "Agentic：wiki_search/wiki_read_page/read_document"),
    "hybrid": ("exp-musique-hybrid", "preset:hybrid-rag-wiki", "/agent-chat",
               "Agentic：wiki 建导航 ＋ 分块精确引用（WeKnora 自带预设）"),
    "smart4": ("exp-musique-smart-v4", "preset:hybrid-rag-wiki", "/agent-chat",
               "Agentic：与 smart-v3 同配置，只把「数字要回原文核实」改成「近似值可直接采用」"),
    "quicknr": ("exp-musique-quick-norerank", "builtin-quick-answer", "/knowledge-chat",
                "A 臂复跑用：经典管线、**不挂重排**（因为 exp-musique-quick 后来挂上了 rerank）"),
    "kbflash": ("exp-musique-quick-kb-flash", "builtin-quick-answer", "/knowledge-chat",
                "B 臂 top-k 10 ＋ rerank ＋ 无闸门改写提示词，作答＝flash（quickprokb 的模型对照片）"),
    "kbproflash": ("exp-musique-quick-kb-proflash", "builtin-quick-answer", "/knowledge-chat",
                   "同 quickprokb，但查询理解钉回 flash（分离「作答模型」与「改写模型」各自的作用）"),
    "quickprokb": ("exp-musique-quick-pro-kb", "builtin-quick-answer", "/knowledge-chat",
                   "同 quickpro（B 臂 top-k 10 ＋ rerank ＋ Pro 作答），另用 rewrite_prompt_system 钉死 kb_search"),
    "quickpro": ("exp-musique-quick-pro", "builtin-quick-answer", "/knowledge-chat",
                 "B 臂模型对照：同配置（top-k 10 ＋ rerank）只换 DeepSeek V4 Pro 作答"),
}

# WeKnora 自带预设 config/agent_type_presets.yaml 的 hybrid-rag-wiki 条目
HYBRID_PRESET = {
    "agent_mode": "smart-reasoning",
    "agent_type": "hybrid-rag-wiki",
    "allowed_tools": [
        "wiki_search", "wiki_read_page", "search_knowledge",
        "read_document", "list_documents", "wiki_flag_issue",
    ],
    "max_iterations": 40,
    "temperature": 0.7,
    "kb_selection_mode": "all",
    "faq_priority_enabled": True,
    "citation_enabled": True,
    "thinking": False,
    "model_id": CHAT_MODEL_ID,
    "rerank_model_id": RERANK_MODEL_ID,
}


def api_key() -> str:
    return KEY_FILE.read_text(encoding="utf-8").strip()


def http_json(method: str, path: str, payload: dict | None = None, timeout: int = 120):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}{BASE_PATH}{path}",
        data=body,
        method=method,
        headers={"X-API-Key": api_key(), "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def load_questions(limit: int | None = None) -> list[dict]:
    rows = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def write_json(name: str, obj) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- agents


def cmd_agents() -> None:
    if not BUILTIN.exists():
        raise SystemExit(f"缺内置 agent 快照：{BUILTIN}")
    builtin = json.loads(BUILTIN.read_text(encoding="utf-8"))
    _, existing = http_json("GET", "/agents?page=1&page_size=100")
    have = {a.get("name") for a in (existing.get("data") or [])}

    created = {}
    for arm, (name, tpl, _endpoint, note) in ARMS.items():
        if tpl.startswith("preset:"):
            cfg = dict(HYBRID_PRESET)
        else:
            cfg = dict((builtin[tpl].get("config") or {}))
        cfg["model_id"] = CHAT_MODEL_ID          # 关键：内置为空，必须显式给
        cfg["rerank_model_id"] = RERANK_MODEL_ID  # 重排按 agent 指定，不继承租户级
        if name in have:
            print(f"[skip] {arm}: 已存在 {name}")
            created[arm] = {"name": name, "reused": True}
            continue
        payload = {
            "name": name,
            "description": f"MuSiQue 对照实验臂 {arm}：{note}",
            "avatar": "",
            "config": cfg,
        }
        status, data = http_json("POST", "/agents", payload)
        print(f"[{status}] {arm} -> {name}: {json.dumps(data, ensure_ascii=True)[:180]}")
        created[arm] = {"name": name, "status": status}
    write_json("qa-agents.json", {"created": created, "arms": ARMS})


def resolve_agent(arm: str) -> str:
    name = ARMS[arm][0]
    _, data = http_json("GET", "/agents?page=1&page_size=100")
    for a in data.get("data") or []:
        if a.get("name") == name:
            return a["id"]
    raise SystemExit(f"找不到 agent {name}，先跑 agents")


# ---------------------------------------------------------------- ask


ANSWER_STYLE = "\n\nAnswer with only the answer span itself, in English. No explanation."


def stream_chat(session_id: str, query: str, agent_id: str, endpoint: str, deadline_sec: float) -> dict:
    """POST /knowledge-chat，逐行读 SSE，遇到 done/error 即返回。"""
    body = json.dumps(
        {
            "query": query + ANSWER_STYLE,
            "knowledge_base_ids": [KB_ID],
            "agent_enabled": True,
            "agent_id": agent_id,
            "disable_title": True,
            "channel": "api",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    # socket 超时只影响"两次事件之间"的等待；产品里真有超长停顿，别让客户端先断
    conn = http.client.HTTPConnection(HOST, PORT, timeout=300)
    t0 = time.time()
    events: list[dict] = []
    event_types: collections.Counter = collections.Counter()
    answer_parts: list[str] = []
    refs: list = []
    tool_calls: list = []
    tool_events: list[dict] = []          # 证据召回用：工具返回的原文/片段
    pending_tool = ""
    error = None
    try:
        conn.request(
            "POST",
            f"{BASE_PATH}{endpoint}/{session_id}",
            body=body,
            headers={
                "X-API-Key": api_key(),
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "text/event-stream",
            },
        )
        resp = conn.getresponse()
        if resp.status != 200:
            return {"status": resp.status, "error": resp.read().decode("utf-8", "replace")[:2000],
                    "elapsed": time.time() - t0, "events": 0}
        while True:
            if time.time() - t0 > deadline_sec:
                error = "deadline"
                break
            # 必须用 resp.readline()：直接读 resp.fp 会绕过 chunked 解码
            try:
                line = resp.readline()
            except Exception as exc:  # socket 超时等
                error = f"read:{exc!r}"
                break
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue
            events.append(ev)
            rt = ev.get("response_type")
            event_types[str(rt)] += 1
            if rt == "error":
                error = str(ev.get("content"))[:500]
                break  # 服务端已报错，没必要继续等
            if ev.get("content") and rt in ("answer", "final_answer", "content"):
                answer_parts.append(str(ev["content"]))
            if ev.get("knowledge_references"):
                refs = ev["knowledge_references"]
            if rt == "tool_call":
                data = ev.get("data") or {}
                name = data.get("name") or data.get("tool") or ""
                if not name:
                    m = re.search(r"tool:\s*(\S+)", str(ev.get("content") or ""))
                    name = m.group(1) if m else ""
                tool_calls.append(name or "?")
                pending_tool = name or "?"
            elif ev.get("tool_calls"):
                tool_calls.extend(ev["tool_calls"])
            if rt == "tool_result":
                data = ev.get("data") or {}
                out = data.get("output")
                if out is None:
                    out = ev.get("content") or ""
                text = str(out)
                tool_events.append(
                    {
                        "tool": pending_tool,
                        "success": data.get("success"),
                        "duration_ms": data.get("duration_ms"),
                        # 证据召回用：抓来源文档 id 与页链接（不依赖截断后的正文）
                        "knowledge_ids": sorted(set(re.findall(r'knowledge_id="([0-9a-f\-]{36})"', text))),
                        "links": re.findall(r"\[\[([^\]|]+)\|([^\]]+)\]\]", text)[:60],
                        "output_len": len(text),
                        "output": text[:2000],
                    }
                )
            # 注意：首个事件是 agent_query 且 done=true，不能当结束；服务端答完会关流
            if rt in ("complete", "final_answer") and ev.get("done"):
                break
    except Exception as exc:
        error = f"conn:{exc!r}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {
        "status": 200,
        "elapsed": round(time.time() - t0, 2),
        "events": len(events),
        "event_types": dict(event_types),
        "stream_answer": "".join(answer_parts),
        "refs": refs,
        "tool_calls": tool_calls,
        "tool_events": tool_events,
        "error": error,
        "last_event": events[-1] if events else None,
    }


def fetch_answer(session_id: str) -> dict:
    _, data = http_json("GET", f"/messages/{session_id}/load")
    rows = data.get("data") or []
    assistants = [r for r in rows if (r.get("role") or "") == "assistant"]
    if not assistants:
        return {"content": "", "refs": [], "completed": False, "messages": len(rows)}
    last = assistants[-1]
    return {
        "content": last.get("content") or "",
        "refs": last.get("knowledge_references") or [],
        "completed": bool(last.get("is_completed")),
        "message_id": last.get("id"),
        "messages": len(rows),
    }


def cmd_ask() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=sorted(ARMS))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only", default="", help="只重跑这些题号（逗号分隔），结果合并回原文件")
    ap.add_argument("--deadline-sec", type=float, default=900.0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--retries", type=int, default=2, help="遇到网络类失败自动重试次数")
    args = ap.parse_args(sys.argv[2:])

    agent_id = resolve_agent(args.arm)
    endpoint = ARMS[args.arm][2]
    questions = load_questions(args.limit)
    out_name = f"qa-{args.arm}{('-' + args.tag) if args.tag else ''}.jsonl"
    out_path = OUT_DIR / out_name
    only = {int(x) for x in args.only.split(",") if x.strip()} if args.only else set()
    if only:
        questions = [q for i, q in enumerate(questions, 1) if i in only]
    print(f"arm={args.arm} endpoint={endpoint} agent_id={agent_id} 题数={len(questions)} 输出={out_name}", flush=True)

    merged: dict[int, dict] = {}
    if only and out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                merged[r["n"]] = r

    with out_path.open("w", encoding="utf-8") as fh:
        todo = [(i, q) for i, q in enumerate(load_questions(args.limit), 1) if (not only) or (i in only)]
        for i, q in todo:
            attempt = 0
            while True:
                status, sess = http_json("POST", "/sessions", {"title": f"{args.arm}-{i:02d}"})
                sid = (sess.get("data") or {}).get("id")
                r = stream_chat(sid, q["question"], agent_id, endpoint, args.deadline_sec)
                msg = fetch_answer(sid)
                transient = r.get("error") and any(
                    k in str(r["error"]) for k in ("EOF", "SSRF", "DNS", "timed out", "connection")
                )
                if not (transient and attempt < args.retries):
                    break
                attempt += 1
                print(f"    [retry {attempt}] #{i:02d} 失败：{str(r['error'])[:90]}", flush=True)
                time.sleep(5 * attempt)
            row = {
                "arm": args.arm,
                "n": i,
                "id": q["id"],
                "hops": q["hops"],
                "question": q["question"],
                "gold": q["answer"],
                "aliases": q.get("answer_aliases") or [],
                "supporting_titles": q.get("supporting_titles") or [],
                "session_id": sid,
                "pred": msg.get("content") or r.get("stream_answer") or "",
                "pred_from": "message" if msg.get("content") else "stream",
                "refs": msg.get("refs") or r.get("refs") or [],
                "tool_calls": r.get("tool_calls") or [],
                "tool_events": r.get("tool_events") or [],
                "elapsed_sec": r.get("elapsed"),
                "events": r.get("events"),
                "error": r.get("error"),
                "completed": msg.get("completed"),
            }
            merged[i] = row
            if not only:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
            print(
                f"[{i:02d}/{len(questions)}] hops={row['hops']} {row['elapsed_sec']}s "
                f"pred={row['pred'][:80]!r} gold={row['gold'][:40]!r} err={row['error']}",
                flush=True,
            )
    if only:
        # 把没重跑的题写回，保持整表完整
        with out_path.open("w", encoding="utf-8") as fh2:
            for k in sorted(merged):
                fh2.write(json.dumps(merged[k], ensure_ascii=False) + "\n")
    print("done ->", out_path)


# ---------------------------------------------------------------- score


def normalize(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"<[^>]{0,400}>", " ", s)          # 去掉 <kb doc=... /> 之类的引用标签
    s = re.sub(r"\[[^\]]{0,120}\]\([^)]*\)", " ", s)  # 去掉 markdown 链接
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def f1_em(pred: str, gold: str) -> tuple[float, float]:
    p, g = normalize(pred).split(), normalize(gold).split()
    if not p or not g:
        return (float(p == g), float(p == g))
    common = collections.Counter(p) & collections.Counter(g)
    same = sum(common.values())
    if same == 0:
        return 0.0, 0.0
    prec, rec = same / len(p), same / len(g)
    return 2 * prec * rec / (prec + rec), float(p == g)


def best_match(pred: str, gold: str, aliases: list[str]) -> tuple[float, float]:
    """对金答案及其别名取最好分（MuSiQue 官方评测同口径）。"""
    cands = [gold] + [a for a in (aliases or []) if a]
    scores = [f1_em(pred, c) for c in cands]
    return max(s[0] for s in scores), max(s[1] for s in scores)


def cmd_score() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=None)
    args = ap.parse_args(sys.argv[2:])
    files = [OUT_DIR / f for f in args.files] if args.files else sorted(OUT_DIR.glob("qa-*.jsonl"))
    summary = {}
    for path in files:
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not rows:
            continue
        by_hop: dict[int, list[tuple[float, float]]] = collections.defaultdict(list)
        all_scores: list[tuple[float, float]] = []
        hit = 0
        for r in rows:
            f1, em = best_match(r["pred"], r["gold"], r.get("aliases") or [])
            all_scores.append((f1, em))
            by_hop[r["hops"]].append((f1, em))
            pred_n, gold_n = normalize(r["pred"]), normalize(r["gold"])
            if gold_n and gold_n in pred_n:
                hit += 1
        def agg(xs):
            n = len(xs) or 1
            return {"n": len(xs), "f1": round(sum(x[0] for x in xs) / n, 4), "em": round(sum(x[1] for x in xs) / n, 4)}
        lat = [r["elapsed_sec"] for r in rows if r.get("elapsed_sec")]
        summary[path.name] = {
            "overall": agg(all_scores),
            "by_hops": {str(h): agg(v) for h, v in sorted(by_hop.items())},
            "answer_contains_gold_rate": round(hit / len(rows), 4),
            "latency_sec": {
                "mean": round(sum(lat) / len(lat), 1) if lat else None,
                "min": min(lat) if lat else None,
                "max": max(lat) if lat else None,
            },
            "errors": sum(1 for r in rows if r.get("error")),
            "mean_refs": round(sum(len(r.get("refs") or []) for r in rows) / len(rows), 2),
            "mean_tool_calls": round(sum(len(r.get("tool_calls") or []) for r in rows) / len(rows), 2),
        }
    write_json("qa-score.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


COMMANDS = {"agents": cmd_agents, "ask": cmd_ask, "score": cmd_score}


# ---------------------------------------------------------------- recall

DOCKER = r"C:\Users\lida\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"


def psql_lines(sql: str) -> list[str]:
    out = subprocess.run(
        [DOCKER, "exec", "WeKnora-postgres", "psql", "-U", "postgres", "-d", "WeKnora", "-t", "-A", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
    )
    return [l for l in (out.stdout or "").splitlines() if l.strip()]


def norm_title(t: str) -> str:
    """段落标题归一：去掉语料文件的 __N 后缀、统一大小写与空白。"""
    t = re.sub(r"__\d+$", "", (t or "").strip())
    return re.sub(r"\s+", " ", t).lower()


def collect_knowledge_ids(node, out: set[str]) -> None:
    """递归收集任意工具结果 JSON 里出现过的 knowledge_id（不含 knowledge_base_id）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "knowledge_id" and isinstance(v, str) and len(v) == 36:
                out.add(v)
            else:
                collect_knowledge_ids(v, out)
    elif isinstance(node, list):
        for v in node:
            collect_knowledge_ids(v, out)


def agent_steps_touches(session_ids: list[str]) -> dict[str, set[str]]:
    """从 messages.agent_steps 里取"这一题真正检索到/读到的段落 id"。

    这是修 C／E 召回口径的关键：search_knowledge 的 SSE 工具输出被压成
    details omitted，但完整结果落在库里的 agent_steps（每条结果带
    knowledge_id / knowledge_title / content）。
    """
    out: dict[str, set[str]] = {}
    if not session_ids:
        return out
    quoted = ",".join("'" + s + "'" for s in session_ids)
    sql = (
        "select session_id || E'\\t' || replace(coalesce(agent_steps::text,'[]'), E'\\n', ' ') "
        f"from messages where role='assistant' and session_id in ({quoted});"
    )
    for line in psql_lines(sql):
        sid, _, raw = line.partition("\t")
        ids: set[str] = set()
        try:
            collect_knowledge_ids(json.loads(raw), ids)
        except json.JSONDecodeError:
            pass
        # 兜底：工具结果里常有被转义的 XML（如 wiki_read_page 的 <sources>），
        # JSON 解析成功也扫不到，所以一律再按文本捞一遍 knowledge_id。
        ids |= set(
            re.findall(
                r"knowledge_id[^0-9a-f]{0,8}([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
                raw,
            )
        )
        out.setdefault(sid, set()).update(ids)
    return out


def cmd_recall() -> None:
    """证据召回：金标支撑段落有没有在"进入上下文/被读到的来源"里出现。

    口径（v1，偏保守）：
      · quick 类：用 assistant 消息的 knowledge_references（每条含 knowledge_id/knowledge_title）——即真正进上下文的块；
      · agent 类：用工具输出里 <sources><source knowledge_id=.../></sources> 暴露的来源文档 id（来自 wiki_read_page）；
        search_knowledge 的输出被引擎截断成 "details omitted"，故 smart 臂这条口径天然吃亏，报告里要注明。
      · 三者都按"段落"（＝一个知识条目）比对，标题去掉 __N 后缀后匹配。
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--out", default="qa-recall.json")
    args = ap.parse_args(sys.argv[2:])

    # knowledge_id -> 段落标题
    id2title = {}
    for line in psql_lines(
        f"select id || E'\\t' || coalesce(title,'') from knowledges "
        f"where knowledge_base_id='{KB_ID}' and deleted_at is null;"
    ):
        kid, _, title = line.partition("\t")
        id2title[kid] = title
    title2ids: dict[str, set[str]] = collections.defaultdict(set)
    for kid, title in id2title.items():
        title2ids[norm_title(title)].add(kid)

    gold_questions = {q["id"]: q for q in load_questions()}
    files = [OUT_DIR / f for f in args.files] if args.files else sorted(OUT_DIR.glob("qa-*.jsonl"))
    summary: dict[str, dict] = {}
    for path in files:
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not rows:
            continue
        per_hop: dict[int, list[float]] = collections.defaultdict(list)
        recs: list[float] = []
        any_hit = all_hit = 0
        no_gold_ids = 0
        steps_touch = agent_steps_touches([r["session_id"] for r in rows if r.get("session_id")])
        for r in rows:
            gold = gold_questions.get(r["id"]) or {}
            gold_titles = [norm_title(t) for t in (gold.get("supporting_titles") or [])]
            gold_ids: set[str] = set()
            for t in gold_titles:
                gold_ids |= title2ids.get(t, set())
            if not gold_ids:
                no_gold_ids += 1
                continue
            touched: set[str] = set()
            for ref in r.get("refs") or []:
                kid = ref.get("knowledge_id") or ""
                if kid:
                    touched.add(kid)
                elif ref.get("knowledge_title"):
                    touched |= title2ids.get(norm_title(ref["knowledge_title"]), set())
            for te in r.get("tool_events") or []:
                kids = te.get("knowledge_ids")
                if kids is None:  # 旧格式：没单独抓，就从留存正文里现挖
                    kids = re.findall(r'knowledge_id="([0-9a-f\-]{36})"', te.get("output") or "")
                touched |= set(kids)
            touched |= steps_touch.get(r.get("session_id") or "", set())
            rec = len(gold_ids & touched) / len(gold_ids)
            recs.append(rec)
            per_hop[r["hops"]].append(rec)
            any_hit += 1 if rec > 0 else 0
            all_hit += 1 if rec >= 1 else 0
        n = len(recs) or 1
        summary[path.name] = {
            "n_scored": len(recs),
            "n_无法映射金标": no_gold_ids,
            "口径": "金标段落 id ∩ （quick: 进上下文的 knowledge_references；agent: agent_steps 里所有工具结果的 knowledge_id ＋ 工具输出里的 <sources>）",
            "mean_recall": round(sum(recs) / n, 4),
            "any_hit_rate": round(any_hit / n, 4),
            "all_hit_rate": round(all_hit / n, 4),
            "by_hops": {str(h): round(sum(v) / len(v), 4) for h, v in sorted(per_hop.items())},
        }
    write_json(args.out, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


COMMANDS["recall"] = cmd_recall


# ---------------------------------------------------------------- tokens

LOG_GLOB = str(ROOT / "data" / "tmp" / "weknora-logs" / "*.log")
RID_RE = re.compile(r"\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]")
TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)")
SID_RE = re.compile(r'session_id=\\?"?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})')
USE_RE = re.compile(
    r"\[LLM Usage\] model=([^,]+), purpose=([^,]*), prompt_prefix=[^,]*, prompt_tokens=(\d+), "
    r"completion_tokens=(\d+), total_tokens=(\d+)"
)


def cmd_tokens() -> None:
    """按会话（＝每题）汇总 LLM 用量，区分输入（prompt）与输出（completion）。

    口径：日志 [LLM Usage] 行按 (时间戳, purpose, prompt_tokens, completion_tokens) 去重
    （两个日志文件有重复行），用同一条日志行里的 request id 关联到 session_id。
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=None)
    ap.add_argument("--out", default="qa-tokens.json")
    args = ap.parse_args(sys.argv[2:])

    rid2sid: dict[str, str] = {}
    usage: dict[str, list[int]] = {}
    seen: set[tuple] = set()
    for path in sorted(glob.glob(LOG_GLOB)):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                rid = RID_RE.search(line)
                if rid:
                    sid = SID_RE.search(line)
                    if sid:
                        rid2sid[rid.group(1)] = sid.group(1)
                if "[LLM Usage]" not in line:
                    continue
                u = USE_RE.search(line)
                if not u or not rid:
                    continue
                ts = TS_RE.search(line)
                key = (ts.group(1) if ts else line[:24], u.group(2), u.group(3), u.group(4))
                if key in seen:
                    continue
                seen.add(key)
                r = usage.setdefault(rid.group(1), [0, 0, 0, 0])
                r[0] += int(u.group(3))   # prompt
                r[1] += int(u.group(4))   # completion
                r[2] += int(u.group(5))   # total
                r[3] += 1                 # calls

    per_session: dict[str, list[int]] = {}
    for rid, (p, c, t, n) in usage.items():
        sid = rid2sid.get(rid)
        if not sid:
            continue
        r = per_session.setdefault(sid, [0, 0, 0, 0])
        r[0] += p
        r[1] += c
        r[2] += t
        r[3] += n

    files = [OUT_DIR / f for f in args.files] if args.files else sorted(OUT_DIR.glob("qa-*.jsonl"))
    summary: dict[str, dict] = {}
    for path in files:
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not rows:
            continue
        q: list[list[int]] = []
        missing = 0
        for r in rows:
            v = per_session.get(r.get("session_id") or "")
            if v:
                q.append(v)
            else:
                missing += 1
        if not q:
            summary[path.name] = {"n": len(rows), "无用量记录": missing}
            continue

        def med(idx: int) -> int:
            xs = sorted(v[idx] for v in q)
            return xs[len(xs) // 2]

        def mean(idx: int) -> int:
            return round(sum(v[idx] for v in q) / len(q))

        summary[path.name] = {
            "n": len(q),
            "无用量记录": missing,
            "每题 prompt 中位": med(0), "每题 prompt 均值": mean(0), "每题 prompt 最大": max(v[0] for v in q),
            "每题 completion 中位": med(1), "每题 completion 均值": mean(1),
            "每题 total 中位": med(2), "每题 total 均值": mean(2), "每题 total 最大": max(v[2] for v in q),
            "每题 LLM 调用中位": med(3), "每题 LLM 调用均值": mean(3),
            "该臂总 token": sum(v[2] for v in q),
            "该臂总 prompt": sum(v[0] for v in q),
            "该臂总 completion": sum(v[1] for v in q),
        }
    write_json(args.out, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- no-intent arm

# 把查询理解阶段的意图判定钉死成 kb_search 的 system prompt 覆写（走 agent 配置的
# rewrite_prompt_system，经典管线在 session_knowledge_qa.go:173 会应用它）。
# 动机：默认 rewrite 提示词把「unlikely in the knowledge base」当判据，而它看不见库；
# 一旦判成 web_search 且 web 未启用，NeedsRetrieval() 会让整轮检索被跳过（见 0045 §7.29）。
NOINTENT_REWRITE_SYSTEM = """You are a query rewriter for a knowledge-base question answering session.

This session answers questions ONLY from the user's personal knowledge base. There is NO internet access. Every question must therefore be answered from the knowledge base, and the intent is ALWAYS `kb_search`.

## Task 1: Query Understanding
Rewrite the user's question so it is self-contained:
- Resolve pronouns and complete omitted information using the conversation history.
- Preserve the original meaning, the question type, and the language.
- CRITICAL: keep every named entity, keyword and search term. Do NOT turn the question into a meta-instruction such as "please search the knowledge base for ...".

## Task 2: Intent Classification
Output `kb_search` for every question. Never output `web_search`, `greeting`, `chitchat`, `follow_up`, `image_only`, `doc_only`, `summarize` or `clarification`: this session has no internet access, no image, no attachment, and no follow-up candidate.

## Output Format
Output ONLY a single JSON object. No markdown, no code fences, no extra text.
{"rewrite_query":"string","intent":"kb_search","image_description":""}

`intent` MUST be exactly "kb_search". `image_description` MUST be an empty string.

## Examples
Input: "How do I rotate an API key?"
Output: {"rewrite_query":"How do I rotate an API key?","intent":"kb_search","image_description":""}

## Conversation History
{{conversation}}
"""


def cmd_mkarm() -> None:
    """从既有 agent 克隆配置建/更新一个对照 agent（只改点名的那几个字段）。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="源 agent 名称")
    ap.add_argument("--name", required=True, help="目标 agent 名称")
    ap.add_argument("--nointent", action="store_true", help="装上强制 kb_search 的改写提示词")
    ap.add_argument("--model-id", default="", help="覆盖作答模型（留空则沿用源 agent）")
    ap.add_argument("--qmodel-id", default="", help="覆盖查询理解专用模型（留空则沿用作答模型）")
    args = ap.parse_args(sys.argv[2:])

    _, data = http_json("GET", "/agents?page=1&page_size=100")
    agents = data.get("data") or []
    src = next((a for a in agents if a.get("name") == args.src), None)
    if src is None:
        raise SystemExit(f"source agent not found: {args.src}")

    cfg = json.loads(json.dumps(src.get("config") or {}))  # deep copy
    if args.nointent:
        cfg["rewrite_prompt_system"] = NOINTENT_REWRITE_SYSTEM
    if args.model_id:
        cfg["model_id"] = args.model_id
    if args.qmodel_id:
        cfg["query_understand_model_id"] = args.qmodel_id

    payload = {
        "name": args.name,
        "description": f"MuSiQue 对照 agent（克隆自 {args.src}）",
        "avatar": src.get("avatar") or "",
        "config": cfg,
    }
    dst = next((a for a in agents if a.get("name") == args.name), None)
    if dst:
        status, body = http_json("PUT", f"/agents/{dst['id']}", payload)
        print(f"[PUT {status}] {args.name} (id={dst['id']})")
    else:
        status, body = http_json("POST", "/agents", payload)
        print(f"[POST {status}] {args.name}: {json.dumps(body, ensure_ascii=True)[:200]}")

    showed = {k: cfg.get(k) for k in (
        "agent_mode", "agent_type", "model_id", "rerank_model_id",
        "embedding_top_k", "rerank_top_k", "enable_rewrite",
    )}
    showed["rewrite_prompt_system"] = f"<{len(cfg.get('rewrite_prompt_system') or '')} chars>"
    print(json.dumps(showed, ensure_ascii=False, indent=2))
    write_json("qa-mkarm.json", {"from": args.src, "name": args.name, "status": status, "config": showed})


COMMANDS["mkarm"] = cmd_mkarm

COMMANDS["tokens"] = cmd_tokens

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法：weknora_qa_eval.py [agents|ask|score]")
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
