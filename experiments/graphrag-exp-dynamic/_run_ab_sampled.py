"""三个问题各采样 3 次（每次 = 默认路径 + Dynamic 各跑一遍），最后打一张带方差的表。

用法：python _run_ab_sampled.py [--reps 3] [--only q1,q2]
每条查询单独起子进程调 _probe_vs.py，互不影响。日志落在 probe-out/log-<qid>-r<N>.txt。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "probe-out"

# (id, 类型, 问题原文, 该问题真正需要的文档代号)
QUERIES = [
    ("q1", "同域·密集", "这批资料里，做 LLM 判官评测时踩过哪些坑？怎么排查？", "01,02,03"),
    ("q2", "同域·稀疏", "单 Agent 和 多 Agent 架构各自解决什么问题？有什么代价？", "04,05"),
    ("q3", "跨域·需两篇", "把评测里“探针设计”的思路套到 Agent 架构选型上，该怎么设判据？", "01,02,03,04,05"),
]


def nums(d, key):
    v = d.get(key) or {}
    return sum(int(x) for x in v.values()) if isinstance(v, dict) else int(v or 0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--only", default="")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    todo = [q for q in QUERIES if not only or q[0] in only]
    OUT.mkdir(exist_ok=True)
    stamp = time.strftime("%m%d-%H%M%S")

    for qid, kind, question, expect in todo:
        for rep in range(1, args.reps + 1):
            tag = f"{qid}-r{rep}-{stamp}"
            print(f"\n{'=' * 78}\n[{qid} · 第 {rep}/{args.reps} 次] {kind}\n{question}\n{'=' * 78}", flush=True)
            cmd = [
                sys.executable, str(ROOT / "_probe_vs.py"),
                "--level", "2", "--paths", "default,dynamic",
                "--query", question, "--expect-docs", expect, "--tag", tag,
            ]
            t0 = time.time()
            log = OUT / f"log-{qid}-r{rep}-{stamp}.txt"
            with log.open("w", encoding="utf-8") as fh:
                proc = subprocess.run(cmd, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, check=False)
            print(f"  -> 退出码 {proc.returncode}，耗时 {time.time() - t0:.0f}s，日志 {log.name}", flush=True)

    # 汇总：每条查询 × 每条路径 × 每次采样
    print(f"\n\n{'=' * 110}\n汇总（采样 {args.reps} 次 / 查询，tag={stamp}）\n{'=' * 110}")
    rows = []
    for qid, kind, question, expect in todo:
        for rep in range(1, args.reps + 1):
            files = sorted(OUT.glob(f"vs-*-{qid}-r{rep}-{stamp}.json"))
            if not files:
                print(f"{qid} r{rep}: 没有结果文件")
                continue
            for r in json.loads(files[-1].read_text(encoding="utf-8")):
                sel = r.get("selection") or {}
                rows.append({
                    "q": qid, "rep": rep, "path": r["path"],
                    "batch": r["batches"],
                    "select_calls": nums(r, "llm_calls"),
                    "prompt_tok": nums(r, "prompt_tokens"),
                    "sec": r["elapsed_s"],
                    "ans": r["answer_chars"],
                    "selected": sel.get("selected_count", r["reports_fed"]),
                    "missing": len(r.get("expect_missing", []) or []),
                    "canned": r.get("answer_is_canned"),
                })
    hdr = f"{'q':3s} {'次':>2s} {'路径':8s} {'批':>3s} {'调用':>4s} {'prompt_tok':>10s} {'秒':>5s} {'答案字符':>7s} {'选中':>5s} {'缺':>3s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['q']:3s} {r['rep']:2d} {r['path']:8s} {r['batch']:3d} {r['select_calls']:4d} "
              f"{r['prompt_tok']:10d} {r['sec']:5.0f} {r['ans']:7d} {r['selected']:5d} {r['missing']:3d}")

    print(f"\n{'=' * 110}\n每条查询 · 每臂的 均值[min–max]\n{'=' * 110}")
    for qid, kind, _, _ in todo:
        for path in ("default", "dynamic"):
            sub = [r for r in rows if r["q"] == qid and r["path"] == path]
            if not sub:
                continue
            def stat(k):
                v = [r[k] for r in sub]
                return f"{sum(v)/len(v):.0f}[{min(v):.0f}–{max(v):.0f}]"
            print(f"  {qid} {kind:10s} {path:8s} n={len(sub)}  "
                  f"批={stat('batch')} 调用={stat('select_calls')} prompt_tok={stat('prompt_tok')} "
                  f"秒={stat('sec')} 答案={stat('ans')} 缺={stat('missing')}")

    (OUT / f"sampled-{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n汇总 JSON: probe-out/sampled-{stamp}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
