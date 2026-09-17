"""抽取服务：线上流水线（Pass 1 → 体检 → 谓词清洗 → 谓词归一 + 红线闸门），供 API / 界面触发。

为什么在 app/ 而不是 scripts/：这是**线上流程**的一部分（界面可触发），scripts/ 里是离线实验脚本。
TODO：目前复用 `scripts/compile_slice_b6.py` 里的提示词与工具函数（单一来源，避免两份实现漂移）；
      后续应把它们搬进 app/compile/（text.py / prompts.py），让 app 层不再依赖 scripts。

红线闸门：体检没过红线 → 任务判 failed、不进入下一步（产物照旧落盘，供人工排查）。

产物（backend/data/compile/<source_id>/）：
  claims_raw.json  Pass 1 原始输出（体检前）
  claims.json      体检后 + 已归一（每条带 predicate_normalized）
  report.json      体检报告（五项 + 红线判定 + 丢弃/改写/标记清单）
  summary.json     元信息（模型、时间、条数、红线结论）
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.agent.llm import DeepSeekLLMClient, LLMMessage
from app.compile.health import run_health_check
from app.kb.manifest import parse_manifest
from scripts.compile_slice_b6 import (
    MANIFEST_PATH,
    PASS1_SYSTEM,
    _build_clean_t,
    _chat_json,
    _clean_predicates,
    _file_units,
    _normalize_predicates,
    _validate_claims,
    _window_text,
)

BACKEND = Path(__file__).resolve().parents[2]          # backend/
DEFAULT_ARTIFACT_ROOT = BACKEND / "data" / "compile"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def red_line_gate(report: dict) -> tuple[bool, str]:
    """体检红线闸门：红线不过 → 该篇编译失败、**不进入下一步**。

    返回 (是否放行, 未通过的原因)。原因是给人看的，按红线项逐条列。
    兼容没有 check_spec 的旧报告（退化为读 verdict）。
    """
    if report.get("red_line_pass"):
        return True, ""
    bad = [c for c in (report.get("check_spec") or []) if c.get("red_line") and not c.get("pass")]
    if not bad:
        # 老报告没有 check_spec：退化为读 verdict；有 red_line 标记就只认红线的，
        # 没有标记（更老的产物）才把所有不过项都算上——宁可严，不可放行。
        bad = [
            {"key": k, "value": v.get("value"), "threshold": v.get("threshold")}
            for k, v in (report.get("verdict") or {}).items()
            if v.get("pass") is False and ("red_line" not in v or v.get("red_line"))
        ]
    detail = (
        "；".join(
            f"{b.get('name') or b.get('key')}（实测 {b.get('value')}，阈值 {b.get('threshold')}）"
            for b in bad
        )
        or "详见体检报告"
    )
    return False, f"体检红线未通过：{detail}。该篇不进入下一步（产物保留供排查）"


def summary_verdict(summary: dict) -> tuple[bool, str]:
    """从**落盘 summary** 还原闸门结论（进程重启后走这条）。

    优先用 `_run` 落下的 `red_line_gate`（带具体是哪条红线不过）；
    老产物没有它时退化为只看 `red_line_pass`——宁可给一句兜底原因，也不放行。
    """
    g = summary.get("red_line_gate") or {}
    if g:
        passed = bool(g.get("passed"))
        return passed, "" if passed else (g.get("reason") or "体检红线未通过（详见体检报告）")
    return red_line_gate(summary)


@dataclass
class Job:
    """一次抽取任务的状态（内存态；进程重启后由落盘 summary.json 推导）。"""

    source_id: str
    status: str = "idle"          # idle | running | done | failed
    stage: str = ""               # pass1 | health | normalize | done
    progress: str = ""
    started_at: str = ""
    finished_at: str = ""
    error: str = ""
    summary: dict = field(default_factory=dict)


class CompileService:
    """按篇管理抽取任务：同一篇同时只允许一个任务。"""

    def __init__(self, artifact_root: Path | str = DEFAULT_ARTIFACT_ROOT) -> None:
        self._root = Path(artifact_root)
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ---------- 路径 / 读取 ----------

    def _dir(self, source_id: str) -> Path:
        d = self._root / source_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def status(self, source_id: str) -> dict:
        job = self._jobs.get(source_id)
        if job is not None:
            return asdict(job)
        f = self._root / source_id / "summary.json"
        if f.exists():
            s = json.loads(f.read_text(encoding="utf-8"))
            ok, why = summary_verdict(s)
            return {
                "source_id": source_id,
                "status": "done" if ok else "failed",
                "stage": "done" if ok else "failed",
                "progress": "完成（来自落盘产物）" if ok else "体检红线未通过（来自落盘产物）",
                "started_at": "",
                "finished_at": "",
                "error": why,
                "summary": s,
            }
        return asdict(Job(source_id=source_id))

    def report(self, source_id: str) -> dict | None:
        f = self._root / source_id / "report.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def all_statuses(self) -> dict[str, dict]:
        """所有源的抽取状态（内存任务优先，其次落盘产物）——供界面表格显示。"""
        out: dict[str, dict] = {}
        if self._root.exists():
            for d in self._root.iterdir():
                f = d / "summary.json"
                if d.is_dir() and f.exists():
                    s = json.loads(f.read_text(encoding="utf-8"))
                    ok, why = summary_verdict(s)
                    out[d.name] = {
                        "status": "done" if ok else "failed",
                        "finished_at": s.get("finished_at", ""),
                        "claims_kept": s.get("claims_kept", 0),
                        "red_line_pass": s.get("red_line_pass", False),
                        "error": why,
                    }
        for sid, job in self._jobs.items():
            out[sid] = {
                "status": job.status,
                "finished_at": job.finished_at,
                "claims_kept": job.summary.get("claims_kept", 0),
                "red_line_pass": job.summary.get("red_line_pass", False),
                "progress": job.progress,
                "error": job.error,
            }
        return out

    def claims(self, source_id: str, limit: int = 0) -> list[dict]:
        """归一后的断言清单（limit=0 → 全部）。"""
        f = self._root / source_id / "claims.json"
        if not f.exists():
            return []
        data = json.loads(f.read_text(encoding="utf-8"))
        rows = data if not limit else data[:limit]
        out = []
        for c in rows:
            out.append(
                {
                    "subject": c.get("subject"),
                    "predicate": c.get("predicate"),
                    "predicate_clean": c.get("predicate_clean"),
                    "predicate_normalized": c.get("predicate_normalized"),
                    "predicate_status": c.get("predicate_status"),
                    "predicate_invented": c.get("predicate_invented"),
                    "sign": c.get("sign"),
                    "polarity": c.get("polarity"),
                    "object": c.get("object"),
                    "marks": (c.get("health") or {}).get("marks") or [],
                    "anchor": (c.get("health") or {}).get("anchor")
                    or (c.get("evidence_texts") or [""])[0][:300],
                }
            )
        return out

    # ---------- 启动 / 执行 ----------

    def start(self, source_id: str, *, api_key: str, model: str = "deepseek-chat") -> dict:
        src = next((s for s in parse_manifest(MANIFEST_PATH) if s.source_id == source_id), None)
        if src is None:
            raise KeyError(f"素材 {source_id} 不在台账中")
        if not src.collected:
            raise ValueError(f"素材 {source_id} 尚未采集")
        cur = self._jobs.get(source_id)
        if cur is not None and cur.status == "running":
            raise RuntimeError(f"{source_id} 的抽取正在进行中")
        job = Job(source_id=source_id, status="running", stage="pass1", progress="准备中", started_at=_now())
        self._jobs[source_id] = job
        self._tasks[source_id] = asyncio.create_task(self._run(job, src, api_key, model))
        return asdict(job)

    async def _run(self, job: Job, src, api_key: str, model: str) -> None:
        try:
            _ft, sections = _file_units(src)[0]
            clean_t = _build_clean_t(sections)
            client = DeepSeekLLMClient(api_key=api_key, model=model)

            # ---- Pass 1：按窗口抽断言（宽召回）----
            windows = _window_text(clean_t, 5000)
            claims: list[dict] = []
            for wi, w in enumerate(windows, start=1):
                job.progress = f"Pass1 抽取窗口 {wi}/{len(windows)}"
                user = (
                    f"内容单元窗口：source_id={job.source_id}；file_idx=0；window={wi}/{len(windows)}。\n"
                    f"下面是该窗口清洗后原文，请抽原子断言：\n\n{w}"
                )
                part, _ = await _chat_json(
                    client,
                    [LLMMessage(role="system", content=PASS1_SYSTEM), LLMMessage(role="user", content=user)],
                    label=f"Pass1 {job.source_id} 窗口{wi}/{len(windows)}",
                )
                claims.extend(part.get("claims") or [])

            # 结构校验（去空 roles、剔除缺 subject/predicate）
            norm: list[dict] = []
            for c in claims:
                if not c.get("roles"):
                    c.pop("roles", None)
                if c.get("subject") and c.get("predicate"):
                    norm.append(c)
            claims = norm
            pred_health = _validate_claims(claims)

            # ---- 体检（质量门）----
            job.stage = "health"
            job.progress = f"体检（{len(claims)} 条）"
            kept, report = run_health_check(claims, clean_t, job.source_id)

            # ---- 谓词归一（独立小步，只映射不增删）----
            job.stage = "clean"
            job.progress = f"谓词清洗（{len(kept)} 条）"
            cl = await _clean_predicates(client, job.source_id, kept, clean_t)
            print(
                f"[B6] {job.source_id}: 谓词清洗 目标 {cl['targets']} 条"
                f" → 清洗 {cl['cleaned']}、无变化 {cl['noop']}、护栏拒绝 {cl['rejected']}"
            )

            job.stage = "normalize"
            job.progress = f"谓词归一（{len(kept)} 条）"
            nv = await _normalize_predicates(client, job.source_id, kept, clean_t)

            # 归一完成 → 回填报告里"标记清单"的归一化谓词（被丢弃的 claim 没归一，UI 显示 —）
            report["marked"] = [
                {
                    "idx": (c.get("health") or {}).get("src_idx", i),
                    "subject": c.get("subject"),
                    "predicate": c.get("predicate"),
                    "predicate_normalized": c.get("predicate_normalized"),
                    "sign": c.get("sign"),
                    "polarity": c.get("polarity"),
                    "object": c.get("object") or "",
                    "marks": (c.get("health") or {}).get("marks") or [],
                    "anchor": (c.get("health") or {}).get("anchor", ""),
                }
                for i, c in enumerate(kept)
                if (c.get("health") or {}).get("marks")
            ]
            # 待定清单（谓词归不进受控关系 → 不进图；内容仍在 chunk 里，靠向量兜底）
            report["pending"] = [
                {
                    "idx": (c.get("health") or {}).get("src_idx", i),
                    "subject": c.get("subject"),
                    "predicate": c.get("predicate"),
                    "invented": c.get("predicate_invented"),
                    "object": c.get("object") or "",
                    "anchor": (c.get("health") or {}).get("anchor", ""),
                }
                for i, c in enumerate(kept)
                if c.get("predicate_status") == "pending"
            ]
            report["predicate_status"] = {"mapped": nv["mapped"], "forced": nv["forced"], "pending": nv["pending"]}
            report["predicate_clean"] = cl

            # 表外词累积表（按篇累积，供定期收敛：补进表 或 确认丢弃）
            tally_path = self._root / "_outside_words.json"
            tally = json.loads(tally_path.read_text(encoding="utf-8")) if tally_path.exists() else {}
            for c in kept:
                w = c.get("predicate_invented")
                if not w:
                    continue
                st = c.get("predicate_status") or ""
                e = tally.setdefault(w, {"mapped": 0, "forced": 0, "pending": 0, "sources": []})
                e[st] = e.get(st, 0) + 1
                if job.source_id not in e["sources"]:
                    e["sources"].append(job.source_id)
            tally_path.write_text(json.dumps(tally, ensure_ascii=False, indent=2), encoding="utf-8")

            # ---- 落盘 ----
            d = self._dir(job.source_id)
            (d / "claims_raw.json").write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")
            (d / "claims.json").write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
            (d / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = {
                "source_id": job.source_id,
                "model": model,
                "finished_at": _now(),
                "claims_raw": len(claims),
                "claims_kept": len(kept),
                "dropped": len(report["dropped"]),
                "rewritten": len(report["rewritten"]),
                "marked": len(report["marked"]),
                "normalized": nv["mapped"],
                "forced": nv["forced"],
                "pending": nv["pending"],
                "invented_words": nv["invented"],
                "cleaned": cl["cleaned"],
                "clean_rejected": cl["rejected"],
                "relations": len(nv["kinds"]),
                "predicate_health": {
                    "kinds": pred_health["pred_kinds"],
                    "long": len(pred_health["long_pred"]),
                    "neg": len(pred_health["neg_in_pred"]),
                    "clause": len(pred_health["clause_in_pred"]),
                },
                "red_line_pass": report["red_line_pass"],
            }
            # 体检红线闸门：不过 → 该篇编译失败、不进入下一步（产物已落盘，供人工排查）
            ok, why = red_line_gate(report)
            summary["red_line_gate"] = {"passed": ok, "reason": why}
            (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

            job.finished_at = _now()
            job.summary = summary
            if ok:
                job.status = "done"
                job.stage = "done"
                job.progress = "完成"
            else:
                job.status = "failed"
                job.stage = "failed"
                job.progress = "体检红线未通过"
                job.error = why
                print(f"[B6] {job.source_id}: {why}")
        except Exception as exc:  # 任务失败不抛出（状态里可见）
            job.status = "failed"
            job.error = str(exc)[:500]
            job.finished_at = _now()
