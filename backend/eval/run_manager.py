"""跑批管理器：把 runner 变成"可启动、可取消、进度可查"的后台任务。

设计角度（0017 需求）：
- FastAPI 路由只负责参数校验与转发；真正的跑批逻辑在 asyncio 任务里执行
- 进度/结果通过 run_store 落库，前端轮询即可；取消用任务 cancel + 事件标记
- 单进程内任务表（dict）；uvicorn 多 worker 时任务不共享，本项目单 worker 可接受
"""

import asyncio
from pathlib import Path

import httpx

from app.agent.llm import DeepSeekLLMClient, FakeLLMClient, LLMClient
from app.tools.executor import ToolExecutor
from app.tools.registry import default_registry
from eval import case_store, run_store
from eval.runner import JUDGE_TEMPERATURE, aggregate, run_all, run_single_entry
from eval.schema import CaseFile


def build_llm(
    deps,
    kind: str,
    http_client: httpx.AsyncClient | None = None,
    temperature: float | None = None,
) -> LLMClient:
    """按 kind 构建 LLM 客户端；real 且未配 Key 时抛明确错误。

    http_client：可注入共享连接池（跑批用）；None 时每次调用自建连接（chat 等低频路径）。
    temperature：采样温度；None=不传（服务端默认 1.0）。判官与 Agent 应使用各自温度。
    """
    if kind == "fake":
        return FakeLLMClient()
    settings = deps.settings_store.get_llm_settings()
    if not settings.api_key:
        raise ValueError("未配置大模型 API Key，无法执行真实跑批")
    return DeepSeekLLMClient(
        api_key=settings.api_key,
        model=settings.model or "deepseek-chat",
        http_client=http_client,
        temperature=temperature,
    )


def select_cases(case_filter: dict, cases_dir: Path | None = None) -> list[CaseFile]:
    """按筛选条件选用例：默认只看 enabled；ids/categories/tags 均可选。"""
    cases = [
        c for c in case_store.list_cases(cases_dir or case_store.CASES_DIR) if c.enabled
    ]
    ids = case_filter.get("ids") or []
    categories = case_filter.get("categories") or []
    tags = case_filter.get("tags") or []
    if ids:
        id_set = set(ids)
        cases = [c for c in cases if c.id in id_set]
    if categories:
        cat_set = set(categories)
        cases = [c for c in cases if c.category in cat_set]
    if tags:
        tag_set = set(tags)
        cases = [c for c in cases if tag_set.issubset(set(c.tags))]
    return cases


class RunManager:
    """管理后台跑批任务：启动 / 取消 / 查询。"""

    def __init__(self, db_path=None, cases_dir=None) -> None:
        self._db_path = Path(db_path) if db_path else run_store.DB_PATH
        self.db_path = self._db_path  # 供 API 路由存取用例结果用
        self.cases_dir = Path(cases_dir) if cases_dir else case_store.CASES_DIR
        run_store.init_db(self._db_path)
        self._tasks: dict[int, asyncio.Task] = {}

    def start(
        self,
        deps,
        *,
        name: str,
        case_filter: dict,
        llm: str = "real",
        concurrency: int = 2,
        retries: int = 1,
        repeat: int = 1,
        variant: str = "baseline",
        temperature: float | None = None,
    ) -> int:
        """创建跑批记录并启动后台任务，返回 run_id。"""
        cases = select_cases(case_filter, self.cases_dir)
        if not cases:
            raise ValueError("筛选条件下没有可用用例")
        config = {
            "name": name,
            "case_filter": case_filter,
            "llm": llm,
            "concurrency": concurrency,
            "retries": retries,
            "repeat": repeat,
            "variant": variant,
            "temperature": temperature,
        }
        run_id = run_store.create_run(self._db_path, name, config, total=len(cases))
        task = asyncio.create_task(
            self._execute(
                run_id, deps, cases, llm, concurrency, retries, repeat, variant, temperature
            )
        )
        self._tasks[run_id] = task
        return run_id

    async def _execute(
        self,
        run_id: int,
        deps,
        cases: list[CaseFile],
        llm: str,
        concurrency: int,
        retries: int,
        repeat: int,
        variant: str,
        temperature: float | None,
    ) -> None:
        """后台执行体：跑批 → 逐条落库 → 汇总收尾。"""
        run_store.update_run(self._db_path, run_id, status="running", started=True)
        cancel_event = asyncio.Event()
        # 真实跑批共享一个连接池：50 路并发下连接可复用，避免每次 LLM 调用都做 TLS 握手
        shared_http: httpx.AsyncClient | None = None
        try:
            if llm == "real":
                shared_http = httpx.AsyncClient(
                    timeout=120,
                    limits=httpx.Limits(
                        max_connections=200, max_keepalive_connections=200
                    ),
                )
            client = build_llm(deps, llm, shared_http, temperature=temperature)
            # 判官固定温度、与 Agent 实验温度分离：温度扫描时判定口径保持稳定
            judge_llm = (
                build_llm(deps, llm, shared_http, temperature=JUDGE_TEMPERATURE)
                if llm == "real"
                else None
            )
            tools = ToolExecutor(default_registry())

            async def on_case(_entry: dict, completed: int, total: int) -> None:
                # 落库挪到线程池：SQLite 提交带 fsync，阻塞事件循环会卡死 50 路流式响应
                await asyncio.to_thread(
                    run_store.insert_case_result, self._db_path, run_id, _entry
                )
                await asyncio.to_thread(
                    run_store.update_run,
                    self._db_path,
                    run_id,
                    progress=completed,
                    total=total,
                )

            report = await run_all(
                cases,
                client,
                tools,
                concurrency=concurrency,
                retries=retries,
                repeat=repeat,
                on_case=on_case,
                cancel_event=cancel_event,
                variant=variant,
                judge_llm=judge_llm,
            )
            run_store.update_run(
                self._db_path,
                run_id,
                status="done",
                progress=len(cases),
                summary=report["summary"],
                finished=True,
            )
        except asyncio.CancelledError:
            # 用户取消：不再更新 summary，只标状态
            cancel_event.set()
            run_store.update_run(
                self._db_path, run_id, status="canceled", finished=True
            )
            raise
        except Exception as exc:
            run_store.update_run(
                self._db_path,
                run_id,
                status="error",
                error=str(exc),
                finished=True,
            )
        finally:
            self._tasks.pop(run_id, None)
            if shared_http is not None:
                await shared_http.aclose()  # 跑批结束关闭连接池，防止连接泄漏

    def cancel(self, run_id: int) -> bool:
        """取消跑批；任务不存在或已结束返回 False。"""
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def rerun_case(self, run_id: int, case_id: str, deps) -> dict:
        """重跑单条用例：覆盖原结果、清旧标注、重算 summary。

        语义（用户确认）：修复问题后重跑验证，覆盖原 run 中该条，便于对比同一位置；
        运行中的跑批禁止重跑；重跑会使该 run 的核验状态失效。
        """
        run = run_store.get_run(self._db_path, run_id)
        if run is None:
            raise ValueError(f"跑批不存在：{run_id}")
        if self.is_active(run_id):
            raise ValueError("跑批运行中，不能重跑单条用例")
        case = case_store.get_case(case_id, self.cases_dir)
        if case is None:
            raise ValueError(f"用例不存在：{case_id}")
        config = run.get("config") or {}
        llm_kind = config.get("llm", "real")
        retries = int(config.get("retries", 1))
        repeat = int(config.get("repeat", 1))
        variant = str(config.get("variant", "baseline"))
        temperature = config.get("temperature")  # 重跑沿用原跑批的 Agent 温度
        client = build_llm(deps, llm_kind, temperature=temperature)
        tools = ToolExecutor(default_registry())
        judge_llm = (
            build_llm(deps, llm_kind, temperature=JUDGE_TEMPERATURE)
            if llm_kind == "real"
            else None
        )
        entry = await run_single_entry(
            case, client, tools, retries, judge_llm, repeat, variant
        )
        run_store.insert_case_result(self._db_path, run_id, entry)
        run_store.clear_case_annotation(self._db_path, run_id, case_id)
        run_store.clear_verified(self._db_path, run_id)  # 结果变了，核验失效
        rows = run_store.get_run_cases(self._db_path, run_id)
        run_store.update_run(self._db_path, run_id, summary=aggregate(rows))
        return entry

    def is_active(self, run_id: int) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    @property
    def active_ids(self) -> list[int]:
        return [rid for rid, t in self._tasks.items() if not t.done()]
