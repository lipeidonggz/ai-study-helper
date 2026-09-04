"""入库文本改造测试：叶子节标题参与检索（own-002 教训，2026-09-04）。"""

from app.kb.ingest import _leaf_title


def test_multi_level_path_takes_leaf():
    path = (
        "0022 · 主动式验证与评测可信度工程 / 过程细节 · 第二阶段：真正的主动调校 "
        "/ 温度定档：1.0 定案，2.0 失控宣判"
    )
    assert _leaf_title(path) == "温度定档：1.0 定案，2.0 失控宣判"


def test_top_level_section_equals_file_title_skipped():
    """顶层节（路径只有一段，通常=文档标题或引言）不拼，避免与来源行重复。"""
    title = "How we contain Claude across products"
    assert _leaf_title(title) is None
    assert _leaf_title("引言") is None


def test_empty_path_returns_none():
    assert _leaf_title("") is None
    assert _leaf_title("   ") is None
