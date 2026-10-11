"""第一轮产物分析：把 data/tmp/weknora-a5/ 的 dump 压成一组可对账的数。

输出：
    data/tmp/weknora-a5/round1-analysis.json
    控制台摘要（ASCII 友好）
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "tmp" / "weknora-a5"


def load(name: str):
    obj = json.loads((D / name).read_text(encoding="utf-8"))
    return obj.get("response", obj)


def main() -> None:
    stats = load("wiki-stats.json")
    graph = load("wiki-graph.json")
    index = load("wiki-index.json")
    pages_obj = load("wiki-pages.json")
    folders_obj = load("wiki-folders.json")
    lint = load("wiki-lint.json")

    pages = pages_obj.get("pages") or []

    # ---- 页面 ----
    by_type = Counter(p.get("page_type") for p in pages)
    lens = sorted(len(p.get("content") or "") for p in pages)
    depths = Counter(p.get("depth") for p in pages)
    cats = Counter((p.get("wiki_path") or p.get("category_path") or "") for p in pages)
    with_refs = sum(1 for p in pages if p.get("source_refs"))
    ref_counts = [len(p.get("source_refs") or []) for p in pages]

    # ---- 链接图 ----
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    node_ids = {n.get("id") or n.get("slug") for n in nodes}
    dangling = []
    for e in edges:
        tgt = e.get("target") or e.get("to")
        if tgt not in node_ids:
            dangling.append((e.get("source") or e.get("from"), tgt))

    # ---- lint ----
    issues = lint.get("issues") or []
    lint_by_type = Counter(i.get("type") for i in issues)
    lint_by_sev = Counter(i.get("severity") for i in issues)
    autofixable = sum(1 for i in issues if i.get("auto_fixable"))

    # ---- 目录 ----
    folders = folders_obj.get("folders") or []

    out = {
        "kb_stats": {k: v for k, v in (stats or {}).items() if k != "recent_updates"},
        "kb_recent_updates_n": len((stats or {}).get("recent_updates") or []),
        "pages": {
            "total": len(pages),
            "by_type": dict(by_type),
            "content_chars": {
                "min": lens[0] if lens else 0,
                "p50": int(statistics.median(lens)) if lens else 0,
                "p90": lens[int(len(lens) * 0.9)] if lens else 0,
                "max": lens[-1] if lens else 0,
                "total": sum(lens),
            },
            "depth_hist": dict(sorted((d if d is not None else -1, c) for d, c in depths.items())),
            "category_paths": dict(cats.most_common(15)),
            "with_source_refs": with_refs,
            "source_refs_total": sum(ref_counts),
            "source_refs_p50": int(statistics.median(ref_counts)) if ref_counts else 0,
        },
        "graph": {
            "nodes": len(nodes),
            "edges": len(edges),
            "dangling_edges": len(dangling),
            "dangling_sample": [list(x) for x in dangling[:10]],
            "meta": graph.get("meta"),
        },
        "lint": {
            "health_score": lint.get("health_score"),
            "total_issues": len(issues),
            "by_type": dict(lint_by_type),
            "by_severity": dict(lint_by_sev),
            "auto_fixable": autofixable,
            "summary": lint.get("summary"),
        },
        "folders": {"count": len(folders), "sample": folders[:10]},
        "index": {
            "version": index.get("version"),
            "groups": [{"type": g.get("type"), "count": len(g.get("pages") or g.get("items") or [])}
                       for g in (index.get("groups") or [])],
            "intro_len": len(index.get("intro") or ""),
        },
    }
    (D / "round1-analysis.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    # 控制台只打关键数，正文不打印
    brief = {
        "stats": out["kb_stats"],
        "pages": {k: v for k, v in out["pages"].items() if k != "category_paths"},
        "graph": {k: v for k, v in out["graph"].items() if k != "meta"},
        "lint": out["lint"],
        "folders_count": out["folders"]["count"],
        "index": out["index"],
    }
    print(json.dumps(brief, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
