"""Run prompt-tune 5 times with identical args and capture the intermediate results.

Captures, per run:
  - detected language (from prompt text)
  - inferred domain / report role
  - entity type list
  - report rating criterion
  - which INPUT DOCUMENTS the few-shot examples were taken from
    (they are verbatim chunks, so we can map them back to source files)

Secrets: read from backend/data/app.db, injected into the child env only, and the
child log is redacted line by line (same pattern as _run_index.py).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
APP_DB = REPO / "backend" / "data" / "app.db"
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
RUNS = 5
TAG = "auto5"


def read_llm_settings() -> dict[str, str]:
    con = sqlite3.connect(f"file:{APP_DB}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    return json.loads(row[0])


def redact(text: str, secret: str) -> str:
    return SECRET_RE.sub("<redacted-key>", text.replace(secret, "<redacted-key>"))


def _long_paragraphs(text: str, min_len: int = 350) -> list[str]:
    return [p.strip() for p in text.split("\n\n") if len(p.strip()) >= min_len]


def extract(out_dir: Path, docs: dict[str, str]) -> dict:
    ex = (out_dir / "extract_graph.txt").read_text(encoding="utf-8", errors="ignore")
    rep = (out_dir / "community_report_graph.txt").read_text(encoding="utf-8", errors="ignore")
    summ = (out_dir / "summarize_descriptions.txt").read_text(encoding="utf-8", errors="ignore")

    m = re.search(r"^entity_types:\s*\[(.*?)\]", ex, flags=re.M)
    types = [t.strip() for t in m.group(1).split(",")] if m else []

    m_role = re.search(r"^You are (.*)$", rep, flags=re.M)
    role = m_role.group(1).strip() if m_role else ""

    m_rate = re.search(r"REPORT RATING:(.*)", rep)
    rating = m_rate.group(1).strip()[:220] if m_rate else ""

    # which source documents do the few-shot examples come from
    sources: list[str] = []
    for p in _long_paragraphs(ex):
        probe = p[:80].replace("\n", " ")
        hit = next((name for name, body in docs.items() if probe in body), None)
        if hit:
            sources.append(hit)

    return {
        "types": types,
        "n_types": len(types),
        "role": role[:180],
        "rating": rating,
        "example_sources": sources,
        "summ_role_same_as_report": role[:60] in summ,
        "sizes": {
            "extract": len(ex),
            "report": len(rep),
            "summarize": len(summ),
        },
    }


def main() -> int:
    llm = read_llm_settings()
    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = llm["api_key"]
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["GRAPHRAG_SHIM_VERBOSE"] = "1"

    docs = {
        f.name: f.read_text(encoding="utf-8", errors="ignore")
        for f in sorted((ROOT / "input").glob("*.txt"))
    }
    print(f"loaded {len(docs)} input docs for example-source matching")

    records = []
    for i in range(1, RUNS + 1):
        out_dir = ROOT / f"prompts-{TAG}-{i}"
        args = ["--selection-method", "auto", "--n-subset-max", "32", "--k", "4",
                "--output", f"prompts-{TAG}-{i}"]
        cmd = [sys.executable, "-m", "graphrag", "prompt-tune", "--root", str(ROOT), *args]
        print(f"\n=== run {i}/{RUNS} -> {out_dir.name} ===", flush=True)
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert proc.stdout is not None
        with (ROOT / f"prompt-tune-{TAG}-{i}.out.log").open("w", encoding="utf-8") as fh:
            for line in proc.stdout:
                fh.write(redact(line.rstrip("\n"), llm["api_key"]) + "\n")
        proc.wait()
        if proc.returncode != 0:
            print(f"  run {i} FAILED exit={proc.returncode}")
            continue
        rec = {"run": i, **extract(out_dir, docs)}
        records.append(rec)
        print(f"  n_types={rec['n_types']}  examples from={rec['example_sources']}")
        print(f"  role: {rec['role'][:110]}")

    (ROOT / f"_runs_{TAG}.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nwritten: _runs_{TAG}.json  ({len(records)} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
