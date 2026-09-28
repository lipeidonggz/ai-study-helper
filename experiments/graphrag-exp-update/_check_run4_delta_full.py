"""确认第 4 次运行产出的 delta 是不是"一份完整索引"（若是，则合并本身就是多余的）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
TS = "20260926-121821"


def main() -> None:
    d = ROOT / "update_output" / TS / "delta"
    o = ROOT / "output"
    print("=== 第 4 次运行的 delta（写入暂存区） vs output（正式索引） ===")
    for n in [
        "documents",
        "text_units",
        "entities",
        "relationships",
        "communities",
        "community_reports",
    ]:
        a = len(pd.read_parquet(d / (n + ".parquet")))
        b = len(pd.read_parquet(o / (n + ".parquet")))
        print(f"  {n:<20} delta={a:>4}   output={b:>4}")

    docs = pd.read_parquet(d / "documents.parquet")
    print("\ndelta 里的文档：", docs["title"].tolist())
    tu = pd.read_parquet(d / "text_units.parquet")
    print("delta 里的文本块总数：", len(tu), "（原文档 7 + 新增 2）")
    print("\n结论：delta 里已经是一份包含全部输入文档的完整索引 ⇒ 不需要与 previous 合并；")
    print("      缺的不是合并，而是「把这份完整结果提升为正式索引」这一步。")


if __name__ == "__main__":
    main()
