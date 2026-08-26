"""上下文组装测试：歧义词附加说明（半确定性消歧，不改用户原话）。"""

from app.agent.context import _ambiguity_hint, assemble


def test_ambiguity_hint_hits():
    """命中高频歧义词时返回对应提示。"""
    assert "整除" in _ambiguity_hint("19 整除 4 的商是多少？")
    assert "左右" in _ambiguity_hint("3 点左右到")


def test_ambiguity_hint_misses():
    """未命中歧义词返回空串。"""
    assert _ambiguity_hint("你好，介绍一下你自己") == ""
    # 单字"商"不命中（避免"商家""商业"误触发）
    assert _ambiguity_hint("附近有什么商家？") == ""
    # "整数商"是清晰术语，不命中（避免诱导过度澄清）
    assert _ambiguity_hint("19 除以 4 的整数商是多少？") == ""


def test_assemble_appends_hint_without_touching_user_message():
    """命中的输入应追加附加说明消息，且用户原话不被修改。"""
    msgs = assemble("general", [], "19 整除 4 的商是多少？")
    assert msgs[-2].content == "19 整除 4 的商是多少？"  # 用户原话原样
    assert msgs[-1].role == "system"
    assert msgs[-1].content.startswith("附加说明")
    assert "整除" in msgs[-1].content


def test_assemble_no_hint():
    """未命中的输入只有 system + user 两条消息。"""
    msgs = assemble("general", [], "你好")
    assert len(msgs) == 2
    assert msgs[-1].role == "user"
