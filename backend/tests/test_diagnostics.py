"""白盒归因件单测（P3）：充分性三态短路 + 漏引记账 + 提炼归因。"""

import asyncio
import json

from eval.diagnostics import run_diagnostics
from eval.schema import CaseFile, CaseInput, Expected, InputMessage


def _rag_case() -> CaseFile:
    case = CaseFile(
        id="rag-diag",
        category="rag",
        title="t",
        mode="rag",
        input=CaseInput(messages=[InputMessage(role="user", content="q")]),
        expected=Expected(behavior="作答", criteria=["answer_correct"]),
    )
    case.annotation.checklist_points = {
        "core": [
            {"id": "p1", "text": "O2 主张规则机械编码进仓库"},
            {"id": "p2", "text": "A5 主张环境层隔离"},
        ],
        "ext": [],
    }
    return case


class _Result:
    def __init__(self, output: str, hits: list[dict], gate: bool = True):
        self.output = output
        self.retrieval_evidence = (
            [{"gate": gate, "reason": "注入", "hits": hits}] if gate else []
        )


def _stub(responses: list[dict]):
    class _Resp:
        def __init__(self, content: str):
            self.content = content
            self.usage = {"total_tokens": 1}

    class _Stub:
        def __init__(self):
            self.queue = [_Resp(json.dumps(r, ensure_ascii=False)) for r in responses]
            self.calls = 0

        async def chat(self, messages):
            self.calls += 1
            return self.queue.pop(0)

    return _Stub()


def _points_json(values: dict[str, str]) -> dict:
    return {"points": [{"id": k, "v": v, "reason": "r"} for k, v in values.items()]}


async def _kb_searcher(query: str) -> list[dict]:
    return [{"source_id": "A5", "section_path": "s1", "text": "A5 原文块"}]


def test_sufficiency_pass_then_internal_and_summarization():
    """注入足以支撑失败要点 → 充分性过；提炼归因 fail；漏引记账 pass（一次调用）。"""
    case = _rag_case()
    result = _Result(
        output="O2 主张机械编码[1]，A5 主张环境层隔离[2]。",
        hits=[{"source_id": "O2", "text": "机械编码"}, {"source_id": "A5", "text": "环境层隔离"}],
    )
    stub = _stub(
        [
            _points_json({"p1": "supported", "p2": "supported"}),
            {"missing": []},
        ]
    )
    out = asyncio.run(
        run_diagnostics(case, result, stub, failing_point_ids=["p1", "p2"])
    )
    assert stub.calls == 2
    assert out["retrieval_sufficiency"] == "pass"
    assert out["summarization_attribution"] == "fail"
    assert out["citation_internal"] == "pass"
    assert out["sufficiency_first_blocked"] is None


def test_sufficiency_fail_s2_short_circuits():
    """注入缺 p2 → 全库可支撑 → S2（锅在检索），短路不跑引用/提炼。"""
    case = _rag_case()
    result = _Result(
        output="只写了 O2 部分。",
        hits=[{"source_id": "O2", "text": "机械编码"}],
    )
    stub = _stub(
        [
            _points_json({"p1": "supported", "p2": "unsupported"}),
            _points_json({"p2": "supported"}),
        ]
    )
    out = asyncio.run(
        run_diagnostics(
            case,
            result,
            stub,
            failing_point_ids=["p1", "p2"],
            searcher=_kb_searcher,
        )
    )
    assert stub.calls == 2  # 注入批量 + 全库单点；未进漏引/提炼
    assert out["retrieval_sufficiency"] == "fail"
    assert out["sufficiency_first_blocked"]["point_id"] == "p2"
    assert out["sufficiency_first_blocked"]["state"] == "S2"
    assert out["citation_internal"] == "na"


def test_sufficiency_fail_s3_when_kb_lacks():
    """全库也没有 → S3（语料缺口或期望过高），同样短路。"""
    case = _rag_case()
    result = _Result(
        output="只写了 O2 部分。",
        hits=[{"source_id": "O2", "text": "机械编码"}],
    )

    async def empty_searcher(query: str) -> list[dict]:
        return []

    stub = _stub(
        [
            _points_json({"p1": "supported", "p2": "unsupported"}),
            _points_json({"p2": "unsupported"}),
        ]
    )
    out = asyncio.run(
        run_diagnostics(
            case,
            result,
            stub,
            failing_point_ids=["p1", "p2"],
            searcher=empty_searcher,
        )
    )
    assert out["retrieval_sufficiency"] == "fail"
    assert out["sufficiency_first_blocked"]["state"] == "S3"


def test_citation_internal_records_missing():
    """漏引记账：内容取自注入却未标注 → citation_internal fail 且带清单（不改判分）。"""
    case = _rag_case()
    result = _Result(
        output="O2 主张机械编码进仓库（无编号）。",
        hits=[{"source_id": "O2", "text": "机械编码"}],
    )
    stub = _stub(
        [
            _points_json({"p1": "supported", "p2": "supported"}),
            {"missing": [{"statement": "O2 主张机械编码", "note": "[1]"}]},
        ]
    )
    out = asyncio.run(
        run_diagnostics(case, result, stub, failing_point_ids=["p1", "p2"])
    )
    assert out["citation_internal"] == "fail"
    assert len(out["citation_internal_notes"]) == 1
    assert "O2 主张机械编码" in out["citation_internal_notes"][0]


def test_no_points_or_no_failing_returns_empty():
    """无点表 / 无内容失败要点（如纯 format fail）→ 充分性无本可查，返回空 dict。"""
    case = _rag_case()
    case.annotation.checklist_points = {}
    result = _Result("x", [])
    out = asyncio.run(run_diagnostics(case, result, _stub([]), failing_point_ids=[]))
    assert out == {}
