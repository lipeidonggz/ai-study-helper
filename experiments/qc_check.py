"""C/B 初值量化证伪检查 · 第一批：语料静态分布 + 用例清单。"""

import collections
import glob
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    docs = get("/api/kb/documents")
    print("=== SOURCES ===")
    for d in docs:
        if d["status"] == "ready":
            print(
                f"{d['source_id']}\t{d['name']}\tchunks={d['chunk_count']}\t"
                f"category={d['category']}\tcarrier={d['carrier']}"
            )

    print("\n=== CHUNK / SECTION STATS ===")
    for d in docs:
        if d["status"] != "ready":
            continue
        sid = d["source_id"]
        chunks = get(f"/api/kb/documents/{sid}/chunks")
        sec_sizes = collections.Counter(c["section_path"] for c in chunks)
        sizes = sorted(sec_sizes.values(), reverse=True)
        toks = sorted((c.get("tokens") or 0) for c in chunks)
        n_sec = len(sec_sizes)
        n_chunk = len(chunks)
        big = sum(1 for s in sizes if s > 4)
        pct = lambda xs, p: xs[min(len(xs) - 1, int(len(xs) * p))]
        print(
            f"{sid}\tchunks={n_chunk}\tsections={n_sec}\t"
            f"max_section_blocks={sizes[0] if sizes else 0}\tp90={pct(sizes, .9) if sizes else 0}\t"
            f"big_sections(>4blocks)={big}\t"
            f"tokens: max={toks[-1] if toks else 0} p90={pct(toks, .9) if toks else 0} "
            f"p50={pct(toks, .5) if toks else 0}"
        )
        if big:
            for sec, n in sec_sizes.most_common(8):
                if n > 4:
                    print(f"    section[{n} blocks]: {sec}")

    print("\n=== ENABLED CASES ===")
    for p in sorted(glob.glob(r"backend/eval/cases/rag/*.json")):
        with open(p, encoding="utf-8") as f:
            case = json.load(f)
        if not case.get("enabled", True):
            continue
        q = case["input"]["messages"][0]["content"]
        sr = (case.get("source_ref") or {}).get("manifest_id", "")
        print(f"{case['id']}\t锚定源={sr}\tquery={q}")


if __name__ == "__main__":
    main()
