"""把这 14 篇评测相关掘金文抽成索引输入（沿用 graphrag-exp-dynamic 那套口径）。

- 标题：取页面 <h1>（干净），退化到 <title> 去掉 " - 掘金"
- 正文：优先我们自己的小节抽取器（chunker.extract_sections_html_auto），退化到 <article>
- 清洗：剥页头元数据（日期/阅读量/系列…）、去重复首段、压空白
- 7682756080149839923 抓取被限流（2KB），用之前存下的正文兜底
输出：data/tmp/graphrag-exp-eval/input/<id>.txt，并打印总规模（字符 + token）
"""

from __future__ import annotations

import importlib.util
import io
import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
RAW = ROOT / "raw"
OUT = ROOT / "input"

IDS = [
    "7677133668058873910",
    "7677548292540825663",
    "7678149259078877227",
    "7680074044788228142",
    "7680772297335783443",
    "7680850719455035433",
    "7681477377654325248",
    "7682220809205907490",
    "7682670225502421030",
    "7682710321414094894",
    "7682710321414111278",
    "7682716879560687626",
    "7682659072476364809",
    "7682756080149839923",
]

FALLBACK = {
    # 这一篇抓取被限流：用之前增量实验里存下的正文
    "7682756080149839923": REPO / "data" / "tmp" / "graphrag-exp-update" / "input" / "doc2.txt",
}

META_PREFIXES = [
    re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d+\s+阅读\d+\s*分钟\s*"),
    re.compile(r"^.{0,24}?沛东的认知和实践\s*\d{4}-\d{2}-\d{2}\s+\d+\s+阅读\d+\s*分钟\s*"),
]
META_PARAGRAPH = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d+\s+阅读\d+分钟$")
META_STARTS = ("系列：", "对应原始记录：", "上一篇", "下一篇")
META_EXACT = {"沛东的认知和实践", "掘金", "关注", "点赞"}


def load_chunker():
    path = REPO / "backend" / "app" / "kb" / "chunker.py"
    spec = importlib.util.spec_from_file_location("ash_chunker", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def zh_len(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def clean_body(text: str) -> tuple[str, list[str]]:
    paras = [re.sub(r"[ \t\u3000]+", " ", p).strip() for p in text.split("\n")]
    paras = [p for p in paras if p]
    dropped: list[str] = []
    while paras:
        head = paras[0]
        out = head
        for pat in META_PREFIXES:
            out = pat.sub("", out)
        if out != head:
            dropped.append(head[:60])
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
            dropped.append(head[:60])
            paras.pop(0)
            continue
        break
    while len(paras) >= 2 and paras[0] == paras[1]:
        dropped.append("(重复首段)")
        paras.pop(1)
    return "\n\n".join(paras), dropped


def ntok(text: str) -> int:
    try:
        from graphrag.tokenizer.get_tokenizer import get_tokenizer

        return len(get_tokenizer().encode(text))
    except Exception:  # noqa: BLE001
        return len(text) // 2


def main() -> int:
    chunker = load_chunker()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for pid in IDS:
        raw = RAW / f"{pid}.html"
        if pid in FALLBACK and (not raw.exists() or raw.stat().st_size < 10_000):
            text = FALLBACK[pid].read_text(encoding="utf-8")
            title = text.splitlines()[0].lstrip("# ").strip()
            body = "\n\n".join(text.split("\n\n")[2:])
            rows.append((pid, title, body, "fallback"))
        else:
            html = raw.read_text(encoding="utf-8", errors="ignore")
            soup = BeautifulSoup(html, "html.parser")
            h1 = soup.select_one("h1")
            title = h1.get_text(" ", strip=True) if h1 else ""
            if not title and soup.title and soup.title.string:
                title = re.sub(r"\s*-\s*掘金\s*$", "", soup.title.string.strip())
            secs = chunker.extract_sections_html_auto(html)
            j1 = "\n".join(p for s in secs for p in s.paragraphs)
            node = soup.select_one(".mark_content") or soup.select_one(".article-content") or soup.select_one("article")
            j2 = re.sub(r"\n{3,}", "\n\n", node.get_text("\n")).strip() if node else ""
            use_sections = zh_len(j1) >= 0.95 * zh_len(j2)
            body = j1 if use_sections else j2
            rows.append((pid, title, body, "sections" if use_sections else "article"))

    total_chars = total_tok = 0
    for pid, title, body, how in rows:
        clean, dropped = clean_body(body)
        text = f"# {title}\n\n## {title}\n\n{clean.strip()}\n"
        (OUT / f"{pid}.txt").write_text(text, encoding="utf-8")
        t = ntok(text)
        total_chars += len(text)
        total_tok += t
        print(f"{pid}  {len(text):7,} 字符 / {t:6,} token  [{how}]  剥掉 {len(dropped)} 段")
        print(f"    标题：{title}")
    print()
    print(f"合计：{len(rows)} 篇 · {total_chars:,} 字符 · {total_tok:,} token")
    return 0


if __name__ == "__main__":
    sys.exit(main())
