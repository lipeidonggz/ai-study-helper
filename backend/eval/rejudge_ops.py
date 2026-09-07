"""判官重判操作（2026-09-07）：对已存 attempt 输出只重跑判官，不改 agent 输出。

三种操作，均以"审计 overlay"落库（不覆盖原始判定，保留判官版本对比）：
- coverage_rejudge：覆盖标注 ×N 次（checklist 型 16 点，evidence-first），产 点 × rep 矩阵；
- citation_rejudge：引用真实性单次重判（A：写回最新判官版本结论供界面审计）；
- citation_stability：引用真实性同输出 ×N（B：声明组 × rep 矩阵 + 稳定性汇总）。

确定性提取保证：同一输出每次提取的声明组（gid/文本/refs）一致，故矩阵行稳定。
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from eval.runner import (
    LLMClient,
    _JUDGE_CORE_SYSTEM,
    _JUDGE_COVERAGE_MODULE,
    _build_coverage_prompt,
    _build_evidence_block,
    _chat_judge_once,
    _citation_v2_judge,
    _parse_flat_coverage,
)
from eval.schema import CaseFile


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def attempt_evidence(attempt: dict) -> list[dict]:
    """从 attempt 的 trace 提取检索证据（与 runner 白盒同源）。"""
    for t in attempt.get("trace") or []:
        if isinstance(t, dict) and t.get("type") == "retrieval":
            return [t.get("data") or {}]
    return []


def _point_ids(case: CaseFile) -> list[str]:
    pts = case.annotation.checklist_points or {}
    ids: list[str] = []
    for group in ("core", "ext"):
        for p in pts.get(group) or []:
            if isinstance(p, dict) and p.get("id"):
                ids.append(p["id"])
    return ids


def _point_id_groups(case: CaseFile) -> tuple[list[str], list[str]]:
    pts = case.annotation.checklist_points or {}
    core_ids = [
        p["id"] for p in (pts.get("core") or []) if isinstance(p, dict) and p.get("id")
    ]
    ext_ids = [
        p["id"] for p in (pts.get("ext") or []) if isinstance(p, dict) and p.get("id")
    ]
    return core_ids, ext_ids


def _points_meta(case: CaseFile) -> dict[str, dict[str, str]]:
    """覆盖点要求元数据（text + 覆盖探针），随记录落库供界面展示点要求。"""
    pts = case.annotation.checklist_points or {}
    meta: dict[str, dict[str, str]] = {}
    for group in ("core", "ext"):
        for p in pts.get(group) or []:
            if isinstance(p, dict) and p.get("id"):
                meta[str(p["id"])] = {
                    "text": str(p.get("text") or ""),
                    "probe": str(p.get("probe") or ""),
                }
    return meta


def _flatten_coverage(parsed: dict, ids: list[str]) -> dict[str, dict]:
    flat: dict[str, dict] = {}
    for group in ("core", "ext"):
        for pid, item in (parsed.get(group) or {}).items():
            if isinstance(item, dict):
                flat[pid] = {
                    "v": item.get("v", ""),
                    "evidence": item.get("evidence", ""),
                }
    return {pid: flat.get(pid, {"v": "", "evidence": ""}) for pid in ids}


async def coverage_rejudge(
    case: CaseFile,
    attempt: dict,
    judge_llm: LLMClient,
    repeats: int,
) -> dict:
    """覆盖标注同输出 × repeats：只跑覆盖标注一路（不做 boundary/format/transparency）。"""
    ids = _point_ids(case)
    core_ids, ext_ids = _point_id_groups(case)
    evidence = attempt_evidence(attempt)
    evidence_text, _hits = _build_evidence_block(evidence)
    output = (attempt.get("output") or "").strip() or "（无输出）"
    system = _JUDGE_CORE_SYSTEM + _JUDGE_COVERAGE_MODULE
    prompt = _build_coverage_prompt(case, evidence_text, output)
    sem = asyncio.Semaphore(4)

    async def _one(_i: int) -> dict:
        async with sem:
            try:
                raw, _usage = await _chat_judge_once(judge_llm, system, prompt)
                parsed = _parse_flat_coverage(raw, core_ids, ext_ids) or {
                    "core": {},
                    "ext": {},
                }
                return {"ok": True, "points": _flatten_coverage(parsed, ids)}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    reps = await asyncio.gather(*[_one(i) for i in range(repeats)])
    summary: dict[str, dict[str, int]] = {}
    for pid in ids:
        summary[pid] = {}
    for rep in reps:
        for pid, item in (rep.get("points") or {}).items():
            v = str(item.get("v") or "parse_fail")
            summary.setdefault(pid, {})[v] = summary[pid].get(v, 0) + 1
    return {
        "created_at": _now_iso(),
        "repeats": repeats,
        "point_ids": ids,
        "points_meta": _points_meta(case),
        "reps": reps,
        "summary": summary,
        "errors": sum(1 for r in reps if not r.get("ok")),
    }


async def citation_rejudge(
    case: CaseFile,
    attempt: dict,
    judge_llm: LLMClient,
) -> dict:
    """引用真实性单次重判（判官最新版本），返回结论 + 声明组明细。"""
    evidence = attempt_evidence(attempt)
    result = SimpleNamespace(
        output=(attempt.get("output") or "").strip() or "（无输出）"
    )
    verdict, reason, _usage, detail = await _citation_v2_judge(
        case, result, "citation_truth", judge_llm, evidence=evidence or None
    )
    return {
        "created_at": _now_iso(),
        "verdict": verdict,
        "reason": reason or "",
        "detail": detail or {},
    }


async def citation_stability(
    case: CaseFile,
    attempt: dict,
    judge_llm: LLMClient,
    repeats: int,
) -> dict:
    """引用真实性同输出 × repeats：声明组 × rep 矩阵 + 稳定性汇总。"""
    evidence = attempt_evidence(attempt)
    sem = asyncio.Semaphore(3)

    async def _one(_i: int) -> dict:
        async with sem:
            result = SimpleNamespace(
                output=(attempt.get("output") or "").strip() or "（无输出）"
            )
            verdict, reason, _usage, detail = await _citation_v2_judge(
                case, result, "citation_truth", judge_llm, evidence=evidence or None
            )
            return {"verdict": verdict, "reason": reason or "", "detail": detail or {}}

    reps = await asyncio.gather(*[_one(i) for i in range(repeats)])
    first = next((r for r in reps if r.get("detail", {}).get("groups")), None)
    groups = (first or {}).get("detail", {}).get("groups") or []
    matrix: list[dict[str, Any]] = []
    summary: dict[int, dict[str, int]] = {}
    for g in groups:
        gid = g.get("gid")
        row: dict[str, Any] = {
            "gid": gid,
            "claim": g.get("claim", ""),
            "refs": g.get("refs") or [],
            "cells": [],
        }
        summary[gid] = {}
        for rep in reps:
            g2 = next(
                (x for x in (rep.get("detail", {}).get("groups") or []) if x.get("gid") == gid),
                {},
            )
            cs = str(g2.get("content_supported") or "")
            ao = str(g2.get("attribution_ok") or "")
            state = (
                "violation"
                if cs == "violation" or ao == "violation"
                else ("equivocal" if cs == "equivocal" or ao == "equivocal" else "ok")
            )
            row["cells"].append(state)
            summary[gid][state] = summary[gid].get(state, 0) + 1
        matrix.append(row)
    return {
        "created_at": _now_iso(),
        "repeats": repeats,
        "verdicts": [r.get("verdict") for r in reps],
        "matrix": matrix,
        "summary": summary,
    }
