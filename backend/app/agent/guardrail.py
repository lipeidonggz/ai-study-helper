"""输出护栏：PromptLeakGuard（系统提示泄露）+ SelfHarmGuard（自伤高危短语）+ RefusalContentGuard（拒绝场景可执行内容）。

设计角度（2026-08-25）：
- 提示注入无法 100% 防住（业界共识是分层防御），输出护栏是最后一道
  确定性防线：检测目标明确（系统提示原文片段）→ 纯字符串匹配，无需 LLM
- 挂在 loop 的 yield 点：流式输出过程中一旦命中立即截断并替换为固定文案，
  保证"系统提示原文不会到达用户"；评测 runner 复用 loop 因此同样受保护
- 误报控制：只检测足够长的完整短语（>= 12 字符），正常回答不会逐字包含

SelfHarmGuard（2026-08-28）：自伤场景高危短语护栏。
- 为什么需要：提示词已要求不得描述部位级操作，但模型仍会偶尔写出"冲手腕"
  这类短语（连续两轮复测各漏 1/20）——提示词无法 100% 压制，属长尾；
  与 PromptLeakGuard 同为分层防御的最后一道确定性兜底
- 拦截策略：命中即截断当前 chunk 到短语前，追加安全说明，终止本轮
- 词表刻意小而高信号：只收"几乎只会出现在自伤场景"的短语，控制误报

RefusalContentGuard（2026-08-31）：拒绝场景可执行内容护栏（通用，条件启用）。
- 为什么需要：refuse-005（用药）与 refuse-001（危险命令）同族——拒绝类红线
  用例的病根是"模型在解释为什么不给时滑向给出具体可执行内容"（药名举例、
  命令原文），提示词强化逐个压不住（各漏 1/20），必须确定性兜底。
- 通用化设计：拦截目标不是穷举内容，而是"可执行内容形态"——挂载两类检测器：
  危险命令（rm -rf / format / del /f 等窄名单+模式）与 药名/剂型/剂量（窄名单+模式）。
- 与 SelfHarmGuard 的关键区别：**条件启用**——输入侧判定为"行动执行型风险请求"
  （用药 OR 破坏操作）时才检测；学习型请求（"布洛芬怎么起作用"/"rm -rf 是什么"）
  不启用，避免误伤正常学习。
- 名单随评测失败样本生长（与歧义词表同维护逻辑），漏网靠提示词兜底。
- 取舍：学习助手定位下注入+拦截足够；金融/医疗严苛场景需要代码层强制校验。
"""

import re

from app.agent.context import system_prompt

# 命中泄露后替换给用户的固定文案（不包含任何内部信息）
BLOCK_MESSAGE = "我不能透露内部指令或系统提示。"
_MIN_FRAGMENT_LEN = 12  # 低于此长度的短语区分度不够，可能误报

# 自伤场景高危短语（高信号：正常对话几乎不会出现，命中即拦截）
SELF_HARM_PHRASES: tuple[str, ...] = (
    "冲手腕",
    "冲一下手腕",
    "划手腕",
    "割手腕",
    "画下想伤害的位置",
)

# 命中后追加的安全说明（不包含任何部位级操作描述）
SELF_HARM_SAFE_SUFFIX = (
    "我不能提供任何涉及身体部位的具体操作描述。"
    "如果你正在经历痛苦，请立即联系信任的人或拨打心理援助热线"
    "（如 12356），或到就近医院寻求专业帮助。你的安全是最重要的。"
)

# 行动执行型用药请求的输入判定（半确定性，与歧义词表同思路）：
# 正向句式（寻求具体用药方案）命中且不含学习型信号时判定为 True。
# 句式只收强信号（"吃什么药"等），覆盖高频表达；漏判回到提示词兜底。
MEDICATION_ACTIONABLE_PATTERNS = (
    re.compile(r"吃什么药|用什么药|吃啥药|该吃.{0,4}药|给我.{0,6}药|推荐.{0,6}药|开药|用药建议|直接告诉.{0,4}药"),
)
# 学习型负向信号：请求在"讲原理/机制/成分"时是学习型，不启用护栏
MEDICATION_LEARNING_WORDS = (
    "原理", "机制", "为什么", "怎么起作用", "作用机理", "成分", "副作用", "禁忌", "区别", "比较", "注意事项",
)

# 窄高频药名名单（拒绝场景下出现即违规；随评测失败样本生长，不求穷尽）
MEDICATION_NAMES: tuple[str, ...] = (
    "布洛芬", "对乙酰氨基酚", "扑热息痛", "阿司匹林", "阿莫西林", "头孢",
    "泰诺", "芬必得", "美林", "感冒灵", "板蓝根", "快克", "感康",
    "蒙脱石散", "黄连素", "藿香正气", "双黄连", "吗丁啉",
)

# 剂型/剂量指令模式：明确给出可执行用药形式（拒绝场景下出现即违规）
MEDICATION_DOSAGE_PATTERNS = (
    re.compile(r"\d+\s*(?:mg|ml|克|毫克|片|粒)"),
    re.compile(r"[一二三四五六七八九十]+\s*片"),
    re.compile(r"(?:药片|片剂|胶囊|口服液|混悬液|滴剂|糖浆)"),
)

MEDICATION_SAFE_SUFFIX = (
    "我不能提供具体用药建议。请及时就医或咨询药师，"
    "让专业人员根据你的具体情况判断用药方案。"
)

# 行动执行型破坏操作请求的输入判定（与用药判定同构）：
# 正向句式（删除/格式化/清空/破坏 + 系统/磁盘/文件等目标）命中且不含学习型信号。
DESTRUCTIVE_ACTIONABLE_PATTERNS = (
    # 目标词聚焦破坏性对象（系统盘/磁盘/数据库/服务器等）；普通"删除文件"不算
    re.compile(r"(?:删除|格式化|清空|抹掉|破坏|摧毁|攻击|黑掉|删掉).{0,8}(?:系统盘|系统|磁盘|硬盘|注册表|数据库|服务器|网站|分区)"),
    re.compile(r"(?:系统盘|系统|磁盘|硬盘|注册表|数据库|服务器|网站|分区).{0,8}(?:删除|格式化|清空|抹掉|破坏|摧毁)"),
    re.compile(r"给我.{0,6}(?:命令|代码).{0,8}(?:删除|格式化|清空|破坏)"),
    re.compile(r"写.{0,6}(?:命令|代码).{0,8}(?:删除|格式化|清空|破坏)"),
)
DESTRUCTIVE_LEARNING_WORDS = (
    "原理", "是什么", "为什么", "作用", "机制", "风险", "区别", "历史", "讲解", "介绍",
)

# 窄高频危险命令名单（拒绝场景下出现即违规；随评测失败样本生长）
DANGEROUS_COMMAND_NAMES: tuple[str, ...] = (
    "rm -rf", "rm -fr", "rm -r -f",
    "del /f /s", "del /s /q", "rmdir /s /q",
    "format c:", "diskpart", "mkfs", "fdisk", "reg delete",
    "dd if=/dev/zero", "dd if=/dev/urandom",
    ":(){",
)

# 危险命令形态模式（覆盖变体写法，如 rm -rf /、format /q、> /dev/sda）
DANGEROUS_COMMAND_PATTERNS = (
    re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*"),
    re.compile(r"del\s+/(?:f|s|q)"),
    re.compile(r"rmdir\s+/s\s*/q"),
    re.compile(r"format\s+(?:[a-z]:|/q)"),
    re.compile(r"dd\s+if=/dev/(?:zero|urandom)"),
    re.compile(r"mkfs\.[a-z0-9]+"),
    re.compile(r"reg\s+delete"),
    re.compile(r">\s*/dev/sd[a-z]"),
)

DESTRUCTIVE_SAFE_SUFFIX = (
    "我不能提供具体可执行内容。这类操作会造成系统/数据不可逆的损害，"
    "请勿尝试；如有清理空间、重装系统等正当需求，建议使用系统官方工具或寻求专业帮助。"
)


def extract_fragments(mode: str, variant: str = "baseline") -> list[str]:
    """从系统提示提取有区分度的检测片段：按句子切分，过滤过短短语。"""
    prompt = system_prompt(mode, variant)
    parts = re.split(r"[。；;!?！？\n]", prompt)
    return [p.strip() for p in parts if len(p.strip()) >= _MIN_FRAGMENT_LEN]


class PromptLeakGuard:
    """增量检测器：维护已输出缓冲，每收到一段文本就检查是否命中系统提示片段。"""

    def __init__(
        self,
        mode: str,
        variant: str = "baseline",
        fragments: list[str] | None = None,
    ) -> None:
        self._fragments = (
            fragments if fragments is not None else extract_fragments(mode, variant)
        )
        self._buffer = ""

    def check(self, text: str) -> bool:
        """追加一段文本并返回是否已发生泄露（命中任一系统提示片段）。"""
        self._buffer += text
        return any(f in self._buffer for f in self._fragments)

    def leaked_fragments(self) -> list[str]:
        """返回已命中的片段（供 trace 记录，便于事后排查与评测判定）。"""
        return [f for f in self._fragments if f in self._buffer]


class SelfHarmGuard:
    """自伤高危短语护栏：命中返回短语在当前 chunk 内的起始位置，未命中返回 None。"""

    def __init__(self, phrases: tuple[str, ...] | None = None) -> None:
        self._phrases = phrases or SELF_HARM_PHRASES
        self._buffer = ""
        self._hit: str | None = None

    def check(self, text: str) -> int | None:
        """追加一段文本并返回命中短语的起始位置（当前 chunk 内）；未命中返回 None。"""
        self._buffer += text
        for p in self._phrases:
            idx = self._buffer.find(p)
            if idx >= 0:
                self._hit = p
                # 短语可能跨 chunk：位置落在更早的 chunk 时按 0 处理（前缀为空）
                in_chunk = idx - (len(self._buffer) - len(text))
                return max(in_chunk, 0)
        return None

    def hit_fragment(self) -> str | None:
        """返回命中的短语（供 trace 记录）。"""
        return self._hit


def is_actionable_med_request(user_message: str) -> bool:
    """判定是否为行动执行型用药请求：正向句式命中且不含学习型信号。"""
    if not any(p.search(user_message) for p in MEDICATION_ACTIONABLE_PATTERNS):
        return False
    if any(w in user_message for w in MEDICATION_LEARNING_WORDS):
        return False
    return True


def is_actionable_destructive_request(user_message: str) -> bool:
    """判定是否为行动执行型破坏操作请求：正向句式命中且不含学习型信号。"""
    if not any(p.search(user_message) for p in DESTRUCTIVE_ACTIONABLE_PATTERNS):
        return False
    if any(w in user_message for w in DESTRUCTIVE_LEARNING_WORDS):
        return False
    return True


def is_actionable_risk_request(user_message: str) -> bool:
    """通用判定：行动执行型风险请求（用药 OR 破坏操作）→ 启用可执行内容护栏。"""
    return is_actionable_med_request(user_message) or is_actionable_destructive_request(
        user_message
    )


class RefusalContentGuard:
    """拒绝场景可执行内容护栏（通用，条件启用）。

    输入侧判定为"行动执行型风险请求"时启用；输出侧拦截可执行内容形态：
    危险命令（窄名单+模式）与 药名/剂型/剂量指令（窄名单+模式）。
    enabled=False 时不检测（学习型请求不经过本护栏）。
    """

    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled
        self._buffer = ""
        self._hit: str | None = None
        self._kind: str | None = None  # "command" | "drug"

    def check(self, text: str) -> int | None:
        """追加一段文本并返回命中位置（当前 chunk 内）；未命中或未启用返回 None。"""
        if not self._enabled:
            return None
        self._buffer += text
        base = len(self._buffer) - len(text)  # 当前 chunk 起点（用于跨 chunk 位置计算）

        # 1) 危险命令形态模式
        for pat in DANGEROUS_COMMAND_PATTERNS:
            m = pat.search(self._buffer)
            if m:
                self._hit = m.group(0)
                self._kind = "command"
                return max(m.start() - base, 0)
        # 2) 剂型/剂量指令模式（药名可能不带完整名字，先查形态）
        for pat in MEDICATION_DOSAGE_PATTERNS:
            m = pat.search(self._buffer)
            if m:
                self._hit = m.group(0)
                self._kind = "drug"
                return max(m.start() - base, 0)
        # 3) 窄名单：危险命令名 + 高频药名（可能跨 chunk）
        for name in (*DANGEROUS_COMMAND_NAMES, *MEDICATION_NAMES):
            idx = self._buffer.find(name)
            if idx >= 0:
                self._hit = name
                self._kind = "command" if name in DANGEROUS_COMMAND_NAMES else "drug"
                return max(idx - base, 0)
        return None

    def hit_fragment(self) -> str | None:
        """返回命中的命令/药名/模式（供 trace 记录）。"""
        return self._hit

    def hit_kind(self) -> str | None:
        """返回命中类别：command / drug（供 loop 选择安全后缀）。"""
        return self._kind
