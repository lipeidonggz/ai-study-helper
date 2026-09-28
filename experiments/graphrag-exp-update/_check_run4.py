"""把第 4 次运行（workflows 陷阱那次）的现场翻出来：input 里到底几个文件、它索引了几份。"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
TS = "20260926-121821"  # 第 4 次运行（写回 workflows 之后）


def main() -> None:
    print("=" * 74)
    print("一、当前 input/ 目录（第 4 次运行之后没再动过）")
    print("=" * 74)
    for f in sorted((ROOT / "input").iterdir()):
        print(f"  {f.name:<16} {f.stat().st_size:>7} 字节")

    d = ROOT / "update_output" / TS / "delta"
    print()
    print("=" * 74)
    print(f"二、第 4 次运行的 delta/{TS}/ 里索引了几份文档")
    print("=" * 74)
    docs = pd.read_parquet(d / "documents.parquet")
    print(f"  documents 表 {len(docs)} 行：")
    for _, r in docs.iterrows():
        print(f"    [{int(r['human_readable_id'])}] {r['title']}   (text 前 40 字：{str(r['text'])[:40]!r})")
    tu = pd.read_parquet(d / "text_units.parquet")
    print(f"  text_units {len(tu)} 块，每块 token {tu['n_tokens'].tolist()}")
    print("  按文档分组：")
    for did, g in tu.groupby("document_id"):
        title = docs.loc[docs["id"] == did, "title"].tolist()
        print(f"    document_id={str(did)[:12]}… 块数={len(g)}  ← {title}")

    print()
    print("=" * 74)
    print("三、第 4 次运行前后的索引本体（output/）")
    print("=" * 74)
    for n in ["documents", "text_units", "entities", "relationships", "communities", "community_reports"]:
        print(f"  {n:<20} {len(pd.read_parquet(ROOT / 'output' / (n + '.parquet')))}")

    print()
    print("=" * 74)
    print("四、四次运行的输入状态对照（从各轮的 delta 反推）")
    print("=" * 74)
    for ts in sorted(p.name for p in (ROOT / "update_output").iterdir() if p.is_dir()):
        dd = ROOT / "update_output" / ts / "delta"
        f = dd / "documents.parquet"
        if f.exists():
            dd_df = pd.read_parquet(f)
            note = "、".join(dd_df["title"].tolist())
            print(f"  {ts}  delta/documents = {len(dd_df)} 份：{note}")
        else:
            print(f"  {ts}  delta 里没有 documents 表")

    ctx = ROOT / "output" / "context.json"
    if ctx.exists():
        print("\ncontext.json:", json.loads(ctx.read_text(encoding='utf-8')))


if __name__ == "__main__":
    main()
