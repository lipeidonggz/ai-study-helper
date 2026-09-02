"""解析 data/kb-src/MANIFEST.md 素材台账。

设计角度：台账是素材的唯一权威入口（编号/类别/名称/许可/路径/采集状态），
入库状态另存 SQLite（kb.db），两者在 API 层合并展示。
"""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SourceDoc:
    source_id: str
    category: str
    name: str
    url: str
    license: str
    risk: str
    carrier: str
    paths: list[str]
    collected: bool
    knowledge_date: str  # 从名称解析的 YYYY-MM，解析不到为空
    decay_class: str  # never / slow / fast


_DECAY_BY_CATEGORY = {
    "教程": "slow",
    "业界文章": "fast",
    "周刊": "fast",
    "国内厂商": "fast",
    "项目自有": "never",
    "学术论文": "slow",
}


def parse_manifest(path: str | Path) -> list[SourceDoc]:
    """从 Markdown 表格解析全部素材。"""
    text = Path(path).read_text(encoding="utf-8")
    docs: list[SourceDoc] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 9 or not re.match(r"^[A-Z]+\d*$", cells[0]):
            continue
        sid, category, name, url, license_, risk, carrier, local_path, status = cells[:9]
        paths = [p.strip() for p in local_path.split(";") if p.strip()]
        collected = "✅" in status
        m = re.search(r"(\d{4})-(\d{2})", name)
        knowledge_date = f"{m.group(1)}-{m.group(2)}" if m else ""
        docs.append(
            SourceDoc(
                source_id=sid,
                category=category,
                name=name,
                url=url,
                license=license_,
                risk=risk,
                carrier=carrier,
                paths=paths,
                collected=collected,
                knowledge_date=knowledge_date,
                decay_class=_DECAY_BY_CATEGORY.get(category, "slow"),
            )
        )
    return docs
