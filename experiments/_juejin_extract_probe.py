"""探针：掘金文章页能否抽出干净正文（三种口径对比）。只读 + 打印，不写文件。"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parent.parent.parent  # data/tmp -> data -> repo
HTML = REPO / "data" / "tmp" / "juejin-raw.html"


def load_chunker():
    path = REPO / "backend" / "app" / "kb" / "chunker.py"
    spec = importlib.util.spec_from_file_location("ash_chunker", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def stats(tag: str, text: str) -> None:
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    print(f"\n--- {tag}")
    print(f"    字符 {len(text):,}（中文 {zh:,}）")
    print(f"    开头: {text[:160]!r}")


def main() -> None:
    html = HTML.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    print("TITLE:", (soup.title.string or "").strip() if soup.title else "(无)")
    print("OG:title:", (soup.find("meta", attrs={"property": "og:title"}) or {}).get("content", "(无)"))

    # 口径 1：我们自己的抽取器
    chunker = load_chunker()
    secs = chunker.extract_sections_html_auto(html)
    joined = "\n".join(p for s in secs for p in s.paragraphs)
    stats(f"① 我们的 extract_sections_html_auto（{len(secs)} 个小节）", joined)
    for s in secs[:6]:
        print(f"       · path={s.path!r} paras={len(s.paragraphs)}")

    # 口径 2：直接取 .mark_content
    mc = soup.select_one(".mark_content") or soup.select_one(".article-content") or soup.select_one("article")
    if mc:
        text = re.sub(r"\n{3,}", "\n\n", mc.get_text("\n"))
        stats("② .mark_content / article 直接取文本", text)
    else:
        print("\n--- ② 没找到 .mark_content / article")

    # 口径 3：全页兜底（看正文是否至少存在于 HTML 里）
    body = re.sub(r"\s+", " ", soup.body.get_text(" ")) if soup.body else ""
    stats("③ 全页 body 文本（含导航等噪声）", body)


if __name__ == "__main__":
    sys.exit(main())
