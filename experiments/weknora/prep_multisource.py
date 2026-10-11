"""为"多源页"实验准备语料：把 A5 与 O2 两篇原文（HTML）抽成 Markdown。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\prep_multisource.py [源代号 ...]
    不带参数时默认抽 A5 与 O2；例如 `... prep_multisource.py P6 P7`。
产出：
    data/tmp/weknora-multisource/<代号>.md          （喂 WeKnora「手工知识」接口）
    data/tmp/weknora-multisource/meta.json          （节数 / 字符数 / 词表重叠）
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.kb.chunker import extract_sections_html  # noqa: E402

SRC_DIR = ROOT / "data" / "kb-src" / "articles"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-multisource"

# 源代号 → (相对 SRC_DIR 的路径, 页面标题)
SOURCES = {
    "A5": ("anthropic/05-how-we-contain-claude.html", "A5 · How we contain Claude across products"),
    "O2": ("openai/02-harness-engineering.html", "O2 · Harness engineering"),
    "P6": ("personal/06-hamel-your-ai-product-needs-evals.html", "P6 · Your AI Product Needs Evals"),
    "P7": ("personal/07-hamel-ai-evals-faq.html", "P7 · AI Evals FAQ"),
}

STOP = set(
    """the a an and or of to in for on with that this is are was were be been being it its as at by from
    we our you your they their he she his her not but if then than so such can could may might will would
    have has had do does did more most other others into out up down over under again further once here
    there when where why how all any both each few nor only own same too very s t just don now
    what which who whom these those i me my myself ourselves themselves""".split()
)


def to_markdown(html_path: Path, title: str) -> tuple[str, list[str]]:
    sections = extract_sections_html(html_path.read_text(encoding="utf-8"))
    lines = [f"# {title}", ""]
    for sec in sections:
        lines.append(f"## {sec.path}")
        lines.append("")
        for para in sec.paragraphs:
            lines.append(para.strip())
            lines.append("")
    return "\n".join(lines).strip() + "\n", [s.path for s in sections]


def key_terms(text: str) -> Counter:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-\.]{2,}", text.lower())
    return Counter(w.strip(".-") for w in words if w not in STOP and len(w) > 3)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wanted = [c.upper() for c in sys.argv[1:]] or ["A5", "O2"]
    unknown = [c for c in wanted if c not in SOURCES]
    if unknown:
        raise SystemExit(f"未知源代号：{unknown}（可选：{', '.join(SOURCES)}）")

    meta: dict[str, object] = {}
    terms: dict[str, Counter] = {}
    for code in wanted:
        rel, title = SOURCES[code]
        md, paths = to_markdown(SRC_DIR / rel, title)
        (OUT_DIR / f"{code}.md").write_text(md, encoding="utf-8")
        terms[code] = key_terms(md)
        meta[code] = {
            "src": rel,
            "sections": len(paths),
            "chars": len(md),
            "section_paths": paths,
        }

    top_shared: list[tuple[str, list[int]]] = []
    if len(wanted) == 2:
        ta, tb = terms[wanted[0]], terms[wanted[1]]
        shared = {
            w: [ta[w], tb[w]]
            for w in set(ta) & set(tb)
            if ta[w] >= 4 and tb[w] >= 4
        }
        top_shared = sorted(shared.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:40]
        meta["shared_terms_ge4x_ge4"] = {w: c for w, c in top_shared}
        meta["shared_term_count_ge4x_ge4"] = len(shared)

    (OUT_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for code in wanted:
        print(code, meta[code]["sections"], "sections,", meta[code]["chars"], "chars")
    if top_shared:
        print(f"\ntop shared terms ({wanted[0]} count, {wanted[1]} count):")
        for w, (ca, cb) in top_shared[:30]:
            print(f"  {w:<28} {ca:>4} {cb:>4}")


if __name__ == "__main__":
    main()
