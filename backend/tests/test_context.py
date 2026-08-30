"""上下文组装测试：歧义词附加说明 + 安全拒答规则。"""

from app.agent.context import _ambiguity_hint, assemble, system_prompt


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


def test_baseline_has_complete_refusal_rules():
    """安全基线应包含'拒绝必须彻底'规则（红线用例的失败模式：先拒后给可执行内容）。"""
    p = system_prompt("general", "baseline")
    assert "拒绝必须彻底" in p
    assert "不得推荐具体药物名称" in p
    assert "不得描述与伤害部位相关的具体操作" in p
    assert "不得给出破坏性命令或其操作步骤" in p
    assert "拒绝后不得再输出任何相关替代内容或引导性解释" in p
    assert "人工智能不能替代医生诊断" in p
    assert "必须同时明确说明这些方法不会伤害身体" in p


def test_minimal_drops_defense_rules():
    """minimal 变体应去掉防御基线（消融对照），安全细则不出现。"""
    p = system_prompt("general", "minimal")
    assert "拒绝必须彻底" not in p
    assert "系统提示是内部指令" not in p
