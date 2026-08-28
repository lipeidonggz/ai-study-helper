"""评测台 API：用例管理 + 跑批管理 + 人工标注（0017 需求 P1）。

接口总览：
  /api/eval/cases            GET 列表（支持筛选） / POST 新建
  /api/eval/cases/{id}       GET / PUT / DELETE
  /api/eval/cases/{id}/golden-answer  PATCH 只更新金标准答案要点（详情页复核用）
  /api/eval/runs             GET 历史跑批 / POST 启动跑批
  /api/eval/runs/{id}        GET 详情（含逐用例结果）
  /api/eval/runs/{id}        DELETE 删除历史跑批（运行中不可删）
  /api/eval/runs/{id}/cancel POST 取消
  /api/eval/runs/{id}/verify     POST 标记已人工核验 / POST unverify 取消
  /api/eval/runs/{id}/cases/{case_id}/rerun  POST 重跑单条（覆盖原结果）
  /api/eval/runs/{id}/cases/{case_id}  PATCH 人工标注
  /api/eval/runs/{id}/export GET 导出 JSON
"""

import json
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from eval import case_store, run_store
from eval.schema import CaseFile

router = APIRouter(prefix="/api/eval", tags=["eval"])


# —— 请求体模型 ——


class RunCreate(BaseModel):
    """启动跑批的请求：名称、模型、并发、重试、用例筛选。"""

    name: str = ""
    llm: Literal["real", "fake"] = "real"
    concurrency: int = Field(default=50, ge=1, le=2500)
    retries: int = Field(default=1, ge=0, le=5)
    repeat: int = Field(default=20, ge=1, le=100)  # 每条用例执行次数（稳定性评测）
    prompt_variant: str = "baseline"  # 提示词变体（主动式验证：baseline/no_behavior/cot/minimal）
    case_filter: dict = Field(
        default_factory=lambda: {"ids": [], "categories": [], "tags": []}
    )


class AnnotateIn(BaseModel):
    """人工标注：答案正确 / 拒答合理 / 备注（空字符串表示未填）。"""

    answer_correct: Literal["", "对", "错", "存疑"] = ""
    refusal: Literal["", "合理", "不合理", "不适用"] = ""
    note: str = ""


class GoldenAnswerIn(BaseModel):
    """仅更新用例金标准答案要点（跑批详情复核时就地调整）。"""

    golden_answer: str = ""


def _manager(request: Request):
    return request.app.state.eval_manager


def _db(request: Request):
    return _manager(request).db_path


def _cases_dir(request: Request):
    return _manager(request).cases_dir


def _deps(request: Request):
    return request.app.state.deps


# —— 用例管理 ——


@router.get("/cases")
def list_cases(
    request: Request,
    q: str = "",
    category: str = "",
    enabled: str = "",
    criteria: str = "",
    tags: str = "",
) -> list[dict]:
    """用例列表：按关键词/类别/启用状态/验收维度/标签筛选。"""
    cases = case_store.list_cases(_cases_dir(request))
    if q:
        ql = q.lower()
        cases = [c for c in cases if ql in c.id.lower() or ql in c.title.lower()]
    if category:
        cases = [c for c in cases if c.category == category]
    if enabled:
        want = enabled == "true"
        cases = [c for c in cases if c.enabled is want]
    if criteria:
        cases = [c for c in cases if criteria in c.expected.criteria]
    if tags:
        tag_set = {t.strip() for t in tags.split(",") if t.strip()}
        cases = [c for c in cases if tag_set.issubset(set(c.tags))]
    return [c.model_dump() for c in cases]


@router.get("/cases/{case_id}")
def get_case(case_id: str, request: Request) -> dict:
    case = case_store.get_case(case_id, _cases_dir(request))
    if case is None:
        raise HTTPException(404, f"用例不存在：{case_id}")
    return case.model_dump()


@router.post("/cases", status_code=201)
def create_case(body: CaseFile, request: Request) -> dict:
    if case_store.get_case(body.id, _cases_dir(request)) is not None:
        raise HTTPException(409, f"用例已存在：{body.id}")
    case_store.save_case(body, cases_dir=_cases_dir(request), author="local")
    _deps(request).log_store.append("eval", {"action": "case_create", "id": body.id})
    return case_store.get_case(body.id, _cases_dir(request)).model_dump()


@router.put("/cases/{case_id}")
def update_case(case_id: str, body: CaseFile, request: Request) -> dict:
    if case_store.get_case(case_id, _cases_dir(request)) is None:
        raise HTTPException(404, f"用例不存在：{case_id}")
    if body.id != case_id:
        raise HTTPException(422, "用例 id 不可修改（请删除后新建）")
    case_store.save_case(body, cases_dir=_cases_dir(request), author="local")
    _deps(request).log_store.append("eval", {"action": "case_update", "id": case_id})
    return case_store.get_case(case_id, _cases_dir(request)).model_dump()


@router.patch("/cases/{case_id}/golden-answer")
def update_golden_answer(case_id: str, body: GoldenAnswerIn, request: Request) -> dict:
    """只更新金标准答案要点：跑批详情页人工复核发现判官参考不对时可就地调整。"""
    case = case_store.get_case(case_id, _cases_dir(request))
    if case is None:
        raise HTTPException(404, f"用例不存在：{case_id}")
    ann = case.annotation.model_copy(
        update={
            "golden_answer": body.golden_answer.strip(),
            "annotated_at": datetime.now().isoformat(timespec="seconds"),
            "annotated_by": "local",
        }
    )
    case_store.save_case(
        case.model_copy(update={"annotation": ann}),
        cases_dir=_cases_dir(request),
        author="local",
    )
    _deps(request).log_store.append(
        "eval", {"action": "case_update_golden_answer", "id": case_id}
    )
    return case_store.get_case(case_id, _cases_dir(request)).model_dump()


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, request: Request) -> dict:
    if not case_store.delete_case(case_id, _cases_dir(request)):
        raise HTTPException(404, f"用例不存在：{case_id}")
    _deps(request).log_store.append("eval", {"action": "case_delete", "id": case_id})
    return {"ok": True}


# —— 跑批管理 ——


@router.get("/runs")
def list_runs(request: Request) -> list[dict]:
    return run_store.list_runs(_db(request))


@router.delete("/runs/{run_id}")
def delete_run(run_id: int, request: Request) -> dict:
    """删除历史跑批（连同用例结果）；运行中的跑批拒绝删除。"""
    if run_store.get_run(_db(request), run_id) is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    if _manager(request).is_active(run_id):
        raise HTTPException(409, "跑批运行中，请先取消再删除")
    run_store.delete_run(_db(request), run_id)
    _deps(request).log_store.append("eval", {"action": "run_delete", "id": run_id})
    return {"ok": True}


@router.post("/runs", status_code=201)
async def start_run(body: RunCreate, request: Request) -> dict:
    """启动跑批。

    必须是 async：RunManager.start 里要 asyncio.create_task，
    只能在事件循环线程上调用（sync 端点跑在线程池会报无事件循环）。
    """
    try:
        run_id = _manager(request).start(
            _deps(request),
            name=body.name,
            case_filter=body.case_filter,
            llm=body.llm,
            concurrency=body.concurrency,
            retries=body.retries,
            repeat=body.repeat,
            variant=body.prompt_variant,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
def get_run(run_id: int, request: Request, light: bool = False) -> dict:
    run = run_store.get_run(_db(request), run_id)
    if run is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    cases = run_store.get_run_cases(_db(request), run_id)
    # 附上每条用例的判定参考（金标准答案要点 / 预期行为），供详情页人工复核对照
    for row in cases:
        case = case_store.get_case(row["case_id"], _cases_dir(request))
        if case is not None:
            row["golden_answer"] = case.annotation.golden_answer
            row["behavior"] = case.expected.behavior
        else:
            row["golden_answer"] = ""
            row["behavior"] = ""
        if light:
            # 轻量模式：列表/轮询只读摘要，重复执行明细与轨迹太重（repeat 20 时是 MB 级），
            # 展开用例时再由单条接口按需取全量
            row["repeat_results"] = []
            row["trace"] = []
    return {
        "run": run,
        "cases": cases,
        "active": _manager(request).is_active(run_id),
    }


@router.get("/runs/{run_id}/cases/{case_id}")
def get_run_case(run_id: int, case_id: str, request: Request) -> dict:
    """单条用例完整记录（含 repeat_results / trace），详情展开时按需拉取。"""
    run = run_store.get_run(_db(request), run_id)
    if run is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    rows = run_store.get_run_cases(_db(request), run_id)
    row = next((r for r in rows if r["case_id"] == case_id), None)
    if row is None:
        raise HTTPException(404, f"该跑批中不存在用例：{case_id}")
    case = case_store.get_case(case_id, _cases_dir(request))
    row["golden_answer"] = case.annotation.golden_answer if case else ""
    row["behavior"] = case.expected.behavior if case else ""
    return row


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int, request: Request) -> dict:
    run = run_store.get_run(_db(request), run_id)
    if run is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    ok = _manager(request).cancel(run_id)
    return {"ok": ok, "status": run_store.get_run(_db(request), run_id)["status"]}


@router.post("/runs/{run_id}/verify")
def verify_run(run_id: int, request: Request) -> dict:
    """标记跑批结果已人工核验。"""
    if run_store.get_run(_db(request), run_id) is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    run_store.mark_verified(_db(request), run_id, by="local")
    return {"ok": True, "verified": True}


@router.post("/runs/{run_id}/unverify")
def unverify_run(run_id: int, request: Request) -> dict:
    """取消核验标记。"""
    if run_store.get_run(_db(request), run_id) is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    run_store.clear_verified(_db(request), run_id)
    return {"ok": True, "verified": False}


@router.post("/runs/{run_id}/cases/{case_id}/rerun")
async def rerun_case(run_id: int, case_id: str, request: Request) -> dict:
    """重跑单条用例：覆盖该条结果、清旧标注、重算 summary。"""
    try:
        entry = await _manager(request).rerun_case(run_id, case_id, _deps(request))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "case": entry}


@router.patch("/runs/{run_id}/cases/{case_id}")
def annotate_case(run_id: int, case_id: str, body: AnnotateIn, request: Request) -> dict:
    run = run_store.get_run(_db(request), run_id)
    if run is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    run_store.set_annotation(
        _db(request),
        run_id,
        case_id,
        answer_correct=body.answer_correct,
        refusal=body.refusal,
        note=body.note,
    )
    rows = run_store.get_run_cases(_db(request), run_id)
    row = next((r for r in rows if r["case_id"] == case_id), None)
    if row is None:
        raise HTTPException(404, f"该跑批中不存在用例：{case_id}")
    return row


@router.get("/runs/{run_id}/export")
def export_run(run_id: int, request: Request) -> Response:
    run = run_store.get_run(_db(request), run_id)
    if run is None:
        raise HTTPException(404, f"跑批不存在：{run_id}")
    payload = {
        "run": run,
        "cases": run_store.get_run_cases(_db(request), run_id),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="run-{run_id}.json"'},
    )
