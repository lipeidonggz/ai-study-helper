"""入库质量体检：全源自动信号扫描（27 源 3333 块）。"""

import json
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8000"
OUT = Path("data/tmp/kb-health.txt")

NOISE = ["关注", "订阅", "版权声明", "cookie", "privacy", "热门", "联系我们", "©"]


def get(path: str):
    return json.loads(
        urllib.request.urlopen(BASE + path, timeout=120).read().decode("utf-8")
    )


def main() -> None:
    docs = get("/api/kb/documents")
    sources = [x for x in docs if x["status"] == "ready"]
    lines = []
    print(f"{'src':6s} {'chunks':>6s} {'tok_max':>7s} {'tok_mean':>8s} {'sections':>8s} "
          f"{'bad_ch':>6s} {'short':>5s} {'src_line_ok':>10s} {'noise_hd':>8s}")
    for s in sorted(sources, key=lambda x: x["source_id"]):
        chunks = get(f"/api/kb/documents/{s['source_id']}/chunks")
        toks = [c.get("tokens") or 0 for c in chunks]
        secs = Counter(c["section_path"] or "" for c in chunks)
        bad = sum(1 for c in chunks if "\ufffd" in c["text"])
        short = sum(1 for c in chunks if len(c["text"]) < 40)
        src_ok = sum(1 for c in chunks if c["text"].startswith("来源："))
        noise_hd = sum(
            1
            for c in chunks[:3]
            if any(n in c["text"] for n in NOISE)
        )
        print(
            f"{s['source_id']:6s} {len(chunks):6d} {max(toks):7d} "
            f"{sum(toks)/max(1,len(toks)):8.0f} {len(secs):8d} {bad:6d} "
            f"{short:5d} {src_ok:4d}/{len(chunks):<5d} {noise_hd:8d}"
        )
        lines.append(f"\n########## {s['source_id']} chunks={len(chunks)} sections={len(secs)} ##########")
        top_sec = [f"{p} x{n}" for p, n in secs.most_common(6)]
        lines.append("top sections: " + " | ".join(top_sec))
        lines.append("first block head: " + chunks[0]["text"][:130].replace("\n", " "))
        lines.append("mid block head:   " + chunks[len(chunks)//2]["text"][:130].replace("\n", " "))
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("\ndetail written:", OUT)


if __name__ == "__main__":
    main()
