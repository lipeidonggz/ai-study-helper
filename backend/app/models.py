"""领域模型：用 dataclass 定义系统里的"数据形状"。

设计角度：为什么单独建 models.py？
- 这些结构会被多个模块共用（API 层校验、存储层持久化、Agent 层传递），
  集中定义避免每个模块各写一份、字段不一致
- 用 dataclass 而不是字典：有字段名、有默认值、IDE 能提示，对初学者友好
注意：这里的模型是"内存中的数据结构"，不是数据库表；持久化时由存储层负责转换。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Message:
    """一条对话消息：谁说的、说了什么。"""

    role: str  # system=系统提示 | user=用户 | assistant=AI | tool=工具结果
    content: str
    # 多轮工具调用时，需要把 AI 的原始 tool_calls 原样回传给 API（OpenAI 协议要求）
    tool_calls: list[dict] | None = None
    created_at: datetime = field(default_factory=datetime.now)  # 默认取创建时刻


@dataclass
class Session:
    """一次会话：会话模式 + 消息列表。"""

    id: str
    mode: str  # general | kb_priority | tool_enhanced
    created_at: datetime = field(default_factory=datetime.now)
    messages: list[Message] = field(default_factory=list)


@dataclass
class ToolDefinition:
    """L2 表单注册的工具定义（ToolStore 持久化；阶段 1 内置工具先走代码注册）。"""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema 格式的参数描述
    execution: dict[str, Any]  # 执行方式：{"type": "http" | "command", ...}


@dataclass
class Document:
    """一份上传的文档（知识库用，阶段 2 实现）。"""

    id: str
    filename: str
    kb_id: str  # 属于哪个知识库
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"  # pending=待处理 | indexing=索引中 | ready=可用 | failed=失败


@dataclass
class GlobalSettings:
    """全局范围控制配置：默认启用哪些知识库/工具，优先级如何。"""

    enabled_kb_ids: list[str] = field(default_factory=list)
    kb_priority: dict[str, int] = field(default_factory=dict)  # 知识库 id -> 优先级数字
    enabled_tools: list[str] = field(default_factory=list)


@dataclass
class SessionSettings:
    """会话级范围控制配置（每个会话可以覆盖全局设置）。"""

    mode: str = "general"


@dataclass
class LLMSettings:
    """大模型调用配置（provider 当前仅支持 deepseek）。"""

    provider: str = "deepseek"
    model: str = "deepseek-chat"
    api_key: str = ""  # 用户在前端配置，仅存本地数据库（不入 git）
