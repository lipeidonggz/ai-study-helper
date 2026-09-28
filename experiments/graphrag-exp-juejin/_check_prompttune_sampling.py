"""验证 prompt-tune 的采样边界：
   load_docs_in_chunks() 里 `limit > len(chunks) ⇒ limit = LIMIT(=15)`，
   然后无条件 `chunks_df.sample(n=limit)`。
⇒ 语料块数 < 15 时会怎样？这里用 pandas 复现该行为（不调 LLM）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    n = len(pd.read_parquet(ROOT / "output" / "text_units.parquet"))
    print(f"我们这篇文档的 chunk 数（= prompt-tune 会切出的块数）：{n}")
    print("prompt-tune 默认 limit = 15")

    df = pd.DataFrame({"text": [f"chunk{i}" for i in range(n)]})
    # 复刻源码：limit 越界 → 回落到 LIMIT=15
    limit = 15 if 15 > len(df) else 15
    print(f"\n源码分支：limit(15) > len(chunks)({len(df)}) ⇒ 警告并把 limit 设为 LIMIT=15")
    try:
        df.sample(n=limit)
        print("  sample(n=15) 成功（说明 pandas 允许超采）")
    except Exception as exc:  # noqa: BLE001
        print(f"  sample(n=15) 抛异常 → {type(exc).__name__}: {exc}")
        print("  ⇒ 小语料（<15 块）跑 prompt-tune 会直接崩在这里，且崩在 LLM 调用之前")

    print("\n对照：显式传 --limit 7 时")
    try:
        out = df.sample(n=7)
        print(f"  sample(n=7) 成功，抽到 {len(out)} 块")
    except Exception as exc:  # noqa: BLE001
        print(f"  失败：{exc}")


if __name__ == "__main__":
    main()
