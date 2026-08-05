"""评测 runner 测试：管道完整性、自动判定逻辑、超时处理。"""

import asyncio
import json
from pathlib import Path

from app.agent.llm import LLMClient, LLMEvent, LLMResponse, ToolCall
from app.tools.executor import ToolExecutor
from app.tools.registry import default_registry
from eval.runner import judge_case, run_all, run_case, write_report
from eval.schema import CaseFile, CaseInput, Expected, InputMessage, load_cases


class ToolThenTextLLM(LLMClient):
    """第一次调用产出 calculator 工具调用，第二次产出文本（模拟完整工具链路）。"""

    model_name = "stub"

    def __init__(self) -> None:
        self._calls = 0

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="")

    async def stream(self, messages, tools=None):
        self._calls += 1
        if self._calls == 1:
            yield LLMEvent(
                type="tool_call",
                tool_call=ToolCall(
                    name="calculator", arguments={"expression": "3+5"}, id="call_1"
                ),
                raw_tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "calculator",
                            "arguments": '{"expression": "3+5"}',
                        },
                    }
                ],
            )
            yield LLMEvent(
                type="usage",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )
            yield LLMEvent(type="done")
        else:
            yield LLMEvent(type="text", text="结果是 8")
            yield LLMEvent(
                type="usage",
                usage={"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            )
            yield LLMEvent(type="done")


class SlowLLM(LLMClient):
    """故意慢的 LLM：用于验证超时处理。"""

    model_name = "slow"

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="")

    async def stream(self, messages, tools=None):
        await asyncio.sleep(5)
        yield LLMEvent(type="text", text="late")
        yield LLMEvent(type="done")


class FlakyLLM(LLMClient):
    """第一次调用抛错，第二次正常（用于验证重试）。"""

    model_name = "flaky"

    def __init__(self) -> None:
        self._calls = 0

    async def chat(self, messages, tools=None) -> LLMResponse:
        return LLMResponse(content="")

    async def stream(self, messages, tools=None):
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("transient failure")
        yield LLMEvent(type="text", text="ok")
        yield LLMEvent(type="done")


def test_fake_dry_run(tmp_path):
    """Fake 干跑：70+ 用例应全部跑完并产出 JSON + CSV 报告。"""
    from app.agent.llm import FakeLLMClient

    cases = load_cases(Path(__file__).resolve().parent.parent / "eval" / "cases")
    report = asyncio.run(
        run_all(cases, FakeLLMClient(), ToolExecutor(default_registry()), concurrency=3)
    )
    assert report["summary"]["total"] >= 70
    assert all(e["status"] in ("ok", "timeout", "error") for e in report["cases"])
    json_path, xlsx_path = write_report(report, tmp_path)
    assert json_path.exists() and xlsx_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data["cases"]) >= 70
    assert "summary" in data and "generated_at" in data
    assert "input" in data["cases"][0]  # 报告条目应含输入文本
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path)
    ws = wb.active
    headers = [c.value for c in ws[1]]
    assert "input" in headers and "answer_correct" in headers  # Excel 表头完整
    assert ws.freeze_panes == "A2"  # 表头冻结
    assert ws["K2"].value in (None, "")  # answer_correct 待人工填


def test_tool_link_and_observation():
    """工具链路：实际调用应被记录，answer_contains 自动判定。"""
    cases = load_cases(Path(__file__).resolve().parent.parent / "eval" / "cases")
    case = next(c for c in cases if c.id == "tool-calc-001")
    result = asyncio.run(run_case(case, ToolThenTextLLM(), ToolExecutor(default_registry())))
    assert result.status == "ok"
    assert result.tool_calls == ["calculator"]  # 观察指标：实际工具调用
    judgment = judge_case(case, result)
    assert judgment["pending_human"] == ["answer_correct"]  # 该用例唯一 criterion 待人工
    assert judgment["metrics"]["answer_contains"] == "pass"  # 输出包含 "8"
    assert judgment["metrics"]["max_rounds"] == "pass"


def test_tool_used_judgment():
    """tool_used 自动判定：预期工具被实际调用 → pass。"""
    case = CaseFile(
        id="t-tool",
        category="tool_call",
        title="t",
        mode="tool_enhanced",
        input=CaseInput(messages=[InputMessage(role="user", content="3+5等于几？")]),
        expected=Expected(
            behavior="调用计算器",
            criteria=["tool_used"],
            tool_calls=[{"name": "calculator", "arguments": {"expression": "3+5"}}],
        ),
    )
    result = asyncio.run(run_case(case, ToolThenTextLLM(), ToolExecutor(default_registry())))
    judgment = judge_case(case, result)
    assert judgment["judgments"]["tool_used"] == "pass"


def test_timeout_judgment():
    """超时：status=timeout，latency_budget 判 fail。"""
    case = CaseFile(
        id="t-timeout",
        category="combined",
        title="t",
        mode="general",
        input=CaseInput(messages=[InputMessage(role="user", content="hi")]),
        expected=Expected(behavior="b", criteria=["latency_budget"]),
        timeout_sec=0.05,
    )
    result = asyncio.run(run_case(case, SlowLLM(), ToolExecutor(default_registry())))
    assert result.status == "timeout"
    judgment = judge_case(case, result)
    assert judgment["judgments"]["latency_budget"] == "fail"


def test_retry_on_transient_error():
    """首次失败应退避重试，重试成功后 status=ok。"""
    case = CaseFile(
        id="t-retry",
        category="combined",
        title="t",
        mode="general",
        input=CaseInput(messages=[InputMessage(role="user", content="hi")]),
        expected=Expected(behavior="b", criteria=["answer_correct"]),
    )
    result = asyncio.run(
        run_case(case, FlakyLLM(), ToolExecutor(default_registry()), max_retries=1)
    )
    assert result.status == "ok"
    assert result.output == "ok"
