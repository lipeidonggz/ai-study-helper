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
def graph(source_id: str, request: Request, stage: str = "s5"):
    """图产物——供界面画"一跳关系视图"。

    stage=s5（默认）：S5 图组装产物（实体 / 边 / 未建边 / 逐 claim 对账）；
    stage=s6：S6-A 确定性形合并后的产物（实体带 id + aliases、边端点换成实体 id、附合并审计）。
    """
    merged = (stage or "s5").lower() in ("s6", "merged", "merge")
    g = _svc(request).graph(source_id, merged=merged)
    if g is None:
        what = "S6 形合并" if merged else "图组装（S5）"
        raise HTTPException(404, f"{source_id} 尚未跑{what}——先在这篇上点「抽取/重抽」跑一遍")
    return g


@router.get("/{source_id}/statements")
def statements(source_id: str, request: Request):
    """S7 产物：断言（statement）+ doc/概念归属边 + claim_type（局限 / 展望）。"""
    s = _svc(request).statements(source_id)
    if s is None:
        raise HTTPException(404, f"{source_id} 尚未跑 S7 分类——先在这篇上点「抽取/重抽」跑一遍")
    return s


@router.get("/{source_id}/graph_role")
def graph_role(
    source_id: str,
    request: Request,
    claim_type: str = "LimitationStatement",
    budget: int = 3600,
    max_tokens: int = 32000,
    mode: str = "chunks",
):
    """**图路由第一版（按角色过滤）**：doc + claim_type → statement 全集 → 证据块 → 注入文本。

    这是"图参与检索"的最小闭环，也是纯向量做不到的枚举语义（不 top-k）。
    仅用于验证与 A/B，尚未接进问答链路（问答侧仍是纯向量）。
    """
    from app.rag.graph_route import role_chunks
    from app.storage.sqlite.compile_store import CompileStore

    svc = _svc(request)
    if svc.statements(source_id) is None:
        raise HTTPException(404, f"{source_id} 尚未跑 S7/S9（先点「抽取/重抽」）")
    deps = request.app.state.deps
    if not deps.vector_store:
        raise HTTPException(503, "向量库未就绪（chunk 文本从这里取）")
    from scripts.compile_slice_b6 import COMPILE_DB

    return role_chunks(
        source_id, claim_type,
        compile_store=CompileStore(COMPILE_DB),
        vector_store=deps.vector_store,
        budget_tokens=budget,
        max_tokens=max_tokens,
        mode=mode,
    )
