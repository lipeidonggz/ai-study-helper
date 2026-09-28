"""拆开"L2 报告总量是原文 10 倍"是怎么来的：
  ① 每层：社区数 vs 被引用的**去重**块数 → 平均每个块被多少个社区引用（块级共享度数）
  ② 单份报告的内部构成：summary / findings 条数 / 引用标记占多少 token
  ③ 抽 2-3 个真实 L2 社区，把"材料原文"和"报告正文"并排看
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def ntok(tk, s) -> int:
    return len(tk.encode(str(s)))


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    tu = pd.read_parquet(OUT / "text_units.parquet")
    comms = pd.read_parquet(OUT / "communities.parquet")
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    tu_tok = {r["id"]: ntok(tk, r["text"]) for _, r in tu.iterrows()}
    rep_by_cid = {int(r["community"]): r for _, r in reps.iterrows()}
    src = sum(tu_tok.values())

    print("① 块级共享：同一片文本块被多少个社区引用（这是『10 倍』的主因）")
    print(f"{'层':>4} {'社区数':>6} {'引用块槽位':>10} {'去重块数':>9} {'块/社区':>8} {'社区/块':>8} {'报告总 token':>13} {'/原文':>7}")
    for lv in sorted(comms["level"].unique()):
        sub = comms[comms["level"] == lv]
        slots = sum(0 if c["text_unit_ids"] is None else len(c["text_unit_ids"]) for _, c in sub.iterrows())
        distinct = set()
        for _, c in sub.iterrows():
            distinct.update([] if c["text_unit_ids"] is None else list(c["text_unit_ids"]))
        rep_tok = sum(ntok(tk, r["full_content"]) for _, r in reps[reps["level"] == lv].iterrows())
        print(f"{'L' + str(lv):>4} {len(sub):6d} {slots:10d} {len(distinct):9d} {slots / len(sub):8.2f} "
              f"{slots / max(len(distinct), 1):8.2f} {rep_tok:13,d} {rep_tok / src:6.1%}")
    print(f"\n   源文本（105 块合计）={src:,} token；每个块平均 {src / len(tu):,.0f} token")

    print("\n② 单份报告的内部构成（按层平均）")
    for lv in sorted(reps["level"].unique()):
        sub = reps[reps["level"] == lv]
        findings = [len(re.findall(r"\n## ", str(x))) for x in sub["full_content"]]
        refs = [len(" ".join(re.findall(r"\[Data: [^\]]+\]", str(x)))) for x in sub["full_content"]]
        body = [ntok(tk, x) for x in sub["full_content"]]
        print(f"   L{lv}: {len(sub):4d} 份 · 平均 {sum(body) / len(sub):6.0f} token/份 · "
              f"findings {sum(findings) / len(sub):4.1f} 条 · 引用标记占 {sum(refs) / max(sum(len(str(x)) for x in sub['full_content']), 1):5.1%} 的字符")

    print("\n③ 三个真实 L2 样例（材料 → 报告）")
    cand = comms[(comms["level"] == 2)].copy()
    cand["n_tu"] = cand["text_unit_ids"].apply(lambda x: 0 if x is None else len(x))
    cand["n_ent"] = cand["entity_ids"].apply(lambda x: 0 if x is None else len(x))
    picks = list(cand.sort_values(["n_tu", "n_ent"]).head(2)["community"]) + \
            list(cand.sort_values(["n_tu"], ascending=False).head(1)["community"])
    for cid in picks:
        cid = int(cid)
        c = comms[comms["community"] == cid].iloc[0]
        r = rep_by_cid.get(cid)
        ids = [] if c["text_unit_ids"] is None else list(c["text_unit_ids"])
        mat_tok = sum(tu_tok.get(t, 0) for t in ids)
        print(f"\n--- 社区 {cid}（L2）: 材料 {len(ids)} 块 / {mat_tok:,} token · 实体 {0 if c['entity_ids'] is None else len(c['entity_ids'])} 个")
        print(f"    报告：{ntok(tk, r['full_content']):,} token（是材料的 {ntok(tk, r['full_content']) / max(mat_tok, 1):.1f} 倍）· 标题：{str(r['title'])[:70]}")
        for t in ids[:1]:
            print(f"    材料原文（前 260 字）：{str(tu[tu.id == t].iloc[0]['text'])[:260]}")
        body = str(r["full_content"])
        print(f"    报告 summary（前 200 字）：{body.split(chr(10) + chr(10), 1)[-1][:200]}")
        titles = re.findall(r"\n## (.+)", body)
        print(f"    报告 findings 共 {len(titles)} 条，标题：")
        for t in titles[:6]:
            print("      ·", t[:90])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
