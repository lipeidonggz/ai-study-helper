"""MANIFEST.md 台账解析测试：编号/采集状态/日期解析/衰减分类。"""

from pathlib import Path

import pytest

from app.kb.manifest import parse_manifest

_MANIFEST = Path(__file__).resolve().parents[2] / "data" / "kb-src" / "MANIFEST.md"


@pytest.mark.skipif(not _MANIFEST.exists(), reason="素材台账未采集（本地 gitignored）")
def test_parse_manifest_fields():
    docs = {d.source_id: d for d in parse_manifest(_MANIFEST)}
    assert "O2" in docs and "A5" in docs and "OW1" in docs
    # 名称里的年月被解析为 knowledge_date
    assert docs["O2"].knowledge_date == "2026-02"
    assert docs["A5"].knowledge_date == "2026-05"
    # 衰减分类按类别映射
    assert docs["O2"].decay_class == "fast"  # 业界文章
    assert docs["T1"].decay_class == "slow"  # 教程
    assert docs["OW1"].decay_class == "never"  # 项目自有
    # 未采集素材 collected=False
    assert docs["PAPER"].collected is False
    assert docs["O2"].collected is True
    # 多路径素材拆成列表
    assert len(docs["OW1"].paths) >= 1
