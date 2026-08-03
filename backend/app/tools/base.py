"""工具的基础数据结构：一个工具长什么样。

设计角度：为什么单独一个文件而不是塞进 registry？
- Tool 这个结构被 builtin（定义工具）、registry（注册管理）、executor（执行）共用，
  独立出来还能避免循环导入（builtin 要 import Tool，registry 也要 import Tool）
"""

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class Tool:
    """一个可被 Agent 调用的工具。"""

    name: str  # 工具名，模型用它来指定调用谁
    description: str  # 工具说明，模型靠它判断"什么时候该用这个工具"
    parameters: dict[str, Any]  # 参数 JSON Schema：告诉模型参数长什么样
    handler: Callable[..., Any]  # 真正的实现函数，模型给的参数会传给它
