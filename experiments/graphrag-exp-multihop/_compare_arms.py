"""三臂对照（B / A1 / A2）：把 0039 §2.1 主表的每一格都从 output/ 现算一遍。

口径：
  · 源 token        = input/*.txt 用 graphrag 的 tokenizer 数出来的合计
  · token/实体      = 源 token ÷ 实体数
  · 根社区          = communities 表里 level == 0 的社区数
  · 每根覆盖        = 源 token ÷ 根社区数
  · 根层/全层报告比 = 该层（全部层）community_reports.full_content 的 token 合计 ÷ 源 token
只读，不跑模型。
"""

from __future__ import annotations

import glob
import io
from pathlib import Path

import pandas as pd

REPO = Path("D:/lida-data/vscode/ai-study-helper")
ARMS = {
    "B（手工给域）": REPO / "data/tmp/graphrag-exp-multihop/output",
    "A1（自动抽取 ＋ B 报告）": REPO / "data/tmp/graphrag-exp-multihop-autodomain/output",
    "A2（全自动 · 自洽）": REPO / "data/tmp/graphrag-exp-multihop-autodomain-selfconsistent/output",
}
INPUT = REPO / "data/tmp/graphrag-exp-multihop/input"


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    src_tok = sum(len(tk.encode(io.open(f, encoding="utf-8").read())) for f in sorted(glob.glob(str(INPUT / "*"))))
    print(f"源语料：{src_tok:,} token（{len(glob.glob(str(INPUT / '*')))} 篇）\n")

    hdr = f"{'臂':<26}{'实体':>7}{'token/实体':>11}{'关系':>7}{'社区':>7}{'层级':>7}{'根社区':>7}{'每根覆盖':>10}{'根层÷原文':>11}{'全层÷原文':>11}"
    print(hdr)
    print("-" * len(hdr))
    for tag, out in ARMS.items():
        ent = pd.read_parquet(out / "entities.parquet")
        rel = pd.read_parquet(out / "relationships.parquet")
        com = pd.read_parquet(out / "communities.parquet")
        rep = pd.read_parquet(out / "community_reports.parquet")
        n_ent, n_rel, n_com = len(ent), len(rel), len(com)
        levels = sorted(int(x) for x in com["level"].unique())
        roots = int((com["level"] == 0).sum())
        root_rep = sum(len(tk.encode(str(x))) for x in rep[rep["level"] == 0]["full_content"])
        all_rep = sum(len(tk.encode(str(x))) for x in rep["full_content"])
        print(f"{tag:<26}{n_ent:>7,}{src_tok / n_ent:>11.1f}{n_rel:>7,}{n_com:>7,}"
              f"{f'{levels[0]}–{levels[-1]}':>7}{roots:>7,}{src_tok / roots:>10,.0f}"
              f"{root_rep / src_tok:>10.1%}{all_rep / src_tok:>11.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
