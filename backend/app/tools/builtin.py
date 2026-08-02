"""内置工具：计算、日期/时间、笔记（骨架版）。

- 计算器用 AST 解析实现安全求值，不使用 eval。
- 笔记先用进程内字典，后续由 SQLite 持久化。
"""

import ast
import datetime as _dt
import operator as _op

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
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN_OPS:
            return _BIN_OPS[type(n.op)](evaluate(n.left), evaluate(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            value = evaluate(n.operand)
            return value if isinstance(n.op, ast.UAdd) else -value
        raise ValueError("不支持的表达式")

    return evaluate(ast.parse(expr, mode="eval").body)


def _calculate(expression: str) -> str:
    return str(_safe_eval(expression))


def _now(timezone: str = "Asia/Shanghai") -> str:
    del timezone  # 骨架版：使用系统本地时区；时区处理后续完善
    return _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


_notes: dict[str, str] = {}


def _note_add(title: str, content: str) -> str:
    _notes[title] = content
    return f"已保存笔记：{title}"


def _note_get(title: str) -> str:
    return _notes.get(title, f"未找到笔记：{title}")


def builtin_specs() -> list[dict]:
    """内置工具规格：name / description / parameters（JSON Schema）/ handler。"""
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
