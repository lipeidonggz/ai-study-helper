"""Read-only re-check of the WeKnora graph store, via the Neo4j HTTP endpoint.

Why this exists: the first pass (graph_perfile_probe.py) shelled out to
`docker exec ... cypher-shell`, which needs an escalated shell. The HTTP
endpoint on 127.0.0.1:7474 answers a plain sandboxed process, so the numbers
in memory/0050 section 12 can be re-derived with one command:

    python experiments/weknora/graph_verify_http.py

Writes data/tmp/weknora-graph-perfile/verify.json and prints a summary.
Every statement below is MATCH/RETURN -- nothing is written to the store.
"""
from __future__ import annotations

import base64
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "tmp" / "weknora-graph-perfile" / "verify.json"
URL = "http://127.0.0.1:7474/db/neo4j/tx/commit"
AUTH = "Basic " + base64.b64encode(b"neo4j:password").decode()

QUERIES = {
    "total_nodes": "MATCH (n) RETURN count(n) AS nodes",
    "total_rels": "MATCH ()-[r]->() RETURN count(r) AS rels",
    "cross_file_rels (should be 0)":
        "MATCH (a)-[r]->(b) WHERE a.kg <> b.kg RETURN count(r) AS cross_file_rels",
    "nodes_by_file":
        "MATCH (n) RETURN n.kg AS kg, count(*) AS nodes ORDER BY nodes DESC",
    "rels_by_file":
        "MATCH (a)-[r]->(b) RETURN a.kg AS kg, count(r) AS rels ORDER BY rels DESC",
    "rel_types":
        "MATCH ()-[r]->() RETURN type(r) AS rel_type, count(*) AS c ORDER BY c DESC",
    "same_name_entities_across_files":
        "MATCH (n) WITH n.name AS name, count(DISTINCT n.kg) AS k "
        "WHERE k > 1 RETURN name, k ORDER BY k DESC, name LIMIT 30",
}


def run(statements: list[str]) -> list[dict]:
    body = json.dumps({"statements": [{"statement": q} for q in statements]}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": AUTH,
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("errors"):
        raise SystemExit("Neo4j errors: " + json.dumps(payload["errors"], ensure_ascii=False))
    return payload["results"]


def main() -> None:
    labels = list(QUERIES)
    results = run([QUERIES[k] for k in labels])
    out: dict[str, dict] = {}
    for label, res in zip(labels, results):
        cols = res["columns"]
        rows = [r["row"] for r in res["data"]]
        out[label] = {"columns": cols, "rows": rows}
        print(f"===== {label}")
        print("  " + " | ".join(cols))
        for row in rows[:12]:
            print("  " + " | ".join(str(v) for v in row))
        if len(rows) > 12:
            print(f"  ... ({len(rows)} rows total)")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nwrote:", OUT)


if __name__ == "__main__":
    sys.exit(main())
