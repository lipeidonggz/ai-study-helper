"""入库文件选择规则测试：白名单只收目标文件，缺省全收。"""

from app.kb.ingest_rules import collect_source_files
from app.kb.manifest import SourceDoc


def _source(tmp_path, source_id: str, paths: list[str]) -> SourceDoc:
    return SourceDoc(
        source_id=source_id,
        category="教程",
        name="测试",
        url="",
        license="",
        risk="",
        carrier="Markdown",
        paths=[str(tmp_path / p) for p in paths],
        collected=True,
        knowledge_date="",
        decay_class="slow",
    )


def test_rule_limits_to_book(tmp_path):
    """T1 规则 book/*.md：README 多语言与杂项不进库。"""
    (tmp_path / "book").mkdir()
    (tmp_path / "book" / "chapter1.md").write_text("x", encoding="utf-8")
    (tmp_path / "book" / "chapter2.md").write_text("x", encoding="utf-8")
    (tmp_path / "README.en.md").write_text("x", encoding="utf-8")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "a.md").write_text("x", encoding="utf-8")

    src = _source(tmp_path, "T1", ["."])
    files = collect_source_files(src)
    assert [f.name for f in files] == ["chapter1.md", "chapter2.md"]


def test_default_collects_all_text_files(tmp_path):
    """无规则素材（单文件场景）默认收自身；目录场景收全部允许后缀。"""
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "b.html").write_text("x", encoding="utf-8")
    (tmp_path / "c.py").write_text("x", encoding="utf-8")

    src = _source(tmp_path, "X9", ["."])
    files = collect_source_files(src)
    assert [f.name for f in files] == ["a.md", "b.html"]


def test_source_label_curated_and_fallback():
    from app.kb.ingest_rules import source_label

    o2 = _source(None, "O2", [])
    assert source_label(o2) == "OpenAI · Harness engineering"
    # 未知源回退 MANIFEST 名称并去掉尾部年份括号
    unknown = SourceDoc(
        source_id="X9",
        category="业界文章",
        name="某站 · 某篇好文（2025-06）",
        url="",
        license="",
        risk="",
        carrier="HTML",
        paths=[],
        collected=True,
        knowledge_date="2025-06",
        decay_class="fast",
    )
    assert source_label(unknown) == "某站 · 某篇好文"
