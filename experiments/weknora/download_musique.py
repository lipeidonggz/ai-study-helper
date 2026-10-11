"""Download MuSiQue (full) splits for the retrieval experiment.

Why a script: the raw data lives on HuggingFace, and this machine reaches it
only through the local proxy (127.0.0.1:7890). Keeping the download auditable
means the retrieval experiment can be rebuilt from scratch.

Usage:
    python experiments/weknora/download_musique.py --out data/tmp/musique
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.request

PROXY = os.environ.get("MUSIQUE_PROXY", "http://127.0.0.1:7890")

BASE = "https://huggingface.co/datasets/voidful/MuSiQue/resolve/main/"

DEFAULT_FILES = [
    "musique_full_v1.0_dev.jsonl",
    "musique_full_v1.0_test.jsonl",
    "musique_ans_v1.0_dev.jsonl",
]


def opener() -> urllib.request.OpenerDirector:
    handlers = []
    if PROXY:
        handlers.append(urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}))
    return urllib.request.build_opener(*handlers)


def download(op, name: str, out_dir: str) -> str:
    url = BASE + name
    dst = os.path.join(out_dir, name)
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        print(f"[skip] {name} already present ({os.path.getsize(dst)/1e6:.1f} MB)")
        return dst
    tmp = dst + ".part"
    print(f"[get ] {name}")
    with op.open(url, timeout=180) as r, open(tmp, "wb") as fh:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r       {got/1e6:6.1f}/{total/1e6:.1f} MB", end="")
    print()
    os.replace(tmp, dst)
    print(f"[done] {name} -> {dst} ({os.path.getsize(dst)/1e6:.1f} MB)")
    return dst


def main(argv: list[str] | None = None) -> int:
    global PROXY
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/tmp/musique")
    ap.add_argument("--proxy", default=PROXY, help="http proxy; pass '' to disable")
    ap.add_argument("files", nargs="*", default=None)
    args = ap.parse_args(argv)

    PROXY = args.proxy

    os.makedirs(args.out, exist_ok=True)
    op = opener()
    files = args.files or DEFAULT_FILES
    for name in files:
        download(op, name, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
