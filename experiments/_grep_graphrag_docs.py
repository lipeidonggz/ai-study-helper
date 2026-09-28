"""在官方文档页里搜"动态社区选择 / 相关度打分 / 提示词能否按领域调"的证据。"""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

DOCS = Path(__file__).resolve().parent / "graphrag-docs"
KEYS = ["dynamic", "relevance", "relevant", "rate", "rating", "prompt", "domain", "tune", "prompt-tune"]


def text_of(p: Path) -> str:
    soup = BeautifulSoup(p.read_text(encoding="utf-8", errors="ignore"), "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    body = soup.find("main") or soup.body or soup
    return re.sub(r"\n{2,}", "\n", body.get_text("\n"))


def main() -> int:
    for p in sorted(DOCS.glob("*.html")):
        t = text_of(p)
        print("=" * 78)
        print(f"### {p.stem}（正文 {len(t)} 字符）")
        for k in KEYS:
            n = len(re.findall(k, t, re.IGNORECASE))
            print(f"   {k:12s} {n}")
        print("   --- 含 dynamic 的段落 ---")
        for para in [x.strip() for x in t.split("\n") if x.strip()]:
            if re.search("dynamic", para, re.IGNORECASE):
                print("     •", para[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
