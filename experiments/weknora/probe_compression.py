"""算两个知识库的"产物体积 / 原文体积"，并按页面类型拆开。

原文 = A5 的 Markdown 正文字符数（28,956）。
口径说明：这里算的是"索引期产物体积"，不是"回答一个问题要读多少材料"。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("r1", ROOT / "experiments/weknora/weknora_round1.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

SRC = 28956


def main() -> None:
    rows = []
    for name in ("A5 · How we contain Claude across products", "A5 · focused 对照"):
        kb = m.find_kb_by_name(name)
        if not kb:
            print("找不到库:", name)
            continue
        _st, data = m.get(f"/knowledgebase/{kb['id']}/wiki/pages?page_size=200")
        pages = (data or {}).get("pages") or []
        by_type: dict[str, int] = {}
        for p in pages:
            t = p.get("page_type") or "?"
            by_type[t] = by_type.get(t, 0) + len(p.get("content") or "")
        total = sum(by_type.values())
        summ = by_type.get("summary", 0)
        idx = by_type.get("index", 0)
        ec = total - summ - idx
        rows.append(
            "%-22s pages=%3d  total=%6d (%5.1f%%)  entity+concept=%6d (%5.1f%%)  summary=%5d (%4.1f%%)  index=%3d  by_type=%s"
            % (name[:20], len(pages), total, total / SRC * 100, ec, ec / SRC * 100, summ, summ / SRC * 100, idx,
               {k: v for k, v in sorted(by_type.items())})
        )
    print("原文 =", SRC, "字符")
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
