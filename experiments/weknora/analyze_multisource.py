"""多源页实验的产物分析：形状 + 可读性的两个粗指标。

粗指标（只是筛查，不是判据）：
    ① 段内近重复：同一页里是否存在两段（字符 2-gram Jaccard >= 阈值）在说同一件事
       —— 这是"Pass 0 的一句话 description ＋ 原文块"同页并列时最典型的拼接痕迹。
    ② 标题重复：同一页里出现两个同名/近同名小节标题。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\analyze_multisource.py
输出：
    data/tmp/weknora-multisource/analysis.json ＋ 控制台摘要
"""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "tmp" / "weknora-multisource"

DUP_THRESHOLD = 0.55


def load(name: str):
    obj = json.loads((D / name).read_text(encoding="utf-8"))
    return obj.get("response", obj)


def bigrams(text: str) -> set[str]:
    t = re.sub(r"\s+", "", text)
    t = re.sub(r"[\[\]（）()`*#>|·、，。：；！？\"'\-]", "", t)
    return {t[i : i + 2] for i in range(len(t) - 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def split_blocks(content: str) -> list[str]:
    """把正文切成"段落/列表项"，跳过代码块与标题。"""
    blocks: list[str] = []
    in_fence = False
    for raw in content.split("\n"):
        line = raw.strip()
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#"):
            continue
        blocks.append(line)
    return blocks


def headings(content: str) -> list[str]:
    return [l.strip().lstrip("#").strip() for l in content.split("\n") if l.strip().startswith("#")]


def dup_report(page: dict) -> dict:
    blocks = split_blocks(page.get("content") or "")
    grams = [bigrams(b) for b in blocks]
    pairs = []
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            score = jaccard(grams[i], grams[j])
            if score >= DUP_THRESHOLD:
                pairs.append(
                    {
                        "score": round(score, 3),
                        "a": blocks[i][:90],
                        "b": blocks[j][:90],
                    }
                )
    heads = headings(page.get("content") or "")
    dup_heads = [h for h, n in Counter(heads).items() if n > 1 and h]
    return {"dup_pairs": pairs, "dup_headings": dup_heads}


def desc_redundancy(page: dict) -> dict:
    """首行摘要（Pass 0 的 description）与后续段落的最高相似度。

    这是"摘要句 ＋ 原文块"同页并列时最常见的一种重复：
    首行已经说完了，紧接着的原文块把同一件事又说了一遍。
    """
    blocks = split_blocks(page.get("content") or "")
    if len(blocks) < 2:
        return {"max": 0.0, "peer": ""}
    head = bigrams(blocks[0])
    best, peer = 0.0, ""
    for b in blocks[1:]:
        score = jaccard(head, bigrams(b))
        if score > best:
            best, peer = score, b[:90]
    return {"max": round(best, 3), "peer": peer}


def main() -> None:
    pages = (load("wiki-pages.json") or {}).get("pages") or []
    lint = load("wiki-lint.json") or {}
    stats = load("wiki-stats.json") or {}
    folders = (load("wiki-folders.json") or {}).get("folders") or []

    lens = sorted(len(p.get("content") or "") for p in pages)
    ref_counts = [len(p.get("source_refs") or []) for p in pages]
    chunk_refs = [len(p.get("chunk_refs") or []) for p in pages]

    dup_pages = []
    for p in pages:
        rep = dup_report(p)
        if rep["dup_pairs"] or rep["dup_headings"]:
            dup_pages.append({"slug": p.get("slug"), "title": p.get("title"), **rep})

    desc_rows = []
    for p in pages:
        if p.get("page_type") in {"summary", "index"}:
            continue
        rep = desc_redundancy(p)
        desc_rows.append({"slug": p.get("slug"), "refs": len(p.get("source_refs") or []), **rep})
    desc_rows.sort(key=lambda r: -r["max"])

    issues = (lint or {}).get("issues") or []
    out = {
        "pages": {
            "total": len(pages),
            "by_type": dict(Counter(p.get("page_type") for p in pages)),
            "source_refs_hist": dict(sorted(Counter(ref_counts).items())),
            "multi_source_pages": sum(1 for c in ref_counts if c >= 2),
            "chunk_refs_total": sum(chunk_refs),
            "chunk_refs_p50": int(statistics.median(chunk_refs)) if chunk_refs else 0,
            "content_chars": {
                "min": lens[0] if lens else 0,
                "p50": int(statistics.median(lens)) if lens else 0,
                "p90": lens[int(len(lens) * 0.9)] if lens else 0,
                "max": lens[-1] if lens else 0,
                "total": sum(lens),
            },
            "top_by_chars": [
                {"slug": p.get("slug"), "chars": len(p.get("content") or ""),
                 "refs": len(p.get("source_refs") or [])}
                for p in sorted(pages, key=lambda x: -len(x.get("content") or ""))[:12]
            ],
        },
        "lint": {
            "issues_total": len(issues),
            "by_type": dict(Counter(i.get("type") for i in issues)),
            "by_severity": dict(Counter(i.get("severity") for i in issues)),
        },
        "folders": len(folders),
        "stats": {k: v for k, v in (stats or {}).items() if k != "recent_updates"},
        "readability_probe": {
            "threshold": DUP_THRESHOLD,
            "pages_with_dup": len(dup_pages),
            "detail": sorted(dup_pages, key=lambda d: -len(d["dup_pairs"]))[:15],
            "desc_redundancy_top": desc_rows[:15],
            "desc_redundancy_ge_040": sum(1 for r in desc_rows if r["max"] >= 0.40),
            "desc_redundancy_ge_030": sum(1 for r in desc_rows if r["max"] >= 0.30),
            "content_pages": len(desc_rows),
        },
    }
    (D / "analysis.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(out["pages"], ensure_ascii=True, indent=2))
    print("lint:", json.dumps(out["lint"], ensure_ascii=True))
    print(
        "readability_probe:",
        out["readability_probe"]["pages_with_dup"],
        f"/ {len(pages)} pages have near-duplicate blocks (Jaccard >= {DUP_THRESHOLD})",
    )
    for d in out["readability_probe"]["detail"][:8]:
        print(f"  - {d['slug']}  dup_pairs={len(d['dup_pairs'])} dup_heads={d['dup_headings']}")
    rp = out["readability_probe"]
    print(
        "首行摘要 vs 后续段落：",
        f">=0.40 的有 {rp['desc_redundancy_ge_040']} 页，",
        f">=0.30 的有 {rp['desc_redundancy_ge_030']} 页",
        f"（内容页共 {rp['content_pages']}）",
    )
    for r in rp["desc_redundancy_top"][:8]:
        print(f"  - {r['slug']}  max={r['max']} refs={r['refs']}")


if __name__ == "__main__":
    main()
