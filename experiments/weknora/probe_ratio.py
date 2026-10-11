"""复核"产物体积比"的口径：71% 到底怎么算的，以及它是不是被标题行/覆盖率带偏了。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_ratio.py
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "tmp" / "weknora-multisource"


def md_stats(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    head_chars = sum(len(l) + 1 for l in lines if l.startswith("#"))
    head_lines = sum(1 for l in lines if l.startswith("#"))
    return {
        "total_chars": len(text),
        "heading_chars": head_chars,
        "heading_lines": head_lines,
        "body_chars": len(text) - head_chars,
    }


def main() -> None:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    print("== 源侧（我抽取出来的 Markdown）==")
    src_total = src_body = 0
    for code in ("P6", "P7"):
        s = md_stats(D / f"{code}.md")
        src_total += s["total_chars"]
        src_body += s["body_chars"]
        print(
            f"  {code}: total={s['total_chars']}  heading={s['heading_chars']}"
            f"（{s['heading_lines']} 行）  body={s['body_chars']}"
        )
    print(f"  合计: total={src_total}  body={src_body}")

    print("\n== 产物侧（wiki-pages.json，P6+P7 跑完后的 86 页）==")
    obj = json.loads((D / "wiki-pages.json").read_text(encoding="utf-8"))
    pages = obj.get("response", obj).get("pages") or []
    by_type = Counter(p.get("page_type") for p in pages)
    chars_by_type = Counter()
    for p in pages:
        chars_by_type[p.get("page_type")] += len(p.get("content") or "")
    total_content = sum(chars_by_type.values())
    refs = [c for p in pages for c in (p.get("chunk_refs") or [])]
    uniq = set(refs)
    print(f"  页数={len(pages)}  类型={dict(by_type)}")
    print(f"  Σcontent={total_content}")
    print(f"  按类型: { {k: v for k, v in chars_by_type.items()} }")
    print(f"  chunk_refs: 总条数={len(refs)}  去重后={len(uniq)}")

    print("\n== 比值（同一份产物 / 不同的分母）==")
    print(f"  Σcontent / md_total      = {total_content / src_total:.3f}   ← 这就是 71% 的来源")
    print(f"  Σcontent / md_body_only  = {total_content / src_body:.3f}   ← 去掉我插入的 ## 标题行")
    print(f"  索引/摘要贡献            = {chars_by_type.get('summary', 0) + chars_by_type.get('index', 0)}"
          f"（占产物 {(chars_by_type.get('summary', 0) + chars_by_type.get('index', 0)) / total_content:.1%}）")
    print(f"  被引块覆盖率（引用的去重块数 / 272 源块）= {len(uniq)}/272 = {len(uniq) / 272:.1%}")

    print("\n== 对照：A5 首跑（0037 §3.1）==")
    print("  45 页 / Σcontent 44,629 / 源 28,956 ⇒ 154%；源块 70、被引 68（97%）")


if __name__ == "__main__":
    main()
