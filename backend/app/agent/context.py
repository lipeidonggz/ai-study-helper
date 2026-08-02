"""上下文组装器：把系统提示、会话历史、检索结果、工具结果按预算组装。

骨架版：按会话模式生成系统提示；检索注入与 token 预算是阶段 2 的深化点。
"""

from app.agent.llm import LLMMessage

SYSTEM_PROMPTS = {
    "general": "你是通用 AI 助手，使用中文回答。",
    "kb_priority": "你是通用 AI 助手。回答时优先使用个人知识库中的资料，并给出引用来源。",
    "tool_enhanced": "你是通用 AI 助手。适合使用工具时，应调用工具获取准确结果。",
}


def system_prompt(mode: str) -> str:
    return SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["general"])


def assemble(mode: str, history: list[LLMMessage], user_message: str) -> list[LLMMessage]:
    """骨架版：系统提示 + 历史 + 当前消息。检索注入与预算控制在阶段 2 加入。"""
    return [
        LLMMessage(role="system", content=system_prompt(mode)),
        *history,
        LLMMessage(role="user", content=user_message),
    ]
