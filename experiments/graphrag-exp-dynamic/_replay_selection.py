"""离线重放 Dynamic Community Selection 的选择过程（用采样实验里记下的 ratings）。

目的：确认三组问题的成本差到底来自哪里——
  ① 兜底（某轮队列空且至今没有任何相关社区 ⇒ 整层全评）到底触发了几次？
  ② 每一层被评了多少社区、选中了多少？
  ③ 打分成本 vs map 成本在总 prompt token 里各占多少（用实验 JSON 的原始拆分）。

只读 probe-out/*.json + output/*.parquet，不改任何数据。
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
PROBE = HERE / "probe-out"

THRESHOLD = 1
MAX_LEVEL = 2


def load_graph() -> tuple[dict[str, int], dict[str, list[str]], dict[str, str], dict[str, list[str]], set[str]]:
    comms = pd.read_parquet(OUT / "communities.parquet")
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    level = {str(int(r.human_readable_id)): int(r.level) for r in comms.itertuples()}
    # 运行时的候选池 = 该次查询的 community_level 过滤之后的报告集（本次 = level<=2 的 141 份）
    have_report = {str(int(x)) for x in reps["community"] if level[str(int(x))] <= MAX_LEVEL}
    children = {
        str(int(r.human_readable_id)): [str(int(c)) for c in (r.children if r.children is not None else [])]
        for r in comms.itertuples()
    }
    parent = {str(int(r.human_readable_id)): str(int(r.parent)) for r in comms.itertuples()}
    by_level: dict[str, list[str]] = {}
    for cid, lv in level.items():
        if cid in have_report:
            by_level.setdefault(str(lv), []).append(cid)
    return level, children, parent, by_level, have_report


def replay(ratings: dict[str, int], root: list[str], children: dict[str, list[str]],
           parent: dict[str, str], by_level: dict[str, list[str]], have_report: set[str]) -> dict:
    queue = list(root)
    lv = 0
    relevant: set[str] = set()
    rated_by_level: dict[int, int] = {}
    fallbacks: list[int] = []
    missing: list[str] = []
    while queue:
        nxt: list[str] = []
        for com in queue:
            rated_by_level[lv] = rated_by_level.get(lv, 0) + 1
            rating = ratings.get(com)
            if rating is None:
                missing.append(com)
                continue
            if rating >= THRESHOLD:
                relevant.add(com)
                for child in children.get(com, []):
                    if child in have_report:
                        nxt.append(child)
                relevant.discard(parent.get(com, ""))
        queue = nxt
        lv += 1
        if not queue and not relevant and str(lv) in by_level and lv <= MAX_LEVEL:
            fallbacks.append(lv)
            queue = list(by_level[str(lv)])
    return {
        "rated": sum(rated_by_level.values()),
        "rated_by_level": rated_by_level,
        "selected": len(relevant),
        "fallbacks": fallbacks,
        "missing": missing,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--digest", default="", help="把逐次明细摘要写到这个路径（供入库）")
    args = ap.parse_args()

    _level, children, parent, by_level, have_report = load_graph()
    digest: list[dict] = []
    root = by_level["0"]
    print(f"pool(level<=?)=141 roots={len(root)} by_level={ {k: len(v) for k, v in sorted(by_level.items())} }")

    defaults = []
    print(f"\n{'file':<40}{'q':>3} {'rated':>6}{'sel':>5} {'fb':>8}  {'rating_tok':>10}{'map_tok':>9}{'red_tok':>8}{'total':>9}")
    for f in sorted(glob.glob(str(PROBE / "vs-*-0928-152205.json"))):
        tag = Path(f).name
        q = tag.split("-q")[1][0]
        runs = json.load(open(f, encoding="utf-8"))
        for r in runs:
            t = r["prompt_tokens"]
            if r["path"] == "default":
                defaults.append((q, t))
                continue
            sel = r.get("selection") or {}
            ratings = {str(k): int(v) for k, v in (sel.get("ratings") or {}).items()}
            info = replay(ratings, root, children, parent, by_level, have_report)
            ok = "OK" if info["rated"] == sel.get("rated") and info["selected"] == sel.get("selected_count") else "MISMATCH"
            digest.append({
                "query": q,
                "run": int(tag.split("-r")[1][0]),
                "arm": "dynamic",
                "rated": info["rated"],
                "selected": info["selected"],
                "rated_by_level": {str(k): v for k, v in sorted(info["rated_by_level"].items())},
                "fallback_fired": info["fallbacks"],
                "rating_histogram": sel.get("rating_hist"),
                "prompt_tokens_by_stage": t,
                "output_tokens_by_stage": r.get("output_tokens"),
                "prompt_tokens_total": sum(t.values()),
                "replay_matches_library": ok == "OK",
            })
            print(f"{tag[-28:]:<40}{q:>3} {info['rated']:>6}{info['selected']:>5} "
                  f"{str(info['fallbacks']):>8}  {t.get('build_context', 0):>10}{t.get('map', 0):>9}{t.get('reduce', 0):>8}"
                  f"{sum(t.values()):>9}  {ok} rated_by_level={info['rated_by_level']}")

    unit_dflt = [t["map"] for _, t in defaults][0] / 141
    print(f"\n默认路径（无选择环节）：单份报告进 map 的均价 = 365,881 / 141 = {unit_dflt:.0f} prompt token")
    for q, t in defaults:
        pass

    print("\n成本模型：Dynamic ≈ 单份均价 × (被评数 + 入选数) + reduce")
    print(f"{'q':>3}{'次':>3}{'被评':>6}{'入选':>6}{'和':>6}{'打分单价':>10}{'map单价':>9}"
          f"{'预测':>9}{'实测':>9}{'误差':>8}")
    for f in sorted(glob.glob(str(PROBE / "vs-*-0928-152205.json"))):
        tag = Path(f).name
        q = tag.split("-q")[1][0]
        rep = tag.split("-r")[1][0]
        for r in json.load(open(f, encoding="utf-8")):
            if r["path"] != "dynamic":
                continue
            t = r["prompt_tokens"]
            sel = r.get("selection") or {}
            rated, kept = int(sel["rated"]), int(sel["selected_count"])
            pred = unit_dflt * (rated + kept) + t.get("reduce", 0)
            actual = sum(t.values())
            if digest:
                digest[-1].update({
                    "unit_rating_tokens": round(t["build_context"] / rated),
                    "unit_map_tokens": round(t["map"] / kept),
                    "model_predicted_tokens": round(pred),
                    "model_error_pct": round((pred / actual - 1) * 100, 1),
                })
            print(f"{q:>3}{rep:>3}{rated:>6}{kept:>6}{rated + kept:>6}"
                  f"{t['build_context'] / rated:>10.0f}{t['map'] / kept:>9.0f}"
                  f"{pred:>9.0f}{actual:>9.0f}{(pred / actual - 1) * 100:>7.1f}%")

    print("\n各层通过率（评分 >= 1 的社区数 / 被评社区数），以及根层通过数：")
    for f in sorted(glob.glob(str(PROBE / "vs-*-0928-152205.json"))):
        for r in json.load(open(f, encoding="utf-8")):
            if r["path"] != "dynamic":
                continue
            sel = r.get("selection") or {}
            ratings = {str(k): int(v) for k, v in (sel.get("ratings") or {}).items()}
            rated: dict[int, int] = {}
            passed: dict[int, int] = {}
            for cid, val in ratings.items():
                lv = _level[cid]
                rated[lv] = rated.get(lv, 0) + 1
                if val >= THRESHOLD:
                    passed[lv] = passed.get(lv, 0) + 1
            root_pass = sum(1 for cid, val in ratings.items() if _level[cid] == 0 and val >= THRESHOLD)
            detail = " ".join(f"L{k}:{passed.get(k, 0)}/{rated[k]}" for k in sorted(rated))
            print(f"  q{f.split('-q')[1][0]} r{f.split('-r')[1][0]}  根层通过 {root_pass}/17   {detail}")

    print("\n打分调用的成本构成（每次打分 = 一份报告全文 + 固定开销）：")
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    rep_tok = {str(int(r.community)): len(tk.encode(str(r.full_content))) for r in reps.itertuples()}
    print(f"  报告本体均价 = {sum(rep_tok[k] for k in rep_tok) / len(rep_tok):.0f} token/份"
          f"（最长的 {max(rep_tok.values()):,}，最短的 {min(rep_tok.values()):,}）")
    for f in sorted(glob.glob(str(PROBE / "vs-*-0928-152205.json"))):
        for r in json.load(open(f, encoding="utf-8")):
            if r["path"] != "dynamic":
                continue
            sel = r.get("selection") or {}
            ratings = {str(k): int(v) for k, v in (sel.get("ratings") or {}).items()}
            body = sum(rep_tok.get(cid, 0) for cid in ratings)
            calls = r["prompt_tokens"]["build_context"]
            n = len(ratings)
            print(f"  q{f.split('-q')[1][0]} r{f.split('-r')[1][0]}  被评 {n:3d}  报告本体合计 {body:7,d}"
                  f"  实付 {calls:7,d}  固定开销/次 {(calls - body) / n:6.0f}")

    if args.digest:
        out = Path(args.digest)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "note": (
                "Dynamic Community Selection 三次采样（3 问题 × 3 次）的选择过程重放明细。"
                "replay_matches_library=True 表示按官方代码离线重放与线上逐次一致；"
                "fallback_fired 为空表示那条'整层全评'的兜底从未触发。"
                "unit_* 是 prompt token 单价（打分一份 / 放进 map 一份），"
                "model_predicted_tokens = 2595 × (rated + selected) + reduce。"
            ),
            "pool_size": len(have_report),
            "unit_default_map_tokens": round(unit_dflt),
            "runs": digest,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n明细摘要已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
