"""在论文全文里找"实体类型清单"的证据：论文到底有没有给出它用的类型列表。"""

from __future__ import annotations

import io
import re

PATH = "D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag_paper.txt"


def main() -> int:
    s = re.sub(r"\s+", " ", io.open(PATH, encoding="utf-8", errors="ignore").read())

    for kw in ["organization", "named entit", "entity_types", "tailored to the domain",
               "Appendix A", "A.1 Entity Extraction", "types such as"]:
        hits = list(re.finditer(re.escape(kw), s, re.I))
        print(f"=== [{kw}] 命中 {len(hits)} 次")
        for m in hits[:4]:
            a = max(0, m.start() - 230)
            print("   ·", s[a : m.end() + 260])
        print()

    print("=== 搜'类型清单'形状的片段（含 3 个以上逗号分隔的小写词）===")
    for m in re.finditer(r"(?:types?[^.]{0,80}:?\s*\[?)([a-z][a-z _-]{2,}(?:,\s*[a-z][a-z _-]{2,}){2,})", s):
        print("   ·", m.group(0)[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
