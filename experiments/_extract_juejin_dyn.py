"""把动态选择实验要用的 4 篇掘金文抽成索引输入（沿用前两篇的抽取口径）。

口径：priority = chunker.extract_sections_html_auto 抽到的正文（按中文字符数）
      vs .mark_content 直取文本，取中文字符多的那个；
      输出 <id>.txt，首行写 "# 标题"，与 data/tmp/graphrag-exp-juejin/input/doc.txt 一致。
只读 raw HTML + 写 input/*.txt，不碰已有实验目录。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parent.parent.parent
RAW = REPO / "data" / "tmp" / "juejin-dyn" / "raw"
OUT = REPO / "data" / "tmp" / "juejin-dyn" / "input"

IDS = [
    "7682659072476364809",
    "7662937986765733903",
    "7663917079230201865",
    "7666650921661792298",
]

# 干净的标题（页面的 <title> 是"标题＋副标题"拼在一起的，不能用）
TITLES = {
    "7682659072476364809": "金标准写作规范可外化吗：一次盲写验证",
    "7662937986765733903": "单 Agent 架构演进",
    "7663917079230201865": "多 Agent 架构梳理",
    "7666650921661792298": "从 Prompt 到 Loop：LLM 应用的工程方法论",
}

# 页头元数据：日期 + 阅读量 + 系列/仓库链接，混在首段里也要剥掉
META_PREFIXES = [
    re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d+\s+阅读\d+\s*分钟\s*"),
    re.compile(r"^.{0,24}?沛东的认知和实践\s*\d{4}-\d{2}-\d{2}\s+\d+\s+阅读\d+\s*分钟\s*"),
]
META_PARAGRAPH = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d+\s+阅读\d+分钟$")
META_STARTS = ("系列：", "对应原始记录：")
META_EXACT = {"沛东的认知和实践", "上一篇", "下一篇", "掘金"}


def clean_body(text: str) -> tuple[str, list[str]]:
    """剥页头元数据、去首段重复、压空白。返回（正文, 被丢掉的片段）。"""
    paras = [re.sub(r"[ \t\u3000]+", " ", p).strip() for p in text.split("\n")]
    paras = [p for p in paras if p]
    dropped: list[str] = []

    # 只从开头连续剥：日期/阅读量、系列行、纯元数据段
    while paras:
        head = paras[0]
        out = head
        for pat in META_PREFIXES:
            out = pat.sub("", out)
        if out != head:
            dropped.append(head[:80])
            paras[0] = out.strip()
            if not paras[0]:
                paras.pop(0)
            continue
        if (
            META_PARAGRAPH.match(head)
            or head.startswith(META_STARTS)
            or head in META_EXACT
            or re.fullmatch(r"\d+", head)
            or re.fullmatch(r"阅读\d+分钟", head)
        ):
            dropped.append(head[:80])
            paras.pop(0)
            continue
        break

    # 首段重复（页面把摘要与正文各渲染一遍）
    while len(paras) >= 2 and paras[0] and paras[0] == paras[1]:
        dropped.append("(重复首段) " + paras[1][:60])
        paras.pop(1)

    return "\n\n".join(paras), dropped


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
    chunker = load_chunker()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for pid in IDS:
        html = (RAW / f"{pid}.html").read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        title = TITLES[pid]

        secs = chunker.extract_sections_html_auto(html)
        joined1 = "\n".join(p for s in secs for p in s.paragraphs)
        mc = (
            soup.select_one(".mark_content")
            or soup.select_one(".article-content")
            or soup.select_one("article")
        )
        joined2 = re.sub(r"\n{3,}", "\n\n", mc.get_text("\n")).strip() if mc else ""

        # 优先用我们自己的小节抽取器：它会把页面骨架（标题/作者/阅读量）留在独立行里，
        # 便于剥掉；只有当它明显漏内容（中文字符 < 节点的 95%）时才退到节点直取。
        use_sections = zh_len(joined1) >= 0.95 * zh_len(joined2)
        best = joined1 if use_sections else joined2
        which = "sections" if use_sections else "node_text"
        body, dropped = clean_body(best)
        text = f"# {title}\n\n## {title}\n\n{body.strip()}\n"
        dest = OUT / f"{pid}.txt"
        dest.write_text(text, encoding="utf-8")
        rows.append((pid, len(title), len(text), zh_len(text), len(secs), which, title, text))
        print(f"{pid}  {len(text):7,} 字符  中文 {zh_len(text):6,}  小节 {len(secs):3}  via {which}")
        print(f"    标题：{title}")
        print(f"    剥掉：{dropped if dropped else '（无）'}")
        print(f"    开头：{body.strip()[:120]!r}")
        print()
    total = sum(r[2] for r in rows)
    print(f"合计 {total:,} 字符；输出目录 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
