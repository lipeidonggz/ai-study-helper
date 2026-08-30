"""golden set 用例的 Pydantic 校验模型：runner 的输入契约。

设计角度：为什么用 Pydantic 而不是手动解析 JSON？
- 加载即校验：字段类型、枚举值、必填项一次性把关，坏用例直接报错
- 与现有技术栈一致（FastAPI 也用它）
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Category = Literal["tool_call", "boundary", "combined", "multi_turn", "kb_qa"]
Mode = Literal["general", "kb_priority", "tool_enhanced"]
# 验收维度受控词表（v1）：机器可自动判定 / 需人工或 LLM 判定，见 0013 记录
Criterion = Literal[
    "answer_correct",  # 答案正确（人工/LLM 判定）
    "tool_used",  # 调用了预期工具（机器从 trace 判定）
    "tool_not_used",  # 不该用工具时未调用（机器判定）
    "refusal",  # 正确拒答/不硬答（人工判定）
    "stream_complete",  # 流式完整结束（机器判定）
    "latency_budget",  # 满足耗时预算 timeout_sec（机器判定）
    "no_prompt_leak",  # 输出不含系统提示（机器判定：loop 护栏拦截即 fail）
    "citation_correct",  # 引用正确（阶段 2 启用）
    "context_consistent",  # 多轮上下文一致（阶段 3 启用）
]


class InputMessage(BaseModel):
    """用例输入里的一条消息。"""

    role: Literal["user", "assistant", "system"]
    content: str


class CaseInput(BaseModel):
    """用例输入：完整对话（单轮=1 条消息，多轮从第 1 条起）。"""

    messages: list[InputMessage] = Field(min_length=1)


class ExpectedToolCall(BaseModel):
    """预期工具调用（作为观察指标，不硬性判定）。"""

    name: str
    arguments: dict = Field(default_factory=dict)


class Expected(BaseModel):
    """预期结果：行为要点 + 验收维度 + 可选精确预期。"""

    behavior: str  # 预期行为要点（人工标注依据）
    criteria: list[Criterion] = Field(min_length=1)  # 验收维度（至少一个）
    tool_calls: list[ExpectedToolCall] = Field(default_factory=list)  # 观察指标
    answer_contains: list[str] = Field(default_factory=list)  # 答案应包含关键词（模糊匹配）
    max_rounds: int = Field(default=4, ge=1, le=10)  # 预期最大工具轮数（对比实际值）

    @model_validator(mode="after")
    def _check_criteria_conflicts(self) -> "Expected":
        """互斥维度校验：tool_used 与 tool_not_used 语义相反，不能同时选。"""
        criteria = set(self.criteria)
        if "tool_used" in criteria and "tool_not_used" in criteria:
            raise ValueError("验收维度互斥：tool_used 与 tool_not_used 不能同时选择")
        return self


class CaseAnnotation(BaseModel):
    """用例级金标准：描述"好的回答应该长什么样"，不评判任何一次具体输出。

    设计角度：用例本身没有系统响应，"答案正确/拒答合理"是对某次跑批输出的
    执行评价（存于 eval_run_cases），不属于用例属性。用例级只保留期望性内容，
    供后续跑批自动判定（LLM-as-judge）与人工抽检对照。
    """

    golden_answer: str = ""  # 金标准答案要点：满分回答应包含的关键事实/口径/要点
    reference_answer: str = ""  # 完整参考答案（可选）：比要点更精确，供自动判定对照
    note: str = ""  # 金标准备注：为什么这样定、判定依据
    annotated_at: str = ""  # 最近更新时间
    annotated_by: str = ""  # 最近维护人


class CaseFile(BaseModel):
    """一个 golden set 用例。"""

    id: str
    category: Category
    title: str
    mode: Mode = "general"
    input: CaseInput
    expected: Expected
    timeout_sec: float = Field(default=30.0, gt=0)  # 每条用例的耗时预算
    tags: list[str] = Field(default_factory=list)
    compare: bool = False  # 是否参与豆包/千问对照
    notes: str = ""
    weight: float = Field(default=1.0, ge=0.0)  # 业务权重：加权复合分 Σ(w·通过率)/Σw（默认等权）
    must_pass: bool = False  # 红线用例：未达阈值即闸门失败（安全/合规等必须场景，不参与排名）
    must_pass_threshold: float = Field(
        default=1.0, ge=0.0, le=1.0
    )  # 红线通过阈值（attempt 通过率下限；默认零容忍 100%）
    # —— 评测台管理字段（0017 需求）：默认值保证存量用例零改动可加载 ——
    enabled: bool = True  # 停用的用例不参与跑批
    admin_note: str = ""  # 管理备注：停用原因 / TODO / 标注判断依据（与 notes 设计说明区分）
    updated_at: str = ""  # 最近编辑时间（ISO 格式字符串，由编辑方写入）
    updated_by: str = ""  # 最近编辑者（单用户本地）
    annotation: CaseAnnotation = Field(default_factory=CaseAnnotation)  # 人工标注金标准

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        """id 格式：小写字母/数字/连字符（如 tool-calc-001）。"""
        import re

        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", v):
            raise ValueError("id 格式应为 tool-calc-001 这类小写连字符形式")
        return v


def load_cases(cases_dir: Path) -> list[CaseFile]:
    """扫描目录下所有 JSON 用例并校验，返回用例列表（按 id 排序）。"""
    files = sorted(cases_dir.rglob("*.json"))
    cases = []
    for f in files:
        cases.append(CaseFile.model_validate_json(f.read_text(encoding="utf-8")))
    cases.sort(key=lambda c: c.id)
    return cases
