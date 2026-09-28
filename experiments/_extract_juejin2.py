"""抓取第二篇掘金文章的正文，输出成 index 用的 .txt（沿用上一篇的抽取口径）。"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parent.parent.parent
HTML = REPO / "data" / "tmp" / "juejin2-raw.html"


def load_chunker():
    path = REPO / "backend" / "app" / "kb" / "chunker.py"
    spec = importlib.util.spec_from_file_location("ash_chunker", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def zh_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def main() -> int:
    html = HTML.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    print("TITLE:", (soup.title.string or "").strip()[:80])

    chunker = load_chunker()

    # 口径 1：我们自己的抽取器
    secs = chunker.extract_sections_html_auto(html)
    joined1 = "\n".join(p for s in secs for p in s.paragraphs)
    print(f"① extract_sections_html_auto: {len(secs)} 节, {len(joined1)} 字符, 中文 {zh_len(joined1)}")
    for s in secs[:8]:
        print(f"    path={s.path!r} paras={len(s.paragraphs)}")

    # 口径 2：mark_content / article
    mc = soup.select_one(".mark_content") or soup.select_one(".article-content")
    joined2 = ""
    if mc:
        joined2 = re.sub(r"\n{3,}", "\n\n", mc.get_text("\n")).strip()
        print(f"② .mark_content: {len(joined2)} 字符, 中文 {zh_len(joined2)}")
    else:
        print("② 没有 .mark_content")

    best = joined1 if zh_len(joined1) >= zh_len(joined2) else joined2
    if not best.strip():
        print("!! 两种口径都空")
        return 1

    # 标题行：GraphRAG 用文件名当文档标题，这里显式写成第一行标题（沿用上一篇 doc.txt 的写法）
    title = "换文章不天然等于换根因：探针设计与双信号转绿判据"
    out = f"# {title}\n\n## {title}\n\n{best.strip()}\n"
    dest = REPO / "data" / "tmp" / "juejin2.txt"
    dest.write_text(out, encoding="utf-8")
    print(f"\n写出 {dest} —— {len(out)} 字符, 中文 {zh_len(out)}")
    print("开头 200:", out[:200].replace("\n", " | "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
