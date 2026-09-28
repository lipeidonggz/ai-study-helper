"""实体类型（entity type）在整条链上到底被谁用了？

做法：在**已安装的 graphrag 包**里全量搜 type 相关标识符，并打印命中文件，
再单独看两处"最可能用到 type"的地方：社区报告的图上下文构造器、local search 的实体上下文表。
只读。
"""

from __future__ import annotations

import re
from pathlib import Path

SP = Path(
    r"D:\lida-data\vscode\ai-study-helper\data\tmp\graphrag-exp\.venv"
    r"\Lib\site-packages\graphrag"
)

# 只找"真的在读实体类型"的写法，排除 typing / response_type 之类的噪声
PATTERNS = {
    "entity.type（属性访问）": re.compile(r"\bentity\.type\b"),
    "type_col=/\"type\" 映射": re.compile(r"type_col\s*=|TYPE\s*=\s*\"type\""),
    "df[\"type\"] / .type 列访问": re.compile(r"\[\"type\"\]|\.type\b(?!_col)"),
    "entity_types 配置": re.compile(r"entity_types"),
}


def main() -> None:
    files = sorted(SP.rglob("*.py"))
    print(f"扫描 {len(files)} 个 .py 文件\n")
    for label, rx in PATTERNS.items():
        print(f"===== {label}")
        n = 0
        for f in files:
            for i, line in enumerate(
                f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                if rx.search(line):
                    n += 1
                    rel = f.relative_to(SP)
                    print(f"  {rel}:{i}  {line.strip()[:120]}")
        if n == 0:
            print("  （无命中）")
        print()


if __name__ == "__main__":
    main()
