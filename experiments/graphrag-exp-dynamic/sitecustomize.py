"""DeepSeek 兼容 shim（动态实验版，2026-09-27 升级）—— 三件事，全部可用 env 关掉。

## 1. 剥掉 API 层的 pydantic response_format（原始行为，始终生效）

GraphRAG 的社区报告这一步显式传 `response_format=CommunityReportResponse`（pydantic 类），
graphrag_llm 原样透传给 litellm，litellm 再转成 OpenAI 风格的 json_schema 结构化输出参数。
DeepSeek 不认这个参数，直接 400（"This response_format type is unavailable now"）。

## 2. 改成 DeepSeek 支持的 JSON 模式（GRAPHRAG_SHIM_JSON_OBJECT，默认开）

与其丢弃，不如换成 `{"type": "json_object"}`——这是 DeepSeek 支持的 JSON 输出模式，
能显著降低"语法坏 JSON"的比例（2026-09-27 实测：278 条社区报告里有 47 条因为
未转义换行 / 尾逗号之类的语法错而解析失败 ⇒ 报告被静默丢弃）。

## 3. 解析兜底：json_repair（GRAPHRAG_SHIM_JSON_REPAIR，默认开）

`graphrag_llm/utils/structure_response.py` 里是**裸的 json.loads + pydantic 校验**，
没有任何修复尝试（失败就抛，社区报告那层接住后只留一条 WARNING）。
这里给 `lite_llm_completion.structure_completion_response` 加一层：
严格解析失败时用 json_repair 修语法，再走原来的 pydantic 校验；
若修复结果是 list（模型吐了多个 JSON 对象）则取第一个 dict。
**只在严格解析失败时介入**，合法响应完全不受影响。

## 边界（必须记住）

这是**实验脚手架**，不是修复上游。它让"报告尽量都生成出来"，
但**改了失败语义**：原本会失败的调用现在可能被修复成一份报告，
所以对外表述时要说清"我们加了本地兜底"。
"""

from __future__ import annotations

import json
import os
import sys

_MARK = "[deepseek-shim]"


def _env_on(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip() not in {"0", "", "false", "False"}


def _install_response_format() -> None:
    """把 pydantic 的 response_format 换成 DeepSeek 能用的 JSON 模式（或按需丢弃）。"""
    try:
        import litellm
    except Exception:  # noqa: BLE001
        return

    use_json_object = _env_on("GRAPHRAG_SHIM_JSON_OBJECT")

    def _rewrite(kwargs: dict) -> None:
        rf = kwargs.get("response_format")
        if rf is None or isinstance(rf, dict):
            return  # dict（例如 {"type": "json_object"}）原样放行
        if use_json_object:
            kwargs["response_format"] = {"type": "json_object"}
        else:
            kwargs.pop("response_format", None)

    original_completion = litellm.completion
    original_acompletion = litellm.acompletion

    def completion(*args, **kwargs):  # noqa: ANN002, ANN003
        _rewrite(kwargs)
        return original_completion(*args, **kwargs)

    async def acompletion(*args, **kwargs):  # noqa: ANN002, ANN003
        _rewrite(kwargs)
        return await original_acompletion(*args, **kwargs)

    litellm.completion = completion  # type: ignore[assignment]
    litellm.acompletion = acompletion  # type: ignore[assignment]
    if os.environ.get("GRAPHRAG_SHIM_VERBOSE"):
        mode = "-> json_object" if use_json_object else "-> 丢弃"
        print(f"{_MARK} response_format(pydantic) {mode} [pid={os.getpid()}]", file=sys.stderr)


def _install_json_repair() -> None:
    """严格解析失败时用 json_repair 兜底，再走原 pydantic 校验。"""
    try:
        from json_repair import repair_json
        from graphrag_llm.completion import lite_llm_completion as llm_mod
    except Exception:  # noqa: BLE001
        return

    original = llm_mod.structure_completion_response

    def structure_completion_response(response: str, model):  # noqa: ANN001, ANN202
        try:
            return original(response, model)
        except json.JSONDecodeError:
            parsed = json.loads(repair_json(response))
            if isinstance(parsed, list):
                # 模型有时连着吐两个 JSON 对象，json_repair 会合成 list —— 取第一个 dict
                first = next((x for x in parsed if isinstance(x, dict)), None)
                if first is None:
                    raise
                parsed = first
            if not isinstance(parsed, dict):
                raise
            if os.environ.get("GRAPHRAG_SHIM_VERBOSE"):
                print(f"{_MARK} json_repair 兜底成功 [pid={os.getpid()}]", file=sys.stderr)
            return original(json.dumps(parsed, ensure_ascii=False), model)

    llm_mod.structure_completion_response = structure_completion_response  # type: ignore[assignment]
    try:
        import graphrag_llm.utils as utils_mod

        utils_mod.structure_completion_response = structure_completion_response  # type: ignore[assignment]
    except Exception:  # noqa: BLE001
        pass


_install_response_format()
if _env_on("GRAPHRAG_SHIM_JSON_REPAIR"):
    _install_json_repair()
