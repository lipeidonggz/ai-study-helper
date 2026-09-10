"""P1 离线跑批：L3 义合并的**候选清单** + 括号规则的确定性应用（不调 LLM）。

用法：
  cd backend && .venv\\Scripts\\python.exe -m scripts.merge_semantic --in data/tmp/b6_A5_step1m.json
  （可多个 --in 任意选；加 --union 则把多个 bundle 的实体/边并起来跑；--out 落盘候选 JSON）

输出四块：
  1. 概览：L1 合并后实体数 → 括号规则应用后实体数；候选对数（按信号数排序）
  2. 括号规则命中的确定性合并
  3. 候选清单（信号 + 双方次数 + 各自出处；多信号命中排在前面）
  4. 反例探针：清单里若出现"绝不能合"的对，直接点名（P2 的 LLM 必须把它们判 different）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compile.merge import merge_entities, provenance_for  # noqa: E402
from app.compile.merge_semantic import collect_candidates, normalize_brackets  # noqa: E402
from app.storage.sqlite.kb_store import KbStore  # noqa: E402

KB_DB = Path(__file__).resolve().parents[1] / "data" / "kb.db"

# 反例探针：L3 必须判 different（出现在候选里不算错——候选本来就只是"值得问一问"）
PROBES = [
    ("AI", "agent identity"),
    ("MCP", "MCP server"),
    ("sandbox", "container"),
    ("egress control", "egress proxy"),
    ("Claude Code", "Claude Cowork"),
]


def load(paths: list[Path], union: bool) -> dict:
    if not union:
        return json.loads(paths[0].read_text(encoding="utf-8"))
    entities, edges, cu, sid = [], [], {}, ""
    for p in paths:
        b = json.loads(p.read_text(encoding="utf-8"))
        entities += b.get("entities", [])
        edges += b.get("edges", [])
        cu = cu or (b.get("content_unit") or {})
        sid = sid or (b.get("source_id") or "")
    return {"entities": entities, "edges": edges, "content_unit": cu, "source_id": sid}


def main() -> None:
    parser = argparse.ArgumentParser(description="L3 义合并 P1：候选清单 + 括号规则")
    parser.add_argument("--in", dest="inputs", action="append", required=True)
    parser.add_argument("--union", action="store_true", help="把多个 bundle 并起来跑")
    parser.add_argument("--out", default="", help="候选 JSON 落盘路径")
    parser.add_argument("--limit", type=int, default=60, help="打印候选上限（多信号优先）")
    args = parser.parse_args()

    paths = [Path(p) if Path(p).is_absolute() else Path.cwd() / p for p in args.inputs]
    bundle = load(paths, args.union)
    label = f"UNION({len(paths)} runs)" if args.union else paths[0].name

    clean_t = (bundle.get("content_unit") or {}).get("text", "")
    source_id = bundle.get("source_id", "")
    prov: dict[str, set[str]] = {}
    if clean_t and source_id and KB_DB.exists():
        chunks = [c for c in KbStore(KB_DB).list_chunk_offsets(source_id) if c.get("file_idx", 0) == 0]
        names = sorted({e["name"].strip() for e in bundle.get("entities", []) if e.get("name")})
        prov = provenance_for(names, clean_t, chunks)

    # L1 → 括号规则 → 候选
    l1 = merge_entities(bundle.get("entities", []), bundle.get("edges", []), provenance=prov)
    renamed, bracket_log = normalize_brackets(l1["entities"])
    l1b = merge_entities(renamed, l1["edges"], provenance=prov)
    buckets = collect_candidates(l1b["entities"], l1b["edges"], prov)
    cands, subsump, assoc = buckets["merge"], buckets["subsumption"], buckets["association"]

    print(f"\n================ {label} ================")
    print(
        f"实体：{len(bundle.get('entities', []))}（① 原始）→ {len(l1['entities'])}（L1 形合并）"
        f" → {len(l1b['entities'])}（+括号规则）"
    )
    print(f"括号规则命中 {len(bracket_log)} 个：")
    for b in bracket_log:
        print(f"  {b['from']}  →  {b['to']}   （括号内“{b['inner']}”是限定语，外层另有实体 {b['with']}）")

    hi = [c for c in cands if len(c["signals"]) >= 2]
    print(f"\n合并候选 {len(cands)} 对（多信号 {len(hi)} 对；按信号数排序，最多打印 {args.limit} 条）：")
    for c in cands[: args.limit]:
        a, b = c["a"], c["b"]
        sig = "+".join(c["signals"])
        ca, cb = c["counts"][a], c["counts"][b]
        extra = ""
        if "cooccur" in c["detail"]:
            extra = f" 共现{c['detail']['cooccur']}"
        if "shared_neighbors" in c["detail"]:
            extra = f" 共享邻居{c['detail']['shared_neighbors']}"
        if "overlap" in c["detail"]:
            extra = f" token重叠{c['detail']['overlap']}"
        print(f"  [{sig:<18}] {a}×{ca}  ↔  {b}×{cb}{extra}")

    print(f"\n另记：上下位/从属对 {len(subsump)} 对（**不进合并候选**，仅留档参考）：")
    for c in subsump[:12]:
        print(f"  {c['a']}  ⊃  {c['b']}")
    if len(subsump) > 12:
        print(f"  …（其余 {len(subsump) - 12} 对省略）")
    print(f"另记：关联对（共享邻居，非同指）{len(assoc)} 对，仅留档。")

    print("\n反例探针（P2 时必须判 different）：")
    low = {c["a"].lower(): c for c in cands}
    for x, y in PROBES:
        hit = next((c for c in cands if {c["a"].lower(), c["b"].lower()} == {x.lower(), y.lower()}), None)
        print(f"  {x} / {y}：{'在候选里 → 必须判 different' if hit else '不在候选里（更好）'}")

    if args.out:
        out = Path(args.out)
        out.write_text(
            json.dumps(
                {
                    "bracket_merges": bracket_log,
                    "candidates": cands,
                    "subsumption": subsump,
                    "association": assoc,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"\n→ 候选落盘 {out}")


if __name__ == "__main__":
    main()
