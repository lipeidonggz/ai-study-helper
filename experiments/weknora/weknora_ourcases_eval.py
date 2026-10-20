"""我们自己的 RAG 用例 × WeKnora 三臂（wiki / smart / hybrid）直连对照。

与 weknora_qa_eval.py（MuSiQue 专用）的区别：
  · 题目来自 backend/eval/cases/rag/*.json，判分依据是 case 里的 annotation.golden_answer
    （判定模板：判 pass / 判 fail 情形），不是 SQuAD 式 F1；
  · 不注入 ANSWER_STYLE（MuSiQue 要求只吐答案片段，我们的用例要的是产品式完整回答）；
  · 知识库按用例集合指定（当前只支持 a5，指向现成的 A5 wiki 库）。

用法（仓库根目录）：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_ourcases_eval.py agents
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_ourcases_eval.py ask --set a5 --arm wiki
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\weknora_ourcases_eval.py show --set a5

口径：
  · 每题每题一个**新会话**；答案取该会话最后一条 assistant 消息；引用取它的 knowledge_references；
  · 原始行落 data/tmp/weknora-ourcases/qa-<arm>.jsonl（含 tool_calls / tool_events / 时延）；
  · 只走本地 8080，api key 不打印。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import weknora_qa_eval as W  # noqa: E402

CASES_DIR = ROOT / "backend" / "eval" / "cases" / "rag"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-ourcases"

# 用例集合 -> (KB 名, KB id, 题目 id 列表)
# cmp 集合的库是 weknora_cmp_kb.py 建的跨源库，id 从 data/tmp/weknora-ourcases/cmp-kb.json 读
def _cmp_kb() -> tuple[str, str]:
    p = OUT_DIR / "cmp-kb.json"
    if not p.exists():
        raise SystemExit(f"缺 {p}，先跑 weknora_cmp_kb.py create")
    kb = json.loads(p.read_text(encoding="utf-8"))["kb"]
    return kb["name"], kb["id"]


SETS: dict[str, tuple[str, str, list[str]]] = {
    "a5": (
        "A5 · How we contain Claude across products",
        "8990fb05-2432-40a0-b731-04e7fad995da",
        [f"rag-a5-{i:03d}" for i in range(1, 6)],
    ),
}
if (OUT_DIR / "cmp-kb.json").exists():
    _n, _i = _cmp_kb()
    SETS["cmp"] = (_n, _i, [f"rag-cmp-{i:03d}" for i in range(1, 8)])
    SETS["cov"] = (_n, _i, ["rag-cmp-002", "rag-cmp-006", "rag-cmp-007"])
    SETS["cap"] = (_n, _i, ["rag-cmp-002"])

# 长度上限探针：加在**提问**里（不是系统提示词），单变量只有一个 —— 字数上限。
# 必须明说"引用标记不计入"，否则模型会先砍 <kb .../> 标签（实测标签占答案 40–62%）。
LENGTH_CLAUSE = (
    "\n\n【回答长度限制】正文不超过 {n} 字（引用标记如 <kb ... /> 不计入字数）。"
    "请在这个限度内完成对比，不要因为压缩而丢弃引用。"
)

# 结构化探针（v2）：显式覆盖清单表 + 每侧行数上限。行数是可数的，模型不会像数字数那样跑偏；
# 每侧 {n} 行 ⇒ 必须放弃一部分扩展主题 ⇒ "遗漏透明"义务每轮必然触发（由程序核算）。
SLOTS_CLAUSE = (
    "\n\n【输出格式要求（本次必须遵守）】\n"
    "1) 先输出一张「覆盖清单表」：每行 = 一个方面，先写侧别（O2 或 A5），再写方面名，"
    "再写一句话依据；**每一侧最多 {n} 行**。\n"
    "2) 表后另起一段「未覆盖」：逐条点名你没有放进表里的方面（写清侧别与方面名）；"
    "若确实一条都没有，写「无」。\n"
    "3) 然后才写正文对比；正文不要超出表内已声明的范围。\n"
    "4) 未列入表的方面一律视为未覆盖，必须在第 2 步里点名。"
)

# 去耦版（v3）：只保留「显式覆盖清单表 ＋ 未覆盖段」的格式槽，**不设行数上限**。
# 目的：验证"声明"来自格式槽、"覆盖被压坏"来自行数上限这一拆分假设。
COV_TABLE_CLAUSE = (
    "\n\n【输出格式要求（本次必须遵守）】\n"
    "1) 先输出一张「覆盖清单表」：每行 = 一个方面，先写侧别（O2 或 A5），再写方面名，"
    "再写一句话依据；列出你实际展开的全部方面。\n"
    "2) 表后另起一段「未覆盖」：逐条点名你没有放进表里的方面（写清侧别与方面名）；"
    "若确实一条都没有，写「无」。\n"
    "3) 然后才写正文对比；正文不要超出表内已声明的范围。"
)

# 通用版槽位（v4）：不出现任何文档名/侧别名，方面由模型自己从提问里拆。
# 用途：与"无契约"的 A 臂组合，检验"槽位本身是否就够"（2×2 归因里缺的那一格）。
GENERIC_COV_TABLE_CLAUSE = (
    "\n\n【覆盖清单（本次必须遵守）】\n"
    "1) 先把你对用户要求的理解拆成一份方面清单：每个方面写清楚它是用户要求里的哪一层"
    "（例如「甲方的核心主张」「甲乙的异同」「对实践的启示」这类层级，具体是什么由你自己判断），"
    "最多 6 条。\n"
    "2) 对每条标注「覆盖」或「未覆盖」，并给一句话依据。\n"
    "3) 表后另起一段「未覆盖」：逐条点名你没有放进清单的方面（写清它属于用户要求里的哪一层）；"
    "确实没有就写「无」。\n"
    "4) 正文按清单展开，不要超出清单范围。"
)

# 变体：A＝原预设；covB＝预设原文 ＋ 覆盖透明契约（见 weknora_cov_agents.py）
VARIANTS: dict[str, dict[str, str]] = {
    "a": {"wiki": "exp-ourcases-wiki", "smart": "exp-ourcases-smart",
          "hybrid": "exp-ourcases-hybrid"},
    "covB": {"wiki": "exp-ourcases-covB-wiki", "smart": "exp-ourcases-covB-smart",
             "hybrid": "exp-ourcases-covB-hybrid"},
    # v2 契约（列举里带出未展开项，无清单表槽）：目前只建了 wiki 臂
    "cov2": {"wiki": "exp-ourcases-covB2-wiki"},
    # 模型/温度对照（都带 v2 契约、cap=8192）：flash+t0 / pro+t0.7 / pro+t0
    "cov2f0": {"wiki": "exp-ourcases-cov2w-flash-t0"},
    "cov2p7": {"wiki": "exp-ourcases-cov2w-pro-t07"},
    "cov2p0": {"wiki": "exp-ourcases-cov2w-pro-t0"},
    "cov2f7": {"wiki": "exp-ourcases-cov2w-flash-t07"},
    # v3 契约（块名 coverage_policy ＋ 覆盖优先级：先省最不重要的）：只建了 flash+t0 臂
    "cov3f0": {"wiki": "exp-ourcases-cov3w-flash-t0"},
}

# 三臂：只换系统提示词 / 工具白名单 / 轮次预算（见 0049）
W.ARMS = {
    "wiki": ("exp-ourcases-wiki", "builtin-wiki-researcher", "/agent-chat",
             "wiki_search／wiki_read_page／read_document"),
    "smart": ("exp-ourcases-smart", "builtin-smart-reasoning", "/agent-chat",
              "search_knowledge／read_document／list_documents"),
    "hybrid": ("exp-ourcases-hybrid", "preset:hybrid-rag-wiki", "/agent-chat",
               "两套工具全给（wiki ∪ smart）"),
}
W.ANSWER_STYLE = ""          # 产品式回答，不裁成答案片段
W.OUT_DIR = OUT_DIR


def load_cases(set_name: str) -> list[dict]:
    _kb_name, _kb_id, ids = SETS[set_name]
    rows = []
    for cid in ids:
        c = json.loads((CASES_DIR / f"{cid}.json").read_text(encoding="utf-8"))
        rows.append({
            "id": cid,
            "title": c.get("title") or "",
            "question": c["input"]["messages"][-1]["content"],
            "gold": (c.get("annotation") or {}).get("golden_answer") or "",
            "tags": c.get("tags") or [],
        })
    return rows


def fetch_agent_steps(session_id: str) -> list[dict]:
    """取该会话最后一条 assistant 消息的 agent_steps（含每轮 thought 与工具参数）。

    注意：WeKnora 的 SanitizeAgentStepsForStorage 会把工具结果正文替换成短摘要
    （见 internal/agent/tools/persist.go:112-150），但每轮的 thought 与工具参数保留。
    """
    _st, data = W.http_json("GET", f"/messages/{session_id}/load")
    msgs = (data or {}).get("data") or []
    assistants = [m for m in msgs if (m.get("role") or "") == "assistant"]
    if not assistants:
        return []
    steps = []
    for s in assistants[-1].get("agent_steps") or []:
        if not isinstance(s, dict):
            continue
        steps.append({
            "iteration": s.get("iteration"),
            "thought": s.get("thought"),
            "truncated": s.get("truncated"),
            "tool_calls": [
                {"name": tc.get("name"), "args": tc.get("args") or tc.get("arguments")}
                for tc in (s.get("tool_calls") or [])
                if isinstance(tc, dict)
            ],
        })
    return steps


def cmd_agents() -> None:
    W.cmd_agents()


def cmd_ask() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, choices=sorted(SETS))
    ap.add_argument("--arm", required=True, choices=sorted(W.ARMS))
    ap.add_argument("--variant", default="a", choices=sorted(VARIANTS))
    ap.add_argument("--cap-chars", type=int, default=0, help="正文净字数上限（0=不加限制）")
    ap.add_argument("--slots", type=int, default=0, help="每侧覆盖清单行数上限（0=不加限制）")
    ap.add_argument("--cov-table", action="store_true", help="只加覆盖清单表格式槽（不限行数）")
    ap.add_argument("--cov-table-generic", action="store_true",
                    help="加通用覆盖清单槽（不点文档名，方面由模型自拆）")
    ap.add_argument("--tag", default="", help="重复轮次标记，用于区分同名输出文件")
    ap.add_argument("--deadline-sec", type=float, default=600.0)
    args = ap.parse_args(sys.argv[2:])

    for k, v in VARIANTS[args.variant].items():
        if k in W.ARMS:
            W.ARMS[k] = (v, W.ARMS[k][1], W.ARMS[k][2], W.ARMS[k][3])
    kb_name, kb_id, _ids = SETS[args.set]
    W.KB_ID = kb_id                      # stream_chat 在调用时读这个全局
    agent_id = W.resolve_agent(args.arm)
    endpoint = W.ARMS[args.arm][2]
    cases = load_cases(args.set)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.variant == "a" else f"-{args.variant}"
    if args.cap_chars:
        suffix += f"-cap{args.cap_chars}"
    if args.slots:
        suffix += f"-slots{args.slots}"
    if args.cov_table:
        suffix += "-covtable"
    if args.cov_table_generic:
        suffix += "-covtableG"
    if args.tag:
        suffix += f"-{args.tag}"
    out_path = OUT_DIR / f"qa-{args.set}-{args.arm}{suffix}.jsonl"
    print(f"set={args.set} kb={kb_name} arm={args.arm} variant={args.variant} "
          f"agent={agent_id} 题数={len(cases)}", flush=True)

    with out_path.open("w", encoding="utf-8") as fh:
        for i, case in enumerate(cases, 1):
            _st, sess = W.http_json("POST", "/sessions", {"title": f"ourcases-{args.set}-{args.arm}-{i:02d}"})
            sid = (sess.get("data") or {}).get("id")
            question = case["question"]
            if args.cap_chars:
                question += LENGTH_CLAUSE.format(n=args.cap_chars)
            if args.slots:
                question += SLOTS_CLAUSE.format(n=args.slots)
            if args.cov_table:
                question += COV_TABLE_CLAUSE
            if args.cov_table_generic:
                question += GENERIC_COV_TABLE_CLAUSE
            r = W.stream_chat(sid, question, agent_id, endpoint, args.deadline_sec)
            msg = W.fetch_answer(sid)
            row = {
                "arm": args.arm,
                "variant": args.variant,
                "n": i,
                "id": case["id"],
                "title": case["title"],
                "tags": case["tags"],
                "question": question,
                "gold": case["gold"],
                "session_id": sid,
                "pred": msg.get("content") or r.get("stream_answer") or "",
                "pred_from": "message" if msg.get("content") else "stream",
                "refs": msg.get("refs") or r.get("refs") or [],
                "tool_calls": r.get("tool_calls") or [],
                "tool_events": r.get("tool_events") or [],
                "elapsed_sec": r.get("elapsed"),
                "events": r.get("events"),
                "event_types": r.get("event_types"),
                "agent_steps": fetch_agent_steps(sid),
                "error": r.get("error"),
                "completed": msg.get("completed"),
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"[{i:02d}/{len(cases)}] {case['id']} {row['elapsed_sec']}s "
                  f"tools={row['tool_calls'][:6]} refs={len(row['refs'])} "
                  f"err={row['error']} pred={row['pred'][:70]!r}", flush=True)
    print("done ->", out_path)


def cmd_show() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, choices=sorted(SETS))
    ap.add_argument("--chars", type=int, default=700)
    args = ap.parse_args(sys.argv[2:])
    cases = {c["id"]: c for c in load_cases(args.set)}
    for cid, case in cases.items():
        print("=" * 90)
        print(f"[{cid}] {case['title']}\nQ: {case['question']}")
        for arm in sorted(W.ARMS):
            p = OUT_DIR / f"qa-{args.set}-{arm}.jsonl"
            if not p.exists():
                print(f"  -- {arm}: (缺 {p.name})")
                continue
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row["id"] != cid:
                    continue
                print(f"  -- {arm} ({row['elapsed_sec']}s, tools={row['tool_calls'][:5]}, "
                      f"refs={len(row['refs'])})")
                print("     " + (row["pred"] or "")[:args.chars].replace("\n", "\n     "))


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    {"agents": cmd_agents, "ask": cmd_ask, "show": cmd_show}[sys.argv[1]]()


if __name__ == "__main__":
    main()
