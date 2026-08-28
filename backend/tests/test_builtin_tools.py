"""内置工具测试：计算器的精确性语义。

背景（2026-08-25）：boundary-halluc-003 显示"结果是否精确"由模型自行判断是
不可控黑盒；修复原则是精确性由工具层确定性判定，模型只做转述。
"""

import pytest

from app.tools.builtin import _calculate, _now, _parse_delta
import datetime as _dt


def test_exact_int():
    """整数四则运算：结果精确，并带（精确）声明。"""
    assert _calculate("3+5") == "3+5 = 8（精确）"


def test_exact_decimal_literal():
    """十进制小数常量应精确转有理数：2.5*4 = 10 精确，而非浮点近似。"""
    assert _calculate("2.5*4") == "2.5*4 = 10（精确）"


def test_exact_rational():
    """有理数除法：10/3 返回精确分数，不输出浮点近似。"""
    assert _calculate("10/3") == "10/3 = 10/3（精确）"


def test_terminating_decimal_shows_decimal():
    """有限小数（分母质因子只有 2/5）应显示小数而非分数：19/4 → 4.75。"""
    assert _calculate("19/4") == "19/4 = 4.75（精确）"
    assert _calculate("1/8") == "1/8 = 0.125（精确）"


def test_repeating_decimal_shows_fraction():
    """无限循环小数仍显示分数：1/6 分母含质因子 3，不能转有限小数。"""
    assert _calculate("1/6") == "1/6 = 1/6（精确）"


def test_sqrt_perfect_square():
    """完全平方开方：sqrt(144) = 12 精确。"""
    assert _calculate("sqrt(144)") == "sqrt(144) = 12（精确）"


def test_sqrt_irrational():
    """无理数开方：sqrt(2) 必须标注近似值。"""
    result = _calculate("sqrt(2)")
    assert "近似值" in result
    assert "1.414213562373" in result


def test_sqrt_target_case():
    """评测目标用例：sqrt(987654321) 不是完全平方，必须标注近似值。"""
    result = _calculate("sqrt(987654321)")
    assert "近似值" in result
    assert "31426.968052931865" in result


def test_pow_half_target_case():
    """模型绕过 sqrt 用 ** 0.5：同样必须标注近似值（口径一致）。"""
    result = _calculate("987654321 ** 0.5")
    assert "近似值" in result


def test_pow_fraction_perfect():
    """分数次幂完全幂：8 的 1/3 次方 = 2 精确。"""
    assert _calculate("8 ** (1/3)") == "8 ** (1/3) = 2（精确）"


def test_sqrt_fraction_perfect():
    """分数底数完全平方：(4/9) 开方 = 2/3 精确。"""
    assert _calculate("sqrt(4/9)") == "sqrt(4/9) = 2/3（精确）"


def test_negative_sqrt_rejected():
    """负数开方：明确拒绝，不返回 complex 或近似。"""
    with pytest.raises(ValueError, match="负数的平方根"):
        _calculate("sqrt(-1)")


def test_unsupported_function_rejected():
    """白名单外函数：拒绝（如任意代码执行尝试）。"""
    with pytest.raises(ValueError):
        _calculate("abs(-1)")


def test_bool_literal_rejected():
    """bool 是 int 子类但不是数字：True+1 应被拒绝，而不是产生怪异结果。"""
    with pytest.raises(ValueError):
        _calculate("True+1")


def test_parse_delta():
    """相对时间字符串解析：'+2h30m' → 2 小时 30 分钟。"""
    assert _parse_delta("+2h30m") == _dt.timedelta(hours=2, minutes=30)
    assert _parse_delta("-1h") == _dt.timedelta(hours=-1)
    assert _parse_delta("+45s") == _dt.timedelta(seconds=45)
    assert _parse_delta("+10d") == _dt.timedelta(days=10)  # 天单位（combo-004 修复）
    assert _parse_delta("+1d2h30m") == _dt.timedelta(days=1, hours=2, minutes=30)
    assert _parse_delta("") == _dt.timedelta()


def test_now_with_delta():
    """current_datetime 支持相对时间推算：delta 推算后的时间应带星期与推算标注。"""
    base = _now()
    plus = _now(delta="+2h30m")
    assert "星期" in plus  # 推算后仍带星期（日历事实）
    assert "+2h30m" in plus  # 标注推算来源，模型可如实转述
    minus = _now(delta="-1h")
    assert "-1h" in minus


def test_now_with_timezone():
    """current_datetime 支持时区：Asia/Tokyo 应返回 JST 且与系统时区相差固定小时数。"""
    tokyo = _now(timezone="Asia/Tokyo")
    assert "JST" in tokyo  # 东京时区缩写
    assert "星期" in tokyo
    shanghai = _now(timezone="Asia/Shanghai")
    # 东京比北京快 1 小时：解析小时数验证
    import re as _re

    def _hour(s: str) -> int:
        return int(_re.search(r" (\d{2}):", s).group(1))

    assert (_hour(tokyo) - _hour(shanghai)) % 24 == 1


def test_note_get_lists_all_when_title_empty():
    """note_get 空标题应列出全部笔记（模糊查询的确定性兜底）。"""
    from app.tools.builtin import _note_add, _note_get, clear_notes

    clear_notes()
    _note_add("待办", "买牛奶")
    _note_add("读书", "读三国")
    result = _note_get("")
    assert "待办" in result and "买牛奶" in result
    assert "读书" in result


def test_note_search_fuzzy():
    """note_search 应按关键词模糊匹配标题或内容。"""
    from app.tools.builtin import _note_add, _note_search, clear_notes

    clear_notes()
    _note_add("待办", "买牛奶")
    _note_add("读书", "读三国演义")
    assert "待办" in _note_search("牛奶")  # 内容匹配
    assert "读书" in _note_search("读书")  # 标题匹配
    assert "未找到" in _note_search("不存在的词")


def test_note_isolated_per_context():
    """笔记按执行上下文（asyncio task）隔离：一个 attempt 写的笔记，另一个 attempt 看不到。"""
    import asyncio

    from app.tools.builtin import _note_add, _note_get, reset_notes

    async def writer():
        reset_notes()
        _note_add("购物清单", "牛奶")
        return _note_get("购物清单")

    async def reader():
        reset_notes()
        return _note_get("购物清单")

    async def scenario():
        w, r = await asyncio.gather(asyncio.create_task(writer()), asyncio.create_task(reader()))
        return w, r

    w, r = asyncio.run(scenario())
    assert w == "牛奶"  # 写者读到自己的笔记
    assert "未找到" in r  # 读者看不到写者的笔记（上下文隔离）
