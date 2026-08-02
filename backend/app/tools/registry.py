"""工具注册表：内置工具与 L2 注册工具的统一入口。"""

from typing import Any

from app.tools.base import Tool
from app.tools.builtin import builtin_specs


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict]:
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
        tool = self.get(name)
        if tool is None:
            raise KeyError(f"unknown tool: {name}")
        result = tool.handler(**arguments)
        if hasattr(result, "__await__"):
            result = await result
        return result


def default_registry() -> ToolRegistry:
    return ToolRegistry([Tool(**spec) for spec in builtin_specs()])
