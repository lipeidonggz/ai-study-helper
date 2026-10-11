"""P6+P7 复盘：为什么"页面素材＝逐字原文块、96% 的块被引用"，产物却只有源文的 78%？

做法（不碰数据库，直接用当时落盘的快照）：
    · 源 = data/tmp/weknora-multisource/P6.md + P7.md（以及去掉 `## 标题行` 的正文版）
    · 产物 = data/tmp/weknora-multisource/wiki-pages.json（P6+P7 跑完后的 86 页）
    · 按字符 n-gram 做集合对账：源里有多少 n-gram 出现在"引用该文档的页面"里

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_coverage.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "tmp" / "weknora-multisource"
N = 12  # 字符 n-gram 粒度

DOC_IDS = {
    "P6": "2478eb26-2d7e-4aa9-8e08-60a4350ecbb2",
    "P7": "488c2d3c-0bf0-431c-baf8-a34b40cb7302",
}


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def grams(s: str) -> set[str]:
    s = norm(s)
    return {s[i : i + N] for i in range(len(s) - N + 1)}


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    obj = json.loads((D / "wiki-pages.json").read_text(encoding="utf-8"))
    pages = obj.get("response", obj).get("pages") or []

    # 页面按"引用了哪些文档"分组
    def page_texts(pred) -> str:
        return "\n".join(p.get("content") or "" for p in pages if pred(p))

    print(f"页数={len(pages)}  Σcontent={sum(len(p.get('content') or '') for p in pages)}")

    for code, kid in DOC_IDS.items():
        raw = (D / f"{code}.md").read_text(encoding="utf-8")
        body = "\n".join(l for l in raw.split("\n") if not l.startswith("#"))
        head = "\n".join(l for l in raw.split("\n") if l.startswith("#"))

        citing = [p for p in pages if kid in (p.get("source_refs") or [])]
        product = "\n".join(p.get("content") or "" for p in citing)

        g_raw, g_body, g_head = grams(raw), grams(body), grams(head)
        g_prod = grams(product)

        def cov(a: set[str], b: set[str]) -> float:
            return len(a & b) / len(a) if a else 0.0

        print(f"\n== {code} ==")
        print(f"  源：raw {len(raw)} 字符 / {len(g_raw)} 个 {N}-gram；正文 {len(body)} 字符；标题行 {len(head)} 字符")
        print(f"  引用它的页：{len(citing)} 页，Σcontent={len(product)} 字符")
        print(f"  源 n-gram 落在产物里的比例：raw {cov(g_raw, g_prod):.3f}  正文 {cov(g_body, g_prod):.3f}  标题行 {cov(g_head, g_prod):.3f}")
        missing = g_body - g_prod
        print(f"  正文里没进任何页的 n-gram：{len(missing)} / {len(g_body)} = {len(missing)/len(g_body):.1%}")

    # 全库视角：P6+P7 两篇合起来
    print("\n== 两篇合起来（按页去重，多源页只算一次）==")
    kids = set(DOC_IDS.values())
    citing_any = [p for p in pages if kids & set(p.get("source_refs") or [])]
    product = "\n".join(p.get("content") or "" for p in citing_any)
    raw_all = "".join((D / f"{c}.md").read_text(encoding="utf-8") for c in DOC_IDS)
    body_all = "\n".join(
        l for c in DOC_IDS for l in (D / f"{c}.md").read_text(encoding="utf-8").split("\n") if not l.startswith("#")
    )
    g_all, g_body_all, g_prod = grams(raw_all), grams(body_all), grams(product)
    print(f"  源 raw {len(raw_all)} 字符（{len(g_all)} gram）／正文 {len(body_all)} 字符（{len(g_body_all)} gram）")
    print(f"  产物 Σcontent={len(product)} 字符")
    print(f"  覆盖率：raw {len(g_all & g_prod)/len(g_all):.3f}  正文 {len(g_body_all & g_prod)/len(g_body_all):.3f}")
    print(f"  正文 n-gram 的重复率（1 - 去重后/总数）：{(1 - len(g_body_all)/len(body_all)):.3f}")


if __name__ == "__main__":
    main()
