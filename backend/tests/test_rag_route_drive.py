"""判定驱动执行的回归：判定如何决定"要不要尝试图通道"、以及落回向量时的说明。

2026-09-21 定（沛东选 C）：**判定驱动执行**，不再保留"第二判定器"——
老角色路由降格为 enum 的执行器；判定不可用时（L3 关 / judge 不可用 / 出错）落向量并标注。
"""
from app.rag.route_decision import RouteDecision
from app.rag.workflow import _fallback_note, _graph_attempt


def _d(**kw) -> RouteDecision:
    base = dict(route="vector", decided_by="default", need=None)
    base.update(kw)
    return RouteDecision(**base)


def test_only_enum_and_relation_trigger_graph():
    assert _graph_attempt(_d(route="graph", decided_by="lexicon", need="enum")) == "enum"
    assert _graph_attempt(_d(route="graph", decided_by="llm", need="relation")) == "relation"


def test_vector_unsure_and_unavailable_do_not_trigger_graph():
    assert _graph_attempt(_d(route="vector", decided_by="llm", need="vector")) is None
    assert _graph_attempt(_d(route="vector", decided_by="default", need="unsure")) is None
    assert _graph_attempt(None) is None
    assert _graph_attempt(_d(degraded=["llm:disabled"])) is None          # L3 关 → 判定不可用
    assert _graph_attempt(_d(degraded=["llm:error:RuntimeError"])) is None  # L3 出错


def test_fallback_notes_are_never_silent():
    """落回向量必须说明原因（不静默降级）——四种情况各有各的说法。"""
    assert "图通道未产出" in _fallback_note(_d(route="graph", need="enum"))
    assert "图通道未产出" in _fallback_note(_d(route="graph", need="relation"))
    assert "unsure" in _fallback_note(_d(need="unsure"))
    assert "判定不可用" in _fallback_note(_d(need=None))
    assert _fallback_note(_d(need="vector")) == ""                        # 判定说向量：正常路径，无需说明
    assert "判定不可用" in _fallback_note(None)                           # 判定模块整体不可用
