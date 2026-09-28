"""校验 docs/publish/*.md 的 Markdown 结构：代码围栏成对、表格列数一致。"""

from __future__ import annotations

from pathlib import Path

FENCE = chr(96) * 3


def check(p: Path) -> None:
    s = p.read_text(encoding="utf-8")
    lines = s.splitlines()
    fences = s.count(FENCE)
    blocks: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        if line.strip().startswith("|"):
            cur.append((i, line))
        else:
            if cur:
                blocks.append(cur)
                cur = []
    if cur:
        blocks.append(cur)

    problems: list[str] = []
    if fences % 2:
        problems.append(f"代码围栏为奇数：{fences}")
    for b in blocks:
        ncols = len(b[0][1].split("|")[1:-1])
        if len(b) < 3:
            problems.append(f"表块太短 @行{b[0][0]}")
            continue
        sep = b[1][1]
        if len(sep.split("|")[1:-1]) != ncols or not set(
            sep.replace("|", "").replace(" ", "")
        ) <= set("-:"):
            problems.append(f"分隔行不合法 @行{b[0][0]}")
            continue
        for i, line in b:
            if len(line.split("|")[1:-1]) != ncols:
                problems.append(f"行{i} 列数不符（表头 {ncols}）")

    print(f"{p.name}: {len(s)} 字符 · 表格 {len(blocks)} 块 · 围栏 {fences} · "
          f"{'OK' if not problems else '; '.join(problems)}")


def main() -> None:
    for p in sorted(Path("docs/publish").glob("*.md")):
        check(p)


if __name__ == "__main__":
    main()
