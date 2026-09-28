"""核 MultiHop-RAG（论文里的 News 语料）的可获取性与规模。"""

from __future__ import annotations

import json
import urllib.request


def get(url: str, raw: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": "codex-research"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return r.read() if raw else json.load(r)


def main() -> int:
    tree = get("https://huggingface.co/api/datasets/yixuantt/MultiHopRAG/tree/main")
    print("=== 文件清单 ===")
    for f in tree:
        size = f.get("size", 0)
        print(f"  {f['path']:22s} {size:>12,} 字节")

    print()
    head = get(
        "https://huggingface.co/datasets/yixuantt/MultiHopRAG/resolve/main/corpus.json",
        raw=True,
    )[:1200]
    print("=== corpus.json 开头 ===")
    print(head.decode("utf-8", errors="ignore")[:900])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
