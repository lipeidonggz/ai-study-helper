"""B6 · 最小编译切片：真实跑一遍编译层（O2/A5），不写向量库。

为什么做（B6）：
  用真实语料验证编译输出 schema + 编译 prompt 是否可落地——LLM 能否正确抽出
  概念/陈述/边、reify_reason / claim_type 判断是否合理、引文锚定（span→chunk）能否定位到块。

输入/坐标：
  从 MANIFEST 取源 → 复用切块抽取（_file_units）得到"清洗后原文 T"（与 chunk_offsets 同源同坐标）。
  O2/A5 均为单文件、整篇 < 12k token → 一个内容单元（不按小节拆，避免破坏上下文）。

流程：
  清洗后 T → 内容单元（整篇）→ 编译 prompt → DeepSeekLLMClient → 解析编译产物 bundle
  → 引文锚定：逐段归一化 evidence_texts → 在 T 定位起止 → 与 chunk_offsets 重叠并集 → evidence_chunks。

输出：bundle JSON（backend/data/tmp/b6_<source>.json），供人工审质量。不做任何向量入库。
运行：cd backend && .venv\\Scripts\\python.exe -m scripts.compile_slice_b6 --source O2,A5
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from app.agent.llm import DeepSeekLLMClient, LLMMessage
from app.compile.relations import RELATION_ALIASES, RELATIONS, table_lines
from app.kb.ingest import _file_units
from app.kb.manifest import parse_manifest
from app.storage.sqlite.kb_store import KbStore
from app.storage.sqlite.settings_store import SqliteSettingStore


BACKEND = Path(__file__).resolve().parents[1]  # backend/
REPO = BACKEND.parent                          # 仓库根目录
APP_DB = BACKEND / "data" / "app.db"           # 运行时 DB（backend/data）
KB_DB = BACKEND / "data" / "kb.db"
MANIFEST_PATH = REPO / "data" / "kb-src" / "MANIFEST.md"  # 素材台账（仓库根 data/kb-src）
OUT_DIR = BACKEND / "data" / "tmp"
DEFAULT_MODEL = "deepseek-chat"
PASS2_CLAIM_BUDGET = 40  # Pass2 每批 claim 上限（输出规模 ∝ claims；按"块"累积、块不跨批）


# ---------------------------------------------------------------------------
# 编译系统提示词（基础件，草稿；附字段/概念定义 + 规则 + 示例）
# ---------------------------------------------------------------------------
PASS1_SYSTEM = """你是「知识抽取器」。把输入的「清洗后原文窗口」抽成【原子断言】列表（每条 = 一个 subject–predicate–object 的最小断言，可带角色与极性），只输出严格 JSON。

# 输入
一段清洗后原文（纯段落文本、无编号、可能含多个主题）。

# 输出（严格 JSON；无多余文字、无代码围栏）
{"claims":[{"subject":"…","predicate":"…","object":"…","polarity":"negative","roles":{"instrument":["…"]},"evidence_texts":["逐字连续引文","…"]}]}
polarity / roles 可省略。

# A. 抽取范围
抽：针对某个「可被点名的知识对象」（有名有姓，如 containment / model layer / egress control / Claude Cowork；仅举例，不限于此），说了「可被引证之事」的断言——是什么 / 怎么运作 / 为什么 / 有什么局限 / 未来方向 / 与谁的关系 / 谁主张什么 / 分成哪类 / 属性 / 机制。
跳过：过渡 / 引子 / 钩子；只复述上一句的；无新断言的例子；主体是「我们 / 本文 / 这个改动」这类叙述 / 元主体。
拿不准就抽（宁多勿漏）。

# B. 原子性（一条 = 一个最小断言）
- 复合句（一句话两个意思）→ 拆成两条。例：`X is fast and Y is slow` → `X is fast` ／ `Y is slow`。
- 复合谓词（一个主语、两个动词）→ 拆成两条。例：`X reads and writes files` → `X reads files` ／ `X writes files`。
- 列表（一个位置多个项 A/B/C，主语或宾语皆可）→ 每条一个项；只拆「项的个数」，谓词不变、不新造动词。例：`X reads files, sockets, and env vars` → `X reads files` ／ `X reads sockets` ／ `X reads env vars`。

# C. 字段约定
- subject / object = 有名名词短语（可点名对象）。
- predicate = 原文里的动词短语（**只留动词与必要的小品词**，原样抄）。
- 谓词只放关系词：**宾语、从句、修饰、否定、时态、情态都不许揉进谓词**。
  · ✗ "has cost of isolation overhead" → ✓ predicate: "has cost of"、object: "isolation overhead"
  · ✗ "has its own filesystem" → ✓ predicate: "has"、object: "filesystem"
  · ✗ "survived more adversarial attention than" → ✓ 拆成两条（关系一条、比较对象一条）
- 否定：谓词仍用肯定形式，另给 polarity: "negative"。例：`can't inspect` → predicate: "inspect"、polarity: "negative"。
- 时态 / 情态不入谓词：`was` / `may be` / `have been` / `previously` 这类不写进谓语，用动词原形。
- subject 取该断言所描述的、最具体、最像话题的核心实体。例：✗ `An important factor is caching` → ✓ `caching is an important factor`。
- 不作 subject：抽象类别 / 属性 / 从句；报告来源 / 元主体（telemetry / we / 本文 / 作者）。
- **指代不要自行消解**：主语若是 `this / it / they / that / these` 这类代词或指示词，**照抄原文形式即可**，
  不要替换成你推断出的所指（消解由后续步骤做——它们看得到上下文；你在这里替它消解就会写出原文里没有的词，
  破坏"引文可锚"）。这条不违反"宁多勿漏"：断言照抽，主语照抄。
- 动名 / 命题主语必须归约：以 granting / placing / supervising / limiting / having 或 "X that …"、"only when …" 开头的主语，改写为「施事（或核心实体）→ 谓词 → 该命题」。
- 定义 / 归类断言（is / is a / has…）：把被归类 / 被描述的实体放 subject。

# D. 角色（n-ary 关系的限定；写在 roles 里，不占 object）
- 「经由 / 通过 / 借助 / 用 X」这类「手段 / 工具」→ 放进 `roles.instrument`（可多值）；不要塞进 object、不要拆成新条、不要新造动词。
- 没有角色就不要输出 `roles` 字段（不要输出空 `{}`）。
- 例：`X repels attacks via sandboxes and VMs` → 一条：`{subject:X, predicate:protects_against, object:attacks, roles:{instrument:["sandboxes","VMs"]}}`。

# E. evidence_texts
- 每条 claim 给逐字原文引文数组。
- 每个元素 = 同一段原文内的一段连续文字；含标点、与原文逐字一致；不许拼接、不许改写。
- 跨段（即便相邻段）→ 拆成多个数组元素。
- 某段找不到逐字对应 → 放弃该段；整条都找不到 → 放弃该 claim。
"""

# Pass 1 · 谓词归一（独立小步）：原文动词短语 → 受控关系表 id
# 为什么拆出来：把 36 条表塞进抽取会拖累召回（实测 1.00 → 0.80/0.85，见 memory/0028 考古层第二十六段）。
# 这一步**只做映射，不增删 claim**，所以召回由上面那步守住。
PASS1_NORM_SYSTEM = """你是「关系归一器」。输入是一批断言，每条含：claim_idx、subject、predicate（原文动词短语）、object，以及三段原文——
anchor_sentence（**这条断言就是从这句抽出来的**）、context_before / context_after（它的前后各一句）。
**先读 anchor_sentence，再判断 predicate 该归到哪个关系**；前后句只用来消歧。只输出严格 JSON。

# 受控关系表
«关系词表»

# 规则
1. 每条输入输出一条结果（用 claim_idx 回指，全局编号），**不许增删条目**。
2. predicate 取表内 id；**表里确实没有**才可用新词，同时给 predicate_nearest（最接近的表内 id）。
3. 否定不算关系差异：原文是否定 → 谓词用肯定关系 + polarity: "negative"（不要用新词表示否定）。
4. 时态 / 情态 / 修饰不算关系差异：`was` / `may be` / `previously` / `have been` → 用最贴近的关系（通常是 has_property）。
5. 只依据给定信息判断，不要引入外部知识；拿不准选语义更宽的那条。
5b. **表外新词必须给 <code>predicate_nearest</code>**：即使词不在表里，也必须指出最接近的表内 id
    （实在挑不出就选语义最宽的那条，如 affects / has_property）——留空会被判为"待定"，不进图。
6. **方向要单独给**：`changes` 这条只表示"变了"；是变多还是变少，用 `sign: "up" | "down"` 表示
   （`grows` / `increases` / `expands` → sign:up；`reduces` / `minimizes` / `limits` → sign:down）。
7. 情态词（can / may / must）本身不代表关系：先判这句到底说了什么关系。
   · `bounds can be placed on X` → constrains（约束），不是 can
   · `Claude can read files` → can（能力/权限本身）
8. 只输出 JSON：
{"mapping":[{"claim_idx":0,"predicate":"…","sign":"up","polarity":"negative","predicate_nearest":"…","note":"…"}]}
sign / polarity / predicate_nearest / note 可省略。
"""

PASS1_NORM_SYSTEM = PASS1_NORM_SYSTEM.replace("«关系词表»", table_lines())


PASS2_SYSTEM = """你是「知识图谱构建器」。把输入【按块组织的原子断言清单】组装成 KG，只输出严格 JSON，不要多余解释、不要代码围栏。不得改断言内容——证据就在每块的 context 原文里。

# 输入
按块组织的断言清单：每个块含 context（该块清洗后原文）+ 该块内的 claims；每条 claim 含 claim_idx（全局下标）、subject / predicate / object（可空）/ roles（可空）。

# 输出（严格 JSON；无多余文字、无代码围栏）
{"entities":[{"name","type":"concept|document","aliases":[]}],
 "terms":[{"label","denotes":"<entity name>","note":""}],
 "statements":[{"claim_idx":0,"claim_type":null,"reify_reasons":["…"],"about":[],"roles":{},"asserted_by":"<doc ref>"}],
 "relations":[{"from","predicate","to","produced_by":"compile|compile-hypothesis","note":""}],
 "data_evidence":[{"claim_idx":N,"evidence_for":0}]}

# A. 术语
- entity（节点）：type ∈ {concept, document}。concept=可被点名的稳定知识对象/主题。
- term：记录某实体另一正式名称；term.label=该名称；term.denotes=规范 entity 名。仅术语漂移/来源差异才建 term（实体 aliases 不建 term）。
- **aliases（别名）**：同一实体的**另一个名字**；判据 = 把原文里的名字换成它、**句意不变**。
  · 可放：单复数 / 大小写 / 连字符 / 冠词 / 缩写与全称（virtual machine ↔ VM）。
  · 不放（各建 entity、用 relation 连）：它**使用 / 包含 / 依赖**的东西（"OS-level sandbox" 用了 "Seatbelt" → Seatbelt 是独立实体、不是别名）、**描述 / 定语**、**相关但非同一对象**的。
  · **别名不得同时又是独立 entity**：若同一名字既出现在某 entity 的 aliases、又作为 entity 出现，只留 entity，把它从别人的 aliases 里去掉或合并。
- statement：被 reify 成节点的断言（rdf:Statement）。
- relation（边）：实体/陈述间的谓词，取值来自受控词表（见 E）。

# B. 结构规范化（核心：借 context 把 Pass 1 的糙结构归约成干净结构）
- 从句/命题主语或宾语（"supervising …"、"whether …"、"how to …"、"X that …"）→ 用 context 归约成其中的有名概念/实体；不得留作 entity 名或 statement 端点。
- 手段/路径（"经由/通过/借助 X"）若落在谓词或宾语里 → 用 context 取出，放进 statement 的 roles.instrument（可多值）。
- entity 名必须是干净名词概念（可点名）；拒绝整句/命题式短语。
- 同义/变体（同一概念多种叫法）→ 合并到同一 canonical entity（别名口径见 A）。

# C. 每条 claim 的归处
→ reify_reasons 非空 → statement；否则 → entity+relation 或 entity 属性。
- statement：reify_reasons 任一命中（见 D）。
- entity+relation：结构关系（partOf/involves/precedes）、机制/定义（X 是 Y 的一种 / X 通过 A 达成 Y）。谓词"经由 A/B/C"复合 → 拆成 X↔A、X↔B、X↔C。
- entity 属性：单属性（X 由两部分组成）→ 挂 entity 属性/标签，不立 statement。
- data/benchmark（含 %/数字/基准结果）→ 不独立立 statement；设 evidence_for=所支撑 statement 的 claim_idx。

# D. reify_reasons（statement 判据，任一命中即立）
{type_filter, argument_endpoint, concept_central}
- type_filter：该断言论述功能是局限/展望（用户按类型过滤）。
- argument_endpoint：该断言需作 support/attack/complement 的端点。
- concept_central：关于枢纽概念、可被独立提问（含估值/影响断言论"X 重要/导致/影响 Y"、"什么是 X"）。

# E. claim_type 与 relation 谓词
- claim_type ∈ {LimitationStatement, OutlookStatement, null}：Limitation=已承认的当前不足/具体翻车教训；Outlook=面向未来演化的威胁/下一步呼吁；以 what's broken vs looking ahead + 判官 a5-e2/a5-e3 为界；其余 null。claim_type 仅当 type_filter 在场时设。
- relation 谓词（受控词表）∈ {partOf, involves, precedes, support, attack, complement, asserts, hasStatement}。估值/影响（causes/affects/important）是 statement 的谓词，不进此表。

# F. statement 字段
claim_idx（回指条目的全局下标）、claim_type、reify_reasons、about（该断言关于的 entity 名）、roles（若归一出手段/路径；否则省略）、asserted_by（文档 ref）。不回抄 subject/predicate/object（程序取自 claim，防输出过大）。
"""

# 去重段两版：strict=从严/grounded（新）；loose=凭常识合并（旧，仅对比用）
_DEDUP_STRICT = """# 去重（判据；宁拆勿错合）
- 形态变体（形；不看上下文即可判）→ **合**：完全同名 / 单复数 / 大小写 / 连字符 / 冠词 / 缩写与全称（VM、VMs → VM）。
- 上下文里的同义（义；**须 context 支持**）→ **合**：context 明确把两者当同一对象（缩写展开 / 明说"也叫…" / 并列定义）。
- **不得仅凭你自己的常识判"同义"**——上下文不支持就**不合并**。
- 不合并：相近但不同的概念（VM vs sandbox）。
- 拿不准 → **不合并**（错误合并比拆更难挽回）。
- 合并后：canonical 取最常用/最正式的（别名口径见 A）。
- relation 的 from/to 用实体名 或 语句 claim_idx；不出现同对象既局限又展望。
"""
_DEDUP_LOOSE = """# 去重
- 同实体合并到同一 name（canonical）；不出现同对象既局限又展望。
- relation 的 from/to 用实体名 或 语句 claim_idx。
"""


# Pass 2 · 步骤①「抽取」：块(context+claims) → entities + edges（不去重、不分类）
PASS2_STEP1_SYSTEM = """你是「知识图谱抽取器」。从输入的「块 context + 该块 claims」抽出【实体 + 边】，只输出严格 JSON。

# 输入
按块组织：每块含 context（该块清洗后原文）+ 该块 claims（每条 claim = subject/predicate/object（可空）/roles（可空）+ claim_idx）。

# 输出（严格 JSON；无多余文字、无代码围栏）
{"entities":[{"name","type":"concept|document","aliases":[]}],
 "edges":[{"from","predicate","to","roles":{},"claim_idx":0}]}

# 规则
1. 实体（entity）= **可点名的名词概念**（稳定知识对象 / 主题）。判据：**能自然地问"什么是 X？"**。
   · 是（通用例，非本语料）：cache / database index / supply chain / HTTP —— 都是能被单独定义的名词概念。
   · 不是（**不作实体**，即便它是 claim 的 subject / object）：
     - **从句 / 命题式**：以 how to / whether / when / why 开头的句子式；
     - **描述 / 定语短语**：含 "the risk of X / the likelihood of Y / N components / a broader release of …" 这类定语、数量、从句修饰的短语；
   · 短语里**有可点名的概念就取那个概念**（如 "how to optimize the query" → 实体 `query`）；**取不出干净概念就不建实体**。
   · name = 规范名词；type ∈ {concept, document}。
2. 别名（aliases）：同一实体的另一个名字；判据 = 把原文里的名字换成它、句意不变。可放：单复数 / 大小写 / 连字符 / 冠词 / 缩写与全称。不放：它使用/包含/依赖的东西、描述/定语、相关但非同一对象。别名不得又是独立 entity。
3. 边（edges）：from / to **都必须是实体**（见规则 1）；把 claim 建成 from→predicate→to。
   · **任一端不是实体（是描写 / 从句）→ 该端不建实体、且这条 claim 不生成边**（不要为凑边而硬造脏实体）。
   · **一元断言**（object 为空）→ `to` 留空（只有 from 端）。
4. roles：「经由 / 通过 / 借助 X」这类手段 / 路径 → 放到边的 `roles.instrument`（可多值）；不要塞进 to、不要新造动词。
5. 不做去重、不判 claim_type / reify（后续步骤做）：同一实体 / 边可重复出现。
6. 只输出 JSON。
"""


def _build_clean_t(sections) -> str:
    """构建"清洗后全文 T"：与 chunk_sections / chunk_offsets 同一坐标（各节段落按序 \\n 连接）。"""
    return "\n".join(p for s in sections for p in s.paragraphs)


def _window_text(text: str, max_chars: int) -> list[str]:
    """按段落（\\n）边界把长文本切成窗口，每窗 ≤ max_chars（超长单段整体保留）。"""
    paras = text.split("\n")
    windows: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in paras:
        add = len(p) + (1 if cur else 0)
        if cur and cur_len + add > max_chars:
            windows.append("\n".join(cur))
            cur = [p]
            cur_len = len(p)
        else:
            cur.append(p)
            cur_len += add
    if cur:
        windows.append("\n".join(cur))
    return windows


def _norm(s: str) -> str:
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _find_raw(evidence: str, text: str) -> tuple[int, int] | None:
    """在 text 中定位 evidence 的 [start, end)。先精确，再归一化（空白/引号/破折号）。"""
    idx = text.find(evidence)
    if idx >= 0:
        return idx, idx + len(evidence)

    # 归一化折叠：把 text 折叠成一个 collapsed 字符串，并记录每个 collapsed 字符对应的原始起始位置。
    # 重建时再定位 evidence，把 collapsed 下标映射回原始偏移。
    collapsed: list[str] = []
    raw_map: list[int] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in "\u201c\u201d\u2018\u2019\u2013\u2014\u00a0":
            collapsed.append(_norm(c))
            raw_map.append(i)
            i += 1
        elif c.isspace():
            collapsed.append(" ")
            raw_map.append(i)
            while i < len(text) and text[i].isspace():
                i += 1
        else:
            collapsed.append(c)
            raw_map.append(i)
            i += 1
    ctext = "".join(collapsed)
    nev = _norm(evidence)
    ci = ctext.find(nev)
    if ci < 0:
        return None
    start = raw_map[ci]
    end = raw_map[ci + len(nev) - 1] + 1
    return start, end


def _overlap(lo: int, hi: int, chunks: list[dict]) -> list[str]:
    """[lo, hi) 与各 chunk 的 [start, end) 重叠 → 命中 chunk_id 列表（多对多）。"""
    hits = []
    for ch in chunks:
        if ch["start_pos"] < hi and ch["end_pos"] > lo:
            hits.append(ch["chunk_id"])
    return hits


def _extract_json(content: str) -> dict:
    """从 LLM 输出里剥离代码围栏/多余文本，解析出 JSON 对象。"""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("LLM 输出中没有 JSON 对象")
    obj = text[start : end + 1]
    try:
        return json.loads(obj)
    except json.JSONDecodeError as e:
        # 容忍"Extra data"（JSON 后有冗余内容）：取第一个完整 JSON 值。
        try:
            val, _ = json.JSONDecoder().raw_decode(text[start:])
            return val
        except Exception:
            pass
        # 常见 LLM 输出问题：字符串内的裸换行未转义、尾逗号。做一次宽松修复再试。
        repaired = _repair_json(obj)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            dbg = OUT_DIR / f"b6_parse_fail_{int(__import__('time').time())}.json"
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            dbg.write_text(obj, encoding="utf-8")
            raise ValueError(f"LLM JSON 仍无法解析（{e}），原始落盘 {dbg}") from e


def _repair_json(s: str) -> str:
    """宽松修复：把双引号字符串内的裸换行转义、去尾逗号（容忍 LLM 大块 JSON 的不规范）。"""
    out: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ch in "\n\r":
            out.append("\\n")
            continue
        out.append(ch)
    repaired = "".join(out)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


# 句子边界辅助（把短锚扩成"锚句 ± 邻句"用；内容一律从 clean_t 切，无损）
_ABBREV = ("e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "st.", "no.", "fig.",
           "u.s.", "a.m.", "p.m.", "al.", "cf.", "approx.")


def _is_sentence_end(t: str, i: int) -> bool:
    """t[i] 是否为句末标点（避开常见缩写与小数字点号）。"""
    if t[i] not in ".!?…":
        return False
    if t[i] == "." and i > 0 and t[i - 1].isdigit():  # 4.7 / v1.0
        return False
    tail = t[max(0, i - 8) : i + 1].lower()
    return not any(tail.endswith(a) for a in _ABBREV)


def _sentence_bounds(t: str, pos: int) -> tuple[int, int]:
    """pos 所在句子的 [start, end)（遇到换行也算边界）。"""
    start = 0
    i = pos
    while i > 0:
        if t[i - 1] == "\n":
            start = i
            break
        if _is_sentence_end(t, i - 1):
            start = i
            break
        i -= 1
    end = len(t)
    j = pos
    while j < len(t):
        if t[j] == "\n":
            end = j
            break
        if _is_sentence_end(t, j):
            end = j + 1
            break
        j += 1
    return start, end


def _anchor_window(claim: dict, clean_t: str) -> dict:
    """把 claim 的短锚**扩成锚句 ± 邻句**（从 clean_t 无损切出，不改 Pass 1 输出）。

    为什么要这一步：短锚是为"LLM 复制保真 + 程序定位"设计的，信息量不足以判关系；
    但下游要的上下文**不该再由 LLM 复制**（越长越容易重建失真），而应由程序按锚切。
    """
    pos = None
    for e in claim.get("evidence_texts") or []:
        pos = _find_raw(e, clean_t)
        if pos:
            break
    if not pos:
        return {"anchor_sentence": " ".join((claim.get("evidence_texts") or [""])[:1])[:300], "context_before": "", "context_after": ""}
    s, e = _sentence_bounds(clean_t, pos[0])
    ps = pe = es = ee = None
    if s > 0:
        ps, pe = _sentence_bounds(clean_t, max(0, s - 1))
    if e < len(clean_t):
        es, ee = _sentence_bounds(clean_t, min(len(clean_t) - 1, e + 1))
    return {
        "anchor_sentence": clean_t[s:e].strip(),
        "context_before": clean_t[ps:pe].strip() if ps is not None else "",
        "context_after": clean_t[es:ee].strip() if es is not None else "",
    }


# 谓词体检用的模式（Pass 1 质量门：把这些从谓词里赶出去）
_CLAUSE_MARKERS = re.compile(r"\b(when|until|regardless|that|because|whether|while)\b", re.I)
_NEG_MARKERS = re.compile(
    r"\b(not|never|cannot|can't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|won't|couldn't|shouldn't)\b", re.I
)


def _validate_claims(claims: list[dict]) -> dict:
    """Pass 1 谓词体检：长谓词 / 否定混入 / 从句混入 / 表外新词 / 缺宾语的长谓词。"""
    long_pred: list[tuple[int, str]] = []
    neg_in_pred: list[tuple[int, str]] = []
    clause_in_pred: list[tuple[int, str]] = []
    outside: list[tuple[int, str]] = []
    empty_obj_long: list[tuple[int, str]] = []
    kinds: set[str] = set()
    in_table: set[str] = set()
    for i, c in enumerate(claims):
        surf = (c.get("predicate_surface") or "").strip()
        pred = (c.get("predicate") or "").strip()
        obj = (c.get("object") or "").strip()
        wc = len(surf.split())
        if pred:
            kinds.add(pred)
            if pred in RELATIONS:
                in_table.add(pred)
        if wc > 3 and not obj:
            long_pred.append((i, surf))
        if _NEG_MARKERS.search(surf) and not c.get("polarity"):
            neg_in_pred.append((i, surf))
        if _CLAUSE_MARKERS.search(surf):
            clause_in_pred.append((i, surf))
        if pred and pred not in RELATIONS and not c.get("predicate_nearest"):
            outside.append((i, pred))
        if not obj and wc >= 3:
            empty_obj_long.append((i, surf))
    return {
        "pred_kinds": len(kinds),
        "in_table_kinds": len(in_table),
        "outside_kinds": len({p for _, p in outside}),
        "long_pred": long_pred,
        "neg_in_pred": neg_in_pred,
        "clause_in_pred": clause_in_pred,
        "outside": outside,
        "empty_obj_long": empty_obj_long,
    }


def _snap_relation(word: str) -> str | None:
    """表外词的**确定性归一**兜底，两级：

    ① 关系别名（relations.py 的 RELATION_ALIASES，如 controls→constrains、allows→can）
       —— 这是"造词残差 → 定期收敛"的落点；
    ② 形态/时态变体（becomes→become、running→run、placed→place）。
    只在"折回来的词恰好是表内 id"时生效，不会把真缺口误判成表内关系。

    注：系动词 be 家族（are / was / been…）**不在**这里折成 `be`——表内系动词主名是 `is`，
    而 is / is_a / has_property 是三条不同的语义档。模型若直接吐 be 家族，交 `predicate_nearest` 强归，
    由它选合适的那一档，比这里硬折成 `is` 更准。
    """
    w = (word or "").strip().lower()
    if not w:
        return None
    if w in RELATION_ALIASES:
        return RELATION_ALIASES[w]
    cands = [w]
    if w.endswith("ies"):
        cands.append(w[:-3] + "y")
    if w.endswith("es"):
        cands.append(w[:-2])
    if w.endswith("s"):
        cands.append(w[:-1])
    if w.endswith("ing"):
        cands.extend([w[:-3], w[:-3] + "e"])
        if len(w) > 5 and w[-4] == w[-5]:      # running → run（双写辅音）
            cands.append(w[:-4])
    if w.endswith("ed"):
        cands.extend([w[:-2], w[:-1]])
    return next((c for c in cands if c in RELATIONS), None)


PASS1_CLEAN_SYSTEM = """你是「谓词清洗器」。输入是一批断言（每条含 claim_idx、subject、predicate（原文动词短语）、object，以及锚句 anchor_sentence 与前后句）。
把每条断言的 predicate 清洗成**一个干净动词（原形）**，只输出 JSON。

# 要清掉什么（只删、不改写）
- **从句 / 修饰**：`expands quickly enough that the margin collapses` → `expands`
- **并列**：`drafts and publishes` → `drafts`（另一个动作不塞进谓词）
- **时态 / 系动词变体**：`becoming` → `become`；`has been shipped` / `will be shipped` → `ship`；`was` / `will be` → `is`
- **否定**：谓词写**肯定形式**，否定另给 `polarity: "negative"`
  · `should not be quietly ignored` → `ignore` + polarity negative
- **情态**：`may relocate` → `relocate`
- **混进谓词的宾语**：`holds a private ledger` → `holds`（`ledger` 是宾语）

# 红线
1. **只能删、不能换**：清洗后的每个词都必须来自原 predicate、且保持原顺序（允许改时态/单复数形态）。
   凭空换成另一个动词会被程序拒绝（该条保留原谓词并标记 rejected）。
2. 主宾与引文一个字都不许动。
3. 只输出 JSON：
{"cleaning":[{"claim_idx":0,"predicate_clean":"…","polarity":"negative","note":"…"}]}
polarity / note 可省略。
"""


_CLAUSE_IN_PRED = re.compile(r"\b(when|until|regardless|that|because|whether|while|if|so\s+that|in\s+order\s+to)\b", re.I)
_NEG_IN_PRED = re.compile(r"\b(not|never|cannot)\b|n't\b", re.I)


# 系动词 be 家族：表内主名是 `is`，这里只用于**词形比对**（护栏要认 are / was ↔ be 是同一形态）。
_BE_FORMS = {"be", "is", "are", "was", "were", "am", "been", "being"}


def _needs_clean(pred: str) -> bool:
    """哪些谓词要清洗：≥4 词 / 含并列 / 含从句标记 / 含否定 / 含情态词。"""
    p = pred or ""
    return bool(
        len(p.split()) >= 4
        or re.search(r"\b(and|or)\b", p, re.I)
        or _CLAUSE_IN_PRED.search(p)
        or _NEG_IN_PRED.search(p)
        or re.search(r"\b(can|could|may|might|should|must|will|would)\b", p, re.I)
    )


# 常见不规则动词（过去式/过去分词 → 原形）：护栏要用它认"kept→keep / made→make / done→do"。
# 与 SINGULAR_KEEP / IRREGULAR_PLURALS 同类：确定性的语言知识，封闭可枚举。
IRREGULAR_VERBS: dict[str, str] = {
    "kept": "keep", "made": "make", "done": "do", "did": "do", "known": "know", "knew": "know",
    "has": "have", "had": "have", "does": "do", "goes": "go", "went": "go", "gone": "go",
    "driven": "drive", "drove": "drive", "written": "write", "wrote": "write",
    "taken": "take", "took": "take", "given": "give", "gave": "give", "found": "find",
    "held": "hold", "ran": "run", "came": "come", "became": "become", "seen": "see", "saw": "see",
    "got": "get", "sent": "send", "built": "build", "lost": "lose", "met": "meet", "paid": "pay",
    "said": "say", "shown": "show", "spent": "spend", "told": "tell", "thought": "think",
    "understood": "understand", "led": "lead", "left": "leave", "brought": "bring",
    "caught": "catch", "chose": "choose", "chosen": "choose", "dealt": "deal",
    "fell": "fall", "fallen": "fall", "fed": "feed", "fought": "fight",
    "grew": "grow", "grown": "grow", "hidden": "hide", "begun": "begin", "began": "begin",
    "broken": "break", "broke": "break", "bought": "buy", "drawn": "draw", "drew": "draw",
    "eaten": "eat", "ate": "eat", "felt": "feel", "forgotten": "forget", "forgot": "forget",
    "heard": "hear", "meant": "mean", "risen": "rise", "rose": "rise", "sold": "sell",
    "sat": "sit", "slept": "sleep", "spoken": "speak", "spoke": "speak", "stood": "stand",
    "taught": "teach", "worn": "wear", "wore": "wear", "won": "win",
}


def _stem_variants(w: str) -> set[str]:
    """一个词的**可能词干集合**（供"子序列"校验：placed↔place、delegates↔delegate、becomes↔become）。

    教训：早先只取单一词干（placed→plac）导致 3/4 条合法清洗被护栏误拒——词形要按"变体集合"比，
    而不是"折成一个词"。
    """
    w = w.lower()
    out = {w}
    if w in _BE_FORMS:
        out |= _BE_FORMS            # are / is / was / were / been … 都算 be 的形态
    if w in IRREGULAR_VERBS:
        out.add(IRREGULAR_VERBS[w])  # kept → keep / made → make / done → do
    if w.endswith("ies") and len(w) > 4:
        out.add(w[:-3] + "y")
    if w.endswith("ied") and len(w) > 4:
        out.add(w[:-3] + "y")        # applied → apply
    if w.endswith("es") and len(w) > 3:
        out.add(w[:-2])
    if w.endswith("s") and len(w) > 3:
        out.add(w[:-1])
    if w.endswith("ing") and len(w) > 5:
        out.update({w[:-3], w[:-3] + "e"})
        if w[-4] == w[-5]:          # running → run
            out.add(w[:-4])
    if w.endswith("ed") and len(w) > 3:
        out.update({w[:-2], w[:-1]})   # placed → plac / place ✓
    return out


def _is_subsequence(clean: str, original: str) -> bool:
    """"只删不改"校验：clean 的词必须是 original 词序列的子序列（按词干变体比）。"""
    a = [_stem_variants(w) for w in re.findall(r"[A-Za-z0-9\-']+", clean or "")]
    b = [_stem_variants(w) for w in re.findall(r"[A-Za-z0-9\-']+", original or "")]
    if not a:
        return False
    i = 0
    for target in a:
        while i < len(b) and not (target & b[i]):
            i += 1
        if i >= len(b):
            return False
        i += 1
    return True


async def _clean_predicates(
    client: DeepSeekLLMClient,
    source_id: str,
    claims: list[dict],
    clean_t: str,
    *,
    batch: int = 60,
) -> dict:
    """谓词清洗（归一步的预处理）：糊谓词 → 干净动词原形。只改谓词，不动主宾/引文。

    三条护栏：① 只改 predicate 字段；② 结果必须是原谓词的**子序列**（只删不改）；③ 被拒则保留原谓词并记账。
    为什么要排在"归一"之前：`should not be implicitly trusted` 这类整句谓词若直接强归，会被静默塞进
    某条宽关系（悄悄错）；先清洗成 `trusted` + polarity 才是对的。
    """
    targets = [i for i, c in enumerate(claims) if _needs_clean(c.get("predicate") or "")]
    items = []
    for i in targets:
        c = claims[i]
        items.append(
            {
                "claim_idx": i,
                "subject": c.get("subject"),
                "predicate": c.get("predicate"),
                "object": c.get("object") or "",
                **_anchor_window(c, clean_t),
            }
        )

    got: dict[int, dict] = {}

    async def ask(chunk: list[dict], label: str) -> None:
        part, _ = await _chat_json(
            client,
            [
                LLMMessage(role="system", content=PASS1_CLEAN_SYSTEM),
                LLMMessage(role="user", content=json.dumps(chunk, ensure_ascii=False, indent=1)),
            ],
            label=label,
        )
        for m in part.get("cleaning") or []:
            try:
                idx = int(m.get("claim_idx"))
            except (TypeError, ValueError):
                continue
            if m.get("predicate_clean"):
                got[idx] = m

    batches = [items[i : i + batch] for i in range(0, len(items), batch)]
    for bi, chunk in enumerate(batches, start=1):
        await ask(chunk, f"谓词清洗 {source_id} {bi}/{len(batches)}")
        for retry in range(1, 3):
            missing = [it for it in chunk if it["claim_idx"] not in got]
            if not missing:
                break
            print(f"  [补问] 谓词清洗 第 {bi} 批缺 {len(missing)} 条，第 {retry} 次补问")
            await ask(missing, f"谓词清洗 {source_id} {bi}-补{retry}")

    cleaned = rejected = noop = 0
    for i in targets:
        m = got.get(i)
        c = claims[i]
        if not m:
            continue
        cand = str(m["predicate_clean"]).strip()
        if not _is_subsequence(cand, c.get("predicate") or ""):
            c["predicate_clean_rejected"] = cand  # 记账：模型想换成别的词，被护栏拒了
            rejected += 1
            continue
        if cand.lower() == (c.get("predicate") or "").strip().lower():
            noop += 1
            continue
        c["predicate_clean"] = cand
        if str(m.get("polarity") or "").lower() == "negative":  # 只在否定时写；"positive" 是默认值，不落库
            c["polarity"] = "negative"
        cleaned += 1
    return {"targets": len(targets), "cleaned": cleaned, "rejected": rejected, "noop": noop}


async def _normalize_predicates(
    client: DeepSeekLLMClient,
    source_id: str,
    claims: list[dict],
    clean_t: str,
    *,
    batch: int = 60,
) -> dict:
    """谓词归一（独立小步）：Pass 1 的原文动词短语 → 受控关系表 id。只映射，不增删 claim。

    结果写回 claim：`predicate_normalized`（受控 id）、必要时 `polarity` / `predicate_nearest`；
    原文用词保留在 `predicate` 里（审计 + 事后收敛用）。

    输入 = 每条断言的 **锚句 ± 邻句**（用短锚在 clean_t 里定位后**由程序无损切出**）。
    短锚本身是为"LLM 复制保真 + 能否定位"设计的，信息量不足以判关系；但补上下文这一步
    不该再让 LLM 复制（越长越容易重建失真），所以由程序按锚切。
    """
    items = []
    for i, c in enumerate(claims):
        # 清洗后的谓词优先（谓词清洗是归一的预处理）；原文用词仍在 c["predicate"] 里保留
        pred_for_map = c.get("predicate_clean") or c.get("predicate")
        win = _anchor_window(c, clean_t)
        items.append(
            {
                "claim_idx": i,
                "subject": c.get("subject"),
                "predicate": pred_for_map,
                "object": c.get("object") or "",
                **win,
            }
        )

    mapping: dict[int, dict] = {}

    async def ask(chunk_items: list[dict], label: str) -> None:
        user = json.dumps(chunk_items, ensure_ascii=False, indent=1)
        part, _ = await _chat_json(
            client,
            [LLMMessage(role="system", content=PASS1_NORM_SYSTEM), LLMMessage(role="user", content=user)],
            label=label,
        )
        for m in part.get("mapping") or []:
            try:
                idx = int(m.get("claim_idx"))
            except (TypeError, ValueError):
                continue
            if m.get("predicate"):
                mapping[idx] = m

    batches = [items[i : i + batch] for i in range(0, len(items), batch)]
    chars_in = 0
    for i, chunk in enumerate(batches, start=1):
        chars_in += len(json.dumps(chunk, ensure_ascii=False))
        await ask(chunk, f"谓词归一 {source_id} {i}/{len(batches)}")
        for retry in range(1, 3):
            missing = [it for it in chunk if it["claim_idx"] not in mapping]
            if not missing:
                break
            print(f"  [补问] 谓词归一 第 {i} 批缺 {len(missing)} 条，第 {retry} 次补问")
            await ask(missing, f"谓词归一 {source_id} {i}-补{retry}")

    # 三态归类（决定"图里收什么"）：
    #   mapped  = 直接归到表内关系
    #   forced  = 造了表外词，但给了 predicate_nearest → 用它强归（模型的锅，不是表缺口）
    #   pending = 表外词且没给 nearest（或给的不是表内 id）→ 待定：不进图、报告列出、供定期收敛
    for i, c in enumerate(claims):
        m = mapping.get(i) or {}
        word = str(m.get("predicate") or "").strip()
        nearest = str(m.get("predicate_nearest") or "").strip()
        snapped = _snap_relation(word)
        if word in RELATIONS:
            c["predicate_normalized"] = word
            c["predicate_status"] = "mapped"
        elif snapped:
            # 词形/时态变体（becomes→become）：确定性折回表内，算 mapped，记来源供审计
            c["predicate_normalized"] = snapped
            c["predicate_status"] = "mapped"
            c["predicate_snapped_from"] = word
        elif nearest in RELATIONS:
            c["predicate_normalized"] = nearest
            c["predicate_status"] = "forced"
            c["predicate_invented"] = word or "(空)"
            c["predicate_nearest"] = nearest
        else:
            c["predicate_status"] = "pending"
            c["predicate_invented"] = word or "(空)"
        if str(m.get("polarity") or "").lower() == "negative":  # 只在否定时写
            c["polarity"] = "negative"
        if m.get("sign"):
            c["sign"] = m["sign"]

    kinds = {c["predicate_normalized"] for c in claims if c.get("predicate_normalized")}
    pending = [c for c in claims if c.get("predicate_status") == "pending"]
    return {
        "mapped": sum(1 for c in claims if c.get("predicate_status") == "mapped"),
        "forced": sum(1 for c in claims if c.get("predicate_status") == "forced"),
        "pending": len(pending),
        "pending_words": sorted({c.get("predicate_invented") for c in pending}),
        "invented": sorted({c.get("predicate_invented") for c in claims if c.get("predicate_invented")}),
        "total": len(claims),
        "kinds": sorted(kinds),
        "outside": sorted(k for k in kinds if k not in RELATIONS),
        "no_window": sum(1 for c in claims if not _anchor_window(c, clean_t)["anchor_sentence"]),
        "avg_window": int(sum(len(it["anchor_sentence"]) + len(it["context_before"]) + len(it["context_after"]) for it in items) / max(1, len(items))),
        "batches": len(batches),
        "chars_in": chars_in,
    }


async def _chat_json(
    client: DeepSeekLLMClient,
    messages: list[LLMMessage],
    *,
    label: str = "",
    retries: int = 3,
) -> tuple[dict, str]:
    """调用 LLM 并解析出 JSON 对象；输出畸形/被截断时重试。返回 (对象, 原始输出文本)。"""
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        msgs = list(messages)
        if attempt > 1:
            msgs.append(
                LLMMessage(
                    role="user",
                    content="上次输出不是合法 JSON（可能被截断或夹带多余文字）。请重新输出一个完整、合法的 "
                    "JSON 对象：无代码围栏、无解释文字，括号与引号闭合。",
                )
            )
        resp = await client.chat(msgs)
        try:
            return _extract_json(resp.content), resp.content
        except Exception as e:  # JSON 畸形 / 被截断
            last_err = e
            print(f"[B6] {label or 'LLM'}: JSON 解析失败（第 {attempt}/{retries} 次）：{e}")
    raise RuntimeError(f"{label or 'LLM'}: 重试 {retries} 次仍无法解析 JSON") from last_err


async def _pass2_extract(
    client: DeepSeekLLMClient,
    source_id: str,
    batch: list[dict],
    stats: dict,
    label: str,
) -> dict:
    """抽取一批块（entities + edges）；解析持续失败时把批拆半重试（防单批过大被截断）。"""
    user2 = (
        f"source_id={source_id}；asserted_by={source_id}。\n按块组织的断言清单：\n"
        + json.dumps(batch, ensure_ascii=False, indent=2)
    )
    stats["in"] += len(user2)
    try:
        part, raw = await _chat_json(
            client,
            [LLMMessage(role="system", content=PASS2_STEP1_SYSTEM), LLMMessage(role="user", content=user2)],
            label=label,
        )
    except Exception:
        if len(batch) <= 1:
            raise
        mid = len(batch) // 2
        print(f"[B6] {source_id}: {label} 解析持续失败，拆半重试（{len(batch)} → {mid}+{len(batch) - mid} 块）")
        a = await _pass2_extract(client, source_id, batch[:mid], stats, f"{label}a")
        b = await _pass2_extract(client, source_id, batch[mid:], stats, f"{label}b")
        return {
            "entities": a.get("entities", []) + b.get("entities", []),
            "edges": a.get("edges", []) + b.get("edges", []),
        }
    stats["out"] += len(raw)
    return part


def _a4(bundle: dict, clean_t: str, chunks: list[dict], source_id: str, file_idx: int) -> dict:
    """引文锚定：对每条 statement，逐段定位 evidence_texts，取并集得 evidence_chunks。"""
    for st in bundle.get("statements", []):
        quotes = st.get("evidence_texts")
        if isinstance(quotes, str):  # 兼容旧版单字符串输出
            quotes = [quotes]
        if not (isinstance(quotes, list) and quotes):
            old = st.get("evidence_text")
            quotes = [old] if isinstance(old, str) and old else []
        quotes = [q for q in quotes if isinstance(q, str) and q]
        if not quotes:
            st["clean_ref"] = {"source_id": source_id, "file_idx": file_idx, "start_pos": None, "end_pos": None}
            st["anchor"] = "section"
            st["evidence_chunks"] = []
            st["a4_note"] = "无 evidence_texts"
            continue

        ranges: list[tuple[int, int]] = []
        failed: list[str] = []
        for q in quotes:
            pos = _find_raw(q, clean_t)
            if pos is None:
                failed.append(q[:28])
            else:
                ranges.append(pos)

        if not ranges:
            st["clean_ref"] = {"source_id": source_id, "file_idx": file_idx, "start_pos": None, "end_pos": None}
            st["anchor"] = "section"
            st["evidence_chunks"] = []
            st["a4_note"] = "未定位到任何分段（全部降级 section 弱锚）：" + "; ".join(failed)
        else:
            st["clean_ref"] = {"source_id": source_id, "file_idx": file_idx, "start_pos": min(r[0] for r in ranges), "end_pos": max(r[1] for r in ranges)}
            st["anchor"] = None
            hits: set[str] = set()
            for r in ranges:
                hits.update(_overlap(r[0], r[1], chunks))
            st["evidence_chunks"] = sorted(hits)
            ext = ("；分段降级:" + "; ".join(failed)) if failed else ""
            if st["evidence_chunks"]:
                st["a4_note"] = "OK" + ext
            else:
                st["a4_note"] = "定位到但未重叠任何 chunk（检查偏移空间）" + ext
    return bundle


def _build_pass2_blocks(claims: list[dict], clean_t: str, chunks: list[dict]) -> list[dict]:
    """按"锚句所在块"把 claims 分组为 Pass 2 输入块。

    block = {chunk_id, context(块文本), claims:[{claim_idx, subject, predicate, object, roles?}]}。
    每个块文本只出现一次（去重）；claim 不带短锚（块文本已含）。未定位的 claim 归入孤立块。
    """
    order = {ch["chunk_id"]: ch["start_pos"] for ch in chunks}
    blocks: dict[str, dict] = {}
    orphan: dict = {"chunk_id": "_orphan", "context": "", "claims": []}
    for ci, c in enumerate(claims):
        # 图里只收 mapped / forced 的断言：待定（谓词归不进受控关系）不生成边
        if c.get("predicate_status") == "pending":
            continue
        cid: str | None = None
        for e in c.get("evidence_texts", []):
            p = _find_raw(e, clean_t)
            if p is None:
                continue
            for ch in chunks:
                if ch["start_pos"] < p[1] and ch["end_pos"] > p[0]:
                    cid = ch["chunk_id"]
                    break
            if cid:
                break
        if cid and cid not in blocks:
            ch = next(x for x in chunks if x["chunk_id"] == cid)
            blocks[cid] = {"chunk_id": cid, "context": clean_t[ch["start_pos"]:ch["end_pos"]], "claims": []}
        blk = blocks[cid] if cid else orphan
        item = {
            "claim_idx": ci,
            "subject": c.get("subject"),
            "predicate": c.get("predicate_normalized") or c.get("predicate"),  # 边用受控关系；无归一时回落原文
            "object": c.get("object"),
        }
        if c.get("predicate"):
            item["predicate_surface"] = c["predicate"]
        if c.get("polarity"):
            item["polarity"] = c["polarity"]
        if c.get("roles"):
            item["roles"] = c["roles"]
        blk["claims"].append(item)
    out = sorted(blocks.values(), key=lambda b: order.get(b["chunk_id"], 10 ** 9))
    if orphan["claims"]:
        out.append(orphan)
    return out


async def _compile_source(
    source_id: str,
    api_key: str,
    model: str,
    token_limit: int = 12000,
    pass1_window_chars: int = 5000,
    only_pass1: bool = False,
    tag: str = "",
    pass2_from: str = "",
    dedup: str = "strict",
) -> dict | None:
    """对单个源做整篇编译（一个内容单元），返回 bundle（含引文锚定结果）；源缺失返回 None。"""
    manifest = parse_manifest(MANIFEST_PATH)
    src = next((s for s in manifest if s.source_id == source_id), None)
    if src is None:
        print(f"[B6] 源 {source_id} 不在 manifest（{MANIFEST_PATH}）")
        return None

    units = _file_units(src)  # [(file_title, sections)]
    if not units:
        print(f"[B6] {source_id}: 未切出任何内容")
        return None
    if len(units) > 1:
        print(f"[B6] {source_id}: 多文件源（{len(units)} 文件），本切片仅编译第 0 个文件")

    file_title, sections = units[0]
    clean_t = _build_clean_t(sections)
    if len(clean_t) > token_limit * 3.5:  # 粗略：字符约为 token 的 3.5 倍，超限则提醒
        print(f"[B6] {source_id}: 内容单元约 {len(clean_t)} 字符，可能超 {token_limit} token，请检查")

    kb = KbStore(KB_DB)
    chunks = [c for c in kb.list_chunk_offsets(source_id) if c.get("file_idx", 0) == 0]

    content_unit = {
        "source_id": source_id,
        "file_idx": 0,
        "section_path": "/".join([s.path for s in sections if s.path]),
        "clean_start": 0,
        "clean_end": len(clean_t),
        "text": clean_t,
    }
    client = DeepSeekLLMClient(api_key=api_key, model=model)

    if pass2_from:
        cf = OUT_DIR / f"b6_claims_{source_id}_{pass2_from}.json"
        claims: list[dict] = json.loads(cf.read_text(encoding="utf-8"))
        print(f"[B6] {source_id}: Pass2-only，载入 claims {len(claims)} 条（{cf.name}）")
    else:
        # Pass 1：按窗口抽原子断言 + 逐字短锚（不判类型；窗口小 → 输出不超限 + 省输出 token）
        windows = _window_text(clean_t, pass1_window_chars)
        claims = []
        for wi, w in enumerate(windows):
            u1 = (
                f"内容单元窗口：source_id={source_id}；file_idx=0；window={wi + 1}/{len(windows)}；"
                f"section_path={content_unit['section_path']}。\n下面是该窗口清洗后原文，请抽原子断言：\n\n" + w
            )
            part1, _ = await _chat_json(
                client,
                [LLMMessage(role="system", content=PASS1_SYSTEM), LLMMessage(role="user", content=u1)],
                label=f"Pass1 {source_id} 窗口{wi + 1}/{len(windows)}",
            )
            claims.extend(part1.get("claims", []))
        print(f"[B6] {source_id}: Pass1 窗口 {len(windows)} 个 / 输入 {len(clean_t)} 字符 / claims {len(claims)} 条")
        # B8 结构校验/归一：去空 roles、剔除缺必填字段(subject/predicate)的 claim
        _before = len(claims)
        norm: list[dict] = []
        for c in claims:
            if not c.get("roles"):
                c.pop("roles", None)  # 空 roles 不落
            if c.get("subject") and c.get("predicate"):  # object 可选（一元断言）
                norm.append(c)
        dropped = _before - len(norm)
        claims = norm
        if dropped:
            print(f"[B6] {source_id}: 结构校验剔除 {dropped} 条缺必填字段的 claim（剩余 {len(claims)}）")
        # 谓词体检（Pass 1 质量门）：长谓词 / 否定混入 / 从句混入 / 表外新词
        v = _validate_claims(claims)
        print(
            f"[B6] {source_id}: Pass1 谓词体检 谓词种数={v['pred_kinds']}"
            f"（表内 {v['in_table_kinds']} / 表外 {v['outside_kinds']}）"
            f" | 长谓词={len(v['long_pred'])} 否定混入={len(v['neg_in_pred'])}"
            f" 从句混入={len(v['clause_in_pred'])} 缺宾语且长={len(v['empty_obj_long'])}"
        )
        if v["outside"]:
            print(f"[B6] {source_id}: 表外新词（前 10）：{sorted({p for _, p in v['outside']})[:10]}")
        if v["long_pred"]:
            print(f"[B6] {source_id}: 长谓词样例（前 5）：{[s for _, s in v['long_pred'][:5]]}")
        # 谓词归一（独立小步，只映射不增删）——输入是"锚句 ± 邻句"（由程序按短锚无损切出）
        nv = await _normalize_predicates(client, source_id, claims, clean_t)
        print(
            f"[B6] {source_id}: 谓词归一 {nv['mapped']}/{nv['total']} 条"
            f" | 关系种数={len(nv['kinds'])} | 表外新词={len(nv['outside'])}"
            f" | 无锚 {nv['no_window']} | 锚窗均长 {nv['avg_window']} 字符"
            f" | {nv['batches']} 批 / 输入 {nv['chars_in']} 字符"
            + (f" {nv['outside'][:8]}" if nv["outside"] else "")
        )
        suffix = f"_{tag}" if tag else ""
        claims_file = OUT_DIR / f"b6_claims_{source_id}{suffix}.json"
        claims_file.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")
        if only_pass1:
            print(f"[B6] {source_id}: 仅 Pass1，claims 落盘 {claims_file}")
            return None

    # Pass 2 · 步骤①「抽取」：块(context + claims) → entities + edges（不去重、不分类）
    blocks = _build_pass2_blocks(claims, clean_t, chunks)
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_n = 0
    for blk in blocks:
        n = len(blk["claims"])
        if cur and cur_n + n > PASS2_CLAIM_BUDGET:
            batches.append(cur)
            cur, cur_n = [], 0
        cur.append(blk)
        cur_n += n
    if cur:
        batches.append(cur)
    print(f"[B6] {source_id}: Pass2①抽取 输入 {len(blocks)} 块 / {len(claims)} claims / {len(batches)} 批")
    entities: list[dict] = []
    edges: list[dict] = []
    stats = {"in": 0, "out": 0}
    for bi, batch in enumerate(batches, start=1):
        part = await _pass2_extract(client, source_id, batch, stats, f"Pass2① {source_id} 批{bi}/{len(batches)}")
        entities.extend(part.get("entities", []))
        edges.extend(part.get("edges", []))
    p2_in, p2_out = stats["in"], stats["out"]
    print(
        f"[B6] {source_id}: Pass2①抽取 字符 输入={p2_in} 输出={p2_out} 比例 in:out={p2_in / max(1, p2_out):.1f}:1"
        f" | entities(原样)={len(entities)} edges={len(edges)}"
    )

    # 步骤②归并/消解、③分类：待实现（先只做①抽取）
    bundle: dict = {"entities": entities, "edges": edges, "content_unit": content_unit, "source_id": source_id}
    return bundle


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="B6 最小编译切片：O2/A5 编译层真实跑批（不向量入库）")
    parser.add_argument("--source", default="O2,A5", help="逗号分隔的源 id")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--token-limit", type=int, default=12000)
    parser.add_argument("--pass1-window-chars", type=int, default=5000)
    parser.add_argument("--only-pass1", action="store_true", help="只跑 Pass 1 并落盘 claims，不做 Pass 2")
    parser.add_argument("--tag", default="", help="输出文件后缀标签（区分多遍跑批）")
    parser.add_argument("--pass2-from", default="", help="Pass2-only：从 b6_claims_<source>_<tag>.json 载入 claims，跳过 Pass 1")
    parser.add_argument("--dedup", default="strict", choices=["strict", "loose"], help="Pass2 去重法：strict(从严/grounded) / loose(凭常识，旧)")
    args = parser.parse_args()

    settings = SqliteSettingStore(APP_DB).get_llm_settings()
    if not settings.api_key:
        sys.exit("[B6] 未配置大模型 API Key（设置面板配置），无法编译")
    model = args.model or settings.model or DEFAULT_MODEL

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for sid in [s.strip() for s in args.source.split(",") if s.strip()]:
        bundle = await _compile_source(
            sid, settings.api_key, model, args.token_limit, args.pass1_window_chars, args.only_pass1, args.tag, args.pass2_from, args.dedup
        )
        if bundle is None:
            continue
        suffix = f"_{args.tag}" if args.tag else ""
        out = OUT_DIR / f"b6_{sid}{suffix}.json"
        out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        results[sid] = str(out)
        stmts = bundle.get("statements", [])
        n_ok = sum(1 for s in stmts if s.get("a4_note") == "OK")
        print(
            f"[B6] {sid}: 实体 {len(bundle.get('entities', []))} / 边 {len(bundle.get('edges', bundle.get('relations', [])))} "
            f"/ 陈述 {len(stmts)} / 定位成功 {n_ok} → {out}"
        )
    if not results:
        sys.exit("[B6] 没有产出 bundle")


if __name__ == "__main__":
    asyncio.run(main())
