"""Pass 1 召回度量：对每遍 Pass 1 输出，按 A5 召回金标准算 recall。

匹配：把 gold 的 anchor_text 与每条 claim 的 evidence_texts 都定位到 clean_T，区间重叠即命中（不看措辞）。
用法：cd backend && python -m scripts.analyze_pass1_recall --source A5 --tags x2,x3
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from app.kb.manifest import parse_manifest
from scripts.compile_slice_b6 import MANIFEST_PATH, OUT_DIR, _build_clean_t, _file_units, _find_raw


GOLD_PATH = Path(__file__).resolve().parents[1] / "eval" / "compile" / "a5_recall_gold.json"


def _clean_t(source: str) -> str:
    src = next(s for s in parse_manifest(MANIFEST_PATH) if s.source_id == source)
    _ft, sections = _file_units(src)[0]
    return _build_clean_t(sections)


def _ranges_containing(t: str, txts) -> list[tuple[int, int]]:
    out = []
    for x in txts:
        p = _find_raw(x, t)
        if p:
            out.append(p)
    return out


def _overlap(a: tuple[int, int], ranges) -> bool:
    return any(not (b[1] <= a[0] or b[0] >= a[1]) for b in ranges)


def _para_index(t: str, pos: int) -> int:
    """pos 落在第几个段落（clean_T 的 \\n 分隔段）。"""
    cur = 0
    for i, seg in enumerate(t.split("\n")):
        if cur <= pos < cur + len(seg):
            return i
        cur += len(seg) + 1
    return -1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="A5")
    ap.add_argument("--tags", default="x2,x3")
    args = ap.parse_args()
    tags = [t for t in args.tags.split(",") if t]

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    items = gold["items"]
    t = _clean_t(args.source)
    # 预定位 gold 锚句
    gold_span: dict[str, tuple[int, int] | None] = {}
    for it in items:
        gold_span[it["id"]] = _find_raw(it["anchor_text"], t)

    locatable = sum(1 for v in gold_span.values() if v)
    print(f"== {args.source} 召回金标准：{len(items)} 项，可定位 {locatable} 项 ==")
    for it in items:
        if gold_span[it["id"]] is None:
            print(f"  [未定位] {it['id']}  '{it['anchor_text'][:50]}'")

    for tag in tags:
        f = OUT_DIR / f"b6_claims_{args.source}_{tag}.json"
        if not f.exists():
            print(f"({tag}: 缺 {f.name})")
            continue
        claims = json.loads(f.read_text(encoding="utf-8"))
        # 段落级匹配：gold 锚句所在段 == 某 claim 证据所在段 → 命中
        ev_paras = set()
        for c in claims:
            for e in c.get("evidence_texts", []):
                p = _find_raw(e, t)
                if p:
                    ev_paras.add(_para_index(t, p[0]))
        hit, hit_must, miss = 0, 0, []
        must_total = 0
        for it in items:
            sp = gold_span[it["id"]]
            ok = sp is not None and _para_index(t, sp[0]) in ev_paras
            if it.get("must"):
                must_total += 1
                if ok:
                    hit_must += 1
            if ok:
                hit += 1
            else:
                miss.append(it["id"])
        print(f"\n[{tag}] claims={len(claims)}  recall={hit}/{len(items)}={hit/len(items):.2f}"
              f"  must-recall={hit_must}/{must_total}")
        if miss:
            print(f"   漏: {', '.join(miss)}")
        # ③ 证据可锚率
        ev_total = sum(len(c.get("evidence_texts", [])) for c in claims)
        ev_ok = sum(1 for c in claims for e in c.get("evidence_texts", []) if _find_raw(e, t))
        any_ok = sum(1 for c in claims if any(_find_raw(e, t) for e in c.get("evidence_texts", [])))
        all_ok = sum(1 for c in claims if c.get("evidence_texts") and all(_find_raw(e, t) for e in c.get("evidence_texts", [])))
        # ② 结构合规项
        miss_field = sum(1 for c in claims if not (c.get("subject") and c.get("predicate")))  # object 可选
        empty_roles = sum(1 for c in claims if "roles" in c and not c.get("roles"))
        clause_subj = sum(1 for c in claims
                          if re.search(r"^(granting|placing|supervising|limiting|having|doing|moving|building|enforcing|applying)\b", c.get("subject", ""), re.I)
                          or " only when " in c.get("subject", "") or len(c.get("subject", "")) > 45)
        via = sum(1 for c in claims if re.search(r" via | through ", f"{c.get('predicate')} {c.get('object')}"))
        n = len(claims) or 1
        print(f"   证据可锚: 任一 {any_ok}/{n}={any_ok/n:.3f}  全锚 {all_ok}/{n}={all_ok/n:.3f}  (证据条 {ev_ok}/{ev_total})")
        print(f"   结构: 缺必填字段={miss_field}  空roles={empty_roles}  从句/超长主语={clause_subj}  via/through残留={via}")


if __name__ == "__main__":
    main()
