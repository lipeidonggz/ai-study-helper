"""证据定位：金标准关键词 → 源内证据块 → 在 query 源内检索 top25 中的实际排名。"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000"

# 每条用例：{case_id: {source_id: [关键词, ...]}}（小写匹配；证据关键词提炼自金标准）
CASE_KW = {
    "rag-a5-001": {"A5": ["external content", "user misuse", "the environment in which the agent runs"]},
    "rag-a5-002": {"A5": ["83"]},
    "rag-a5-003": {"A5": ["able to do", "supervise the agent", "containment"]},
    "rag-a5-004": {"A5": ["93", "approval fatigue"]},
    "rag-a5-005": {
        "A5": [
            "supervise the agent’s behavior via a human-in-the-loop",
            "able to do",
        ]
    },
    "rag-cmp-001": {
        "O2": ["humans steer", "agents execute", "agants.md", "environment", "intent", "feedback loop", "blast"],
        "A5": ["supervise", "blast radius", "environment layer", "sandbox", "what it can do", "model"],
    },
    "rag-o2-002": {"O2": ["table of contents", "system of record", "agants.md", "100 lines"]},
    "rag-o2-003": {"O2": ["parse data shapes", "zod", "at the boundary"]},
    "rag-o2-004": {"O2": ["one big agants.md", "big agants.md"]},
    "rag-own-001": {"OW1": ["自审自合入", "人工复核", "基础件"]},
    "rag-own-002": {"OW1": ["temperature=1.0", "boundary-fuzzy-002", "1.0", "2.0"]},
    "rag-own-003": {"OW1": ["bootstrap", "2000", "重采样", "置信区间"]},
}


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
    kw_map = CASE_KW.get(cid, {})
    print(f"### {cid}\nQUERY: {q}\n")

    for src, kws in kw_map.items():
        chunks = get(f"/api/kb/documents/{src}/chunks")
        print(f"===== SOURCE {src} (total {len(chunks)} chunks) =====")
        hits = post(
            "/api/kb/search",
            {"query": q, "top_k": 25, "filters": {"source_id": [src]}},
        )
        rank_by_text = {h["text"]: i + 1 for i, h in enumerate(hits)}
        score_by_text = {h["text"]: h["score"] for h in hits}
        print(f"top25 score range: {hits[-1]['score']:.4f} ~ {hits[0]['score']:.4f}")

        seen_blocks: dict[str, list[str]] = {}
        for kw in kws:
            low = kw.lower()
            matched = [
                c
                for c in chunks
                if low in c["text"].lower()
                or low.replace("agants", "agents") in c["text"].lower()
            ]
            if not matched:
                print(f"KW '{kw}': NO MATCH")
                continue
            for c in matched[:4]:
                seen_blocks.setdefault(c["id"], []).append(kw)

        print("-- evidence block candidates (dedup by chunk id) --")
        total_tok = 0
        for cid2, kws2 in seen_blocks.items():
            c = next(x for x in chunks if x["id"] == cid2)
            rank = rank_by_text.get(c["text"], "-")
            score = score_by_text.get(c["text"])
            sc = f"{score:.4f}" if score is not None else "-"
            total_tok += c["tokens"] or 0
            print(
                f"id={c['id']} rank={rank}(score={sc}) tokens={c['tokens']} "
                f"sec=[{c['section_path']}] kws={kws2}"
            )
        print(f">>> 该源候选证据块合计 tokens ≈ {total_tok}")


if __name__ == "__main__":
    main()
