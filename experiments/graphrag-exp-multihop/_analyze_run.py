"""把一个 probe JSON 里的 dynamic 全过程还原出来（逐层评分、剪枝点、父子剔除、按文档分布）。

用法：python _analyze_run.py probe-out/vs-xxxx.json [--level 2]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
LAB = {
    "juejin-01-judge-troubleshooting.txt": "01 判官排查",
    "juejin-02-switch-article.txt": "02 换文章",
    "juejin-03-gold-standard.txt": "03 金标准",
    "juejin-04-single-agent.txt": "04 单Agent",
    "juejin-05-multi-agent.txt": "05 多Agent",
    "juejin-06-prompt-to-loop.txt": "06 Prompt→Loop",
    "a5.txt": "A5 安全",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_file")
    ap.add_argument("--level", type=int, default=2)
    args = ap.parse_args()

    rows = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
    dyn = next((r for r in rows if r["path"] == "dynamic"), None)
    if dyn is None:
        print("这个 JSON 里没有 dynamic 结果")
        return 1
    sel = dyn["selection"]
    ratings = {int(k): int(v) for k, v in sel["ratings"].items()}

    out = ROOT / "output"
    communities = pd.read_parquet(out / "communities.parquet")
    text_units = pd.read_parquet(out / "text_units.parquet")
    documents = pd.read_parquet(out / "documents.parquet")
    doc_of_tu = dict(zip(text_units["id"], text_units["document_id"]))
    label_of_doc = {i: LAB.get(t, t[:8]) for i, t in zip(documents["id"], documents["title"])}

    info: dict[int, dict] = {}
    for _, r in communities.iterrows():
        ids = [] if r["text_unit_ids"] is None else list(r["text_unit_ids"])
        cnt = Counter(label_of_doc.get(doc_of_tu.get(t)) for t in ids)
        cnt.pop(None, None)
        parent = r["parent"]
        info[int(r["community"])] = {
            "level": int(r["level"]),
            "parent": int(parent) if str(parent).lstrip("-").isdigit() else str(parent),
            "docs": dict(cnt),
            "dom": cnt.most_common(1)[0][0] if cnt else "?",
        }
    children: dict[int, list[int]] = defaultdict(list)
    for cid, meta in info.items():
        p = meta["parent"]
        if isinstance(p, int) and p >= 0:
            children[p].append(cid)

    cap = args.level
    in_cap = [cid for cid, m in info.items() if m["level"] <= cap]
    sel_ids = {int(x) for x in sel["selected"]}
    relevant = {cid for cid, v in ratings.items() if v >= 1}
    discarded = sorted(relevant - sel_ids)
    zero = [cid for cid, v in ratings.items() if v == 0]

    print(f"== {Path(args.json_file).name} · community_level={cap} ==")
    print(f"level<= {cap} 的社区共 {len(in_cap)} 个；被评分 {len(ratings)} 个；选中 {len(sel_ids)} 个")
    print(f"评分分布（全体）: {dict(sorted(Counter(ratings.values()).items()))}")
    print()
    print("按层：")
    for lv in sorted({m['level'] for m in info.values()}):
        ids = [i for i in ratings if info[i]["level"] == lv]
        if not ids:
            continue
        print(f"  L{lv}: 被评 {len(ids):3d}  评分 {dict(sorted(Counter(ratings[i] for i in ids).items()))}"
              f"  选中 {sum(1 for i in ids if i in sel_ids):3d}")
    print()
    zero_desc = [(i, "L" + str(info[i]["level"]), info[i]["dom"]) for i in zero]
    print(f"评分为 0 的社区（剪枝点）: {zero_desc}")
    for i in zero:
        ch = children.get(i, [])
        print(f"  → c{i} 的子节点 {ch}，其中被评过的: {[x for x in ch if x in ratings]}")
    print()
    print(f"评分>=1 但最终没选中（父节点被 keep_parent=False 剔除）: {len(discarded)} 个")
    print(f"  按层 {dict(sorted(Counter(info[i]['level'] for i in discarded).items()))}")
    print(f"  按文档 {dict(Counter(info[i]['dom'] for i in discarded).most_common())}")
    print()
    print("=== L0 根节点逐个（评分 / 结果 / 主导文档）===")
    for cid in sorted(i for i in info if info[i]["level"] == 0):
        r = ratings.get(cid, "未评")
        state = "选中" if cid in sel_ids else ("剔除(父)" if cid in relevant else ("未选" if cid in ratings else "未评"))
        print(f"  c{cid:<3d} 评 {str(r):>4}  {state:8s} 主导 {info[cid]['dom']:15s} 文档 {info[cid]['docs']}")
    print()
    print("=== 选中集合按文档 × 层 ===")
    by_doc_level: dict[str, Counter] = defaultdict(Counter)
    for cid in sel_ids:
        by_doc_level[info[cid]["dom"]][info[cid]["level"]] += 1
    for d in sorted(by_doc_level):
        print(f"  {d:16s} {dict(sorted(by_doc_level[d].items()))}")
    print()
    print("答案引用按文档:", dyn.get("cited_by_doc"))
    print("选择阶段:", sel["tokens"], " 批数:", dyn["batches"], " 总 prompt token:", sum(dyn["prompt_tokens"].values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
