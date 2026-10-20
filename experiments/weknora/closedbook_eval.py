#!/usr/bin/env python
"""闭卷基线：同模型、无任何检索工具、直接作答。

动机（2026-10-12 记）：MuSiQue 论文用「Q-only 基线 ≈ 4.6 F1」证明它的题不能靠记忆作答，
但那是 2022 年的模型卡出来的。前沿 LLM 上这条约束是否还成立，直接决定我们这份 30 题
的对比实验能不能声称「答对＝用到了知识库」。本脚本用与主臂相同的模型、相同的答案格式
要求，但不给任何工具、不绑知识库，量出「纯记忆」能拿多少分。

做法：/agent-chat ＋ 一个 allowed_tools=[] 的 agent（没有工具可调 → 只能直答），
请求体 knowledge_base_ids=[] 不绑库。用法：
    python experiments/weknora/closedbook_eval.py run [--variants flash,pro] [--only 21]
    python experiments/weknora/closedbook_eval.py score
"""
from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

import weknora_qa_eval as ev  # noqa: E402  （复用常量与打分口径）

FLASH_MODEL = ev.CHAT_MODEL_ID                      # deepseek-flash（主臂模型）
PRO_MODEL = "ee081caf-43b9-4870-ad26-847cacbbd904"  # DeepSeek V4 Pro（B 臂模型）
VARIANTS = {
    "flash": ("exp-musique-closedbook-flash", FLASH_MODEL),
    "pro": ("exp-musique-closedbook-pro", PRO_MODEL),
}
ANSWER_STYLE = ev.ANSWER_STYLE


def post_sse(path: str, payload: dict, deadline_sec: float = 300.0) -> dict:
    """POST 一个 SSE 端点；返回 event 计数、拼接出的答案与错误信息。"""
    conn = http.client.HTTPConnection(ev.HOST, ev.PORT, timeout=300)
    t0 = time.time()
    kinds: dict[str, int] = {}
    parts: list[str] = []
    error = None
    try:
        conn.request(
            "POST",
            f"{ev.BASE_PATH}{path}",
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"X-API-Key": ev.api_key(), "Content-Type": "application/json; charset=utf-8"},
        )
        resp = conn.getresponse()
        if resp.status != 200:
            return {"status": resp.status, "error": resp.read().decode("utf-8", "replace")[:400],
                    "elapsed": round(time.time() - t0, 2)}
        while True:
            if time.time() - t0 > deadline_sec:
                error = "deadline"
                break
            line = resp.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text.startswith("data:"):
                continue
            try:
                e = json.loads(text[5:].strip())
            except json.JSONDecodeError:
                continue
            rt = str(e.get("response_type"))
            kinds[rt] = kinds.get(rt, 0) + 1
            if rt in ("answer", "final_answer", "content") and e.get("content"):
                parts.append(str(e["content"]))
            if rt == "error":
                error = str(e.get("content"))[:300]
                break
            if rt in ("complete", "final_answer") and e.get("done"):
                break
    except Exception as exc:  # 网络类
        error = f"conn:{exc!r}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return {"status": 200, "kinds": kinds, "stream_answer": "".join(parts),
            "elapsed": round(time.time() - t0, 2), "error": error}


def ensure_agent(name: str, model_id: str) -> str:
    """建/取一个「没有任何工具」的 agent。"""
    _, data = ev.http_json("GET", "/agents?page=1&page_size=100")
    for a in data.get("data") or []:
        if a.get("name") == name:
            return a["id"]
    cfg = dict((json.loads(ev.BUILTIN.read_text(encoding="utf-8"))["builtin-smart-reasoning"].get("config") or {})
               if ev.BUILTIN.exists() else {})
    cfg.update({
        "agent_mode": "smart-reasoning",
        "model_id": model_id,
        "rerank_model_id": ev.RERANK_MODEL_ID,
        "allowed_tools": [],
        "thinking": False,
        "max_iterations": 3,
        "system_prompt": ("You are a helpful assistant answering factual questions from your own knowledge. "
                          "You have no retrieval tools. Do not ask clarifying questions."),
    })
    status, body = ev.http_json("POST", "/agents", {
        "name": name, "description": "闭卷基线：同模型、无检索工具、直接作答",
        "avatar": "", "config": cfg,
    })
    print(f"[create {status}] {name}: {json.dumps(body, ensure_ascii=True)[:160]}", flush=True)
    _, data = ev.http_json("GET", "/agents?page=1&page_size=100")
    for a in data.get("data") or []:
        if a.get("name") == name:
            return a["id"]
    raise SystemExit(f"建 agent 失败：{name}")


def cmd_run() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="flash,pro")
    ap.add_argument("--only", default="")
    ap.add_argument("--deadline-sec", type=float, default=300.0)
    ap.add_argument("--rounds", type=int, default=1, help="跑几轮（第 1 轮写 qa-closedbook-<variant>.jsonl，之后 -r2/-r3）")
    args = ap.parse_args(sys.argv[2:])

    questions = ev.load_questions(None)
    only = {int(x) for x in args.only.split(",") if x.strip()}
    todo = [(i, q) for i, q in enumerate(questions, 1) if not only or i in only]

    for variant in [v.strip() for v in args.variants.split(",") if v.strip()]:
        name, model_id = VARIANTS[variant]
        agent_id = ensure_agent(name, model_id)
        print(f"\n=== closed-book[{variant}] model={model_id} agent={agent_id} 题数={len(todo)} 轮数={args.rounds} ===", flush=True)
        for rnd in range(1, args.rounds + 1):
          out_path = ev.OUT_DIR / f"qa-closedbook-{variant}{'' if rnd == 1 else f'-r{rnd}'}.jsonl"
          rows = []
          for i, q in todo:
            _, sess = ev.http_json("POST", "/sessions", {"title": f"closedbook-{variant}-r{rnd}-{i:02d}"})
            sid = (sess.get("data") or {}).get("id")
            r = post_sse(f"/agent-chat/{sid}", {
                "query": q["question"] + ANSWER_STYLE,
                "knowledge_base_ids": [],          # 不绑库
                "agent_enabled": True,
                "agent_id": agent_id,
                "disable_title": True,
                "channel": "api",
            }, args.deadline_sec)
            msg = ev.fetch_answer(sid)
            row = {
                "arm": f"closedbook-{variant}", "round": rnd, "n": i, "id": q["id"], "hops": q["hops"],
                "question": q["question"], "gold": q["answer"], "aliases": q.get("answer_aliases") or [],
                "session_id": sid,
                "pred": msg.get("content") or r.get("stream_answer") or "",
                "pred_from": "message" if msg.get("content") else "stream",
                "refs": [], "tool_calls": [], "tool_events": [],
                "elapsed_sec": r.get("elapsed"), "events": sum((r.get("kinds") or {}).values()),
                "error": r.get("error"), "completed": msg.get("completed"),
            }
            rows.append(row)
            f1, em = ev.best_match(row["pred"], row["gold"], row["aliases"])
            print(f"[{variant} r{rnd} {i:02d}/{len(questions)}] hops={row['hops']} {row['elapsed_sec']}s "
                  f"f1={f1:.2f} pred={row['pred'][:56]!r} gold={row['gold'][:26]!r}", flush=True)
          rows.sort(key=lambda x: x["n"])
          out_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
          print(f"done -> {out_path}", flush=True)


def cmd_score() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", nargs="*", default=["qa-closedbook-flash.jsonl", "qa-closedbook-pro.jsonl"])
    args = ap.parse_args(sys.argv[2:])
    out = {}
    for f in args.files:
        path = ev.OUT_DIR / f
        if not path.exists():
            continue
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        by_hop: dict[int, list[tuple[float, float]]] = {}
        alls = []
        for r in rows:
            f1, em = ev.best_match(r["pred"], r["gold"], r.get("aliases") or [])
            alls.append((f1, em))
            by_hop.setdefault(int(r["hops"]), []).append((f1, em))
        def agg(xs):
            n = len(xs) or 1
            return {"n": len(xs), "f1": round(sum(x[0] for x in xs) / n, 4), "em": round(sum(x[1] for x in xs) / n, 4)}
        out[f] = {"overall": agg(alls), "by_hops": {str(h): agg(v) for h, v in sorted(by_hop.items())}}
    print(json.dumps(out, ensure_ascii=False, indent=2))


COMMANDS = {"run": cmd_run, "score": cmd_score}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法：closedbook_eval.py [run|score]")
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
