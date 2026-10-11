"""挑"多源页"实验的语料配对：把 16 篇 HTML 文章两两比对，看谁跟谁共享概念最多。

用法：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\probe_pair_overlap.py
口径（⚠️ 只是筛选用的代理指标，不是 Pass 0 的实体表）：
    共同词 = 在 A、B 两篇里都出现 >= min_count 次的实词（去掉停用词与"通用工程词"）。
    真判据是 WeKnora Pass 0 抽出的 slug 是否相同——本脚本只用来避免"配一对根本没交集的语料"。
"""

from __future__ import annotations

import itertools
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.kb.chunker import extract_sections_html  # noqa: E402
from app.kb.ingest_rules import SOURCE_LABELS  # noqa: E402

SRC_DIR = ROOT / "data" / "kb-src" / "articles"

# 源代号 → HTML 相对路径（MANIFEST.md 里逐个核过）
FILES = {
    "A1": "anthropic/01-building-effective-agents.html",
    "A2": "anthropic/02-multi-agent-research-system.html",
    "A3": "anthropic/03-effective-context-engineering.html",
    "A4": "anthropic/04-effective-harnesses-long-running.html",
    "A5": "anthropic/05-how-we-contain-claude.html",
    "A6": "anthropic/06-claude-code-best-practices.html",
    "O2": "openai/02-harness-engineering.html",
    "O3": "openai/03-new-tools-for-building-agents.html",
    "P1": "personal/01-lilian-llm-powered-autonomous-agents.html",
    "P2": "personal/02-lilian-harness-engineering-self-improvement.html",
    "P3": "personal/03-simon-the-year-in-llms-2025.html",
    "P4": "personal/04-chip-agents.html",
    "P5": "personal/05-eugene-llm-patterns.html",
    "P6": "personal/06-hamel-your-ai-product-needs-evals.html",
    "P7": "personal/07-hamel-ai-evals-faq.html",
}

STOP = set(
    """the a an and or of to in for on with that this is are was were be been being it its as at by from
    we our you your they their he she his her not but if then than so such can could may might will would
    have has had do does did more most other others into out up down over under again further once here
    there when where why how all any both each few nor only own same too very just don now
    what which who whom these those i me my myself ourselves themselves also because one two three first
    make makes made like just still often become becomes keep keeps without within about above after
    before during while any some every no not yes""".split()
)

# 通用工程词：两篇都高频，但不代表"共享概念/实体"（否则 agent/code 会把一切配对抬上来）
GENERIC = set(
    """agent agents ai code coding tool tools model models system systems engineer engineering
    build builds building built use uses used using user users run runs work works working
    new way ways thing things example examples output inputs input file files test tests
    rather much many part parts able need needs good great better best large small
    through well may however therefore thus also only even more less""".split()
)

MIN_COUNT = 3


def to_text(rel: str) -> str:
    sections = extract_sections_html((SRC_DIR / rel).read_text(encoding="utf-8"))
    return "\n".join(
        para.strip() for sec in sections for para in sec.paragraphs
    )


def terms(text: str) -> Counter:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-\.]{2,}", text.lower())
    return Counter(
        w.strip(".-")
        for w in words
        if len(w) > 3 and w not in STOP and w not in GENERIC
    )


def main() -> None:
    counts: dict[str, Counter] = {}
    sizes: dict[str, int] = {}
    for code, rel in FILES.items():
        text = to_text(rel)
        counts[code] = terms(text)
        sizes[code] = len(text)

    pairs = []
    for a, b in itertools.combinations(sorted(FILES), 2):
        shared = [
            w for w in set(counts[a]) & set(counts[b])
            if counts[a][w] >= MIN_COUNT and counts[b][w] >= MIN_COUNT
        ]
        score = len(shared)
        shared.sort(key=lambda w: -(counts[a][w] + counts[b][w]))
        pairs.append((score, a, b, shared))
    pairs.sort(reverse=True)

    print(f"sources: {len(FILES)}  (raw text chars)")
    print("  " + ", ".join(f"{c}={sizes[c]}" for c in sorted(sizes)))
    print()
    print(f"top pairs by shared terms (>= {MIN_COUNT} in both docs):")
    for score, a, b, shared in pairs[:8]:
        top = ", ".join(f"{w}({counts[a][w]}/{counts[b][w]})" for w in shared[:12])
        print(f"  {score:>4}  {a}+{b}  {top}")
    print()
    print("指定配对（含 A5+O2 这个上一轮常用配对）")
    watch = [
        ("A5", "O2"),  # 上一轮 GraphRAG 常用配对
        ("A4", "O2"),  # 两篇都叫 harness
        ("A4", "A3"),
        ("A2", "A3"),
        ("A1", "A2"),
        ("A5", "A4"),
        ("O2", "O3"),
        ("P6", "P7"),
        ("P2", "O2"),
    ]
    for score, a, b, shared in pairs:
        if (a, b) in watch or (b, a) in watch:
            print(
                f"  {score:>4}  {a}+{b}  chars={sizes[a] + sizes[b]}  "
                + ", ".join(shared[:10])
            )

    print()
    print("短名单：共享词 >= 40 且两篇合计 <= 60k 字符（成本可控的候选配对）")
    for score, a, b, shared in pairs:
        if score < 40:
            continue
        if sizes[a] + sizes[b] > 60_000:
            continue
        top = ", ".join(f"{w}({counts[a][w]}/{counts[b][w]})" for w in shared[:10])
        print(f"  {score:>4}  {a}+{b}  chars={sizes[a] + sizes[b]}  {top}")


if __name__ == "__main__":
    main()
