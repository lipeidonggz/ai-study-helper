"""领域模型（骨架版）：会话、消息、工具定义、文档、设置。"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Session:
    id: str
    mode: str  # general | kb_priority | tool_enhanced
    created_at: datetime = field(default_factory=datetime.now)
    messages: list[Message] = field(default_factory=list)


@dataclass
class ToolDefinition:
    """L2 表单注册的工具定义（ToolStore 持久化；阶段 1 内置工具先走代码注册）。"""

    name: str
    description: str
    parameters: dict[str, Any]
    execution: dict[str, Any]  # {"type": "http" | "command", ...}


@dataclass
class Document:
    id: str
    filename: str
    kb_id: str
    created_at: datetime = field(default_factory=datetime.now)
    status: str = "pending"  # pending | indexing | ready | failed


@dataclass
class GlobalSettings:
    """全局范围控制配置：知识库/工具默认启停与优先级。"""

    enabled_kb_ids: list[str] = field(default_factory=list)
    kb_priority: dict[str, int] = field(default_factory=dict)
    enabled_tools: list[str] = field(default_factory=list)


@dataclass
class SessionSettings:
    """会话级范围控制配置。"""

    mode: str = "general"
