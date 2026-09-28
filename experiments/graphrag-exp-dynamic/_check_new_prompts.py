"""检查 prompt-tune 新产出的三份提示词：领域框定是否中立、语言要求、实体类型、主题词密度。"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
P = ROOT / "prompts-tuned"
JUDGE = ["judge", "判官", "LLM-as-Judge", "flip-rate", "Krippendorff"]
TOPICS = {
    "判官评测": ["判官", "judge", "评测", "flip-rate"],
    "Agent 架构": ["Agent 架构", "ReAct", "Plan-And-Execute", "多 Agent"],
    "应用方法论": ["Prompt 工程", "Context 工程", "Harness", "Loop 工程"],
    "Agent 安全": ["containment", "sandbox", "prompt injection", "遏制"],
}


def show(name: str) -> None:
    path = P / name
    s = path.read_text(encoding="utf-8")
    print(f"\n{'=' * 78}\n=== {name}（{len(s)} 字符）")
    print("--- 开头 700 字 ---")
    print(s[:700])
    print()
    print("--- 主题词密度 ---")
    low = s.lower()
    for topic, kws in TOPICS.items():
        n = sum(low.count(k.lower()) for k in kws)
        print(f"  {topic:10s} {n:4d}")
    print("--- 语言要求相关行 ---")
    for line in s.splitlines():
        if re.search(r"language|Chinese|中文|English", line):
            print("   ·", line.strip()[:150])


def main() -> int:
    for name in ["extract_graph.txt", "summarize_descriptions.txt", "community_report_graph.txt"]:
        show(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
