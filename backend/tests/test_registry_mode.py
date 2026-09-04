"""工具注册表按模式过滤测试（RAG 模式不注册 note 工具，own-002 教训 2026-09-04）。"""

from app.tools.registry import NOTE_TOOL_NAMES, default_registry, registry_for_mode


def test_rag_mode_excludes_note_tools():
    reg = registry_for_mode("rag")
    names = {s["function"]["name"] for s in reg.schemas()}
    assert NOTE_TOOL_NAMES.isdisjoint(names)
    assert "calculator" in names  # 其它工具保留


def test_general_mode_keeps_note_tools():
    reg = registry_for_mode("general")
    names = {s["function"]["name"] for s in reg.schemas()}
    assert NOTE_TOOL_NAMES.issubset(names)


def test_default_registry_unchanged_by_without():
    """without 不修改原注册表。"""
    reg = default_registry()
    filtered = reg.without(NOTE_TOOL_NAMES)
    assert {s["function"]["name"] for s in reg.schemas()} == {
        s["function"]["name"] for s in default_registry().schemas()
    }
    assert len(filtered.schemas()) == len(reg.schemas()) - len(NOTE_TOOL_NAMES)
