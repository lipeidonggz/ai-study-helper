"""内置工具测试：计算器的精确性语义。

背景（2026-08-25）：boundary-halluc-003 显示"结果是否精确"由模型自行判断是
不可控黑盒；修复原则是精确性由工具层确定性判定，模型只做转述。
"""

import pytest

from app.tools.builtin import _calculate


def test_exact_int():
    """整数四则运算：结果精确，并带（精确）声明。"""
    assert _calculate("3+5") == "3+5 = 8（精确）"


def test_exact_decimal_literal():
    """十进制小数常量应精确转有理数：2.5*4 = 10 精确，而非浮点近似。"""
    assert _calculate("2.5*4") == "2.5*4 = 10（精确）"


def test_exact_rational():
    """有理数除法：10/3 返回精确分数，不输出浮点近似。"""
    assert _calculate("10/3") == "10/3 = 10/3（精确）"


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
