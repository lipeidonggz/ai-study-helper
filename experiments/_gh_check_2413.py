"""核对 #2413 / #2382：dynamic selection 的"根层无社区"报错到底修没修、进没进主线。"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request


def get(url: str):
    req = urllib.request.Request(
        url, headers={"User-Agent": "codex-research", "Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def show(n: int) -> None:
    print("=" * 74)
    try:
        p = get(f"https://api.github.com/repos/microsoft/graphrag/pulls/{n}")
        print(f"#{n} 是 PR：{p['title']}")
        print(f"   state={p['state']} merged={p.get('merged')} merged_at={p.get('merged_at')} closed_at={p.get('closed_at')}")
        body = re.sub(r"<!--.*?-->", "", (p.get("body") or ""), flags=re.DOTALL)
        print(re.sub(r"\n{2,}", "\n", body).strip()[:700])
    except urllib.error.HTTPError:
        it = get(f"https://api.github.com/repos/microsoft/graphrag/issues/{n}")
        print(f"#{n} 是 issue：{it['title']}")
        print(f"   state={it['state']} closed_at={it.get('closed_at')} comments={it['comments']}")
        print(re.sub(r"\n{2,}", "\n", (it.get("body") or "")).strip()[:700])


def main() -> int:
    for n in (2413, 2382):
        show(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
