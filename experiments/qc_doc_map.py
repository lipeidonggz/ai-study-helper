"""打印 O2/A5 全部 chunk 的 section 地图（含每块开头），用于金标准覆盖度对照。"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")


def get(path: str):
    return json.loads(
        urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=60)
        .read()
        .decode("utf-8")
    )


def main() -> None:
    for src in ("O2", "A5"):
        chunks = get(f"/api/kb/documents/{src}/chunks")
        print(f"\n########## {src} ({len(chunks)} blocks) ##########")
        for c in chunks:
            text = (c["text"] or "").replace("\n", " ⏎ ")
            print(f"[{c['id']}] {c['section_path']} | {text[:130]}")


if __name__ == "__main__":
    main()
