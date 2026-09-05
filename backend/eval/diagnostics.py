"""白盒归因件（P3，2026-09-04 拍板）：verdict-first 编排下，只在黑盒 fail / pending 后调用。

三段输出（diagnostics，不改判分、不产生新的 pending）：
1. 检索充分性（retrieval_sufficiency）：只查金标准 checklist_points 中未覆盖的失败要点；
   对每个要点做 注入蕴含 →（不足才）全库蕴含，落 S1（注入已覆盖）/ S2（库有未注入）/ S3（库无）；
   第一个非 S1 即短路（存在性扫描）。
2. 引用内部件（citation_internal）：充分性过后才跑；漏引记账 = 内容明显取自注入证据却未标注
   [n] / 未点名文档（只在本次真发生检索注入时启用）。
3. 提炼归因（summarization_attribution）：充分性过且存在失败要点 → 料够但模型没用好（模型侧嫌疑）。
"""

from __future__ import annotations

import json
import re
from typing import Callable

from app.agent.llm import LLMClient, LLMMessage

Searcher = Callable[[str], list[dict]]

_ENTAIL_SYSTEM = (
    "你是检索充分性的证据支撑判定（白盒归因，不判模型回答好坏）。给定【要点】与【证据块】，"
    "判断这些证据块单独或合起来是否足以支撑该要点：允许原文需要跨块 / 跨段综合归纳（材料足够即可），"
    "禁止仅凭通用常识或模型自身知识补全（那不是证据支撑）。材料不足标 unsupported。"
    "只输出要求的 JSON，不要输出其他内容。"
)

_MISSING_SYSTEM = (
    "你是漏引记账环节（白盒，不判分、不改判）。模型在本次检索注入后作答；"
    "找出内容明显取自【检索证据】注入块、却没有 [n] 编号也没有点名文档作为来源的声明。"
    "只列确凿的（事实/表述与注入块高度吻合、可指出依据哪块），模糊或可能是内化知识的不列。"
    "只输出要求的 JSON，不要输出其他内容。"
)


def _loads_obj(text: str) -> dict | None:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _extract_json_object(text: str) -> str | None:
    """截取首个 '{' 到花括号深度归零处（容忍判官输出前后杂讯 / 未转义引号）。"""
    start = (text or "").find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _parse_json_dict(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
        t = t.strip()
    for cand in {t, _extract_json_object(t) or ""}:
        if not cand:
            continue
        data = _loads_obj(cand)
        if data is not None:
            return data
    return None


def _fmt_hits(hits: list[dict] | None) -> str:
    hits = hits or []
    if not hits:
        return "（无证据块）"
    lines = []
    for i, h in enumerate(hits, 1):
        src = h.get("source_id", "")
        sec = h.get("section_path", "")
        text = str(h.get("text", ""))[:600]
        lines.append(f"[{i}] {src} | {sec}\n{text}")
    return "\n\n".join(lines)


def _point_map(case) -> dict[str, str]:
    pts = (case.annotation.checklist_points or {}) if case.annotation else {}
    out: dict[str, str] = {}
    for group in ("core", "ext"):
        for p in pts.get(group) or []:
            if isinstance(p, dict) and p.get("id"):
                out[str(p["id"])] = str(p.get("text", ""))
    return out


def _empty() -> dict:
    return {
        "retrieval_sufficiency": "na",
        "sufficiency_first_blocked": None,
        "citation_internal": "na",
        "citation_internal_notes": [],
        "summarization_attribution": "na",
        "notes": [],
    }


async def _entail_batch(
    judge_llm: LLMClient,
    points: list[tuple[str, str]],
    hits: list[dict],
    label: str,
) -> dict[str, str]:
    """一次 LLM 调用：多要点 × 同一批证据，判 supported / unsupported / uncertain。"""
    point_lines = "\n".join(f"- {pid}：{text}" for pid, text in points)
    prompt = f"""判断以下要点能否由给出的证据支撑（检索充分性 白盒判定）。

【证据来源】{label}
【证据块】
{_fmt_hits(hits)}

【要点】
{point_lines}

判定口径：
- supported：证据块单独或合起来足以支撑该要点（允许跨块 / 跨段综合归纳，材料足够即可）；
- unsupported：证据不足或根本没有对应材料；
- uncertain：无法判断（reason 说明缺什么）。
只输出一个 JSON 对象：
{{"points": [{{"id": "<要点 id>", "v": "supported|unsupported|uncertain", "reason": "≤80 字"}}]}}
"""
    resp = await judge_llm.chat(
        [
            LLMMessage(role="system", content=_ENTAIL_SYSTEM),
            LLMMessage(role="user", content=prompt),
        ]
    )
    data = _parse_json_dict(resp.content or "")
    result: dict[str, str] = {}
    if data is None:
        return result
    for item in data.get("points") or []:
        if isinstance(item, dict) and item.get("id"):
            v = str(item.get("v", "")).strip().lower()
            result[str(item["id"])] = (
                "supported" if v.startswith("supported") else "unsupported"
            )
    return result


async def _find_missing_citations(
    judge_llm: LLMClient,
    output: str,
    hits: list[dict],
) -> list[str]:
    """漏引记账：内容明显取自注入证据却未标注来源的声明清单（不判分）。"""
    prompt = f"""找出漏引声明（引用内部件 白盒记账）。

【检索证据】（本次注入；编号与模型输出中的 [n] 一一对应）
{_fmt_hits(hits)}

【模型输出】
{output.strip() or "（无输出）"}

只输出一个 JSON 对象：
{{"missing": [{{"statement": "声明原文（≤120 字）", "note": "依据哪块（编号）"}}]}}
"""
    resp = await judge_llm.chat(
        [
            LLMMessage(role="system", content=_MISSING_SYSTEM),
            LLMMessage(role="user", content=prompt),
        ]
    )
    data = _parse_json_dict(resp.content or "")
    if data is None:
        return []
    missing: list[str] = []
    for item in data.get("missing") or []:
        if isinstance(item, dict) and item.get("statement"):
            note = str(item.get("note", "")).strip()
            missing.append(
                f"{item['statement']}" + (f"（{note}）" if note else "")
            )
    return missing


async def run_diagnostics(
    case,
    result,
    judge_llm: LLMClient | None,
    *,
    failing_point_ids: list[str],
    searcher: Searcher | None = None,
) -> dict:
    """白盒归因入口（P3）。case/result 鸭子类型，避免与 runner 循环依赖。"""
    if judge_llm is None or case.mode != "rag":
        return {}
    points = _point_map(case)
    if not points:
        return {}  # 无点表用例不做充分性诊断（白盒无本）
    failing = [p for p in failing_point_ids if p in points]
    if not failing:
        return {}  # 黑盒 fail/pending 但无内容失败要点（如纯 format fail）→ 无本可查

    evidence = result.retrieval_evidence[-1] if result.retrieval_evidence else {}
    hits = evidence.get("hits") or []
    gate = bool(evidence.get("gate"))
    out = _empty()

    # ① 注入集蕴含（一次批量，所有失败要点）
    verdicts_inj = await _entail_batch(
        judge_llm, [(p, points[p]) for p in failing], hits, "本次注入证据"
    )
    first_blocked: dict | None = None
    for pid in failing:
        if verdicts_inj.get(pid) == "supported":
            continue  # S1：注入已覆盖（料够锅在模型）
        # 注入不足：进全库定位 S2 / S3
        if searcher is None:
            first_blocked = {
                "point_id": pid,
                "state": "S2_S3_UNKNOWN",
                "note": "无全库检索器，无法区分库有未注入 / 库无（人审）",
            }
            break
        kb_hits = await searcher(points[pid])
        verdicts_kb = await _entail_batch(
            judge_llm, [(pid, points[pid])], kb_hits, "全库检索候选"
        )
        state = "S2" if verdicts_kb.get(pid) == "supported" else "S3"
        first_blocked = {
            "point_id": pid,
            "state": state,
            "note": (
                "注入无支撑，全库检索到可支撑块（锅在检索/注入）"
                if state == "S2"
                else "注入与全库均未找到可支撑块（语料缺口或要点需跨段归纳/期望过高，人审）"
            ),
        }
        break

    if first_blocked is not None:
        # 短路：充分性不过 → 定案检索/语料层，不再展开引用内部件与提炼归因
        out["retrieval_sufficiency"] = "fail"
        out["sufficiency_first_blocked"] = first_blocked
        return out

    # ② 充分性过（所有失败要点均被注入支撑，S1）→ 才查后两段
    out["retrieval_sufficiency"] = "pass"
    if failing:
        out["summarization_attribution"] = "fail"
        out["notes"].append(
            "注入证据足以支撑全部失败要点（S1）：失败归因指向模型侧（没用上/用错/提炼错）"
        )
    if gate:
        missing = await _find_missing_citations(judge_llm, result.output, hits)
        if missing:
            out["citation_internal"] = "fail"
            out["citation_internal_notes"] = missing[:10]
        else:
            out["citation_internal"] = "pass"
    return out
