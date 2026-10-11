"""Describe the MuSiQue splits before we decide experiment size.

Answers two questions the experiment plan depends on:
  1) what does each split actually carry (answers? hop counts? supporting flags?)
  2) for the paper's protocol ("first 500 examples, corpus = union of their
     context paragraphs"), how big is that corpus?

Usage:
    python experiments/weknora/musique_shape.py --n 500
"""

from __future__ import annotations

import argparse
import collections
import json
import os

DEV_FULL = "data/tmp/musique/musique_full_v1.0_dev.jsonl"
TEST_FULL = "data/tmp/musique/musique_full_v1.0_test.jsonl"
ANS_DEV = "data/tmp/musique/musique_ans_v1.0_dev.jsonl"


def rows(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def hops(rec: dict) -> int | None:
    dec = rec.get("question_decomposition")
    return len(dec) if dec else None


def summarize(path: str, n: int) -> dict:
    total = 0
    hop_hist: collections.Counter = collections.Counter()
    answerable: collections.Counter = collections.Counter()
    para_counts: list[int] = []
    first_n_titles: list[str] = []
    first_n_chars: list[int] = []
    first_n_paras_total = 0
    # (title, text) — see prep_musique_corpus.py: one title can carry several
    # distinct excerpts, so title alone under-counts the corpus.
    first_n_paras_uniq: dict[tuple[str, str], int] = {}
    first_n_hop: collections.Counter = collections.Counter()
    first_n_split_by_hop_titles: dict[int, set] = collections.defaultdict(set)
    first_n_answers_missing = 0

    for rec in rows(path):
        total += 1
        h = hops(rec)
        if h:
            hop_hist[h] += 1
        if "answerable" in rec:
            answerable[bool(rec["answerable"])] += 1
        paras = rec.get("paragraphs") or []
        para_counts.append(len(paras))
        if total <= n:
            if h:
                first_n_hop[h] += 1
            for p in paras:
                first_n_paras_total += 1
                t = p.get("title", "")
                body = p.get("paragraph_text", "")
                if (t, body) not in first_n_paras_uniq:
                    first_n_paras_uniq[(t, body)] = len(body)
                if h:
                    first_n_split_by_hop_titles[h].add(t)
            if "answer" not in rec or not rec.get("answer"):
                first_n_answers_missing += 1
            first_n_chars.append(sum(len(p.get("paragraph_text", "")) for p in paras))
            first_n_titles.extend(p.get("title", "") for p in paras)

    # per-hop unique paragraph accounting over the first n
    hop_unique_chars: dict[int, int] = {}
    for h, titles in first_n_split_by_hop_titles.items():
        hop_unique_chars[h] = sum(first_n_paras_uniq.get(t, 0) for t in titles)

    return {
        "path": path,
        "total_examples": total,
        "hop_histogram": dict(sorted(hop_hist.items())),
        "answerable": {str(k): v for k, v in answerable.items()},
        "paragraphs_per_question": {
            "min": min(para_counts) if para_counts else 0,
            "max": max(para_counts) if para_counts else 0,
            "mean": round(sum(para_counts) / len(para_counts), 2) if para_counts else 0,
        },
        "first_n": n,
        "first_n_hop_histogram": dict(sorted(first_n_hop.items())),
        "first_n_answers_missing": first_n_answers_missing,
        "first_n_paragraph_slots": first_n_paras_total,
        "first_n_unique_paragraphs": len(first_n_paras_uniq),
        "first_n_unique_chars": sum(first_n_paras_uniq.values()),
        "first_n_chars_per_question_mean": round(
            sum(first_n_chars) / len(first_n_chars), 1
        ) if first_n_chars else 0,
        "first_n_unique_paragraphs_by_hop": {
            str(h): len(t) for h, t in sorted(first_n_split_by_hop_titles.items())
        },
        "first_n_unique_chars_by_hop": {str(k): v for k, v in sorted(hop_unique_chars.items())},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--out", default="data/tmp/musique/shape.json")
    args = ap.parse_args(argv)

    out = {}
    for path in (DEV_FULL, TEST_FULL, ANS_DEV):
        if os.path.exists(path):
            out[os.path.basename(path)] = summarize(path, args.n)
        else:
            out[os.path.basename(path)] = {"missing": True}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
