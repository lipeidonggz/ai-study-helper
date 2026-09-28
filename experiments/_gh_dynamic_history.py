"""追 Dynamic Community Selection 这个功能的历史：PR 说明、发布版本、文档里有没有写。"""

from __future__ import annotations

import json
import re
import urllib.request


def get(url: str):
    req = urllib.request.Request(
        url, headers={"User-Agent": "codex-research", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def main() -> int:
    print("### 1) 相关 PR 的说明")
    for n in (1396, 1450, 1591):
        try:
            p = get(f"https://api.github.com/repos/microsoft/graphrag/pulls/{n}")
            print("=" * 74)
            print(f"PR #{n} · {p['title']} · merged {p.get('merged_at')} · +{p.get('additions')}/-{p.get('deletions')} · files {p.get('changed_files')}")
            body = re.sub(r"<!--.*?-->", "", (p.get("body") or ""), flags=re.DOTALL).strip()
            body = re.sub(r"\n{2,}", "\n", body)
            print(body[:1100] if body else "(无描述)")
        except Exception as e:  # noqa: BLE001
            print("PR", n, "ERR", e)

    print()
    print("### 2) 相关 issue（搜一下有没有专门讨论这个功能的坑）")
    try:
        res = get(
            "https://api.github.com/search/issues?q=repo:microsoft/graphrag+%22dynamic+community+selection%22+in:title&per_page=20"
        )
        print(f"命中 {res.get('total_count')} 条")
        for it in res.get("items", []):
            print(f"  #{it['number']} [{it['state']}] {it['title'][:90]}  ({it['created_at'][:10]})")
    except Exception as e:  # noqa: BLE001
        print("search ERR", e)

    print()
    print("### 3) 文档里有没有写这个功能（查 repo docs 目录树）")
    try:
        tree = get("https://api.github.com/repos/microsoft/graphrag/git/trees/main?recursive=1")
        docs = [x["path"] for x in tree["tree"] if x["path"].startswith("docs/") and x["path"].endswith((".md", ".mdx"))]
        print(f"docs 下 markdown 文件 {len(docs)} 个：")
        for p in docs:
            if any(k in p.lower() for k in ("query", "global", "prompt", "config")):
                print("   ", p)
    except Exception as e:  # noqa: BLE001
        print("tree ERR", e)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
