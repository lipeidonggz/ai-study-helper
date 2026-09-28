"""列出 graphrag 3.2.0 注册的全部工作流，摘一句它的作用（取模块 docstring 首行 / 启动日志）。"""

from __future__ import annotations

import re
from pathlib import Path

SP = Path(
    r"D:\lida-data\vscode\ai-study-helper\data\tmp\graphrag-exp\.venv"
    r"\Lib\site-packages\graphrag\index\workflows"
)


def main() -> None:
    rows = []
    for f in sorted(SP.glob("*.py")):
        if f.name in {"__init__.py", "factory.py"}:
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        doc = re.search(r'"""(.+?)"""', text, re.S)
        first = doc.group(1).strip().splitlines()[0] if doc else ""
        started = re.search(r'logger\.info\("Workflow started: ([a-z_]+)"', text)
        rows.append((f.stem, started.group(1) if started else "-", first))
    print(f"{'文件':<34}{'工作流名':<34}作用")
    for name, wf, first in rows:
        print(f"{name:<34}{wf:<34}{first}")
    print(f"\n共 {len(rows)} 个单步工作流文件")


if __name__ == "__main__":
    main()
