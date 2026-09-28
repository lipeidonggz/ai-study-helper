"""掩码探针：查应用把 LLM 配置存在哪、有没有填 key。

**安全约定**：只暴露"有没有配"，绝不打印任何密钥内容。

⚠️ 2026-09-25 教训：本探针第一版**只按列名判断敏感**（列名 key/value），
于是 settings 表里那列 JSON 文本（`{"provider": …, "api_key": "sk-…"}`）
被原样打印，**泄露了一把真实 API Key**。
⇒ 修正：改按**值的内容**判敏感——命中 `sk-…` 之类模式、或 JSON 里的敏感键，
一律替换成 `<redacted len=N>`；JSON 结构本身保留（provider/model 仍可读）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

CANDIDATES = ["backend/data/app.db", "data/app.db", "backend/data/settings.db"]
SENSITIVE = ("key", "secret", "token", "password", "api", "credential")
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}|[A-Za-z0-9_\-]{32,}", re.I)


def redact_value(value: object) -> str:
    """把单个值渲染成"可安全打印"的形式。"""
    if value is None:
        return "<null>"
    s = str(value)
    if s == "":
        return "<empty>"
    if SECRET_RE.search(s):
        return f"<redacted len={len(s)}>"
    return s


def mask(value: object, column: str) -> str:
    """按列名 + 按内容双重判敏感（两者任一命中就脱敏）。"""
    if value is None:
        return "<null>"
    s = str(value)
    if s == "":
        return "<empty>"

    # 先试 JSON：保留结构（能看 provider/model），只脱敏敏感键
    try:
        obj = json.loads(s)
    except Exception:  # noqa: BLE001
        obj = None
    if isinstance(obj, dict):
        safe = {
            k: ("<redacted>" if any(t in k.lower() for t in SENSITIVE) else v)
            for k, v in obj.items()
        }
        return json.dumps(safe, ensure_ascii=False)

    if any(t in column.lower() for t in SENSITIVE):
        return f"<redacted len={len(s)}>"
    return redact_value(value)


def main() -> None:
    for db in CANDIDATES:
        p = Path(db)
        if not p.exists():
            print(f"skip (missing): {db}")
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        print(f"\n===== {db}  ({p.stat().st_size:,} bytes)")
        tabs = [
            r[0]
            for r in con.execute(
                "select name from sqlite_master where type='table' order by name"
            )
        ]
        for t in tabs:
            n = con.execute(f'select count(*) from "{t}"').fetchone()[0]
            cols = [r[1] for r in con.execute(f'pragma table_info("{t}")')]
            print(f"  {t:<28} rows={n:<6} cols={cols}")
            if n == 0 or len(cols) > 8:
                continue
            for row in con.execute(f'select * from "{t}" limit 5'):
                shown = {
                    c: mask(v, c) for c, v in zip(cols, row, strict=False)
                }
                print(f"      {shown}")
        con.close()


if __name__ == "__main__":
    main()
