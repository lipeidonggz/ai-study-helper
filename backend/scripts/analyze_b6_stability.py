"""B6 稳定性分析：对多个 tagged 跑批，跨跑比对 Pass1 / Pass2 各输出项的一致性。

用法：cd backend && python -m scripts.analyze_b6_stability --source A5 --tags r1,r2,r3,r4
（r1/r2 指 b6_A5_r1.json / b6_claims_A5_r1.json；r2 无 tag 版用 --tags 里的 r2 对应 b6_A5.json）
指标：claims 的 exact-key Jaccard / 对象 IoU；entities name IoU；relations (from|predicate|to) Jaccard；
      statements 的 (subject|object) 匹配数 + claim_type / reify_reasons 一致率；chunk 覆盖 IoU。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
OUT = BACKEND / "data" / "tmp"


def _load(tag: str, source: str) -> tuple[int, int]:
    suffix = f"_{tag}" if tag else ""

    def find(base: str) -> Path | None:
        p = OUT / f"{base}{suffix}.json"
        if p.exists():
            return p
        untagged = OUT / f"{base}.json"
        return untagged if untagged.exists() else None

    claims_p = find(f"b6_claims_{source}")
    b_p = find(f"b6_{source}")
    claims = json.loads(claims_p.read_text(encoding="utf-8")) if claims_p else []
    b = json.loads(b_p.read_text(encoding="utf-8")) if b_p else {}
    return claims, b


def _keys(claims) -> set[str]:
    return {f"{c['subject']}|{c['predicate']}|{c['object']}" for c in claims}


def _objs(claims) -> set[str]:
    return {c["object"] for c in claims}


def _names(entities) -> set[str]:
    return {e.get("name") for e in entities if e.get("name")}


def _rels(relations) -> set[str]:
    return {f"{r.get('from')}|{r.get('predicate')}|{r.get('to')}" for r in relations}


def _stmts(b) -> list[dict]:
    return b.get("statements", [])


def _chunks(stmts) -> set[str]:
    s: set[str] = set()
    for st in stmts:
        s.update(st.get("evidence_chunks", []))
    return s


def _jaccard(a: set, b: set) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="A5")
    ap.add_argument("--tags", default="r1,r2,r3,r4")
    args = ap.parse_args()
    tags = [t for t in args.tags.split(",") if t]

    runs: dict[str, tuple[list, dict]] = {}
    for t in tags:
        runs[t] = _load(t, args.source)

    def mean_over_pairs(fn) -> float:
        vals = []
        ts = list(runs.keys())
        for i in range(len(ts)):
            for j in range(i + 1, len(ts)):
                vals.append(fn(runs[ts[i]], runs[ts[j]]))
        return sum(vals) / len(vals) if vals else 0.0

    def size(m):
        return sum(1 for t in tags if m(runs[t]))

    print(f"== B6 稳定性（{args.source}，跑批 {tags}）==")
    print(f"跑批数: {len(tags)}")
    for t in tags:
        c, b = runs[t]
        print(f"  {t}: claims={len(c)}  entity={len(b.get('entities', []))}  关系={len(b.get('relations', []))}"
              f"  term={len(b.get('terms', []))}  dataevidence={len(b.get('data_evidence', []))}"
              f"  statement={len(b.get('statements', []))}")

    print("== 平均（所有跑批对）==")
    print(f"Pass1 claims exact-key Jaccard: {mean_over_pairs(lambda a, b: _jaccard(_keys(a[0]), _keys(b[0]))):.3f}")
    print(f"Pass1 对象集合 IoU: {mean_over_pairs(lambda a, b: _jaccard(_objs(a[0]), _objs(b[0]))):.3f}")
    print(f"Pass2 entity name IoU: {mean_over_pairs(lambda a, b: _jaccard(_names(a[1].get('entities', [])), _names(b[1].get('entities', [])))):.3f}")
    print(f"Pass2 relation (from|pred|to) IoU: {mean_over_pairs(lambda a, b: _jaccard(_rels(a[1].get('relations', [])), _rels(b[1].get('relations', [])))):.3f}")
    print(f"chunk 覆盖 IoU: {mean_over_pairs(lambda a, b: _jaccard(_chunks(_stmts(a[1])), _chunks(_stmts(b[1])))):.3f}")

    # statement 匹配 / 标签一致（把 statement 按 (subject|object) 归并后比较）
    def stmt_map(b):
        m: dict[str, dict] = {}
        for s in _stmts(b):
            m[f"{s.get('subject')}|{s.get('object')}"] = s
        return m

    def match_and_agree(a, b):
        ma, mb = stmt_map(a[1]), stmt_map(b[1])
        common = set(ma) & set(mb)
        n = len(common)
        if n == 0:
            return 0, 0, 0
        ct = sum(1 for k in common if ma[k].get("claim_type") == mb[k].get("claim_type"))
        rr = sum(1 for k in common if sorted(ma[k].get("reify_reasons", [])) == sorted(mb[k].get("reify_reasons", [])))
        return n, ct, rr

    total_n = total_ct = total_rr = 0
    ts = list(runs.keys())
    for i in range(len(ts)):
        for j in range(i + 1, len(ts)):
            n, ct, rr = match_and_agree(runs[ts[i]], runs[ts[j]])
            total_n += n
            total_ct += ct
            total_rr += rr
    print(f"Pass2 statement (subject|object) 匹配(累计): {total_n}/{len(ts) * (len(ts) - 1) // 2} 对")
    print(f"claim_type 一致率(匹配对上): {(total_ct / total_n) if total_n else 0:.3f} ({total_ct}/{total_n})")
    print(f"reify_reasons 一致率(匹配对上): {(total_rr / total_n) if total_n else 0:.3f} ({total_rr}/{total_n})")


if __name__ == "__main__":
    main()
