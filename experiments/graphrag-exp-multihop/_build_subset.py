"""从 MultiHop-RAG 切一个 ~11 万 token 的分层子集（与上一份评测语料同量级，做"同规模不同语料类型"的对照）。

抽样：按分类比例分层、固定种子，逐篇累加直到接近预算。输出：
  input/article-XXX.txt   （首行 # 标题）
  subset_manifest.json    （文件 → 原始文章 title/url/category/published_at/token，供后续"社区→文档"对账）
用法：python _build_subset.py --budget 110000 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def slug(text: str, maxlen: int = 48) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return (s[:maxlen] or "article").rstrip("-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=110_000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", default="input")
    args = ap.parse_args()

    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    data = json.loads((ROOT / "download" / "corpus.json").read_text(encoding="utf-8"))
    for a in data:
        a["_tok"] = len(tk.encode(str(a.get("body", ""))))
        a["_title"] = (a.get("title") or "").strip()
    total = sum(a["_tok"] for a in data)

    rng = random.Random(args.seed)
    by_cat: dict[str, list[dict]] = {}
    for a in data:
        by_cat.setdefault(a.get("category") or "unknown", []).append(a)
    for v in by_cat.values():
        rng.shuffle(v)
    shares = {c: len(v) / len(data) for c, v in by_cat.items()}
    quota = {c: int(args.budget * s) for c, s in shares.items()}

    picked: list[dict] = []
    for c, q in quota.items():
        acc = 0
        for a in by_cat[c]:
            if acc >= q:
                break
            picked.append(a)
            acc += a["_tok"]
    picked.sort(key=lambda a: (a.get("published_at") or "", a["_title"]))

    outdir = ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob("*.txt"):
        old.unlink()
    manifest = []
    acc = 0
    for i, a in enumerate(picked, 1):
        name = f"article-{i:03d}-{slug(a['_title'])}.txt"
        body = str(a.get("body", "")).strip()
        (outdir / name).write_text(f"# {a['_title']}\n\n## {a['_title']}\n\n{body}\n", encoding="utf-8")
        acc += a["_tok"]
        manifest.append({
            "file": name, "title": a["_title"], "url": a.get("url"),
            "category": a.get("category"), "source": a.get("source"),
            "published_at": a.get("published_at"), "tokens": a["_tok"],
        })
    (ROOT / "subset_manifest.json").write_text(
        json.dumps({"budget": args.budget, "seed": args.seed, "articles": manifest,
                    "total_tokens": acc, "full_corpus_tokens": total}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    from collections import Counter

    print(f"子集：{len(picked)} 篇 / {acc:,} token（全语料 {total:,}，占 {acc / total:.1%}）")
    print("分类分布：", dict(Counter(a.get("category") for a in picked).most_common()))
    print("来源数：", len({a.get("source") for a in picked}))
    dates = sorted(str(a.get("published_at"))[:10] for a in picked)
    print(f"时间范围：{dates[0]} → {dates[-1]}")
    print(f"输出：{outdir}（{len(list(outdir.glob('*.txt')))} 个文件）+ subset_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
