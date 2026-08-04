"""golden set 用例文件校验测试：确保所有用例符合 schema 且 id 唯一。"""

from pathlib import Path

from eval.schema import load_cases

CASES_DIR = Path(__file__).resolve().parent.parent / "eval" / "cases"


def test_all_cases_load_and_valid():
    """所有用例文件应能通过 Pydantic 校验（加载即校验）。"""
    cases = load_cases(CASES_DIR)
    assert len(cases) >= 70  # 首批至少 70 条


def test_ids_unique():
    """用例 id 必须全局唯一。"""
    cases = load_cases(CASES_DIR)
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids))


def test_first_batch_category_counts():
    """首批用例结构：工具调用 40 + 边界 20 + 综合 10。"""
    cases = load_cases(CASES_DIR)
    from collections import Counter

    counts = Counter(c.category for c in cases)
    assert counts["tool_call"] >= 40
    assert counts["boundary"] >= 20
    assert counts["combined"] >= 10
