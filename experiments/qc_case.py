"""C/B 初值量化证伪检查 · 单用例：金标准 + 源内/全局检索 top15。"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post(path: str, body: dict):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    cid = sys.argv[1]
    with open(f"backend/eval/cases/rag/{cid}.json", encoding="utf-8") as f:
        case = json.load(f)
    q = case["input"]["messages"][0]["content"]
    ga = case["annotation"].get("golden_answer", "")
    sr = (case.get("source_ref") or {}).get("manifest_id", "")
    print(f"### {cid}\nQUERY: {q}\n锚定源: {sr}\n\n--- GOLDEN ---\n{ga}")

    hits = post("/api/kb/search", {"query": q, "top_k": 15, "filters": None})
    print("\n--- GLOBAL TOP15 ---")
    for i, h in enumerate(hits, 1):
        print(
            f"{i:2d} score={h['score']:.4f} {h['source_id']} | "
            f"{h['section_path']} | {h['text'][:130]}"
        )

    for tok in [s.strip() for s in sr.replace("＋", "+").replace("+", " ").split()]:
        if not tok:
            continue
        src = tok.split("《")[0].strip()
        hits2 = post(
            "/api/kb/search",
            {"query": q, "top_k": 15, "filters": {"source_id": [src]}},
        )
        print(f"\n--- SOURCE {src} TOP15 ---")
        for i, h in enumerate(hits2, 1):
            print(
                f"{i:2d} score={h['score']:.4f} | {h['section_path']} | "
                f"{h['text'][:130]}"
            )


if __name__ == "__main__":
    main()
