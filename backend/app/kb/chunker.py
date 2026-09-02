"""素材清洗与切块：HTML/MD → 标题层级 section → token 上限切块。

v1 规则（2026-09-02 定）：
- 切块锚点 = 标题层级（h1-h4 / #-####），标题并入其下内容
- 长度上限 450 token（e5 硬上限 512，留余量），超限按段落边界续切
- 短节独立成块不合并；不做重叠
- 每块保留 section_path（父标题路径），供引用定位与判官核对
"""

import re
from dataclasses import dataclass
from typing import Callable

from bs4 import BeautifulSoup

MAX_TOKENS = 450

_REMOVE_TAGS = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "svg",
    "iframe",
    "noscript",
    "button",
}
_TEXT_BLOCK_TAGS = {"p", "li", "blockquote", "pre", "td", "th", "dt", "dd", "figcaption", "summary"}
_HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_SENT_END = re.compile(r"[。！？；;]|[.!?](?=\s+[A-Z0-9\"'“”‘’（(])")
_CONTENT_HINTS = [
    "#left_cont",  # 老式中文新闻站点（D8 实测）
    "#article-content",
    "#main-content",
    "#content",
    ".article-content",
    ".news-content",
    ".post-content",
    ".article",
    ".content",
]
_BLOCK_DESC_TAGS = {
    "p", "div", "table", "ul", "ol", "blockquote", "pre",
    "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "form", "figure",
}
_BOILERPLATE = re.compile(
    r"newsletter|subscribe|sign\s?up|get the (latest|developer)|cookie|privacy policy",
    re.IGNORECASE,
)


def _pick_content_root(soup: BeautifulSoup):
    """正文容器选择：main 优先于 article → 常见内容容器（id/class）→ body。

    为什么 main 优先：A5 等页面把开场 lede 放在 main 下、article 外
    （main > section.hero + div > article），只选 article 会漏掉真正的开头；
    main 基本只含正文区（站点外壳 nav/footer/header 已在进入前剔除）。
    """
    for tag in ("main", "article"):
        el = soup.find(tag)
        if el:
            return el
    for selector in _CONTENT_HINTS:
        el = soup.select_one(selector)
        if el:
            return el
    return soup.body or soup


def _is_boilerplate(text: str) -> bool:
    """段落级样板噪声（订阅/营销/隐私等）：命中即丢弃，避免 main 根选到页尾 CTA。"""
    return bool(_BOILERPLATE.search(text)) and len(text) < 160


@dataclass
class Section:
    path: str  # 父标题路径，如 "O2 / give-codex-a-map"
    paragraphs: list[str]


def extract_sections_html(html: str) -> list[Section]:
    """HTML → [(标题路径, [段落文本])]。

    兼容两种正文结构：p/li 段落（现代站点）与"叶子 div 直接装文本"（老式中文新闻站）。
    无文章容器时兜底按空行切段。
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in _REMOVE_TAGS:
        for el in soup.find_all(tag):
            el.decompose()
    root = _pick_content_root(soup)

    sections: list[Section] = []
    cur_path: list[str] = []
    cur_paras: list[str] = []
    skip_noise = False  # 样板噪声小节（如 newsletter CTA）：整节丢弃直到下一个真标题

    def flush() -> None:
        if cur_paras:
            sections.append(Section(path=" / ".join(cur_path) if cur_path else "引言", paragraphs=cur_paras))

    for el in root.descendants:
        if not getattr(el, "name", None):
            continue
        if el.name in _HEADING_TAGS:
            flush()
            level = int(el.name[1])
            heading = el.get_text(" ", strip=True)
            if _is_boilerplate(heading):
                skip_noise = True
                cur_path = []
                cur_paras = []
                continue
            skip_noise = False
            cur_path = cur_path[: level - 1]
            cur_path.append(heading)
            cur_paras = []
        elif el.name in _TEXT_BLOCK_TAGS:
            text = el.get_text(" ", strip=True)
            if text and not skip_noise and not _is_boilerplate(text):
                cur_paras.append(text)
        elif el.name == "div" and not any(el.find(t) for t in _BLOCK_DESC_TAGS):
            # 叶子 div 直接装文本（无块级子元素）视为一段；太短的碎片（装饰性 div）过滤
            text = el.get_text(" ", strip=True)
            if len(text) >= 15 and not skip_noise and not _is_boilerplate(text):
                cur_paras.append(text)
    flush()

    if not sections:
        # 兜底：无标题无段落标签的页面，按空行分段
        text = root.get_text("\n", strip=True)
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if paragraphs:
            sections = [Section(path="引言", paragraphs=paragraphs)]
    return sections


def extract_sections_md(md: str) -> list[Section]:
    """Markdown/MDX → [(标题路径, [段落])]；去掉 front matter，代码块按段落保留。"""
    lines = md.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1 :]
                break

    sections: list[Section] = []
    cur_path: list[str] = []
    cur_paras: list[str] = []

    def flush() -> None:
        if cur_paras:
            sections.append(Section(path=" / ".join(cur_path) if cur_path else "引言", paragraphs=cur_paras))

    for raw in lines:
        line = raw.strip()
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            level = len(m.group(1))
            cur_path = cur_path[: level - 1]
            heading = re.sub(r"\s*\{[^}]*\}$", "", m.group(2).strip())  # 去掉 {.unnum} 等属性
            cur_path.append(heading)
            cur_paras = []
        elif line and not line.startswith(("![", "<!---")):
            cur_paras.append(line)
    flush()
    if not sections:
        sections = [Section(path="引言", paragraphs=cur_paras)]
    return sections


def chunk_sections(
    sections: list[Section],
    token_count_fn: Callable[[str], int],
    max_tokens: int = MAX_TOKENS,
) -> list[dict]:
    """按 token 上限切块：段落累积到超限即切；单段超限按句子/换行边界拆开，杜绝超限块。"""
    chunks: list[dict] = []
    for section in sections:
        cur: list[str] = []
        for para in section.paragraphs:
            if token_count_fn(para) > max_tokens:
                # 单段超限：先落当前累积，再把该段按句子边界拆成多块
                if cur:
                    chunks.append({"section_path": section.path, "text": "\n".join(cur)})
                    cur = []
                for piece in _split_long_para(para, token_count_fn, max_tokens):
                    chunks.append({"section_path": section.path, "text": piece})
                continue
            if cur and token_count_fn("\n".join(cur + [para])) > max_tokens:
                chunks.append({"section_path": section.path, "text": "\n".join(cur)})
                cur = [para]
            else:
                cur.append(para)
        if cur:
            chunks.append({"section_path": section.path, "text": "\n".join(cur)})
    return chunks


def _split_long_para(
    para: str, token_count_fn: Callable[[str], int], max_tokens: int
) -> list[str]:
    """超长段落：先按句子边界贪婪打包；仍有单句超限（如无标点代码块）按前缀二分硬切。"""
    pieces: list[str] = []
    cur = ""
    for sent in _split_sentences(para):
        sent = sent.strip()
        if not sent:
            continue
        if cur and token_count_fn(cur + sent) > max_tokens:
            pieces.append(cur.strip())
            cur = sent
        else:
            cur = cur + sent
    if cur.strip():
        pieces.append(cur.strip())

    result: list[str] = []
    for p in pieces:
        while p and token_count_fn(p) > max_tokens:
            lo, hi, best = 1, len(p), 0
            while lo <= hi:
                mid = (lo + hi) // 2
                if token_count_fn(p[:mid]) <= max_tokens:
                    best, lo = mid, mid + 1
                else:
                    hi = mid - 1
            result.append(p[:best])
            p = p[best:]
        if p:
            result.append(p)
    return result


def _split_sentences(text: str) -> list[str]:
    """在句子终止符后切开：中文句号/叹号/问号/分号直接切；
    ASCII 句号/叹号/问号要求后跟空白 + 大写/数字/引号（避开 e.g.、3.14 这类）。"""
    pieces: list[str] = []
    start = 0
    for m in _SENT_END.finditer(text):
        end = m.end()
        if end > start:
            pieces.append(text[start:end])
            start = end
    if start < len(text):
        pieces.append(text[start:])
    return pieces
