"""检查 D6 的 article 与 article-content 结构，确认正文容器在哪。"""

import sys

sys.stdout.reconfigure(encoding="utf-8")
from bs4 import BeautifulSoup


def main() -> None:
    t = open(
        "data/kb-src/domestic/04-aliyun-agent-infra.html",
        encoding="utf-8",
        errors="replace",
    ).read()
    soup = BeautifulSoup(t, "html.parser")
    art = soup.find("article")
    if art:
        txt = art.get_text(" ", strip=True)
        print("article class:", art.get("class"))
        print("article text len:", len(txt), "| head:", txt[:120])
    for sel in [".article-content", ".article-detail", ".content-wrapper", ".left-content"]:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            print(f"sel {sel}: len={len(txt)} | head:", txt[:120])


if __name__ == "__main__":
    main()
