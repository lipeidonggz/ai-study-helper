"""量化"社区报告到底压缩了多少"：L0/L1/L2 报告总长 vs 源文档总长（字符 + token）。"""

from __future__ import annotations

import glob
import io
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def ntokens(texts: list[str]) -> int:
    try:
        from graphrag.tokenizer.get_tokenizer import get_tokenizer

        tk = get_tokenizer()
        return sum(len(tk.encode(t)) for t in texts)
    except Exception as e:  # noqa: BLE001
        print("  （tokenizer 不可用，按字符估）", e)
        return sum(len(t) for t in texts) // 2


def main() -> int:
    src_files = sorted(glob.glob(str(ROOT / "input" / "*")))
    src = [io.open(f, encoding="utf-8").read() for f in src_files]
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    tu = pd.read_parquet(OUT / "text_units.parquet")

    print("=== 源文档 ===")
    for f, s in zip(src_files, src):
        print(f"   {Path(f).name:34s} {len(s):7,} 字符")
    src_chars, src_tok = sum(len(s) for s in src), ntokens(src)
    print(f"   合计 {src_chars:,} 字符 / {src_tok:,} token")
    print()
    print(f"=== text_unit（真正喂给抽取的切块）{len(tu)} 块，{tu['text'].str.len().sum():,} 字符 / {ntokens(list(tu['text'])):,} token ===")
    print()
    print("=== 社区报告（full_content）===")
    for lv in sorted(reps["level"].unique()):
        sub = reps[reps["level"] == lv]
        chars = int(sub["full_content"].str.len().sum())
        toks = ntokens(list(sub["full_content"]))
        print(f"   L{lv}: {len(sub):3d} 份 | {chars:8,} 字符 ({chars / src_chars:6.1%} 源) | {toks:7,} token ({toks / src_tok:6.1%} 源) | 平均 {chars // max(len(sub),1):,} 字符/份")
    all_chars = int(reps["full_content"].str.len().sum())
    all_toks = ntokens(list(reps["full_content"]))
    print(f"   全部: {len(reps)} 份 | {all_chars:,} 字符 ({all_chars / src_chars:.1%} 源) | {all_toks:,} token ({all_toks / src_tok:.1%} 源)")
    print()
    print("=== 对照：论文里说根层摘要只要 TS 的 2–3% ===")
    l0 = reps[reps["level"] == 0]
    l0_toks = ntokens(list(l0["full_content"]))
    print(f"   我们语料：L0 报告 {l0_toks:,} token / 源文本 {src_tok:,} token = {l0_toks / src_tok:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
