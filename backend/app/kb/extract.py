"""格式适配层：编码自动探测 + PDF 结构化抽取。

背景（2026-09-02 抽查）：D8 等中文站页面实测为 GB18030 编码，按 UTF-8 读会整篇乱码；
O1 是 PDF（7.3MB）。PDF 的"结构"藏在排版里（字号/位置），pypdf 纯文本流拿不到，
换 pdfplumber 按逐字符字号与位置还原标题层级、剔除页眉页脚。
"""

import re
import statistics
from pathlib import Path

import pdfplumber

from app.kb.chunker import Section

_FOOTER_BAND_PT = 56.0  # 页面底部页脚带（top > 页高-56 视为页脚）
_HEADING_RATIO = 1.5  # 标题字号相对正文字号的最小倍数
_PARA_GAP_FACTOR = 0.7  # 行距超过正文字号×该系数 = 段落边界


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _line_size(line: dict) -> float | None:
    sizes = [c.get("size") for c in line.get("chars", []) if c.get("size")]
    return max(sizes) if sizes else None


def _ends_sentence(text: str) -> bool:
    return text.endswith((".", "!", "?", "。", "！", "？", "；", ";", ":", "："))


def _join_line(cur: str, text: str) -> str:
    """拼接续行：处理断词连字符（practi-\\ncal → practical），否则空格连接。"""
    if not cur:
        return text
    if cur.endswith("-") and text and text[0].islower():
        return cur[:-1] + text
    return cur + " " + text


def read_text_auto(path: str | Path) -> str:
    """按字节读取并自动探测编码：UTF-8 优先（含 BOM），失败回退 GB18030。"""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_sections_pdf(path: str | Path) -> list[Section]:
    """PDF → 按标题分节的 [(标题路径, [段落])]。

    做法：逐行取字号/位置 → 剔除底部页脚带 → 跳过封面/目录页 →
    字号 ≥ 正文×1.5 的行识别为标题（连续多行合并为多行大标题）→
    正文按行距拼段（顺带处理断词连字符）。标题成为切块锚点并写入 section_path。
    """
    with pdfplumber.open(str(path)) as pdf:
        page_h = float(pdf.pages[0].height) if pdf.pages else 792.0
        doc_lines: list[dict] = []
        for page in pdf.pages:
            raw_lines = page.extract_text_lines()
            body = [ln for ln in raw_lines if ln["top"] <= page_h - _FOOTER_BAND_PT]
            if not body:
                continue
            if _clean(body[0]["text"]).lower() == "contents":
                continue  # 目录页
            sizes = [_line_size(ln) or 0 for ln in body]
            if len(body) <= 6 and all(s >= 20 for s in sizes):
                continue  # 封面（纯大标题页）
            for ln in body:
                doc_lines.append(
                    {
                        "top": ln["top"],
                        "bottom": ln["bottom"],
                        "size": _line_size(ln),
                        "text": _clean(ln["text"]),
                    }
                )
    if not doc_lines:
        return []

    body_sizes = [d["size"] for d in doc_lines if d["size"]]
    body_size = statistics.median(body_sizes) if body_sizes else 12.0
    heading_threshold = max(14.0, body_size * _HEADING_RATIO)
    para_gap = body_size * _PARA_GAP_FACTOR

    sections: list[Section] = []
    cur: Section | None = None
    cur_para = ""
    prev_bottom: float | None = None
    prev_text = ""

    def end_para() -> None:
        nonlocal cur_para
        if cur is not None and cur_para.strip():
            cur.paragraphs.append(cur_para.strip())
        cur_para = ""

    def flush_section() -> None:
        nonlocal cur
        if cur is not None and cur.paragraphs:
            sections.append(cur)
        cur = None

    i = 0
    n = len(doc_lines)
    while i < n:
        line = doc_lines[i]
        if line["size"] and line["size"] >= heading_threshold:
            parts = [line["text"]]
            j = i + 1
            while (
                j < n
                and doc_lines[j]["size"]
                and doc_lines[j]["size"] >= heading_threshold
                and doc_lines[j]["top"] - line["bottom"] < body_size * 3
            ):
                parts.append(doc_lines[j]["text"])
                line = doc_lines[j]
                j += 1
            flush_section()
            end_para()
            cur = Section(path=" ".join(p for p in parts if p).strip(), paragraphs=[])
            prev_bottom = line["bottom"]
            prev_text = ""
            i = j
            continue

        if cur is None:
            cur = Section(path="引言", paragraphs=[])
        gap = (line["top"] - prev_bottom) if prev_bottom is not None else None
        if cur_para and gap is not None and gap > para_gap:
            end_para()
        if cur_para and gap is None and prev_text and _ends_sentence(prev_text):
            end_para()  # 跨页且上页末行已是完整句子 → 新段落
        cur_para = _join_line(cur_para, line["text"])
        prev_bottom = line["bottom"]
        prev_text = line["text"]
        i += 1

    end_para()
    flush_section()
    return sections
