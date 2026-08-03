"""工具执行器：Agent 与"真实世界"之间的唯一执行入口。

设计角度：为什么在注册表外面再包一层？
- 业务（loop）只需要一个简单的 execute(name, args)，不需要知道注册表细节
- 阶段 4 的沙箱、重试、超时、审计全部加在这个类里，业务代码不用改
"""

from app.tools.registry import ToolRegistry


class ToolExecutor:
    """统一执行工具的入口。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry  # 持有注册表

    async def execute(self, name: str, arguments: dict) -> str:
        """执行工具并强制转成字符串（模型上下文里只能放文本）。"""
        result = await self._registry.execute(name, arguments)
        return str(result)  # 统一转字符串：列表/数字等也能回填进上下文

    def schemas(self) -> list[dict]:
        """透传注册表的工具 Schema 给模型。"""
        return self._registry.schemas()
