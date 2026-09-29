"""把三次采样实验里"该记录的关键数据"导出成 markdown 表格，供对外稿直接粘贴。

口径（每个数字的来源）：
  batch / llm_calls / prompt_tokens / output_tokens / elapsed_s / answer_chars
      ← probe-out/vs-*.json 的对应字段（llm_calls 与 prompt_tokens 都是 result.xxx_categories，分打分/map/reduce 三段）
  rated / selected / rating_hist / selected_by_level
      ← 同文件 selection 段（由 _probe_vs.py 在 DynamicCommunitySelection.select 外层记录）
  selected_by_doc
      ← 选中社区按"主导文档"归属（社区 text_unit 最多的那篇；_probe_vs.load_doc_map 的口径），
        是近似口径：社区可以跨文档。
  默认臂没有选择环节，141 份全给。
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe-out"

QUERIES = {
    "1": "同域·密集",
    "2": "同域·稀疏",
    "3": "跨域",
}
Q_TEXT = {
    "1": "这批资料里，做 LLM 判官评测时踩过哪些坑？怎么排查？",
    "2": "单 Agent 和 多 Agent 架构各自解决什么问题？有什么代价？",
    "3": "把评测里“探针设计”的思路套到 Agent 架构选型上，该怎么设判据？",
}
Q_DOCS = {"1": "01 02 03", "2": "04 05", "3": "01 02 03 04 05"}


def fmt_stage(d: dict, keys: tuple[str, ...]) -> str:
    return "＋".join(f"{int(d.get(k) or 0):,}" for k in keys)


def main() -> int:
    comms = pd.read_parquet(HERE / "output" / "communities.parquet")
    level_of = {str(int(r.human_readable_id)): int(r.level) for r in comms.itertuples()}
    runs: dict[str, list[dict]] = {}
    for f in sorted(glob.glob(str(PROBE / "vs-*-0928-152205.json"))):
        q = Path(f).name.split("-q")[1][0]
        rep = Path(f).name.split("-r")[1][0]
        for r in json.load(open(f, encoding="utf-8")):
            r["_rep"] = rep
            runs.setdefault(q, []).append(r)

    for q in ("1", "2", "3"):
        rows = sorted(runs[q], key=lambda r: (r["_rep"], r["path"] != "default"))
        print(f"\n### q{q} · {QUERIES[q]}")
        print(f"问题：{Q_TEXT[q]}　｜　需要：{Q_DOCS[q]}\n")
        print("| 次 | 臂 | map 批数 | LLM 调用（打分＋map＋reduce） | prompt token（打分＋map＋reduce） | output token（打分＋map＋reduce） | 耗时(s) | 答案字符 |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for r in rows:
            c, t, o = r["llm_calls"], r["prompt_tokens"], r["output_tokens"]
            arm = "默认" if r["path"] == "default" else "Dynamic"
            print(f"| {r['_rep']} | {arm} | {r['batches']} | {fmt_stage(c, ('build_context', 'map', 'reduce'))} "
                  f"| {fmt_stage(t, ('build_context', 'map', 'reduce'))} "
                  f"| {fmt_stage(o, ('build_context', 'map', 'reduce'))} | {r['elapsed_s']:.0f} | {r['answer_chars']:,} |")

        dyn = [r for r in rows if r["path"] == "dynamic"]
        print("\n| Dynamic 次 | 被评社区（L0/L1/L2） | 入选社区（L0/L1/L2） | 评分分布 0–5 | 入选社区按主导文档 |")
        print("| --- | --- | --- | --- | --- |")
        for r in dyn:
            sel = r["selection"]
            ratings = sel.get("ratings") or {}
            rated_lv: dict[int, int] = {}
            for cid in ratings:
                lv = level_of[cid]
                rated_lv[lv] = rated_lv.get(lv, 0) + 1
            rated_s = "/".join(str(rated_lv.get(i, 0)) for i in range(max(rated_lv) + 1))
            hist = sel.get("rating_hist") or {}
            hist_s = " / ".join(str(hist.get(str(i), 0)) for i in range(6))
            by_doc = " ".join(f"{k}:{v}" for k, v in (sel.get("selected_by_doc") or {}).items())
            print(f"| {r['_rep']} | {sel['rated']}（{rated_s}） | {sel['selected_count']} "
                  f"（{sel['selected_by_level']}） | {hist_s} | {by_doc} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
