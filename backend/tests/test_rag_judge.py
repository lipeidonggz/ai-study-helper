"""RAG 判官联动测试：引用真实性条件触发 + 检索证据路由。"""

import asyncio

from eval.runner import CaseResult, judge_case
from eval.schema import CaseFile, CaseInput, Expected, InputMessage


def _rag_case(criteria: list[str]) -> CaseFile:
    return CaseFile(
        id="rag-test",
        category="rag",
        title="测试",
        mode="rag",
        input=CaseInput(messages=[InputMessage(role="user", content="问题")]),
        expected=Expected(behavior="作答", criteria=criteria),
    )


def _result(*, retrieval: list[dict] | None = None) -> CaseResult:
    return CaseResult(
        case_id="rag-test",
        status="ok",
        output="根据资料，答案是 X。[1]",
        retrieval_evidence=retrieval or [],
    )


def test_citation_not_triggered_is_machine_pass():
    """未检索注入时引用真实性不触发：判 pass 且不转人工。"""
    case = _rag_case(["citation_truth"])
    out = asyncio.run(judge_case(case, _result(), judge_llm=None))
    assert out["judgments"]["citation_truth"] == "pass"
    assert out["pending_human"] == []
    assert "未触发" in out["judge_reasons"]["citation_truth"]


def test_citation_triggered_without_judge_goes_pending():
    """门控通过（有检索注入）且无判官时：转人工核验，不静默判过。"""
    case = _rag_case(["citation_truth"])
    ev = [{"gate": True, "reason": "门控通过", "hits": [{"source_id": "O2", "text": "x"}]}]
    out = asyncio.run(judge_case(case, _result(retrieval=ev), judge_llm=None))
    assert "citation_truth" in out["pending_human"]
