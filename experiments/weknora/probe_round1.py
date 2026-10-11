"""第一轮的三个交叉核对：
1) stats.orphan_count 与 lint.orphan_page 为什么对不上；
2) 目录结构长什么样；
3) 索引页与语言（英文源 → 页面是什么语言）。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "tmp" / "weknora-a5"


def load(name: str):
    obj = json.loads((D / name).read_text(encoding="utf-8"))
    return obj.get("response", obj)


def main() -> None:
    pages = load("wiki-pages.json").get("pages") or []
    graph = load("wiki-graph.json")
    lint = load("wiki-lint.json")
    folders = load("wiki-folders.json").get("folders") or []
    stats = load("wiki-stats.json")

    # --- 1) orphan 对账 ---
    in_deg = Counter()
    for e in graph.get("edges") or []:
        tgt = e.get("target") or e.get("to")
        in_deg[tgt] += 1
    lint_orphans = {i.get("page_slug") for i in (lint.get("issues") or []) if i.get("type") == "orphan_page"}
    graph_orphans = {p.get("slug") for p in pages if in_deg.get(p.get("slug"), 0) == 0}
    by_slug = {p.get("slug"): p for p in pages}

    print("[1] orphan 三方对账")
    print("   stats.orphan_count =", stats.get("orphan_count"))
    print("   lint.orphan_page   =", len(lint_orphans))
    print("   graph 上入度为 0   =", len(graph_orphans))
    print("   lint↔graph 同集合? ", lint_orphans == graph_orphans,
          "| lint⊆graph:", lint_orphans <= graph_orphans, "| graph⊆lint:", graph_orphans <= lint_orphans)
    only_graph = sorted(graph_orphans - lint_orphans)
    only_lint = sorted(lint_orphans - graph_orphans)
    print("   只在 graph 侧为孤儿:", len(only_graph), [ (s, (by_slug.get(s) or {}).get('page_type')) for s in only_graph[:6] ])
    print("   只在 lint 侧为孤儿:", len(only_lint), [ (s, (by_slug.get(s) or {}).get('page_type')) for s in only_lint[:6] ])
    print("   孤儿页类型分布:", dict(Counter((by_slug.get(s) or {}).get("page_type") for s in graph_orphans)))

    # --- 2) 目录 ---
    print("\n[2] 目录结构")
    print("   folders:", json.dumps([{k: f.get(k) for k in ("id", "name", "parent_id", "path")} for f in folders], ensure_ascii=True))
    paths = Counter()
    for p in pages:
        wp = p.get("wiki_path") or p.get("category_path") or ""
        paths[wp] += 1
    print("   wiki_path 分布（前 12）:", json.dumps(dict(paths.most_common(12)), ensure_ascii=True))

    # --- 3) 语言与索引页 ---
    print("\n[3] 语言与索引页")
    zh = en = 0
    for p in pages:
        c = p.get("content") or ""
        han = sum(1 for ch in c if "\u4e00" <= ch <= "\u9fff")
        zh += 1 if han > len(c) * 0.05 else 0
        en += 1 if han == 0 else 0
    print(f"   含中文的页 = {zh}/{len(pages)}；纯英文页 = {en}")
    idx = [p for p in pages if p.get("page_type") == "index"]
    if idx:
        i = idx[0]
        print("   index 页:", i.get("title"), "| chars =", len(i.get("content") or ""))
        print("   索引页前 400 字:", json.dumps((i.get("content") or "")[:400], ensure_ascii=True))
    summ = [p for p in pages if p.get("page_type") == "summary"]
    if summ:
        print("   summary 页:", summ[0].get("title"), "| chars =", len(summ[0].get("content") or ""))


if __name__ == "__main__":
    main()
