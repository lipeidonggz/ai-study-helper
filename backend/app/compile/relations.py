"""受控关系表（**草案，待人工复核**）。

为什么要有这张表（memory/0028 考古层第二十四段）：
  实测 Anthropic 那篇的 751 条边用了 **220 种**表面谓词（65 种只出现一次），
  里面混着四类不该进谓词的东西——轻动词（is×46 / include×31 / has×18）、
  塞进谓词的宾语（cost isolation overhead、has its own filesystem）、
  整句（14 个词的谓词）、以及否定/时态/对比（not / isn't same as / previously protected against）。
  其结果：**结构判同指完全失效**（三个不同实体各自只有一条同形边，相似度 1.00）。

## 设计依据：五条用途（改表一律先过这五条）

  1. **判同指（实体消解）**——要求：谓词是受控词，两个实体的"关系轮廓"才可能互相比。
     · 正例：`have been hardened against` 与 `previously protected against` 归到同一条后，
       `VM` 与 `virtual machine` 的轮廓才可能重叠。这是上一轮结构判同指失败的根因。
  2. **图遍历**——要求：少而稳定，检索时知道该沿哪类边走。
     · 例：问"怎么防护" → 沿 `protects_against` / `enforces` / `constrains` 走。
  3. **按关系族挑证据**——要求：关系按语义族分，且每族边界清楚。
     · 例：查定义→`is_a`/`is`；查机制→`uses`/`enforces`/`constrains`；
       查对比→`different_from`/`similar_to`；查风险→`risks`/`threatens`/`protects_against`；查代价→`costs`。
       （②是"沿着边走"，③是"按关系族筛选"，一个管连通、一个管选材。）
  4. **归并同义断言（边层去重）**——要求：同义谓词归到一条，才看得出"两条边说同一件事"。
     · 例：`offers`×12 / `shipped`×12 / `provides`×4 不归一，就永远看不出重复。
  5. **人工复核可读**——要求：短、可统计。
     · 反例：`have survived more adversarial attention than`×12、14 个词的谓词——人眼扫不动、统计做不出。

**判断规则**：一个候选关系只有服务上面**至少一条**用途才进表。过不了这五条的**具体动作动词**
（`mounted` / `exfiltrates` / `approves` / `audits` / `deploys` / `reaches`…）一律不收——
收了它们，表会膨胀回几百条，而五条用途一条都没变好。这类应该表达成「关系 + 宾语」
（`can mount X`）或直接成为**实体**（"数据外泄"本来就是概念）。

## 表分两组，别混

  · `SEMANTIC_RELATIONS`：概念之间的**语义关系**（服务用途 1–4，参与判同指）
  · `PROVENANCE_EDGES`：**出处/归属边**（服务引用审计，回答"这条断言从哪来"，
    **不参与判同指**）
  之前把 `asserted_by` 混在语义关系里，已经造成一次误用：映射实验里 `should be determined by`×2
  被映射成了 `asserted_by`（其实是"由…决定"的语义关系）。

## 其余设计原则

  · **谓词只放关系词**：时态、情态、修饰进专门字段；否定进 polarity（不占词表名额）。
  · **允许兜底**：抽取时可以从表里选，也可以给新谓词，但**必须同时给最接近的表内关系**；
    新谓词按频次事后收敛进表。表是活的。
  · 方向与对称性写在说明里；反向关系一律另立条目（如 has_part / part_of）。

## 待定（等拍板，暂不改表）

  1. `affects` 现在收 14 种 / 30 条（`addresses` / `decide` / `is problematic for` / `err toward`…）
     —— 收紧定义，还是拆成两条？
  2. `shipped`×12 / `built`×4 现在落进 `provides`（语义漂移）——要不要单列 `releases`？
  3. 要不要新增 `become`（状态转变）、`overlap_or_complement`（关系比较）？
  4. `after` 现在 0 条——删掉，还是与 `before` 合并成一条带方向的？
  5. "由…决定"（`should be determined by`）要不要单列 `determines`？
"""

from __future__ import annotations

# 语义关系 id → 中文说明（含方向/对称性约定）
SEMANTIC_RELATIONS: dict[str, str] = {
    # 定义与结构
    "is_a": "X 是 Y 的一种（上下位）",
    "is": "X 就是 / 被描述为 Y（等同或判断，非上下位）",
    "has_part": "X 拥有 / 包含 Y（整体—部分、成员、组成）",
    "part_of": "X 是 Y 的一部分 / 成员（has_part 的反向）",
    "has_property": "X 具有属性 / 处于某状态 Y",
    # 能力与权限
    "can": "X 有能力 / 被允许做 Y（否定走 polarity）",
    "requires": "X 需要 / 依赖 Y",
    # 约束与防护
    "constrains": "X 限制 / 约束 Y",
    "blocks": "X 阻断 / 拒绝 / 阻止 Y",
    "enforces": "X 强制执行 / 落实 Y",
    "protects_against": "X 防护 / 抵御 Y",
    # 手段与因果
    "uses": "X 使用 / 借助 Y（手段、途径、机制）",
    "causes": "X 导致 / 引起 Y（含代价、后果）",
    "affects": "X 影响 Y（方向不明确）",
    "reduces": "X 减少 / 降低 Y",
    "increases": "X 增加 / 放大 Y",
    "costs": "X 的代价 / 开销是 Y",
    # 风险与暴露
    "risks": "X 带来风险 / 暴露于风险 Y",
    "threatens": "X 攻击 / 威胁 Y",
    "exposes": "X 暴露 / 使可及 Y",
    "visible_to": "X 对 Y 可见（含不可见）",
    # 环境与位置
    "runs_in": "X 运行 / 执行于 Y（环境、宿主、平台）",
    "located_in": "X 位于 Y",
    "outside_of": "X 位于 Y 之外",
    # 顺序与比较
    "before": "X 发生在 Y 之前",
    "after": "X 发生在 Y 之后",
    "different_from": "X 与 Y 不同（对称）",
    "similar_to": "X 与 Y 相似（对称）",
    # 观测与支持
    "detects": "X 检测 / 发现 Y",
    "monitors": "X 监视 / 观测 Y",
    "provides": "X 提供 / 给出 Y",
    "supports": "X 支持 / 帮助 Y",
}

# 出处/归属边（引用审计用；**不参与判同指**）
PROVENANCE_EDGES: dict[str, str] = {
    "asserted_by": "该断言由 X 提出 / 主张（文档或主体）",
    "has_statement": "文档 → 其下的具体化陈述",
    "grounded_in": "具体化陈述 → 证据 chunk",
    "evidence_for": "数据 / 基准 → 它所支持的断言",
}

# 兼容旧名（映射脚本与提示词用）：只含语义关系
RELATIONS = SEMANTIC_RELATIONS

# 抽谓词时禁止把下列内容塞进谓词字段（程序校验用）
POLARITY_FIELD = "polarity"  # 否定：negative / affirmative


def table_lines() -> str:
    """给提示词用的紧凑表（一行一条；只含语义关系，出处边不参与抽谓词）。"""
    return "\n".join(f"- {rid}: {desc}" for rid, desc in SEMANTIC_RELATIONS.items())
