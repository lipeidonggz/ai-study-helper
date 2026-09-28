"""DeepSeek 兼容 shim —— 只为让本实验能跑出社区报告。

## 为什么需要它（2026-09-25 实测）

GraphRAG 的社区报告这一步显式传了 pydantic 模型当 `response_format`
（`community_reports_extractor.py:84  response_format=CommunityReportResponse`），
`graphrag_llm` 把它原样透传给 litellm，litellm 再转成 OpenAI 风格的
json_schema 结构化输出参数。DeepSeek 不认，直接 400：

    "This response_format type is unavailable now"

于是 23 个社区的报告调用**全部失败**，报告表为空，最终
`finalize_community_reports` 的 merge 抛 `KeyError: 'community'`。
（`drop_params=True` 兜不住：litellm 的模型表里没把 response_format 标为 DeepSeek 不支持。）

## 这个 shim 做什么 / 不做什么

- **做**：当 `response_format` 是 pydantic 类（而不是 dict）时，在调用 API 前把它去掉。
  这样 DeepSeek 只收到普通请求；GraphRAG 拿到文本后**仍会在本地**用
  `structure_completion_response()` 把内容解析成同一个 pydantic 模型（这一步原样保留）。
- **不做**：不改提示词、不改 schema 定义、不改任何 GraphRAG 逻辑、不改采集出来的字段。

## 因此结论要带什么限定

这样跑出来的报告，**形状（字段/结构/提示词要求）是可信的**，因为它由提示词与 schema 决定；
但"模型是否严格按 schema 输出"这一层强制被拿掉了 ⇒ **报告质量不能与官方 OpenAI 路径等价看待**。
要严格对比，应换一个支持 json_schema 的模型重跑（未做）。

## 生效方式

只在本实验的 runner 里通过 PYTHONPATH 注入（Python 启动时自动 import sitecustomize），
不影响主项目。
"""

from __future__ import annotations

import os
import sys

_MARK = "[deepseek-shim]"


def _install() -> None:
    try:
        import litellm
    except Exception:  # noqa: BLE001
        return

    def _strip(kwargs: dict) -> None:
        rf = kwargs.get("response_format")
        # dict（例如 {"type": "json_object"}）放行；pydantic 类才剥掉
        if rf is not None and not isinstance(rf, dict):
            kwargs.pop("response_format", None)

    original_completion = litellm.completion
    original_acompletion = litellm.acompletion

    def completion(*args, **kwargs):  # noqa: ANN002, ANN003
        _strip(kwargs)
        return original_completion(*args, **kwargs)

    async def acompletion(*args, **kwargs):  # noqa: ANN002, ANN003
        _strip(kwargs)
        return await original_acompletion(*args, **kwargs)

    litellm.completion = completion  # type: ignore[assignment]
    litellm.acompletion = acompletion  # type: ignore[assignment]
    if os.environ.get("GRAPHRAG_SHIM_VERBOSE"):
        print(f"{_MARK} response_format(pydantic) 将在调用前被剥掉 [pid={os.getpid()}]", file=sys.stderr)


_install()
