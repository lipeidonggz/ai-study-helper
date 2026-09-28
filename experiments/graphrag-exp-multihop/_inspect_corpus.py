"""看 MultiHop-RAG 语料的构成：篇数、分类、时间范围、正文 token 分布。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    data = json.loads((ROOT / "download" / "corpus.json").read_text(encoding="utf-8"))
    tk = get_tokenizer()
    print(f"篇数：{len(data)}")
    cats = Counter(a.get("category") for a in data)
    print("分类分布：", dict(cats.most_common()))
    dates = sorted(a.get("published_at", "")[:10] for a in data if a.get("published_at"))
    print(f"时间范围：{dates[0]} → {dates[-1]}")
    srcs = Counter(a.get("source") for a in data)
    print(f"来源数：{len(srcs)}，前 8：", dict(srcs.most_common(8)))
    toks = [len(tk.encode(str(a.get("body", "")))) for a in data]
    toks_sorted = sorted(toks)
    n = len(toks_sorted)
    print()
    print(f"正文 token：合计 {sum(toks):,} · 中位 {toks_sorted[n // 2]:,} · 最小 {toks_sorted[0]:,} · 最大 {toks_sorted[-1]:,}")
    empty = sum(1 for t in toks if t < 20)
    print(f"几乎空的正文（<20 token）：{empty} 篇")
    print()
    print("=== 累计 token 到多少时需要多少篇 ===")
    acc = 0
    for i, t in enumerate(sorted(toks, reverse=True), 1):
        acc += t
        if i in (50, 100, 200, 300, 500) or acc >= 100_000 and acc - t < 100_000:
            print(f"  取最大的 {i:4d} 篇 → 累计 {acc:,} token")
        if acc > 1_800_000:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
