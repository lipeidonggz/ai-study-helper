"""把 cmp-001 金标准更新为演进感知版（2026-09-04 沛东认可）。"""

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

NEW_GOLDEN = """判 pass：以下三条须同时满足（内容与解释以意思一致为准、表述不限；精确名称/出处须准确）——
1. 内容正确：回答须在"两文处于术语演进窗口期"这一事实上站得住，允许（鼓励但不强制）先厘清概念状态再对比。可确证的对比要点：
   - 两文并非对同一已统一术语的两种观点：A5《How we contain Claude》用 containment（遏制 / 爆炸半径）框架，通篇未以 harness engineering 命名其主张；O2《Harness engineering》以 harness 指工程化脚手架（环境、意图、反馈回路、架构约束）。"harness 同时涵盖两者"属于后人归纳，晚于两文。
   - 共同点（可确证）：都把 Agent 工程的关键放在模型之外的外层系统/环境约束设计，而非依赖模型本身或逐条人工监督——O2：环境/意图/反馈回路、把规则机械编码进仓库；A5：环境层遏制（沙箱/VM/出口边界）、监督"能做什么"优于监督"在做什么"。
   - 差异点（可确证）：O2 偏工程效能与自主（Humans steer, agents execute 式分工、仓库即规则地图）；A5 偏安全与爆炸半径封顶。两者可视为同一实践领域演进谱系上的互补支流。
   - 若回答使用"两者互补 / 处于同一谱系"等归纳性表述，须与原文可确证内容一致，不得编造原文没有的定义或归属。
2. 表达合适：结构化（先厘清概念状态或直接同/异分列均可），两文观点来源分明、不混淆；给出明确对比结论并收口；允许在结论后补充"如需更严谨可对照原文/补录更早语料"，但不得以资料不完整或术语不同替代结论本身。
3. 引用真实（条件触发）：若论断依托检索内容或显式声称来源——来源须真实存在、归属正确、内容与原文吻合（锚定 O2 与 A5，两篇归属不得互换）；凭内化知识作答且未声称来源的不触发本条款。
判 fail：满足以下任一情形即 fail——
1. 内容：①编造统一概念或定义（如声称 A5 以 harness engineering 命名其主张、或两文明文共享同一术语框架）；②张冠李戴（把 O2 观点安到 A5 或反之）；③把"后人归纳的异同"表述为两文明文主张且不加区分；④只覆盖一篇或完全回避可确证对比。
2. 表达：通篇只质疑任务前提 / 声明资料不完整 / 要求用户补料，而未给出任何可确证的结构化对比与结论（逃避作答）。
3. 引用（条件触发）：伪造来源 / 内容与所引原文不符 / 归属错误 / 依托检索作答却未标注来源。"""


def main() -> None:
    path = Path("backend/eval/cases/rag/rag-cmp-001.json")
    case = json.loads(path.read_text(encoding="utf-8"))
    case["annotation"]["golden_answer"] = NEW_GOLDEN
    case["updated_at"] = "2026-09-04T11:30:00"
    case["updated_by"] = "local"  # 沛东人工复核认可（2026-09-04）
    path.write_text(
        json.dumps(case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("updated golden_answer length:", len(NEW_GOLDEN))


if __name__ == "__main__":
    main()
