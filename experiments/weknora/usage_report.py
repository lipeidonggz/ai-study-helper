"""Aggregate WeKnora's `[LLM Usage]` log lines per prompt_prefix.

The retrieval experiment needs per-turn accounting, and the compile story needs
per-document accounting. Both come from the same log lines; the only trap is
that the two log files we follow overlap, so the same call can appear twice.
We dedupe on (timestamp, purpose, tokens).

Usage:
    python experiments/weknora/usage_report.py --glob "data/tmp/weknora-logs/*.log"
    python experiments/weknora/usage_report.py --since 2026-10-09T12:10 --until 2026-10-09T12:30
"""

from __future__ import annotations

import argparse
import collections
import glob
import re
import sys

LINE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?)Z?.*?"
    r"\[LLM Usage\] model=(?P<model>[^,]+), purpose=(?P<purpose>[^,]*), "
    r"prompt_prefix=(?P<prefix>[^,]*), prompt_tokens=(?P<pt>\d+), "
    r"completion_tokens=(?P<ct>\d+), total_tokens=(?P<tt>\d+)"
)


def parse(paths: list[str]):
    seen = set()
    rows = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "LLM Usage" not in line:
                    continue
                m = LINE.search(line)
                if not m:
                    continue
                key = (m["ts"], m["purpose"], m["pt"], m["ct"])
                if key in seen:
                    continue
                seen.add(key)
                rows.append(m.groupdict())
    rows.sort(key=lambda r: r["ts"])
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="data/tmp/weknora-logs/*.log")
    ap.add_argument("--since", default=None, help="ISO prefix, e.g. 2026-10-09T12:10")
    ap.add_argument("--until", default=None)
    ap.add_argument("--by", choices=["prefix", "purpose", "minute"], default="prefix")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(args.glob))
    rows = parse(paths)
    if args.since:
        rows = [r for r in rows if r["ts"] >= args.since]
    if args.until:
        rows = [r for r in rows if r["ts"] <= args.until]

    groups: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if args.by == "prefix":
            key = r["prefix"] or "(none)"
        elif args.by == "purpose":
            key = r["purpose"]
        else:
            key = r["ts"][:16]
        groups[key].append(r)

    print(f"files={len(paths)} usage_rows={len(rows)} groups={len(groups)}")
    print(
        f"{'key':<24}{'calls':>6}{'prompt':>10}{'compl':>9}{'total':>10}"
        f"  {'first':<20}{'last':<20} purposes"
    )
    for key, rs in sorted(groups.items(), key=lambda kv: (kv[1][0]["ts"], kv[0]))[: args.top]:
        pt = sum(int(r["pt"]) for r in rs)
        ct = sum(int(r["ct"]) for r in rs)
        tt = sum(int(r["tt"]) for r in rs)
        purposes = collections.Counter(r["purpose"] for r in rs)
        pp = " ".join(f"{k}:{v}" for k, v in purposes.most_common())
        print(
            f"{key:<24}{len(rs):>6}{pt:>10}{ct:>9}{tt:>10}"
            f"  {rs[0]['ts'][11:19]:<20}{rs[-1]['ts'][11:19]:<20} {pp}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
