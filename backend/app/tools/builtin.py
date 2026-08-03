"""内置工具：计算、日期/时间、笔记（骨架版）。

设计角度：为什么单独一个文件？
- 内置工具是"出厂自带"的能力，和用户 L2 注册的工具（阶段 4）分开管理
- 每个工具 = 描述 + 参数 schema + 实现函数，由注册表统一收编

安全说明：计算器用 AST 解析实现"安全求值"，绝不使用 eval——
eval 会执行任意代码；AST 方案只允许数字和四则运算，其他一律拒绝。
"""

import ast  # 把表达式解析成语法树，然后只允许白名单节点
import datetime as _dt  # 日期时间（下划线前缀，避免与工具名冲突）
import operator as _op  # 运算符节点 → Python 内置运算函数的映射

# 允许的运算符白名单：语法树节点类型 -> 对应的计算函数
_BIN_OPS = {
    ast.Add: _op.add,
    ast.Sub: _op.sub,
    ast.Mult: _op.mul,
    ast.Div: _op.truediv,
    ast.FloorDiv: _op.floordiv,
    ast.Mod: _op.mod,
    ast.Pow: _op.pow,
}


def _safe_eval(expr: str) -> float:
    """只允许数字与四则运算/幂/取模的表达式求值。"""

    def evaluate(n):
        """递归求值一个语法树节点。"""
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value  # 数字节点：直接返回
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            # 二元运算：先递归算左右两边，再按运算符计算
            return _BIN_OPS[type(n.op)](evaluate(n.left), evaluate(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            value = evaluate(n.operand)
            return value if isinstance(n.op, ast.UAdd) else -value  # 一元正负号
        raise ValueError("不支持的表达式")  # 其他任何节点一律拒绝

    return evaluate(ast.parse(expr, mode="eval").body)


def _calculate(expression: str) -> str:
    """计算器工具的实现：安全求值后转成字符串返回。"""
    return str(_safe_eval(expression))


def _now(timezone: str = "Asia/Shanghai") -> str:
    del timezone  # 骨架版：使用系统本地时区；时区处理后续完善
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


_notes: dict[str, str] = {}  # 笔记暂存进程内存（后续由 SQLite 持久化）


def _note_add(title: str, content: str) -> str:
    """保存笔记工具的实现。"""
    _notes[title] = content
    return f"已保存笔记：{title}"


def _note_get(title: str) -> str:
    """读取笔记工具的实现。"""
    return _notes.get(title, f"未找到笔记：{title}")


def builtin_specs() -> list[dict]:
    """内置工具规格列表：name / description / parameters（JSON Schema）/ handler。"""
    return [
        {
            "name": "calculator",
            "description": "安全计算数学表达式（支持 + - * / // % ** 与括号）",
            "parameters": {
                "type": "object",
                "properties": {"expression": {"type": "string", "description": "数学表达式"}},
                "required": ["expression"],
            },
            "handler": _calculate,
        },
        {
            "name": "current_datetime",
            "description": "获取当前日期和时间",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "时区，默认 Asia/Shanghai"}
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
            "description": "读取一条笔记",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string", "description": "笔记标题"}},
                "required": ["title"],
            },
            "handler": _note_get,
        },
    ]
