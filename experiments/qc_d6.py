"""诊断 D6 HTML 结构：为何抽取器返回 0 sections。"""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    t = open(
        "data/kb-src/domestic/04-aliyun-agent-infra.html",
        encoding="utf-8",
        errors="replace",
    ).read()
    for tag in ["<article", "<main", "<h1", "<h2", "<h3", 'class="content', 'id="content', "article-content", "<p", "<section"]:
        print(tag, t.lower().count(tag))
    print("head:", t[:300].replace("\n", " "))
    idx = t.lower().find("article")
    if idx >= 0:
        print("first 'article' context:", t[max(0, idx - 200): idx + 300].replace("\n", " ")[:500])
    # 找正文容器候选：常见 class/id
    for pat in ["content", "markdown", "editor", "doc", "article-body", "detail"]:
        ms = re.findall(rf'class="[^"]*{pat}[^"]*"', t[:200000], re.I)[:5]
        if ms:
            print(pat, "->", ms)


if __name__ == "__main__":
    main()
