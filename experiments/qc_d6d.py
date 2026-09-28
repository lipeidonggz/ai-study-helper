"""D6：p 长度分布 + script 是否含正文数据。"""

import re
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
    ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    lens = sorted((len(x) for x in ps), reverse=True)
    print("p count:", len(ps), "| top lens:", lens[:12])
    for x in ps[:6]:
        print("p:", x[:100])
    scripts = [s.get_text() for s in soup.find_all("script")]
    print("scripts:", len(scripts), "total chars:", sum(len(s) for s in scripts))
    kw = "Agent Infra"
    hits = [i for i, s in enumerate(scripts) if kw in s]
    print("script indices containing keyword:", hits)
    for i in hits[:2]:
        idx = scripts[i].find(kw)
        print(f"script[{i}] around kw:", scripts[i][max(0, idx - 200): idx + 200][:400])


if __name__ == "__main__":
    main()
