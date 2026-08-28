"""内置工具：计算、日期/时间、笔记（骨架版）。

设计角度：为什么单独一个文件？
- 内置工具是"出厂自带"的能力，和用户 L2 注册的工具（阶段 4）分开管理
- 每个工具 = 描述 + 参数 schema + 实现函数，由注册表统一收编

安全说明：计算器用 AST 解析实现"安全求值"，绝不使用 eval——
eval 会执行任意代码；AST 方案只允许数字和四则运算，其他一律拒绝。

精确性语义（2026-08-25 修复 boundary-halluc-003 根因）：
- "结果是否精确"由工具层确定性判定，不让模型猜（模型判断依据是不可控黑盒）
- 四则运算用 Fraction 做精确有理数运算：10/3 返回 10/3（精确），不存在近似
- 幂/开方：整数指数精确；分数指数判定是否为完全幂——完全幂精确，否则
  明确标注"近似值：精确值为无理数或无法用有限小数表示"
- 返回值自带精确性声明，模型的任务退化为"如实转述工具声明"
"""

import ast  # 把表达式解析成语法树，然后只允许白名单节点
import contextvars  # 按执行上下文（asyncio task）隔离笔记存储——repeat 并行化的前提
import datetime as _dt  # 日期时间（下划线前缀，避免与工具名冲突）
import math  # 无理数近似的浮点计算
import operator as _op  # 运算符节点 → Python 内置运算函数的映射
import re  # delta 相对时间解析
from decimal import Decimal  # 有限小数的精确十进制字符串
from fractions import Fraction  # 精确有理数：避免 float 的二进制近似
from zoneinfo import ZoneInfo  # IANA 时区转换（Windows 需 tzdata 包）

# 允许的运算符白名单：语法树节点类型 -> 对应的计算函数（Pow 单独处理）
_BIN_OPS = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
}


def _integer_root(n: int, q: int) -> int | None:
    """判定 n 是否为完全 q 次幂：是则返回整数根，否则返回 None。

    用整数二分，无浮点误差；这是"精确性判定"的确定性基础。
    """
    if n < 0:
        if q % 2 == 0:
            return None  # 负数没有偶次实数根
        r = _integer_root(-n, q)
        return -r if r is not None else None
    if n == 0:
        return 0
    # hi 取"比真实根大"的最小 2 的幂：hi ** q >= n 恒成立
    hi = 1 << ((n.bit_length() + q - 1) // q)
    lo = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        p = mid ** q
        if p == n:
            return mid
        if p < n:
            lo = mid + 1
        else:
            hi = mid - 1
    return None


def _sqrt(value: Fraction) -> tuple[Fraction | float, bool]:
    """平方根：完全平方 → (精确根, True)；否则 → (浮点近似, False)。"""
    if value < 0:
        raise ValueError("不支持负数的平方根")
    rn = _integer_root(value.numerator, 2)
    rd = _integer_root(value.denominator, 2)
    if rn is not None and rd is not None:
        return Fraction(rn, rd), True
    return math.sqrt(float(value)), False


def _pow(base: Fraction, exp: Fraction) -> tuple[Fraction | float, bool]:
    """幂运算：
    - 整数指数 → Fraction 精确运算
    - 分数指数 p/q → (base ** p) 开 q 次根；完全幂则精确，否则近似
    """
    if exp.denominator == 1:
        try:
            return base ** exp.numerator, True
        except ZeroDivisionError:
            raise ValueError("不支持 0 的负次幂")
    if base < 0:
        raise ValueError("不支持负数的分数次幂")
    p, q = exp.numerator, exp.denominator
    try:
        radicand = base ** p  # Fraction：p 可为负，Fraction 支持负指数
    except ZeroDivisionError:
        raise ValueError("不支持 0 的负次幂")
    rn = _integer_root(radicand.numerator, q)
    rd = _integer_root(radicand.denominator, q)
    if rn is not None and rd is not None:
        return Fraction(rn, rd), True
    return float(radicand) ** (1.0 / q), False


def _evaluate(n) -> tuple[Fraction | float, bool]:
    """递归求值一个语法树节点，返回 (值, 是否精确)。

    精确性是工具层的确定性判定结果，是模型转述的唯一事实依据。
    """

    if (
        isinstance(n, ast.Constant)
        and isinstance(n.value, (int, float))
        and not isinstance(n.value, bool)  # bool 是 int 子类，但语义上不是数字
    ):
        # Fraction(str(v))：十进制小数精确转有理数（0.5 → 1/2），
        # 避免 Fraction(float) 的二进制近似（0.1 会变成巨大分数）
        return Fraction(str(n.value)), True
    if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
        left, lex = _evaluate(n.left)
        right, rex = _evaluate(n.right)
        return _BIN_OPS[type(n.op)](left, right), lex and rex
    if isinstance(n, ast.BinOp) and type(n.op) is ast.Pow:
        base, _ = _evaluate(n.left)
        exp, _ = _evaluate(n.right)
        return _pow(base, exp)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        value, exact = _evaluate(n.operand)
        return (value if isinstance(n.op, ast.UAdd) else -value), exact
    if isinstance(n, ast.Call):
        # 白名单函数：目前只有 sqrt（必须恰好一个位置参数，不允许关键字参数）
        if (
            isinstance(n.func, ast.Name)
            and n.func.id == "sqrt"
            and len(n.args) == 1
            and not n.keywords
        ):
            value, _ = _evaluate(n.args[0])
            return _sqrt(value)
        raise ValueError("不支持的函数调用")
    raise ValueError("不支持的表达式")  # 其他任何节点一律拒绝


def _is_terminating_decimal(value: Fraction) -> bool:
    """判定最简分数能否写成有限小数：分母质因子只有 2 和 5（Fraction 已保证最简）。"""
    d = value.denominator
    while d % 2 == 0:
        d //= 2
    while d % 5 == 0:
        d //= 5
    return d == 1


def _format_value(value: Fraction, exact: bool) -> str:
    """把结果格式化成可读文本：
    - 精确且整数 → 整数（如 8）
    - 精确且可转有限小数（分母质因子只有 2/5）→ 有限小数（如 19/4 → 4.75）
    - 精确且无限循环小数 → 最简分数（如 10/3 → 10/3）
    - 近似 → 12 位小数
    """
    if exact:
        if value.denominator == 1:
            return str(value.numerator)
        if _is_terminating_decimal(value):
            # Decimal 精确转十进制字符串，避免 float 对大分母的精度损失
            return format(
                Decimal(value.numerator) / Decimal(value.denominator), "f"
            )
        return f"{value.numerator}/{value.denominator}"
    return f"{float(value):.12f}"


def _calculate(expression: str) -> str:
    """计算器工具的实现：安全求值，返回带精确性声明的结果。

    例：
      "3+5"                 → "3+5 = 8（精确）"
      "10/3"                → "10/3 = 10/3（精确）"
      "sqrt(144)"           → "sqrt(144) = 12（精确）"
      "sqrt(987654321)"     → "sqrt(987654321) ≈ 31426.968052931865（近似值：精确值为无理数或无法用有限小数表示）"
    """
    value, exact = _evaluate(ast.parse(expression, mode="eval").body)
    if exact:
        return f"{expression} = {_format_value(value, exact)}（精确）"
    return (
        f"{expression} ≈ {_format_value(value, exact)}"
        "（近似值：精确值为无理数或无法用有限小数表示）"
    )


def _parse_delta(delta: str) -> _dt.timedelta:
    """解析相对时间字符串，如 '+10d' / '+2h30m' / '-1h' / '+30m' / '+45s'（d=天）。"""
    total = _dt.timedelta()
    for m in re.finditer(r"([+-]?\d+)\s*([dhms])", delta):
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "d":
            total += _dt.timedelta(days=n)
        elif unit == "h":
            total += _dt.timedelta(hours=n)
        elif unit == "m":
            total += _dt.timedelta(minutes=n)
        else:
            total += _dt.timedelta(seconds=n)
    return total


def _now(timezone: str = "Asia/Shanghai", delta: str = "") -> str:
    try:
        tz = ZoneInfo(timezone) if timezone else None
    except Exception:
        tz = None  # 未知/无效时区回退系统本地时区
    if tz is None:
        now = _dt.datetime.now().astimezone()
    else:
        now = _dt.datetime.now(tz)  # 指定时区的 aware 时间（astimezone() 无参会转回系统时区）
    note = ""
    if delta.strip():
        now = now + _parse_delta(delta)  # 相对时间推算（时钟事实，工具确定性给出）
        note = f"（相对当前时间 {delta} 推算）"
    # 星期几是日历事实（确定性），工具直接给出，避免模型用知识猜或用计算器瞎算
    # （tool-datetime-003 教训：工具不给星期时模型可能绕圈或虚假转述）
    return (
        f"{now.strftime('%Y-%m-%d %H:%M:%S %Z')}（{_WEEKDAYS_CN[now.weekday()]}）"
        f"{note}"
    )


_WEEKDAYS_CN = [
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
]


_notes_ctx: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "note_store", default=None
)


def _notes() -> dict[str, str]:
    """取当前执行上下文的笔记存储；首次访问时创建独立空 dict（避免共享默认值）。"""
    store = _notes_ctx.get()
    if store is None:
        store = {}
        _notes_ctx.set(store)
    return store


def clear_notes() -> None:
    """清空当前上下文的笔记（跑批开始前的兜底清理）。"""
    _notes().clear()


def reset_notes() -> None:
    """为当前执行上下文重置一份全新笔记存储。

    contextvars 按 asyncio task 隔离：评测 repeat 并行化后，每个 attempt 是独立 task，
    各自从空笔记开始——repeat 是真正独立的样本（旧实现是进程级共享 dict，会串味）。
    """
    _notes_ctx.set({})


def _note_add(title: str, content: str) -> str:
    """保存笔记工具的实现。"""
    _notes()[title] = content
    return f"已保存笔记：{title}"


def _note_get(title: str = "") -> str:
    """读取笔记工具的实现：title 为空时列出全部笔记（用户模糊查询时的确定性兜底）。"""
    store = _notes()
    if not title.strip():
        if not store:
            return "（暂无笔记）"
        return "\n".join(f"{k}: {v}" for k, v in store.items())
    return store.get(title, f"未找到笔记：{title}")


def _note_search(keyword: str) -> str:
    """模糊检索笔记：匹配标题或内容包含关键词的笔记（检索是工具的职责，不让模型猜）。"""
    kw = keyword.strip()
    if not kw:
        return "请提供检索关键词"
    hits = [f"{k}: {v}" for k, v in _notes().items() if kw in k or kw in v]
    return "\n".join(hits) if hits else f"未找到包含「{kw}」的笔记"


def builtin_specs() -> list[dict]:
    """内置工具规格列表：name / description / parameters（JSON Schema）/ handler。"""
    return [
        {
            "name": "calculator",
            "description": "安全计算纯数学表达式（支持 + - * / // % ** 与括号，支持 sqrt()；结果自带精确/近似声明）。仅支持数学表达式，不支持时间/日期字符串（如'16:29 + 2:30'），时间推算请用 current_datetime 的 delta 参数",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "数学表达式"}},
                "required": ["expression"],
            },
            "handler": _calculate,
        },
        {
            "name": "current_datetime",
            "description": "获取当前日期、时间和星期几；支持相对时间推算（delta 参数，如 '+2h30m' 表示 2 小时 30 分钟后）",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "时区，默认 Asia/Shanghai"},
                    "delta": {"type": "string", "description": "相对时间推算，如 '+10d'（天）或 '+2h30m'（小时/分钟），正数未来/负数过去，留空返回当前时间"}
                },
            },
            "handler": _now,
        },
        {
            "name": "note_add",
            "description": "保存一条笔记",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "笔记标题"},
                    "content": {"type": "string", "description": "笔记内容"},
                },
                "required": ["title", "content"],
            },
            "handler": _note_add,
        },
        {
            "name": "note_get",
            "description": "按标题精确读取一条笔记；title 留空时列出全部笔记",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string", "description": "笔记标题"}},
            },
            "handler": _note_get,
        },
        {
            "name": "note_search",
            "description": "模糊检索笔记：按关键词匹配标题或内容，返回所有相关笔记（不知道确切标题时用本工具）",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string", "description": "检索关键词（匹配标题或内容）"}},
                "required": ["keyword"],
            },
            "handler": _note_search,
        },
    ]
