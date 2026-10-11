"""把 A5 原文（HTML）抽成 Markdown，复刻我们自己的抽取口径，供 WeKnora「手工知识」接口使用。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\extract_a5.py
产出：
    data/tmp/weknora-a5/A5.md         （正文，用于 POST /knowledge/manual）
    data/tmp/weknora-a5/A5.meta.json  （节数 / 字符数 / token 估算）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.kb.chunker import extract_sections_html  # noqa: E402

SRC = ROOT / "data" / "kb-src" / "articles" / "anthropic" / "05-how-we-contain-claude.html"
OUT_DIR = ROOT / "data" / "tmp" / "weknora-a5"


def count_tokens(text: str) -> int | None:
    try:
        import tiktoken  # type: ignore

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:
        return None


def main() -> None:
    html = SRC.read_text(encoding="utf-8")
    sections = extract_sections_html(html)

    lines: list[str] = ["# A5 · How we contain Claude across products", ""]
    for sec in sections:
        lines.append(f"## {sec.path}")
        lines.append("")
        for para in sec.paragraphs:
            lines.append(para.strip())
            lines.append("")
    md = "\n".join(lines).strip() + "\n"

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "A5.md").write_text(md, encoding="utf-8")

    toks = count_tokens(md)
    meta = {
        "source_html": str(SRC.relative_to(ROOT)),
        "sections": len(sections),
        "chars": len(md),
        "chars_no_space": len(md.replace(" ", "").replace("\n", "")),
        "tokens_cl100k": toks,
        "section_paths": [s.path for s in sections],
    }
    (OUT_DIR / "A5.meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: meta[k] for k in ("sections", "chars", "tokens_cl100k")}, ensure_ascii=True))
    print("sections:", json.dumps(meta["section_paths"], ensure_ascii=True)[:800])


if __name__ == "__main__":
    main()
