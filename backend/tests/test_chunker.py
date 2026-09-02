"""清洗与切块单元测试：HTML/MD 标题锚点 + token 上限。"""

from app.kb.chunker import chunk_sections, extract_sections_html, extract_sections_md


def test_extract_html_sections_and_remove_nav():
    html = """
    <html><body>
      <nav><a>导航不该进来</a></nav>
      <h1>标题一</h1>
      <p>第一段。</p>
      <p>第二段。</p>
      <h2>子标题</h2>
      <p>子内容。</p>
      <script>var x = 1;</script>
    </body></html>
    """
    sections = extract_sections_html(html)
    assert [s.path for s in sections] == ["标题一", "标题一 / 子标题"]
    assert all("导航" not in p and "var x" not in p for s in sections for p in s.paragraphs)


def test_extract_md_front_matter_and_headings():
    md = """---
title: 测试
---

# 第一章

正文一。

## 1.1 小节

- 要点一
- 要点二
"""
    sections = extract_sections_md(md)
    assert [s.path for s in sections] == ["第一章", "第一章 / 1.1 小节"]
    assert "正文一。" in sections[0].paragraphs
    assert sections[1].paragraphs == ["- 要点一", "- 要点二"]


def test_md_heading_attributes_stripped():
    """Markdown 标题里的 {.unnum} 等属性尾巴不应进入 section_path。"""
    md = "# 结语：重点 Agent = LLM + 提示 {.unnum}\n\n正文。"
    sections = extract_sections_md(md)
    assert sections[0].path == "结语：重点 Agent = LLM + 提示"


def test_long_english_para_splits_at_sentence_end():
    """英文段落超限时应在句号处断开（此前漏了 ASCII 句号，断在行尾）。"""
    def tokens(text: str) -> int:
        return len(text)

    from app.kb.chunker import Section

    para = "This is sentence one. This is sentence two. This is sentence three. "
    para = para * 3  # 9 句，每句 22 字符
    sections = [Section(path="s", paragraphs=[para])]
    chunks = chunk_sections(sections, tokens, max_tokens=60)
    assert all(tokens(c["text"]) <= 60 for c in chunks)
    # 断点应落在句号后（不拆散句子）
    assert all(c["text"].rstrip().endswith(".") for c in chunks)
    joined = "".join(c["text"] for c in chunks)
    assert joined.replace(" ", "").startswith(para.replace(" ", ""))


def test_sentence_split_avoids_abbreviation_and_decimal():
    from app.kb.chunker import _split_sentences

    text = "Use e.g. as an example. The value is 3.14 now. Next sentence!"
    pieces = [p for p in _split_sentences(text) if p.strip()]
    assert pieces == [
        "Use e.g. as an example.",
        " The value is 3.14 now.",
        " Next sentence!",
    ]


def test_chunk_by_token_cap():
    def tokens(text: str) -> int:
        return len(text)

    from app.kb.chunker import Section

    sections = [Section(path="s", paragraphs=["aaaa", "bbbb", "cccc", "dddd"])]
    chunks = chunk_sections(sections, tokens, max_tokens=9)
    # 每块不超过 9 个字符（token 模拟；4 字符段落 + 换行 = 9），段落不拆碎
    assert [c["text"] for c in chunks] == ["aaaa\nbbbb", "cccc\ndddd"]


def test_single_long_para_is_split():
    def tokens(text: str) -> int:
        return len(text)

    from app.kb.chunker import Section

    # 单段 40 字符无句子边界，max_tokens=10 → 必须拆成 4 块，不能整段成块
    long_para = "x" * 40
    sections = [Section(path="s", paragraphs=[long_para])]
    chunks = chunk_sections(sections, tokens, max_tokens=10)
    assert len(chunks) == 4
    assert all(tokens(c["text"]) <= 10 for c in chunks)
    assert "".join(c["text"] for c in chunks) == long_para


def test_long_para_split_at_sentence_boundary():
    def tokens(text: str) -> int:
        return len(text)

    from app.kb.chunker import Section

    # 句号边界优先：按句号贪婪打包到接近 max_tokens，不把句子拆碎
    para = "aaaa。bbbb。cccc。dddd。"
    sections = [Section(path="s", paragraphs=[para])]
    chunks = chunk_sections(sections, tokens, max_tokens=10)
    assert [c["text"] for c in chunks] == ["aaaa。bbbb。", "cccc。dddd。"]
    assert all(tokens(c["text"]) <= 10 for c in chunks)
