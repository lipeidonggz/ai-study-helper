"""离线跑 Pass 2-② 第一刀（形合并）：对已有 ① 产物做归一，打印合并效果。

用法（不联网、不花 token）：
  cd backend && .venv\\Scripts\\python.exe -m scripts.merge_entities --in data/tmp/b6_A5_step1m.json
  （可多个 --in；加 --out 目录则把合并后的 bundle 落盘）

输出四块：
  1. 规模：实体 / 边 合并前后，自环丢弃数，悬挂端点
  2. 合并明细：canonical ← 各变体（出现次数）
  3. 剩余候选：合并后仍"疑似同物多名"的（交给第二刀：义合并）
     · D1 去标点后同名（read-only / read only 之外的标点差异）
     · D2 带括号注释（括号可能是必要限定，故第一刀不动）
     · D3 缩写 vs 全称（VM / virtual machine、MCP / model context protocol 这类）
  4. 反例探针：看着像但绝不能合并的对，必须 0 误合
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 允许直接跑脚本时 import app.*

from app.compile.merge import (  # noqa: E402
    alias_collisions,
    form_key,
    merge_entities,
    missed_plural_pairs,
    provenance_for,
)
from app.storage.sqlite.kb_store import KbStore  # noqa: E402

KB_DB = Path(__file__).resolve().parents[1] / "data" / "kb.db"

PROBES = [
    ("Claude Code", "Claude Cowork"),
    ("CLAUDE.md", "Claude"),
    ("egress control", "egress proxy"),
    ("sandbox", "container"),
    ("NIST", "NCSC"),
    ("https", "http"),
    ("model layer", "model"),
    ("VM", "VSock"),
]


def _alnum_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _initials(name: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", name)
    return "".join(w[0] for w in words).lower()


def remaining_candidates(entities: list[dict]) -> dict[str, list[str]]:
    names = [e["name"] for e in entities]

    d1: dict[str, list[str]] = {}
    for n in names:
        d1.setdefault(_alnum_key(n), []).append(n)
    d1 = {k: sorted(set(v)) for k, v in d1.items() if len(set(v)) > 1}

    d2 = sorted(n for n in names if "(" in n or ")" in n)

    d3: list[str] = []
    by_initials: dict[str, list[str]] = {}
    for n in names:
        by_initials.setdefault(_initials(n), []).append(n)
    for n in names:
        acronym = re.sub(r"[^A-Za-z0-9]", "", n)
        if not (2 <= len(acronym) <= 6 and acronym.isupper()):
            continue
        for other in by_initials.get(acronym.lower(), []):
            if other != n and other.upper() != n.upper():
                d3.append(f"{n} ↔ {other}")
    return {"D1_去标点同名": sorted(f"{sorted(v)}" for v in d1.values()), "D2_带括号": d2, "D3_缩写↔全称": sorted(set(d3))}


def run_one(path: Path, out_dir: Path | None) -> dict:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    src_entities = bundle.get("entities", [])
    src_edges = bundle.get("edges", [])

    # L2 出处：从"清洗后原文 + chunk 偏移"确定性推出（名字字面出现于哪些块）
    prov: dict[str, set[str]] = {}
    cu = bundle.get("content_unit") or {}
    clean_t = cu.get("text") or ""
    source_id = bundle.get("source_id") or ""
    if clean_t and source_id and KB_DB.exists():
        chunks = [c for c in KbStore(KB_DB).list_chunk_offsets(source_id) if c.get("file_idx", 0) == 0]
        names = sorted({e["name"].strip() for e in src_entities if e.get("name")})
        prov = provenance_for(names, clean_t, chunks)

    res = merge_entities(src_entities, src_edges, provenance=prov)
    a = res["audit"]
    found = sum(1 for v in prov.values() if v)

    print(f"\n================ {path.name} ================")
    print(
        f"实体 {a['entities_in']} → {a['entities_out']}"
        f"（异形合并 {a['form_merged_names']} 个名字 / {a['form_merged_groups']} 组；同名合并 {a['exact_dup_records']} 条 / {a['exact_dup_groups']} 组）"
        f" | 边 {a['edges_in']} → {a['edges_out']} | 自环丢弃 {a['self_loops_dropped']}"
    )
    if prov:
        print(
            f"L2 出处：{found}/{len(prov)} 个实体名在原文里找到字面位置"
            f" | 低置信合并（已合、待抽检）{a['low_confidence_merges']} 组"
        )
    if a["dangling_endpoints"]:
        print(f"悬挂端点（边引用了不在实体表里的名字）：{a['dangling_endpoints']}")

    print(f"\n-- 异形合并明细（{len(res['merge_log'])} 组）--")
    for m in res["merge_log"]:
        variants = "  ".join(f"{v}×{c}" for v, c in sorted(m["variants"].items(), key=lambda kv: -kv[1]))
        print(f"  {m['canonical']:<34} ← {variants}")
    if res["dup_log"]:
        dups = "  ".join(f"{d['name']}×{d['records']}" for d in sorted(res["dup_log"], key=lambda d: -d["records"])[:12])
        print(f"-- 同名重复（跨批同形，{len(res['dup_log'])} 组；仅列前 12）--\n  {dups}")
    if res["review_log"]:
        print(f"-- 待抽检清单（已合并，但两形出处不共现 → 请人工/第二刀过一眼；{len(res['review_log'])} 组）--")
        for d in res["review_log"]:
            vs = "  ".join(f"{v}×{c}{d['chunks'].get(v, [])}" for v, c in sorted(d["variants"].items(), key=lambda kv: -kv[1]))
            print(f"  [{d['key']}]  {vs}")

    cand = remaining_candidates(res["entities"])
    print("\n-- 剩余候选（交给第二刀：义合并）--")
    missed = missed_plural_pairs(res["entities"])
    print(f"  D0 疑似单复数漏合（{len(missed)}）：" + ("无" if not missed else ""))
    for x, y in missed:
        print(f"    {x}  ↔  {y}")
    coll = alias_collisions(res["entities"])
    print(f"  D0b 别名冲突（同一别名挂在多个实体上，{len(coll)}）：" + ("无" if not coll else ""))
    for n, ids in coll:
        print(f"    {n}  → {ids}")
    print(f"  D1 去标点同名（{len(cand['D1_去标点同名'])}）：")
    for c in cand["D1_去标点同名"]:
        print(f"    {c}")
    print(f"  D2 带括号注释（{len(cand['D2_带括号'])}）：" + (", ".join(cand["D2_带括号"]) if cand["D2_带括号"] else " 无"))
    print(f"  D3 缩写↔全称（{len(cand['D3_缩写↔全称'])}）：")
    for c in cand["D3_缩写↔全称"]:
        print(f"    {c}")

    def resolve(nm: str) -> str | None:
        low = nm.lower()
        for e in res["entities"]:  # 先按规范名精确命中
            if e["name"].lower() == low:
                return e["id"]
        for e in res["entities"]:  # 再按别名
            if low in {a.lower() for a in e.get("aliases", [])}:
                return e["id"]
        return None

    bad = [f"{x} / {y}" for x, y in PROBES if resolve(x) is not None and resolve(x) == resolve(y)]
    print(f"\n-- 反例探针：{'全部未合并 ✓' if not bad else '❌ 误合 ' + ', '.join(bad)}")

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{path.stem}_merge1.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ 落盘 {out}")
    return res


def main() -> None:
    parser = argparse.ArgumentParser(description="Pass 2-② 第一刀（形合并）离线跑批")
    parser.add_argument("--in", dest="inputs", action="append", required=True, help="① 产物 bundle JSON（可多次）")
    parser.add_argument("--out", default="", help="合并后 bundle 的落盘目录（可选）")
    parser.add_argument("--union", action="store_true", help="把所有输入的实体/边并成一份再合并（看多遍聚合后的归一效果）")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else None
    paths = []
    for p in args.inputs:
        path = Path(p)
        if not path.is_absolute():
            path = Path.cwd() / path
        paths.append(path)

    if args.union:
        entities: list[dict] = []
        edges: list[dict] = []
        content_unit: dict = {}
        source_id = ""
        for path in paths:
            b = json.loads(path.read_text(encoding="utf-8"))
            entities.extend(b.get("entities", []))
            edges.extend(b.get("edges", []))
            content_unit = content_unit or (b.get("content_unit") or {})
            source_id = source_id or (b.get("source_id") or "")
        union_path = paths[0].parent / f"UNION_{len(paths)}runs.json"
        union_path.write_text(
            json.dumps(
                {"entities": entities, "edges": edges, "content_unit": content_unit, "source_id": source_id},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        run_one(union_path, out_dir)
        union_path.unlink()
        return

    for path in paths:
        run_one(path, out_dir)


if __name__ == "__main__":
    main()
