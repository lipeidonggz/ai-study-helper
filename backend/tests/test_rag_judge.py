"""RAG 判官联动测试：引用真实性条件触发 + 检索证据路由 + 清单型金标准规则聚合。"""

import asyncio
import json

from eval.runner import (
    CaseResult,
    _aggregate_checklist,
    _aggregate_group_verdicts,
    _checklist_points_block,
    _deterministic_extract_claims,
    _extract_json_object,
    _is_checklist_golden,
    _is_checklist_shape,
    _judge_shape,
    _judge_system,
    _parse_checklist,
    _parse_group_verdicts,
    _parse_unified_user,
    _parse_unified_checklist,
    _resolve_citation_groups,
    judge_case,
)
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


def _result(
    *,
    retrieval: list[dict] | None = None,
    output: str = "根据资料，答案是 X。[1]",
) -> CaseResult:
    return CaseResult(
        case_id="rag-test",
        status="ok",
        output=output,
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


def _checklist_case() -> CaseFile:
    case = _rag_case(["answer_correct"])
    case.annotation.golden_answer = (
        "判 pass：内容正确——O2 侧核心主题（全部覆盖）：①外层系统设计；②规则机械编码；"
        "扩展主题（至少覆盖 2 个）：③熵与垃圾回收；④agent legibility；"
        "A5 侧核心主题（全部覆盖）：①遏制；②三层防御。遗漏透明：未覆盖全部扩展主题时须声明未展开内容。"
    )
    case.annotation.checklist_rule = {
        "ext_min_per_group": 2,
        "transparency": "conditional",
    }
    return case


def _mark_checklist(case: CaseFile) -> CaseFile:
    case.annotation.checklist_rule = {
        "ext_min_per_group": 2,
        "transparency": "conditional",
    }
    return case


def test_is_checklist_golden_detects_config():
    assert _is_checklist_golden(_checklist_case()) is True
    assert _is_checklist_golden(_rag_case(["answer_correct"])) is False


def test_judge_shape_explicit_overrides_marker_sniffing():
    """显式 judge_shape 优先于文案嗅探：标 essence 的清单文案走非清单路径。"""
    case = _checklist_case()
    assert _judge_shape(case) == "checklist"  # 兜底推断
    assert _is_checklist_shape(case) is True
    case.annotation.judge_shape = "essence"
    assert _judge_shape(case) == "essence"
    assert _is_checklist_shape(case) is False


def test_judge_shape_refusal_inference():
    case = _rag_case(["refusal"])
    assert _judge_shape(case) == "refusal"


def test_judge_system_composed_by_shape():
    """checklist 形态 = 核心段 + 清单模块；非清单只有核心段（不再嗅探金标准）。"""
    checklist = _checklist_case()
    essence = _rag_case(["answer_correct"])
    assert "清单型专用" in _judge_system(checklist)
    assert "清单型专用" not in _judge_system(essence)
    assert "仅当金标准含显式清单结构" not in _judge_system(checklist)


def test_checklist_aggregate_rules():
    rule = {"ext_min_per_group": 2, "transparency": "conditional", "ext_groups": {}}
    ok = {
        "core": {"a": {"v": "covered"}, "b": {"v": "covered"}},
        "ext": {"x": {"v": "covered"}, "y": {"v": "covered"}},
        "transparency": {"v": "covered"},
    }
    assert _aggregate_checklist(ok, rule)[0] == "pass"

    core_miss = dict(ok)
    core_miss["core"]["b"] = {"v": "not_covered"}
    assert _aggregate_checklist(core_miss, rule)[0] == "fail"
    assert "核心主题未覆盖" in _aggregate_checklist(core_miss, rule)[1]

    ext_low = dict(ok)
    ext_low["ext"] = {"x": {"v": "covered"}}
    assert _aggregate_checklist(ext_low, rule)[0] == "fail"

    no_transparency = dict(ok)
    no_transparency["transparency"] = {"v": "not_covered"}
    assert _aggregate_checklist(no_transparency, rule)[0] == "fail"

    eq = dict(ok)
    eq["core"]["b"] = {"v": "equivocal"}
    assert _aggregate_checklist(eq, rule)[0] is None  # 转人工


def _stub_llm(*contents: str):
    class _Resp:
        def __init__(self, content: str):
            self.content = content
            self.usage = {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 15,
            }

    class _Stub:
        def __init__(self):
            self.queue = [_Resp(c) for c in contents]
            self.calls = 0

        async def chat(self, messages):
            self.calls += 1
            return self.queue.pop(0)

    return _Stub()


def _checklist_golden_text() -> str:
    return (
        "判 pass：内容正确——O2 侧核心主题（全部覆盖）：①外层系统设计；②规则机械编码；"
        "扩展主题（至少覆盖 2 个）：③熵与垃圾回收；④agent legibility；"
        "A5 侧核心主题（全部覆盖）：①遏制；②三层防御；扩展主题（至少覆盖 2 个）：⑤读入信任；"
        "遗漏透明（条件强制）：未覆盖两侧全部扩展主题时须主动声明未展开内容并点名 1 个未展开项。"
        "表达合适：跨文档对比须结构化且来源分明。"
    )


def _ok_content_json() -> dict:
    return {
        "core": {"O2-①": {"v": "covered", "evidence": "..."},
                 "A5-①": {"v": "covered", "evidence": "..."}},
        "ext": {"O2-③": {"v": "covered", "evidence": "..."},
                "A5-⑤": {"v": "covered", "evidence": "..."}},
        "transparency": {"v": "covered", "evidence": "..."},
    }


def test_extract_and_parse_checklist_with_noise():
    """解析容错：JSON 前后有杂讯时用平衡花括号截取仍能解析（Run#114 pending 修法）。"""
    noisy = (
        "好的，核对结果如下：\n"
        + json.dumps(
            {"core": {"a": {"v": "covered"}}, "ext": {}, "transparency": {"v": "covered"}},
            ensure_ascii=False,
        )
        + "\n以上是全部结论。"
    )
    assert _extract_json_object(noisy) is not None
    parsed = _parse_checklist(noisy)
    assert parsed is not None and parsed["core"]["a"]["v"] == "covered"


def test_parse_unified_checklist_shape():
    raw = json.dumps(
        {"content": {"core": {}}, "format": {"v": "pass", "reason": "ok"}},
        ensure_ascii=False,
    )
    content, fmt = _parse_unified_checklist(raw)
    assert content == {"core": {}}
    assert fmt["v"] == "pass"
    assert _parse_unified_checklist('{"content": 1}') is None


def test_resolve_citation_groups_rules():
    hits = [
        {"source_id": "O2", "section_path": "s1", "text": "O2 块1"},
        {"source_id": "A5", "section_path": "s2", "text": "A5 块2"},
        {"source_id": "A5", "section_path": "s3", "text": "A5 块3"},
    ]
    claims = [
        {"claim": "O2 说规则编码进仓库。[1]", "refs": ["[1]"]},
        {"claim": "A5 说三层防御。", "refs": ["A5"]},
        {"claim": "引用不存在的 [9]", "refs": ["[9]"]},
        {"claim": "显式清单却无引用", "refs": []},
    ]
    out = _resolve_citation_groups(claims, hits)
    # [1] → 1 组（block）；A5 点名 → 1 组（source，含 A5 全部注入块）；[9] / 空 → 不产组
    assert len(out["groups"]) == 2
    g0, g1 = out["groups"]
    assert g0["kind"] == "block" and [b["block_no"] for b in g0["blocks"]] == [1]
    assert g1["kind"] == "source" and [b["block_no"] for b in g1["blocks"]] == [2, 3]
    assert all(b.get("source_label") for g in out["groups"] for b in g["blocks"])  # Q2
    assert any("来源不存在" in f for f in out["fails"])
    assert len(out["uncertain"]) == 1
    # P2：引用缺失不再由红线判 fail（漏引移交白盒）
    assert not any("引用缺失" in f for f in out["fails"])


def test_parse_and_aggregate_group_verdicts():
    groups = [{"gid": 0, "claim": "c1"}, {"gid": 1, "claim": "c2"}]
    ok = {"groups": [
        {"gid": 0, "content_supported": "ok", "attribution_ok": "ok", "reason": "并集支撑"},
        {"gid": 1, "content_supported": "ok", "attribution_ok": "ok", "reason": "并集支撑"},
    ]}
    parsed = _parse_group_verdicts(json.dumps(ok, ensure_ascii=False))
    verdict, reason = _aggregate_group_verdicts(parsed, groups)
    assert verdict == "pass" and "2 条" in reason

    bad = {"groups": [
        {"gid": 0, "content_supported": "ok", "attribution_ok": "violation", "reason": "张冠李戴"},
        {"gid": 1, "content_supported": "ok", "attribution_ok": "ok", "reason": "吻合"},
    ]}
    verdict, reason = _aggregate_group_verdicts(
        _parse_group_verdicts(json.dumps(bad, ensure_ascii=False)), groups
    )
    assert verdict == "fail" and "归属错误" in reason

    eq = {"groups": [
        {"gid": 0, "content_supported": "equivocal", "attribution_ok": "ok", "reason": "块不够"},
        {"gid": 1, "content_supported": "ok", "attribution_ok": "ok", "reason": "吻合"},
    ]}
    verdict, _ = _aggregate_group_verdicts(
        _parse_group_verdicts(json.dumps(eq, ensure_ascii=False)), groups
    )
    assert verdict is None  # 转人工


def test_named_source_claim_grouped_as_source_union():
    """点名整篇文档的声明 → 一组（源全部注入块），不再逐块判“单块完整支撑整句”。"""
    hits = [
        {"source_id": "O2", "section_path": "s1", "text": "O2 引言（核心主张）"},
        {"source_id": "O2", "section_path": "s2", "text": "O2 致谢"},
        {"source_id": "O2", "section_path": "s3", "text": "O2 相关链接"},
    ]
    claims = [
        {"claim": "O2《Harness engineering》核心主张：设计环境、明确意图、反馈回路。",
         "refs": ["O2"]},
    ]
    out = _resolve_citation_groups(claims, hits)
    assert len(out["groups"]) == 1
    g = out["groups"][0]
    assert g["kind"] == "source"
    assert len(g["blocks"]) == 3  # 一条声明一组，而非三条对子


# —— 确定性阶段一提取（2026-09-05 定：替代 LLM 拆声明） ——


def test_deterministic_mid_markers_split_claims():
    """句中多个标引组各自锚定前段；句尾无标引的综论段不产声明。"""
    text = (
        "Anthropic 把关卡写进沙箱与代理层（代码/环境层面）[4]，"
        "OpenAI 把关卡写进 linter、CI 与仓库文档（组织层面）[9][11]，"
        "两者都用机械的可审计机制替代人的实时判断"
    )
    claims = _deterministic_extract_claims(text, [])
    assert len(claims) == 2
    assert claims[0]["refs"] == ["[4]"]
    assert claims[0]["claim"].startswith("Anthropic 把关卡写进沙箱")
    assert claims[1]["refs"] == ["[9]", "[11]"]
    assert claims[1]["claim"].startswith("OpenAI 把关卡写进 linter")
    assert "替代人的实时判断" not in claims[1]["claim"]


def test_deterministic_end_marker_covers_whole_unit():
    """句尾/格尾标引锚定整句/整格（#16 作者行：作者名 + 致谢同格共用一个 [7]）。"""
    text = "Ryan Lopopolo（技术团队成员），致谢 Victor Zhu、Zach Brock [7]。"
    claims = _deterministic_extract_claims(text, [])
    assert len(claims) == 1
    assert claims[0]["refs"] == ["[7]"]
    assert claims[0]["claim"].startswith("Ryan Lopopolo")
    assert "致谢 Victor Zhu" in claims[0]["claim"]


def test_deterministic_table_cells_are_units():
    """表格每格一个单元；只有带标引的格产声明。"""
    text = (
        "| 维度 | OpenAI | Anthropic |\n"
        "|---|---|---|\n"
        "| 作者 | Ryan[7] | 未署名[1] |\n"
        "| 发布日期 | 无明确日期[10] | 2026-05[3] |\n"
    )
    claims = _deterministic_extract_claims(text, [])
    refs = [c["refs"] for c in claims]
    assert refs == [["[7]"], ["[1]"], ["[10]"], ["[3]"]]


def test_deterministic_meta_statements_skipped():
    """证据范围元陈述（含标引）由规则表跳过，不产声明。"""
    text = "需要先说明一个重要前提：根据我检索到的资料，两篇文章在各自来源 [1] 覆盖不全。"
    assert _deterministic_extract_claims(text, []) == []


def test_deterministic_title_named_source_claim():
    """无 [n] 但带标题点名 + 内容归因 → 源级声明（refs=该源 id）。"""
    hits = [{"source_id": "O2", "text": "OpenAI 文章内容"}]
    text = (
        "OpenAI《Harness engineering》核心主张：当工程师团队的主要工作不再是写代码，"
        "而是设计环境、明确意图、构建反馈回路。"
    )
    claims = _deterministic_extract_claims(text, hits)
    assert len(claims) == 1
    assert claims[0]["refs"] == ["O2"]


def test_deterministic_brand_only_prose_not_commitment():
    """品牌泛称、无标题无编号的综论（#13 误杀类）不进红线。"""
    text = (
        "Anthropic 解决的是“agent 会不会把坏事做大的边界问题”——"
        "即 agent 的能力半径如何被限制在安全壳内。"
    )
    assert _deterministic_extract_claims(text, []) == []


def test_deterministic_trailing_marker_after_period_binds():
    """句末标点后的行尾标引归属前句（…处理。[6]），不产生只有编号的碎声明。"""
    text = "技术债像高息贷款，持续小额偿还优于累积后痛苦的爆发式处理。[6]"
    claims = _deterministic_extract_claims(text, [])
    assert len(claims) == 1
    assert claims[0]["refs"] == ["[6]"]
    assert claims[0]["claim"].startswith("技术债像高息贷款")
    assert "爆发式处理" in claims[0]["claim"]


def test_deterministic_report_verb_forward_anchor():
    """标引插在主语与引述动词之间（…也在 [3] 中说 X）→ 前向锚定，不切残句。"""
    text = (
        "Anthropic 自己也在 [3] 中说模型级失败概率下降要靠模型训练与防护"
        "（safeguards），而这正对应 OpenAI 用工程手段在代码库层面做的事。"
    )
    claims = _deterministic_extract_claims(text, [])
    assert len(claims) == 1
    assert claims[0]["refs"] == ["[3]"]
    assert claims[0]["claim"].startswith("Anthropic 自己也在")
    assert "safeguards" in claims[0]["claim"]


def test_unified_checklist_judge_single_call_both_dimensions():
    """v2a：answer_correct + format_appropriate 一次调用出两个结论（Run#114 职责越界修法）。"""
    case = _rag_case(["answer_correct", "format_appropriate"])
    case.annotation.golden_answer = _checklist_golden_text()
    _mark_checklist(case)
    stub = _stub_llm(
        json.dumps(
            {
                "content": _ok_content_json(),
                "format": {"v": "pass", "reason": "结构化、来源分明"},
            },
            ensure_ascii=False,
        )
    )
    result = _result()
    result.output = "O2 主张外层系统设计[1]，A5 主张遏制[2]；两文还有 X 未展开。"
    out = asyncio.run(
        judge_case(case, result, judge_llm=stub, diagnostics_enabled=False)
    )
    assert stub.calls == 1  # 单次调用，不是两个维度各调一次
    assert out["judgments"]["answer_correct"] == "pass"
    assert out["judgments"]["format_appropriate"] == "pass"
    assert out["pending_human"] == []


def test_unified_checklist_judge_format_fail():
    case = _rag_case(["answer_correct", "format_appropriate"])
    case.annotation.golden_answer = _checklist_golden_text()
    _mark_checklist(case)
    stub = _stub_llm(
        json.dumps(
            {
                "content": _ok_content_json(),
                "format": {"v": "fail", "reason": "对比未区分来源"},
            },
            ensure_ascii=False,
        )
    )
    result = _result()
    result.output = "O2 主张外层系统设计[1]，A5 主张遏制[2]；两文还有 X 未展开。"
    out = asyncio.run(
        judge_case(case, result, judge_llm=stub, diagnostics_enabled=False)
    )
    assert out["judgments"]["answer_correct"] == "pass"
    assert out["judgments"]["format_appropriate"] == "fail"


def _split_checklist_case() -> CaseFile:
    """有点表 + boundary_claims + format_expected 的 checklist 用例（走拆分编排）。"""
    case = _rag_case(["answer_correct", "format_appropriate"])
    case.annotation.checklist_points = {
        "core": [{"id": "c1", "text": "核心点", "probe": "覆盖须含 X"}],
        "ext": [
            {"id": "e1", "text": "扩展1", "group": "O2"},
            {"id": "e2", "text": "扩展2", "group": "O2"},
        ],
    }
    case.annotation.checklist_rule = {
        "ext_min_per_group": 2,
        "transparency": "conditional",
    }
    case.annotation.boundary_claims = [
        {
            "id": "b1",
            "claim": "A5 未以 harness engineering 命名",
            "violation_example": "声称 A5 自称 harness engineering",
        }
    ]
    case.annotation.format_expected = "结构化对比并收口"
    return case


def test_split_judge_all_covered_shortcuts_transparency():
    """拆分编排：core/ext 全 covered → transparency 短路不调用（3 次调用）。"""
    case = _split_checklist_case()
    coverage = json.dumps(
        {
            "c1": {"v": "covered", "evidence": "输出有 X"},
            "e1": {"v": "covered", "evidence": "有扩展1"},
            "e2": {"v": "covered", "evidence": "有扩展2"},
        },
        ensure_ascii=False,
    )
    boundary = json.dumps(
        {"v": "pass", "violations": [], "reason": "无违反"},
        ensure_ascii=False,
    )
    fmt = json.dumps({"v": "pass", "reason": "结构化、有收口"}, ensure_ascii=False)
    stub = _stub_llm(coverage, boundary, fmt)
    result = _result()
    result.output = "核心 X[1]；扩展1、扩展2 都覆盖了。"
    out = asyncio.run(judge_case(case, result, judge_llm=stub))
    assert stub.calls == 3  # coverage + boundary + format（transparency 短路）
    assert out["judgments"]["answer_correct"] == "pass"
    assert out["judgments"]["format_appropriate"] == "pass"
    assert out["pending_human"] == []


def test_split_judge_ext_low_triggers_transparency_and_boundary_fail():
    """扩展不足 → transparency 条件触发（第 4 次调用）；boundary 违反合并 fail。"""
    case = _split_checklist_case()
    coverage = json.dumps(
        {
            "c1": {"v": "covered", "evidence": "输出有 X"},
            "e1": {"v": "covered", "evidence": "有扩展1"},
            "e2": {"v": "not_covered", "evidence": "未提及扩展2"},
        },
        ensure_ascii=False,
    )
    boundary = json.dumps(
        {
            "v": "fail",
            "violations": [{"claim_id": "b1", "quote": "A5 自称 harness engineering"}],
            "reason": "输出冒认术语",
        },
        ensure_ascii=False,
    )
    fmt = json.dumps({"v": "pass", "reason": "形态 OK"}, ensure_ascii=False)
    transparency = json.dumps(
        {
            "v": "covered",
            "evidence": "结尾声明还有未展开",
            "declared": True,
            "named_items": ["扩展2"],
            "matched_uncovered": ["e2"],
        },
        ensure_ascii=False,
    )
    stub = _stub_llm(coverage, boundary, fmt, transparency)
    result = _result()
    result.output = "核心 X[1]；扩展1 覆盖；A5 自称 harness engineering；还有内容未展开。"
    out = asyncio.run(
        judge_case(case, result, judge_llm=stub, diagnostics_enabled=False)
    )
    assert stub.calls == 4  # coverage + boundary + format + transparency
    assert out["judgments"]["answer_correct"] == "fail"
    reason = out["judge_reasons"]["answer_correct"]
    assert "扩展主题仅覆盖" in reason  # 扩展不足
    assert "事实边界违反" in reason  # boundary 违规合并
    assert out["judgments"]["format_appropriate"] == "pass"


def _citation_evidence() -> list[dict]:
    return [{
        "gate": True,
        "reason": "类型=compare；注入 2 块",
        "hits": [
            {"source_id": "O2", "section_path": "s1", "text": "规则机械编码进仓库"},
            {"source_id": "A5", "section_path": "s2", "text": "监督能做什么而非在做什么"},
        ],
    }]


def test_citation_v2_no_explicit_commitment_passes_without_stage2():
    """P2：输出无显式引用承诺（无 [n] / 无标题点名归因）→ 红线不触发，零 LLM 调用。"""
    case = _rag_case(["citation_truth"])
    case.annotation.golden_answer = _checklist_golden_text()
    _mark_checklist(case)
    stub = _stub_llm()
    out = asyncio.run(
        judge_case(
            case,
            _result(retrieval=_citation_evidence(), output="根据资料，答案是 X。"),
            judge_llm=stub,
        )
    )
    assert stub.calls == 0  # 确定性提取无声明 → 不进阶段二
    assert out["judgments"]["citation_truth"] == "pass"
    assert "红线未触发" in out["judge_reasons"]["citation_truth"]


def test_citation_v2_deterministic_groups_pass_single_call():
    """cited 声明 → 确定性提取 + 声明组核对一次 LLM → pass。"""
    case = _rag_case(["citation_truth"])
    case.annotation.golden_answer = _checklist_golden_text()
    _mark_checklist(case)
    stage2 = json.dumps(
        {"groups": [
            {"gid": 0, "content_supported": "ok", "attribution_ok": "ok",
             "supporting_blocks": [1], "violations": [],
             "reason": "块原文支撑声明且归属正确"},
        ]},
        ensure_ascii=False,
    )
    stub = _stub_llm(stage2)
    out = asyncio.run(
        judge_case(
            case,
            _result(
                retrieval=_citation_evidence(),
                output="O2 说规则机械编码进仓库。[1]",
            ),
            judge_llm=stub,
        )
    )
    assert stub.calls == 1  # 只剩阶段二一次
    assert out["judgments"]["citation_truth"] == "pass"
    assert "1 条" in out["judge_reasons"]["citation_truth"]


def test_citation_v2_bad_ref_fails_without_stage2():
    """引用编号超出注入证据 → 程序直接判 fail（来源不存在），不进阶段二。"""
    case = _rag_case(["citation_truth"])
    case.annotation.golden_answer = _checklist_golden_text()
    _mark_checklist(case)
    stub = _stub_llm()
    out = asyncio.run(
        judge_case(
            case,
            _result(retrieval=_citation_evidence(), output="某文说 X。[9]"),
            judge_llm=stub,
        )
    )
    assert stub.calls == 0
    assert out["judgments"]["citation_truth"] == "fail"
    assert "来源不存在" in out["judge_reasons"]["citation_truth"]


def test_citation_v2_claim_cap_turns_human():
    """确定性提取超过硬上限 80 → 整份转人工，不部分采纳、不进入阶段二。"""
    case = _rag_case(["citation_truth"])
    case.annotation.golden_answer = _checklist_golden_text()
    _mark_checklist(case)
    output = "\n".join(
        f"声明{i} 说 X。[1]" for i in range(81)
    )
    stub = _stub_llm()
    out = asyncio.run(
        judge_case(
            case,
            _result(retrieval=_citation_evidence(), output=output),
            judge_llm=stub,
        )
    )
    assert stub.calls == 0  # 未进阶段二
    assert "citation_truth" in out["pending_human"]
    assert "80" in out["judge_reasons"]["citation_truth"]


def _plain_golden() -> str:
    return (
        "判 pass：内容正确——harness engineering 的关键点：环境 / 意图 / 反馈回路；"
        "表达合适：问定义先给定义再给定位。判 fail：缺任一关键点或问定义却整篇概述。"
    )


def test_unified_user_judge_single_call_for_plain_golden():
    """非清单型 answer_correct + format_appropriate 也一次调用出两个结论（P1）。"""
    case = _rag_case(["answer_correct", "format_appropriate"])
    case.annotation.golden_answer = _plain_golden()
    stub = _stub_llm(
        json.dumps(
            {
                "verdicts": {
                    "answer_correct": {"v": "pass", "reason": "三关键点齐备"},
                    "format_appropriate": {"v": "fail", "reason": "问定义却给整篇概述"},
                }
            },
            ensure_ascii=False,
        )
    )
    result = _result()
    result.output = "harness engineering 围绕环境、意图、反馈回路……概述……"
    out = asyncio.run(judge_case(case, result, judge_llm=stub))
    assert stub.calls == 1
    assert out["judgments"]["answer_correct"] == "pass"
    assert out["judgments"]["format_appropriate"] == "fail"
    assert out["pending_human"] == []
    assert "diagnostics" in out


def test_unified_user_judge_malformed_goes_pending():
    """统一用户判官未按结构输出 → 两个维度都转人工，不静默判过。"""
    case = _rag_case(["answer_correct", "format_appropriate"])
    case.annotation.golden_answer = _plain_golden()
    stub = _stub_llm("我判 pass")
    result = _result()
    result.output = "harness engineering 是……"
    out = asyncio.run(judge_case(case, result, judge_llm=stub))
    assert stub.calls == 1
    assert "answer_correct" not in out["judgments"]
    assert "format_appropriate" not in out["judgments"]
    assert out["pending_human"] == ["answer_correct", "format_appropriate"]
    assert "判官未按统一用户维度结构输出" in out["judge_reasons"]["answer_correct"]


def test_parse_unified_user_shape():
    raw = json.dumps(
        {
            "verdicts": {
                "answer_correct": {"v": "pass", "reason": "ok"},
                "format_appropriate": {"v": "uncertain", "reason": "缺信息"},
            }
        },
        ensure_ascii=False,
    )
    parsed = _parse_unified_user(raw)
    assert parsed["answer_correct"]["v"] == "pass"
    assert _parse_unified_user('{"verdicts": 1}') is None


def test_checklist_points_block_formats_ids():
    """机器可读点表存在时输出 id 表；不存在时返回空串（回退主题名）。"""
    case = _rag_case(["answer_correct"])
    assert _checklist_points_block(case) == ""
    case.annotation.checklist_points = {
        "core": [{"id": "o2-c1", "text": "外层系统设计"}],
        "ext": [],
        "transparency": {"id": "transparency", "text": "条件强制规则"},
    }
    block = _checklist_points_block(case)
    assert "o2-c1" in block and "外层系统设计" in block
    assert "transparency" in block
