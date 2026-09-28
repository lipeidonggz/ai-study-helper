"""把两份语料的"每社区覆盖多少源 token"与论文 Table 6 对齐比较。

论文数字（Table 6，§4.1）：
  Podcast: 源 1,014,611 token；C0 34 社区；C1 367；C2 969；C3 1310；TS 1669
  News   : 源 1,707,694 token；C0 55；C1 555；C2 1797；C3 2142；TS 3197
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PAPER = {
    "Podcast": {"src": 1_014_611, "levels": {"C0": 34, "C1": 367, "C2": 969, "C3": 1310, "TS": 1669}},
    "News": {"src": 1_707_694, "levels": {"C0": 55, "C1": 555, "C2": 1797, "C3": 2142, "TS": 3197}},
}

CORPORA = {
    "小语料（7 篇 / 3.2 万 token）": Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-dynamic/output"),
    "评测语料（14 篇 / 10.9 万 token）": Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-eval/output"),
}
SRC_TOKENS = {
    "小语料（7 篇 / 3.2 万 token）": 32_363,
    "评测语料（14 篇 / 10.9 万 token）": 108_786,
}


def main() -> int:
    print("=== 每个社区平均覆盖多少源 token（越大越说明一份报告要压缩很多材料）===")
    print(f"{'':34s} {'源 token':>10s} {'L0':>8s} {'L1':>8s} {'L2':>8s} {'全部社区':>9s}")
    for name, d in PAPER.items():
        src = d["src"]
        lv = d["levels"]
        total = lv["C1"] + lv["C2"] + lv["C3"] + lv["C0"]
        print(f"{'论文 · ' + name:34s} {src:10,d} {src / lv['C0']:8,.0f} {src / lv['C1']:8,.0f} {src / lv['C2']:8,.0f} {src / total:9,.0f}")
    for name, path in CORPORA.items():
        if not path.exists():
            print(f"{name:34s} (没有 output)")
            continue
        comms = pd.read_parquet(path / "communities.parquet")
        src = SRC_TOKENS[name]
        cnt = comms.groupby("level").size().to_dict()
        total = len(comms)
        print(f"{name:34s} {src:10,d} {src / max(cnt.get(0, 1), 1):8,.0f} "
              f"{src / max(cnt.get(1, 1), 1):8,.0f} {src / max(cnt.get(2, 1), 1):8,.0f} {src / total:9,.0f}")

    print()
    print("=== 实体密度（实体数 / 源 token，越大说明'每个概念都成了实体'）===")
    for name, path in CORPORA.items():
        if not path.exists():
            continue
        ents = len(pd.read_parquet(path / "entities.parquet"))
        rels = len(pd.read_parquet(path / "relationships.parquet"))
        tus = len(pd.read_parquet(path / "text_units.parquet"))
        src = SRC_TOKENS[name]
        print(f"  {name:34s} 实体 {ents:5,d}（{src / ents:6.1f} token/实体）· 关系 {rels:5,d} · 块 {tus:4,d}（{ents / tus:5.1f} 实体/块）")
    print()
    print("论文那两份语料的实体数没在 Table 6 里给，但社区数（≈图被切多碎）可以直接比：")
    print("  论文 Podcast：1M token 切出 34 个根社区 / 1669 个最细社区 ⇒ 平均 29,842 token/根社区、608 token/最细社区")
    print("  我们的评测语料：10.9 万 token 切出 47 个根社区 / 1185 个最细社区 ⇒ 平均 2,315 token/根社区、92 token/最细社区")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
