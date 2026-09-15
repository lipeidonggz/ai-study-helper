"""谓词清洗的「只删不改」护栏单元测试（回归探针）。

不变式：清洗结果必须能由原谓词**删词**得到（允许时态 / 单复数形态变化），不得换成另一个动词。
护栏靠词形变体集合比对（`_stem_variants`）实现。

根因类（2026-09-15）：早先只取单一词干（placed→plac），kept→keep / made→make / known→know
这类**合法**清洗被误判成"换词"，A5 一遍跑下来 51 条清洗里误拒 5 条。

注意：负例（必须被拒的那些）**刻意不用本项目语料里的例子**，免得过拟合到 A5。
"""

from scripts.compile_slice_b6 import (
    _is_subsequence,
    _needs_clean,
    _snap_relation,
    _stem_variants,
)


# ---------- 词形变体集合 ----------

def test_stem_variants_covers_irregular_and_regular_forms():
    assert "keep" in _stem_variants("kept")
    assert "make" in _stem_variants("made")
    assert "do" in _stem_variants("done")
    assert "know" in _stem_variants("known")
    assert "apply" in _stem_variants("applied")
    assert "place" in _stem_variants("placed")
    assert "run" in _stem_variants("running")
    assert "box" in _stem_variants("boxes")
    assert "have" in _stem_variants("has")
    assert "have" in _stem_variants("had")
    assert "be" in _stem_variants("are")
    assert "be" in _stem_variants("was")


# ---------- 子序列校验：合法清洗必须通过 ----------

def test_subsequence_accepts_legal_cleaning():
    # 回归探针：A5 一遍跑里被误拒的 5 条（原样钉住）
    assert _is_subsequence("know", "is best known as")
    assert _is_subsequence("do", "would have done")
    assert _is_subsequence("keep", "are kept deliberately minimal")
    assert _is_subsequence("make", "made them harder to audit")
    assert _is_subsequence("apply", "needs to be applied to")
    assert _is_subsequence("have", "has authority to grant")
    # 常规清洗形态：去修饰 / 去情态 / 去否定 / 改时态
    assert _is_subsequence("trusted", "should not be implicitly trusted")
    assert _is_subsequence("inspect", "can inspect")
    assert _is_subsequence("become", "will need to become")
    assert _is_subsequence("enforce", "enforces security controls over")
    assert _is_subsequence("limit", "can help limit")
    # 子序列允许跳词，但不允许加词
    assert _is_subsequence("keep", "must keep pace to meet")


# ---------- 子序列校验：换词 / 乱序必须被拒 ----------

def test_subsequence_rejects_substitution_and_reorder():
    assert not _is_subsequence("handle", "grows large enough that")
    assert not _is_subsequence("cause", "affects the outcome")
    assert not _is_subsequence("inspect", "can read")
    assert not _is_subsequence("read can", "can read")
    assert not _is_subsequence("", "can read")


# ---------- 谁需要清洗 ----------

def test_needs_clean_targets():
    assert _needs_clean("has its own filesystem")          # ≥4 词，宾语混进来了
    assert _needs_clean("morph and evolve")                # 并列
    assert _needs_clean("can isolate")                     # 情态
    assert _needs_clean("should not be implicitly trusted")  # 否定
    assert _needs_clean("grows large enough that the loop tips")  # 从句
    assert not _needs_clean("isolates")                    # 干净动词原形不动
    assert not _needs_clean("has")
    assert not _needs_clean("is")


# ---------- 表外词的两级确定性兜底 ----------

def test_snap_relation_alias_and_form():
    assert _snap_relation("controls") == "constrains"   # ① 关系别名
    assert _snap_relation("prevents") == "blocks"
    assert _snap_relation("becomes") == "become"        # ② 形态/时态
    assert _snap_relation("Becomes") == "become"        # 大小写不敏感
    assert _snap_relation("") is None
    # 真缺口不许假装归一（留给「待定」）
    assert _snap_relation("frobnicates") is None
    # be 家族故意不在这里折（表内 is / is_a / has_property 是三条不同语义档）
    assert _snap_relation("are") is None
