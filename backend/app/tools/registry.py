"""工具注册表：统一管理"有哪些工具可用"。

设计角度：为什么需要注册表？
- 模型需要拿到所有工具的 JSON Schema 才能决定调用谁（schemas()）
- 执行时按名字找到对应实现并调用（execute()）
- 阶段 4 的 L2 用户注册工具也会走同一个注册表，接口不变
"""

from typing import Any

from app.tools.base import Tool
from app.tools.builtin import builtin_specs  # 内置工具定义


class ToolRegistry:
    """按名字管理工具的注册表。"""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}  # 名字 -> 工具，用字典查找更快
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        """注册一个工具（重名会覆盖）。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名字取工具；不存在返回 None。"""
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
        """把所有工具转成 OpenAI 协议认识的格式（type=function + function 对象）。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in self._tools.values()
        ]

    async def execute(self, name: str, arguments: dict) -> Any:
        """执行一个工具：按名字找到实现，把参数传进去。

        支持 handler 是异步函数的情况（await 它）；同步函数直接返回结果。
        """
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"unknown tool: {name}")  # 模型调了不存在的工具，明确报错
        result = tool.handler(**arguments)  # 字典展开成关键字参数传给实现
        if hasattr(result, "__await__"):  # 返回的是协程则等待它
            result = await result
        return result


def default_registry() -> ToolRegistry:
    """创建包含全部内置工具的注册表（应用启动时用）。"""
    return ToolRegistry([Tool(**spec) for spec in builtin_specs()])
