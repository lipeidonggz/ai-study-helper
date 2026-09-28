"""一口气跑完 6 条 A/B 对照（每条：默认 vs dynamic），最后打一张总表。

每条 query 单独起一个子进程调 _probe_vs.py——彼此隔离，且中途失败不影响其余。
用法：python _run_ab_all.py [--only q1,q3] [--level 2] [--paths default,dynamic]
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

# (id, 类型, 问题, 该问题真正需要的文档代号)
QUERIES = [
    ("q1", "单域·密集", "这批资料里，做 LLM 判官评测时踩过哪些坑？怎么排查？", "01,02,03"),
    ("q2", "单域·稀疏", "单 Agent 和 多 Agent 架构各自解决什么问题？有什么代价？", "04,05"),
    ("q3", "跨域·需两篇", "把评测里“探针设计”的思路套到 Agent 架构选型上，该怎么设判据？", "01,02,03,04,05"),
    ("q4", "跨语言·单域", "How does Anthropic contain Claude across products? What layers are used?", "A5"),
    ("q5", "负样本", "这三篇资料里怎么做 Kubernetes 集群的滚动升级？", ""),
    ("q6", "单域·极窄", "Krippendorff's alpha 在判官一致性里是怎么用的？", "01"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--paths", default="default,dynamic")
    args = ap.parse_args()

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    todo = [q for q in QUERIES if not only or q[0] in only]
    OUT.mkdir(exist_ok=True)

    stamp = time.strftime("%m%d-%H%M%S")
    for qid, kind, question, expect in todo:
        print(f"\n{'=' * 78}\n[{qid}] {kind} · 期望文档: {expect or '（无，负样本）'}\n{question}\n{'=' * 78}", flush=True)
        cmd = [
            sys.executable,
            str(ROOT / "_probe_vs.py"),
            "--level", str(args.level),
            "--paths", args.paths,
            "--query", question,
            "--tag", f"{qid}-{stamp}",
        ]
        if expect:
            cmd += ["--expect-docs", expect]
        proc = subprocess.run(cmd, cwd=str(ROOT), check=False)
        if proc.returncode != 0:
            print(f"!! {qid} 失败，返回码 {proc.returncode}", flush=True)

    # 汇总
    print(f"\n\n{'=' * 78}\n汇总（tag={stamp}）\n{'=' * 78}")
    rows = []
    for qid, kind, question, expect in todo:
        files = sorted(OUT.glob(f"vs-*-{qid}-{stamp}.json"))
        if not files:
            print(f"{qid}: 没找到结果文件")
            continue
        data = json.loads(files[-1].read_text(encoding="utf-8"))
        for r in data:
            sel = r.get("selection") or {}
            rows.append({
                "q": qid, "kind": kind, "path": r["path"],
                "fed": r["reports_fed"], "batch": r["batches"],
                "select_calls": r["llm_calls"]["build_context"],
                "select_tok": r["prompt_tokens"]["build_context"],
                "map_tok": r["prompt_tokens"]["map"],
                "total_tok": sum(r["prompt_tokens"].values()),
                "selected": sel.get("selected_count", r["reports_fed"]),
                "by_doc": sel.get("selected_by_doc", r.get("fed_by_doc")),
                "cited": r.get("cited_by_doc"),
                "missing": r.get("expect_missing", []),
                "canned": r.get("answer_is_canned"),
                "sec": r["elapsed_s"],
                "ans": r["answer_chars"],
            })
    if not rows:
        return 1
    hdr = f"{'q':3s} {'type':10s} {'path':8s} {'fed':>4s} {'sel':>4s} {'批':>3s} {'选择调用':>6s} {'选择tok':>8s} {'map_tok':>8s} {'总tok':>9s} {'缺':>4s} {'兜底':>4s} {'秒':>5s} {'答案':>5s}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['q']:3s} {r['kind']:10s} {r['path']:8s} {r['fed']:4d} {r['selected']:4d} {r['batch']:3d} "
              f"{r['select_calls']:6d} {r['select_tok']:8d} {r['map_tok']:8d} {r['total_tok']:9d} "
              f"{len(r['missing']):4d} {'是' if r['canned'] else '否':>4s} {r['sec']:5.0f} {r['ans']:5d}")
        print(f"        选中按文档 {r['by_doc']}   引用按文档 {r['cited']}   期望缺 {r['missing']}")
    (OUT / f"summary-{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n汇总 JSON: probe-out/summary-{stamp}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
