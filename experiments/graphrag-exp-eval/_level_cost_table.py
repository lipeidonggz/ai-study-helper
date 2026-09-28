"""按层算两个不同的东西（别混为一谈）：
  A. 成本口径：该层报告 token 合计 ÷ 全文 token —— "只用这一层做 global search，上下文有多大"
  B. 压缩口径：该层报告 token 合计 ÷ 该层真正覆盖的源材料 token（text_unit 去重）—— "摘要有没有压"
另外给出该层覆盖了全文的多少（覆盖率），避免把"小覆盖"误读成"高压缩"。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"

SRC = {"小语料": 32_363, "评测语料": 108_786}
DIRS = {
    "小语料": Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-dynamic/output"),
    "评测语料": Path("D:/lida-data/vscode/ai-study-helper/data/tmp/graphrag-exp-eval/output"),
}


def main() -> int:
    from graphrag.tokenizer.get_tokenizer import get_tokenizer

    tk = get_tokenizer()
    for tag, d in DIRS.items():
        if not d.exists():
            continue
        tu = pd.read_parquet(d / "text_units.parquet")
        comms = pd.read_parquet(d / "communities.parquet")
        reps = pd.read_parquet(d / "community_reports.parquet")
        tok_of_tu = {r["id"]: len(tk.encode(str(r["text"]))) for _, r in tu.iterrows()}
        src = SRC[tag]
        print("=" * 92)
        print(f"### {tag}：源文本 {src:,} token")
        print(f"{'层':>4} {'社区数':>6} {'报告 token 合计':>14} {'成本：/源文本':>13} {'覆盖材料 token':>14} {'覆盖率':>8} {'压缩：材料/报告':>15}")
        for lv in sorted(comms["level"].unique()):
            sub = comms[comms["level"] == lv]
            rep_tok = int(sum(len(tk.encode(str(x))) for x in reps[reps["level"] == lv]["full_content"]))
            covered: set[str] = set()
            for _, c in sub.iterrows():
                covered.update([] if c["text_unit_ids"] is None else list(c["text_unit_ids"]))
            mat_tok = sum(tok_of_tu.get(t, 0) for t in covered)
            print(f"{'L' + str(lv):>4} {len(sub):6d} {rep_tok:14,d} {rep_tok / src:12.1%} {mat_tok:14,d} "
                  f"{mat_tok / src:7.1%} {mat_tok / rep_tok if rep_tok else 0:15.2f}")
        all_rep = int(sum(len(tk.encode(str(x))) for x in reps["full_content"]))
        print(f"{'全层':>4} {len(comms):6d} {all_rep:14,d} {all_rep / src:12.1%} {'—':>14} {'—':>8} {'—':>15}")
        print("  说明：压缩口径用'该层 text_unit 去重后的 token'做分母；覆盖率 = 该层材料 / 全文。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
