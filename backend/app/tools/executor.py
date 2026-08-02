"""工具执行器：统一执行入口。

骨架版为简单转发；阶段 4 在这里加沙箱、重试、超时与审计。
"""

from app.tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, name: str, arguments: dict) -> str:
        result = await self._registry.execute(name, arguments)
        return str(result)

    def schemas(self) -> list[dict]:
        return self._registry.schemas()
