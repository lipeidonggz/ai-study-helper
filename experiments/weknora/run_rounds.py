"""给各臂补跑第 2／3 轮（同配置重复跑，用于算多轮均值）。

为什么需要它：现在每个臂只有单轮结果，而 LLM 温度 0.7、每题新会话，
单轮差 0.1 分就可能只是运气。重复跑是唯一能压掉这种抖动的办法。

顺序：从便宜到贵，先拿到低成本臂的多轮数据。
命名：qa-<arm>-<tag>.jsonl 由 weknora_qa_eval.py 的 --tag 生成。
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "experiments" / "weknora" / "weknora_qa_eval.py"
PY = sys.executable

# (arm, tag) —— tag 决定输出文件名 qa-<arm>-<tag>.jsonl
RUNS = [
    ("quicknr", "r2"), ("quicknr", "r3"),
    ("quick", "rerank-r2"), ("quick", "rerank-r3"),
    ("wiki", "v2-r2"), ("wiki", "v2-r3"),
    ("smart", "v3-r2"), ("smart", "v3-r3"),
    ("hybrid", "v2-r2"), ("hybrid", "v2-r3"),
    ("smart4", "r2"), ("smart4", "r3"),
]


def main() -> int:
    t0 = time.time()
    for i, (arm, tag) in enumerate(RUNS, 1):
        print(f"\n===== [{i}/{len(RUNS)}] arm={arm} tag={tag}  "
              f"开始 {time.strftime('%H:%M:%S')}（累计 {(time.time()-t0)/60:.1f} 分钟）", flush=True)
        rc = subprocess.call([PY, str(RUNNER), "ask", "--arm", arm, "--tag", tag, "--retries", "3"])
        print(f"===== [{i}/{len(RUNS)}] arm={arm} tag={tag} 结束 rc={rc}  "
              f"{time.strftime('%H:%M:%S')}", flush=True)
    print(f"\n全部完成，总耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
