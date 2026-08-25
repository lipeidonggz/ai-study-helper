"""批量生成用例金标准答案要点（golden_answer）初稿。

背景（2026-08-25 讨论）：70 条用例逐条人工写金标准负担重且质量不稳定；
改由 LLM 按"预期行为 + 输入 + 验收维度"批量起草一稿，人工只做抽查微调。
判官会以金标准为参考，因此生成时注入反幻觉/工具事实等判定口径。

用法（backend 目录下，需要真实模型出网）：
  python -m eval.generate_golden            # 只为空金标准的用例生成
  python -m eval.generate_golden --all      # 全量重新生成（覆盖已有）
  python -m eval.generate_golden --limit 5  # 只处理前 5 条
  python -m eval.generate_golden --dry-run  # 只打印将处理哪些，不写文件
"""

import argparse
import asyncio
import json
from datetime import datetime

from app.agent.llm import DeepSeekLLMClient, FakeLLMClient, LLMClient, LLMMessage
from app.di import build_deps
from eval import case_store
from eval.schema import CaseFile

_SYSTEM = (
    "你是评测用例设计专家。为评测用例撰写'金标准答案要点'（golden_answer）："
    "一句话描述满分回答应包含什么。只输出要点本身，不要解释、不要序号、不要引号。"
)

_PROMPT_TMPL = """为下面这条评测用例撰写金标准答案要点。

【用例输入】
{input}

【预期行为】
{behavior}

【验收维度】
{criteria}

【预期工具调用】（观察指标，可选）
{tool_calls}

【用例说明】
{notes}

写作要求：
- 要点要具体到可判定：关键事实、数值、口径、必须出现的表述。
- 涉及"拒绝/澄清/不硬答"的用例：写清楚什么样的处理算合理。
- 涉及数值计算的用例：区分"工具算出的真实结果（有依据）"与"编造"；
  允许给出工具计算的高精度近似值，但必须标注近似，不得把近似值表述为精确值。
- 涉及反幻觉的用例：明确指出"工具报错或没有结果时，不得把未知/错误结果当成事实输出"。
- 一句话写完，可适当用分号列举多个要点。"""


def _build_llm(deps, kind: str) -> LLMClient:
    if kind == "fake":
        return FakeLLMClient()
    settings = deps.settings_store.get_llm_settings()
    if not settings.api_key:
        raise SystemExit("未配置大模型 API Key，无法生成金标准（或使用 --llm fake 干跑）")
    return DeepSeekLLMClient(api_key=settings.api_key, model=settings.model or "deepseek-chat")


def _prompt_for(case: CaseFile) -> str:
    return _PROMPT_TMPL.format(
        input=" | ".join(f"{m.role}: {m.content}" for m in case.input.messages),
        behavior=case.expected.behavior,
        criteria=", ".join(case.expected.criteria),
        tool_calls=(
            json.dumps(
                [t.model_dump() for t in case.expected.tool_calls],
                ensure_ascii=False,
            )
            if case.expected.tool_calls
            else "（无）"
        ),
        notes=case.notes or "（无）",
    )


async def generate_one(case: CaseFile, llm: LLMClient) -> str:
    """为单条用例生成金标准答案要点，返回生成的文本。"""
    resp = await llm.chat(
        [
            LLMMessage(role="system", content=_SYSTEM),
            LLMMessage(role="user", content=_prompt_for(case)),
        ]
    )
    return (resp.content or "").strip()


async def main() -> int:
    parser = argparse.ArgumentParser(description="批量生成用例金标准初稿")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条")
    parser.add_argument("--all", action="store_true", help="全量重新生成（覆盖已有金标准）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将处理哪些，不写文件")
    parser.add_argument("--llm", choices=["real", "fake"], default="real")
    args = parser.parse_args()

    cases = case_store.list_cases()
    if args.limit:
        cases = cases[: args.limit]
    targets = cases if args.all else [c for c in cases if not c.annotation.golden_answer.strip()]
    skipped = len(cases) - len(targets)
    if not targets:
        print(f"没有需要生成金标准的用例（跳过 {skipped} 条已有金标准）")
        return 0
    print(f"待生成 {len(targets)} 条（跳过已有金标准 {skipped} 条）")
    if args.dry_run:
        for c in targets:
            print(f"  {c.id}: {c.title}")
        return 0

    deps = build_deps()
    llm = _build_llm(deps, args.llm)
    ok = 0
    failed: list[str] = []
    for i, case in enumerate(targets, 1):
        try:
            golden = await generate_one(case, llm)
            ann = case.annotation.model_copy(
                update={
                    "golden_answer": golden,
                    "annotated_at": datetime.now().isoformat(timespec="seconds"),
                    "annotated_by": "generator",
                }
            )
            case_store.save_case(case.model_copy(update={"annotation": ann}))
            ok += 1
            print(f"[{i}/{len(targets)}] {case.id} OK {golden[:50]}")
        except Exception as exc:
            failed.append(case.id)
            print(f"[{i}/{len(targets)}] {case.id} FAIL {exc}")
    print(f"完成：成功 {ok}，失败 {len(failed)}" + (f"：{failed}" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
