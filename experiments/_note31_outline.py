"""量 0031 各节的体量，给结构改进提供具体数字。"""

from __future__ import annotations

import re
from pathlib import Path

P = Path(r"D:\lida-data\vscode\ai-study-helper\memory\0031-graphrag-search-modes.html")


def main() -> None:
    lines = P.read_text(encoding="utf-8").splitlines()
    heads: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = re.search(r"<h([234])[^>]*>(.*?)</h\1>", line)
        if m:
            heads.append((i, int(m.group(1)), re.sub(r"<[^>]+>", "", m.group(2)).strip()))
    heads.append((len(lines), 0, "EOF"))
    print(f"{'行':>5} {'层级':>4} {'本段字符':>9}  标题")
    total = 0
    for (i, lv, t), (j, _, _) in zip(heads, heads[1:]):
        seg = "\n".join(lines[i:j])
        if lv == 3:
            total += len(seg)
        print(f"{i + 1:>5} {'h' + str(lv):>4} {len(seg):>9}  {t[:60]}")
    print(f"\nh3 段落合计 {total} 字符")


if __name__ == "__main__":
    main()
