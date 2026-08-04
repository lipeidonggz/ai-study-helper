"""最小评测 runner：读用例 → 跑 Agent → 自动判定 → 出报告。

设计角度：为什么进程内直连 loop 而不走 HTTP API？
- 直接拿到完整 trace（工具调用、轮数、token、耗时），无需解析 SSE
- 不需要后端服务在跑，适合批量跑批与 CI
- 与测试同构：FakeLLM 干跑 / 真实 LLM 跑批可切换

用法（backend 目录下）：
  python -m eval.runner --llm fake              # 干跑验证管道（不花钱）
  python -m eval.runner --limit 10              # 真实模型跑前 10 条
  python -m eval.runner --concurrency 1         # 串行（note 用例严格隔离）
"""

import argparse
import asyncio
import csv
import json
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app.agent.llm import DeepSeekLLMClient, FakeLLMClient, LLMClient, LLMMessage
from app.agent.loop import run_agent_turn
from app.agent.trace import Trace
from app.di import build_deps
from app.tools.builtin import clear_notes
from app.tools.executor import ToolExecutor
from app.tools.registry import default_registry
from eval.schema import CaseFile, load_cases

CASES_DIR = Path(__file__).resolve().parent / "cases"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
DEFAULT_CONCURRENCY = 3


@dataclass
class CaseResult:
    """单条用例的运行结果。"""

    case_id: str
    status: str  # ok | timeout | error
    output: str = ""
    elapsed_ms: float = 0.0
    rounds: int = 0
    tool_calls: list[str] = field(default_factory=list)
    tokens: dict = field(default_factory=dict)
    error: str = ""
    trace_types: list[str] = field(default_factory=list)


async def run_case(
    case: CaseFile, llm: LLMClient, tools: ToolExecutor
) -> CaseResult:
    """跑单条用例：组装输入（最后一条为当前消息，前面为历史）、超时控制、收集指标。"""
    messages = case.input.messages
    user_message = messages[-1].content  # 当前用户消息
    # 多轮用例就绪后生效：最后一条之前的都是历史
    history = [LLMMessage(role=m.role, content=m.content) for m in messages[:-1]]
    trace = Trace()
    chunks: list[str] = []
    start = time.perf_counter()

    async def consume():
        async for chunk in run_agent_turn(
            user_message,
            mode=case.mode,
            llm=llm,
            tools=tools,
            history=history,
            trace=trace,
        ):
            chunks.append(chunk)

    try:
        await asyncio.wait_for(consume(), timeout=case.timeout_sec)
        status, error = "ok", ""
    except asyncio.TimeoutError:
        status, error = "timeout", f"timeout after {case.timeout_sec}s"
    except Exception as exc:  # 单条失败不影响其他用例
        status, error = "error", str(exc)

    elapsed_ms = round((time.perf_counter() - start) * 1000, 1)
    # 从 trace 提取指标：实际工具调用、轮数、token 汇总
    tool_names = [s["data"]["name"] for s in trace.steps() if s["type"] == "tool_exec"]
    done = next((s for s in trace.steps() if s["type"] == "done"), None)
    rounds = done["data"].get("rounds", 0) if done else 0
    tokens = done["data"].get("tokens", {}) if done else {}
    return CaseResult(
        case_id=case.id,
        status=status,
        output="".join(chunks),
        elapsed_ms=elapsed_ms,
        rounds=rounds,
        tool_calls=tool_names,
        tokens=tokens,
        error=error,
        trace_types=[s["type"] for s in trace.steps()],
    )


def judge_case(case: CaseFile, result: CaseResult) -> dict:
    """自动判定机器可判定的 criteria，其余标记待人工。

    机器可判定：tool_used / tool_not_used / stream_complete / latency_budget
    待人工/LLM：answer_correct / refusal（进 CSV 标注列）
    """
    judgments: dict[str, str] = {}
    pending: list[str] = []
    exp = case.expected

    for c in exp.criteria:
        if c == "tool_used":
            expected_names = {t.name for t in exp.tool_calls}
            judgments[c] = (
                "pass" if expected_names and expected_names.issubset(set(result.tool_calls)) else "fail"
            )
        elif c == "tool_not_used":
            judgments[c] = "pass" if not result.tool_calls else "fail"
        elif c == "stream_complete":
            judgments[c] = "pass" if result.status == "ok" and "done" in result.trace_types else "fail"
        elif c == "latency_budget":
            judgments[c] = (
                "pass"
                if result.status == "ok" and result.elapsed_ms <= case.timeout_sec * 1000
                else "fail"
            )
        else:
            pending.append(c)  # answer_correct / refusal 等需判断力

    metrics = {
        "answer_contains": (
            "pass" if all(k in result.output for k in exp.answer_contains) else "fail"
        )
        if exp.answer_contains
        else "na",
        # max_rounds 的语义：预期最多调用几次工具（不是循环轮数）
        "max_rounds": "pass" if len(result.tool_calls) <= exp.max_rounds else "fail",
        "tool_calls_count": len(result.tool_calls),
    }
    return {"judgments": judgments, "pending_human": pending, "metrics": metrics}


async def run_all(
    cases: list[CaseFile],
    llm: LLMClient,
    tools: ToolExecutor,
    concurrency: int = DEFAULT_CONCURRENCY,
    limit: int | None = None,
) -> dict:
    """并发跑批（信号量限流）并聚合报告。"""
    selected = cases[:limit] if limit else cases
    clear_notes()  # 批次开始前重置笔记状态
    sem = asyncio.Semaphore(concurrency)

    async def worker(case: CaseFile) -> CaseResult:
        async with sem:
            return await run_case(case, llm, tools)

    results = await asyncio.gather(*(worker(c) for c in selected))
    entries = []
    for case, result in zip(selected, results):
        judgment = judge_case(case, result)
        entries.append(
            {
                "id": case.id,
                "category": case.category,
                "title": case.title,
                "mode": case.mode,
                "status": result.status,
                "elapsed_ms": result.elapsed_ms,
                "rounds": result.rounds,
                "tool_calls": result.tool_calls,
                "tokens": result.tokens,
                "output": result.output,
                "error": result.error,
                "judgments": judgment["judgments"],
                "pending_human": judgment["pending_human"],
                "metrics": judgment["metrics"],
            }
        )
    summary = _aggregate(entries)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "llm": llm.model_name,
        "summary": summary,
        "cases": entries,
    }


def _aggregate(entries: list[dict]) -> dict:
    """汇总统计：状态分布、分类自动通过率、工具调用率、平均耗时、token 总量。"""
    total = len(entries)
    status_counts = Counter(e["status"] for e in entries)
    judged = [e for e in entries if e["status"] == "ok"]

    def pass_rate(criterion: str) -> dict:
        vals = [e["judgments"][criterion] for e in judged if criterion in e["judgments"]]
        if not vals:
            return {"judged": 0, "pass_rate": None}
        return {"judged": len(vals), "pass_rate": round(vals.count("pass") / len(vals), 3)}

    # 分类的自动通过率：该分类下所有自动判定项的 pass 占比
    category_stats: dict[str, dict] = {}
    for e in entries:
        cat = e["category"]
        cat_stats = category_stats.setdefault(cat, {"cases": 0, "ok": 0, "judged": 0, "pass": 0})
        cat_stats["cases"] += 1
        if e["status"] == "ok":
            cat_stats["ok"] += 1
            for v in e["judgments"].values():
                cat_stats["judged"] += 1
                cat_stats["pass"] += v == "pass"
    for stats in category_stats.values():
        stats["auto_pass_rate"] = (
            round(stats["pass"] / stats["judged"], 3) if stats["judged"] else None
        )

    tool_cases = [e for e in judged if e["category"] == "tool_call"]
    tool_used_count = sum(1 for e in tool_cases if e["tool_calls"])
    return {
        "total": total,
        "status": dict(status_counts),
        "tool_call_rate": round(tool_used_count / len(tool_cases), 3) if tool_cases else None,
        "avg_elapsed_ms": round(sum(e["elapsed_ms"] for e in judged) / len(judged), 1) if judged else 0,
        "total_tokens": sum(e["tokens"].get("total", 0) for e in judged),
        "judgments": {c: pass_rate(c) for c in ("tool_used", "tool_not_used", "stream_complete", "latency_budget")},
        "category_stats": category_stats,
    }


def write_report(report: dict, report_dir: Path) -> tuple[Path, Path]:
    """写 JSON 全量明细 + CSV 人工标注表。"""
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = report_dir / f"report-{timestamp}.json"
    csv_path = report_dir / f"annotate-{timestamp}.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["id", "category", "behavior", "output", "status", "elapsed_ms", "rounds", "tool_calls", "judgments", "answer_correct", "refusal"]
        )
        for e in report["cases"]:
            writer.writerow(
                [
                    e["id"],
                    e["category"],
                    e["title"],
                    e["output"][:200],
                    e["status"],
                    e["elapsed_ms"],
                    e["rounds"],
                    ",".join(e["tool_calls"]),
                    json.dumps(e["judgments"], ensure_ascii=False),
                    "",  # answer_correct 待人工填
                    "",  # refusal 待人工填
                ]
            )
    return json_path, csv_path


def print_summary(summary: dict) -> None:
    """控制台打印汇总。"""
    print("===== 评测汇总 =====")
    print(f"用例数: {summary['total']}  状态: {summary['status']}")
    print(f"工具调用率: {summary['tool_call_rate']}  平均耗时: {summary['avg_elapsed_ms']}ms  token总量: {summary['total_tokens']}")
    for cat, stats in summary["category_stats"].items():
        print(f"  {cat}: {stats['ok']}/{stats['cases']} 通过, 自动判定通过率 {stats['auto_pass_rate']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="golden set 评测 runner")
    parser.add_argument("--cases", type=Path, default=CASES_DIR, help="用例目录")
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 条")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--llm", choices=["real", "fake"], default="real", help="fake=干跑不花钱")
    parser.add_argument("--report-dir", type=Path, default=REPORTS_DIR)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    print(f"加载用例 {len(cases)} 条")
    tools = ToolExecutor(default_registry())
    if args.llm == "fake":
        llm: LLMClient = FakeLLMClient()
    else:
        deps = build_deps()
        settings = deps.settings_store.get_llm_settings()
        if not settings.api_key:
            print("未配置大模型 API Key（请先在界面设置中配置）")
            return 1
        llm = DeepSeekLLMClient(api_key=settings.api_key, model=settings.model or "deepseek-chat")

    report = asyncio.run(run_all(cases, llm, tools, args.concurrency, args.limit))
    json_path, csv_path = write_report(report, args.report_dir)
    print_summary(report["summary"])
    print(f"报告: {json_path}")
    print(f"人工标注表: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
