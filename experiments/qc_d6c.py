"""定位 D6 真正正文容器（按文本长度找最大文本块容器）。"""

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
    cands = []
    for el in soup.find_all(["div", "section", "article"]):
        txt = el.get_text(" ", strip=True)
        n_p = len(el.find_all("p"))
        if len(txt) > 500:
            cands.append((len(txt), n_p, el.name, str(el.get("class")), txt[:80]))
    cands.sort(reverse=True)
    for c in cands[:10]:
        print(c[0], "p=", c[1], c[2], c[3], "|", c[4])


if __name__ == "__main__":
    main()
