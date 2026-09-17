"""Pass 1 补问的确定性护栏测试。

背景（2026-09-17 实测）：有一次某个窗口的输出**整批没写 evidence_texts**（83 条连续），
体检按"没写引文 → 丢弃"全数丢掉，等于一次丢了 21% 的抽取结果。

处置口径：这类**批次级故障不该用丢弃兜**——像归一步那样**补问一次**（只补引文、不重抽断言）。
但补问也是 LLM 输出，**必须过同一道确定性护栏**：引文要在窗口里逐字定位得到（`_find_raw`），
定位不到＝编的，丢掉；宁可留空，也不许把编的引文塞进去。
"""

from scripts.compile_slice_b6 import _accept_fixed_evidence

WINDOW = (
    "The mechanisms here include system prompts, classifiers, probes, and training modifications. "
    "These defenses are strong. On the evaluation benchmark, attack success stayed near 0.1%."
)


def test_accepts_only_locatable_quotes():
    raw = [
        {"idx": 0, "evidence_texts": ["These defenses are strong."]},                    # 逐字 → 收
        {"idx": 1, "evidence_texts": ["this sentence does not exist anywhere"]},          # 编的 → 丢
        {"idx": 2, "evidence_texts": ["These defenses are strong.", "编的句子"]},          # 部分可用 → 留可用的
    ]
    got = _accept_fixed_evidence(raw, WINDOW)
    assert set(got) == {0, 2}
    assert got[0] == ["These defenses are strong."]
    assert got[2] == ["These defenses are strong."]


def test_tolerates_normalization_and_string_form():
    raw = [
        {"idx": 3, "evidence_texts": "These defenses are strong."},                       # 单字符串
        {"idx": 4, "evidence_texts": ["These  defenses are strong"]},                     # 空白差异（归一化可定位）
        {"idx": 5, "evidence_texts": []},                                                 # 空数组 → 不记
        {"idx": 6, "evidence_texts": ["   "]},                                            # 空白串 → 不记
    ]
    got = _accept_fixed_evidence(raw, WINDOW)
    assert set(got) == {3, 4}


def test_ignores_bad_index_and_non_list():
    raw = [
        {"idx": "x", "evidence_texts": ["These defenses are strong."]},
        {"evidence_texts": ["These defenses are strong."]},
        {"idx": 7, "evidence_texts": None},
        {"idx": 8, "evidence_texts": [123]},
    ]
    assert _accept_fixed_evidence(raw, WINDOW) == {}
