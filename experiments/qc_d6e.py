"""D6：定位 script 内正文数据结构（键名/转义层级/正文容器）。"""

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
    scripts = [s.get_text() for s in soup.find_all("script")]
    s = scripts[5]
    print("script[5] head 600:", s[:600])
    # 找常见数据键
    for key in ["__INITIAL_STATE__", "window.", "content", "articleId", "lake-"]:
        idx = s.find(key)
        print(f"key '{key}' at", idx)
    idx = s.find("\\u548C Harness")
    print("around body start:", s[max(0, idx - 300): idx + 150][:500])


if __name__ == "__main__":
    main()
