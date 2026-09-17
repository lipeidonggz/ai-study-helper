"""编译层 API：触发抽取（Pass 1 → 体检 → 谓词归一）、查状态、看体检报告与 claim。

与界面配合：触发后前端轮询 `GET /api/compile/{source_id}` 看阶段与进度；
完成后取 `report`（体检报告）与 `claims`（归一后的断言清单）。
"""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/compile", tags=["compile"])


def _svc(request: Request):
    return request.app.state.compile_service


@router.get("")
def list_statuses(request: Request):
    """所有源的抽取状态（供界面表格显示"抽取/重抽"与状态徽章）。"""
    return _svc(request).all_statuses()


@router.get("/{source_id}")
def status(source_id: str, request: Request):
    """抽取任务状态（idle / running / done / failed）+ 阶段进度 + 摘要。"""
    return _svc(request).status(source_id)


@router.post("/{source_id}/extract")
async def extract(source_id: str, request: Request):
    """触发抽取（重抽 = 直接再跑一次，产物覆盖）。"""
    deps = request.app.state.deps
    llm = deps.settings_store.get_llm_settings()
    if not llm.api_key:
        raise HTTPException(503, "未配置大模型 API Key（设置面板）")
    try:
        return _svc(request).start(source_id, api_key=llm.api_key, model=llm.model or "deepseek-chat")
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@router.get("/{source_id}/report")
def report(source_id: str, request: Request):
    """体检报告（五项检查 + 红线判定 + 丢弃/改写/标记清单）。"""
    r = _svc(request).report(source_id)
    if r is None:
        raise HTTPException(404, f"{source_id} 尚未抽取")
    return r


@router.get("/{source_id}/claims")
def claims(source_id: str, request: Request, limit: int = 0):
    """归一后的断言清单（limit=0 返回全部；含标记与锚定原文）。"""
    return _svc(request).claims(source_id, limit=limit)


@router.get("/{source_id}/graph")
def graph(source_id: str, request: Request):
    """S5 图组装产物（实体 / 边 / 未建边的 claim / 对账表）——供界面画"一跳关系视图"。"""
    g = _svc(request).graph(source_id)
    if g is None:
        raise HTTPException(404, f"{source_id} 尚未跑图组装（S5）")
    return g
