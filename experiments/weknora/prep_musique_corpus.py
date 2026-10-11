"""Turn a MuSiQue slice into (a) a question set and (b) an uploadable corpus.

Design notes
------------
* Source file: `musique_full_v1.0_dev.jsonl` (4,834 rows = 2,417 answerable +
  2,417 unanswerable). We keep the answerable half, in dataset order — that is
  the closest match to the paper's "first N QA examples in the original dataset
  order" that still has gold answers.
* Sampling: `--stratified` takes an equal number of 2/3/4-hop questions so that
  "does the advantage grow with hop depth" is testable; otherwise we take the
  first N in dataset order.
* Corpus: union of the sampled questions' context paragraphs (the paper's
  setting), written one file per paragraph — one knowledge item per paragraph,
  which is how the compile pipeline is meant to be fed.

Usage:
    python experiments/weknora/prep_musique_corpus.py --n 30 --stratified
    python experiments/weknora/prep_musique_corpus.py --n 100 --out data/tmp/musique/slice-100
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import unicodedata

SRC = "data/tmp/musique/musique_full_v1.0_dev.jsonl"


_BAD = re.compile(r"[\\/:*?\"<>|\r\n\t]+")


def slugify(title: str, maxlen: int = 80) -> str:
    name = unicodedata.normalize("NFKC", title).strip()
    name = _BAD.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return name[:maxlen] or "untitled"


def load(path: str):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pick(rows: list[dict], n: int, stratified: bool) -> list[dict]:
    if not stratified:
        return rows[:n]
    per_hop = collections.defaultdict(list)
    for r in rows:
        per_hop[len(r["question_decomposition"])].append(r)
    chosen: list[dict] = []
    hops = sorted(per_hop)
    quota = {h: 0 for h in hops}
    for i in range(n):
        quota[hops[i % len(hops)]] += 1
    for h in hops:
        chosen.extend(per_hop[h][: quota[h]])
    return chosen


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--out", default=None, help="default data/tmp/musique/slice-<n>")
    ap.add_argument("--stratified", action="store_true", help="equal per hop level")
    ap.add_argument("--limit-per-question", type=int, default=None,
                    help="cap context paragraphs kept per question (None = all)")
    args = ap.parse_args(argv)

    out = args.out or f"data/tmp/musique/slice-{args.n}{'-strat' if args.stratified else ''}"
    os.makedirs(os.path.join(out, "corpus"), exist_ok=True)

    rows = [r for r in load(args.src) if r.get("answerable")]
    chosen = pick(rows, args.n, args.stratified)

    # Dedupe by (title, text), NOT by title: MuSiQue reuses one article title for
    # different excerpts (in the first 30 questions alone, 36 titles come with
    # more than one distinct paragraph), and a title-only key would silently
    # drop paragraphs that may be some question's gold evidence.
    para_text: dict[tuple[str, str], str] = {}
    questions = []
    for r in chosen:
        paras = r["paragraphs"]
        if args.limit_per_question:
            keep = [p for p in paras if p.get("is_supporting")]
            rest = [p for p in paras if not p.get("is_supporting")]
            paras = keep + rest[: max(0, args.limit_per_question - len(keep))]
        for p in paras:
            t = p["title"]
            body = p["paragraph_text"]
            if (t, body) not in para_text:
                para_text[(t, body)] = body
        questions.append(
            {
                "id": r["id"],
                "hops": len(r["question_decomposition"]),
                "question": r["question"],
                "answer": r["answer"],
                "answer_aliases": r.get("answer_aliases", []),
                "supporting_titles": [p["title"] for p in paras if p.get("is_supporting")],
                "decomposition": [
                    {"question": d["question"], "answer": d["answer"]}
                    for d in r["question_decomposition"]
                ],
                "n_paragraphs": len(paras),
            }
        )

    written = 0
    used_names: dict[str, int] = {}
    for (title, text) in para_text:
        name = slugify(title)
        if name in used_names:
            used_names[name] += 1
            name = f"{name}__{used_names[name]}"
        else:
            used_names[name] = 0
        with open(os.path.join(out, "corpus", name + ".md"), "w", encoding="utf-8") as fh:
            fh.write(f"# {title}\n\n{text}\n")
        written += 1

    with open(os.path.join(out, "questions.jsonl"), "w", encoding="utf-8") as fh:
        for q in questions:
            fh.write(json.dumps(q, ensure_ascii=False) + "\n")

    hop_hist = collections.Counter(q["hops"] for q in questions)
    manifest = {
        "src": args.src,
        "n_questions": len(questions),
        "stratified": args.stratified,
        "limit_per_question": args.limit_per_question,
        "hop_histogram": {str(k): v for k, v in sorted(hop_hist.items())},
        "corpus_docs": written,
        "corpus_chars": sum(len(t) for t in para_text.values()),
        "corpus_docs_dedupe": "title+text",
        "out_dir": out,
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("sample corpus files:", sorted(os.listdir(os.path.join(out, "corpus")))[:5])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
