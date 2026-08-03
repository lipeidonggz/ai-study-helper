"""上下文组装器：把"系统提示 + 历史 + 当前消息"拼成发给模型的消息列表。

设计角度：为什么单独一个文件？
- 模型能"看到"什么，完全由这个文件决定——这是 context 工程的核心位置
- 阶段 2 的检索注入、token 预算、上下文压缩都会加在这里，单独成文件便于演进

当前是雏形：只按会话模式选择系统提示；历史原样透传。
"""

from app.agent.llm import LLMMessage

# 不同模式给模型不同的"身份设定"，这就是范围控制的执行点之一：
# - general：通用助手
# - kb_priority：优先用个人知识库（阶段 2 生效）
# - tool_enhanced：适合时用工具
SYSTEM_PROMPTS = {
    "general": "你是通用 AI 助手，使用中文回答。",
    "kb_priority": "你是通用 AI 助手。回答时优先使用个人知识库中的资料，并给出引用来源。",
    "tool_enhanced": "你是通用 AI 助手。适合使用工具时，应调用工具获取准确结果。",
}


def system_prompt(mode: str) -> str:
    """按模式取系统提示；未知模式回退到通用提示（防御性兜底）。"""
    return SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["general"])


def assemble(mode: str, history: list[LLMMessage], user_message: str) -> list[LLMMessage]:
    """组装完整消息列表：系统提示放最前（优先级最高），历史随后，当前消息最后。

    骨架版说明：检索注入与 token 预算控制在阶段 2 加入。
    """
    return [
        LLMMessage(role="system", content=system_prompt(mode)),
        *history,
        LLMMessage(role="user", content=user_message),
    ]
