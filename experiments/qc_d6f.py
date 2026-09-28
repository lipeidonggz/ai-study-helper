"""试还原 D6 lake 正文：转义处理 + 结构统计（标题/p 数）。"""

import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
from bs4 import BeautifulSoup


def unescape_lake(s: str) -> str:
    s = s.replace('\\"', '"').replace("\\\\", "\\").replace("\\/", "/")
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def main() -> None:
    t = open(
        "data/kb-src/domestic/04-aliyun-agent-infra.html",
        encoding="utf-8",
        errors="replace",
    ).read()
    start = t.find("GLOBAL_CONFIG.larkContent = '")
    print("start:", start)
    if start < 0:
        return
    seg = t[start + len("GLOBAL_CONFIG.larkContent = '"):]
    # 找最近的 '; 结尾；考虑内部转义 \' 与 \" 的影响
    end = None
    for i, ch in enumerate(seg):
        if ch == "'" and (i == 0 or seg[i - 1] != "\\"):
            if seg[i + 1: i + 2] == ";":
                end = i
                break
    print("end quote at:", end, "| next:", seg[end + 1: end + 20] if end else "")
    if end is None:
        return
    html = unescape_lake(seg[:end])
    print("restored html chars:", len(html))
    soup = BeautifulSoup(html, "html.parser")
    for tag in ["h1", "h2", "h3", "strong", "b", "p", "li"]:
        print(tag, len(soup.find_all(tag)))
    ps = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    print("p lens top:", sorted((len(x) for x in ps), reverse=True)[:8])
    print("first 300:", html[:300].replace("\n", " "))

    sys.path.insert(0, "backend")
    from app.kb.chunker import extract_sections_html

    secs = extract_sections_html(html)
    print("sections via extractor:", len(secs))
    for s in secs[:6]:
        print(" -", s.path[:60], "| paras:", len(s.paragraphs), "|", s.paragraphs[0][:60] if s.paragraphs else "")


if __name__ == "__main__":
    main()
