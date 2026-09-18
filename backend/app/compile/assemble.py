"""S5 · 图组装：**端点规范化（LLM）+ 建边（程序）**。

职责切分（2026-09-17 重构，沛东定调"组图是确定性的活，不该给大模型做"）：
  · **LLM 只做一件事**：把 claim 的端点（不规范的短语）转成"可点名的名词概念"（能自然问"什么是 X？"）；
  · **组图交给程序**：一条 claim 至多一条边（列表早在 Pass 1 就拆开了）、方向由被动规则定、
    roles / polarity 从 claim 带过来、claim_idx 恒定——**这些都不该由 LLM 决定**；
  · **不出别名**：同指判定属 S6（形归并 / 义归并 + 候选生成），S5 手里没有"这两个名字同指"的判据。

为什么这么切（实测依据，2026-09-17，旧形态 = 让 LLM 直接输出 entities+edges）：
  · O2 的 314 条 claim 里 **133 条（42%）被模型自己决定不建边、且完全不记账**；A5 是 170/379（45%）；
  · 有 16 条边是"一条 claim 建了 2–3 条"（模型自行拆分）；
  · 出现 36–44 个"声明了却没被任何边引用"的孤立实体（含从块原文顺手抽的概念）。
  病根是同一个：**让 LLM 决定"图长什么样"**。改成"LLM 只出端点名、程序组图"后，这四类问题在结构上
  不可能发生，且**每条 claim 的处置都可对账**（建边 / 因某端取不出概念丢弃（带原因）/ 谓词待定）。

另外两条口径（同轮定）：
  · **不给 LLM 块原文**——需要上下文的定指端点已在 S4.5 处理；给了反而诱导它从原文里抽概念。实测输入可省 ~30%；
  · **S4.5 已解出的端点不再送去规范化**（它已经是可点名概念），直接采用。

流程：
  claims ──程序：跳 pending / 取端点（resolved 优先）/ 标记被动翻转 / 分批──> 批次
         ──LLM：每个待规范端点给一个概念名 或 null──> 映射
         ──程序：端点护栏 → 建边 或 丢弃记账──> entities + edges + 对账表
"""

from __future__ import annotations

import collections
import json
import re

from app.agent.llm import DeepSeekLLMClient, LLMMessage
from app.compile.merge import form_key
from scripts.compile_slice_b6 import _chat_json, _find_raw, _norm

PRONOUNS = {"this", "that", "these", "those", "such", "it", "they", "them", "there", "we", "our", "us"}
CLAUSEISH = re.compile(r"^(how to|whether|when|why|that|which|who|what)\b", re.I)
MAX_NAME_WORDS = 6
STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "with", "by", "at", "as", "its"}

# Unicode 标点归一（**只用于 grounding 比对**），两类必须分开处理——混在一起会出错：
#  · 词内连字符（U+2010 HYPHEN / U+2011 NB-HYPHEN / U+2012 FIGURE DASH）→ ASCII `-`：`high‑utility` ≡ `high-utility`
#  · 破折号 / 减号（U+2013 EN / U+2014 EM / U+2015 HORIZONTAL BAR / U+2212 MINUS）→ **空格**（分隔）
#    实测踩坑：把 em dash 也折成 `-`，`environment—high-utility` 被粘成 `environment-high-utility`，
#    于是 `utility` 从词表里消失 → 名字与原文一字不差却被判"疑似自造"。
_UNI_HYPHEN = re.compile(r"[\u2010\u2011\u2012]")
_DASH_SEP = re.compile(r"[\u2013\u2014\u2015\u2212]")


def _fold_text(s: str) -> str:
    """Unicode 标点归一：词内连字符→`-`、破折号→空格、删软连字符。"""
    s = (s or "").replace("\u00ad", "")
    return _DASH_SEP.sub(" ", _UNI_HYPHEN.sub("-", s))

# 被动式识别（表语 + 过去分词 + **显式 by**）：这类 claim 的 subject 是**受事**，
# 归一步把它映射成主动关系（written by → creates）后，**建边时必须交换两端**，否则语义颠倒。
_PASSIVE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|has been|have been|had been|get|gets|got)\s+\w+(?:ed|en)\b[^.]{0,40}\bby\b",
    re.I,
)
# 状态族：形式像被动，但语义方向本来就这样（X is located in Y ≠ Y locates X）→ **不翻转**
STATEFUL_RELATIONS = {
    "located_in", "visible_to", "part_of", "outside_of", "runs_in", "similar_to",
    "different_from", "comes_from", "before", "after", "has_part", "has_property", "is", "is_a",
}

PROMPT = """你是「端点规范化器」。输入是一批**断言端点**（每条含 idx、raw＝原文里的端点短语，
以及它所在断言的三件套与归一后的谓词）。任务：把每个 raw 端点转成
**一个「可点名的名词概念」**（判据：能自然地问"什么是 X？"），只输出 JSON。

# 规则
1. **只依据本端点的 raw**：概念里的实词取自该端点的 raw 文本；
   **不得从同一断言的另一端（subject / object 的另一侧）借词**——那是串味，会把两端说成同一个东西。
2. **可以删**：冠词、所有格、纯语法框架、从句引导词。**删掉后所指必须不变**才算合格。
3. **只保留必要的限定词**：删掉会改变所指的必须保留；纯修饰、删掉不改变所指的可以删。
   **不要把 raw 整段照搬**——输出要以中心概念为主。
4. **可以概括**：把 raw 里的一串并列或一段描写概括成一个上位词是允许的；
   但**必须在 `derived_from` 里列出取自 raw 的词**（便于回溯；非概括时给空数组）。
5. **写成一个术语式名词短语**（要能当检索键用）：去冠词 / 改单复数 / 统一大小写。
   目标是一个**词条名**——用户会拿它去检索的那个词。**如果你写出来的名字读起来像一句话或一段描述，
   就说明还没概括到位**，回去把它压成词条名（列举、从句、纯修饰一律去掉）。
6. **取不出干净概念 → null**：整段是命题、纯动作或纯描述，且取不出可点名概念时给 null，别硬造；拿不准同样给 null。
7. **用 raw 的语言输出**（本语料是英文）：不要把概念名翻译成中文或别的语言。

# 输出（严格 JSON；无多余文字、无代码围栏）
{"endpoints":[{"idx":0,"concept":"…","derived_from":["…"]}]}
concept 为 null 表示"这段端点取不出可点名的概念"；derived_from 列出构成该概念的 raw 词（无概括时给 []）。
idx 必须原样回抄。
"""


# ---------- 文本与判据 ----------

def _fold(w: str) -> str:
    """词形折叠（只用于"名字是否出自原文"的比对，不用于建实体）——复用 S6 的形归一。"""
    return form_key(w) or w


def _name_grounded(name: str, text_words: set[str]) -> bool:
    """名字是否**出自原文**：实词（去停用词、词形折叠）都要在原文里出现过。

    不用"逐字子串"：会误伤 `doc-gardening agent`（原文带引号）、`repository-local versioned artifact`
    （原文是 `Repository-local, versioned artifacts`，逗号+复数）。逐词比对能容纳这类形态差异，
    又能挡住"完全自造的名字"（词都不在原文里）。
    """
    words = [w for w in re.findall(r"[a-z0-9\-]+", _fold_text(name).lower()) if w not in STOPWORDS]
    if not words:
        return False
    # 两侧都折一次 Unicode 连字符（调用方已折过也无害）——避免"码位不同判成两个词"
    tw = {_fold_text(x) for x in text_words}
    return all(w in tw or _fold(w) in tw for w in words)


def _words_of(text: str) -> set[str]:
    """一段文本里的实词集合（含词形折叠），用于**端点级**接地判断。"""
    out: set[str] = set()
    for w in re.findall(r"[a-z0-9\-]+", _fold_text(text).lower()):
        out.add(w)
        out.add(_fold(w))
    return out


def _content_words(text: str) -> list[str]:
    """文本里的实词（去停用词、折 Unicode 标点），用于档位判定。"""
    return [w for w in re.findall(r"[a-z0-9\-]+", _fold_text(text).lower()) if w not in STOPWORDS]


def _stem4(a: str, b: str) -> bool:
    """词形派生判据：共有前缀 ≥4 个字符（`audit`/`auditability`、`structur`/`structural`）。

    故意宽松——它只决定"算不算词形派生（要不要进待抽检）"，**不用于合并**，代价可控。
    """
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n >= 4 and min(len(a), len(b)) >= 4


def classify_name_tier(name: str, raw: str, derived_from: list[str] | None = None) -> str:
    """端点名的**四档判定**（2026-09-18 定；取代原来单一的"整篇文档 grounding"）。

      · `ok`           A 档：概念的实词**全部**出自本端点 raw（含单复数/大小写/Unicode 连字符折叠）
      · `derived`      B 档：概念实词与本端点 raw 的某词**共有 ≥4 字符前缀**（词形派生，如 auditable→auditability）
      · `generalized`  C 档：给了 `derived_from`，且其中**逐词**都出自本端点 raw（概括/名词化，进待抽检）
      · `reject`       D 档：三者都不沾 → 疑似自造（拒）

    为什么需要分档：`可以概括`（沛东定的）与`不许造词`天然冲突——
    分级后"概念可以是新词，但必须说得出它由 raw 的哪些词而来"，既可核验又不误伤合法概括。
    """
    raw_words = _words_of(raw)
    words = _content_words(name)
    if words and all(w in raw_words or _fold(w) in raw_words for w in words):
        return "ok"
    if words and all(any(_stem4(w, r) for r in raw_words) for w in words):
        return "derived"
    df = [x for x in (derived_from or []) if isinstance(x, str) and x.strip()]
    if df:
        all_grounded = True
        for item in df:
            item_words = _content_words(item)
            if not item_words:
                all_grounded = False
                break
            for w in item_words:
                if w not in raw_words and _fold(w) not in raw_words:
                    all_grounded = False
                    break
            if not all_grounded:
                break
        if all_grounded:
            return "generalized"
    return "reject"


def cross_endpoint_bleed(name: str, raw_self: str, raw_other: str) -> bool:
    """**串味检测（逐词）**：概念里**有词**出自"同一 claim 另一端 raw"、而本端点 raw 里没有。

    实测错案（2026-09-18）：
      `Every function reachable through any domain on an allowlist | is | an attack surface`
      → 主语被归一成 `attack surface`（宾语的概念）→ 两端同名 → 自环。
    为什么现有两道护栏拦不住：①「可点名」查的是**归一后**的名字；②「不造名」查的是**整篇文档**
    （`attack surface` 当然在文档里）。缺的正是这条**端点级**判断。

    为什么用"另一端能找到"而不是"本端找不到就拒"：后者会误伤合法的**概括**
    （`tooling ← tools, abstractions`：tooling 在本端点不字面存在，但那是允许的取法）。

    **必须逐词判**（2026-09-18 二次修正）：早期版本要求"名字**全部**词都能在另一端找到"，
    于是 `allowlisted domain attack surface`（4 词里只有 attack/surface 来自宾语）**漏判**——
    它正是压缩重试引入的那条错案。改成逐词后：
    `attack`/`surface` 在另一端有、在本端没有 → 判串味。
    """
    if not name.strip():
        return False
    self_words = _words_of(raw_self)
    other_words = _words_of(raw_other)
    words = _content_words(name)
    if not words:
        return False
    if all(w in self_words or _fold(w) in self_words for w in words):
        return False                     # 全部出自本端点 → 不是串味
    borrowed = [
        w for w in words
        if (w in other_words or _fold(w) in other_words)
        and not (w in self_words or _fold(w) in self_words)
    ]
    return bool(borrowed)


def valid_entity_name(name: str) -> tuple[bool, str]:
    """端点名的**可点名**判据（程序侧，可枚举）。返回 (是否合格, 原因)。"""
    n = (name or "").strip()
    if not n:
        return False, "空"
    low = n.lower().strip(" .")
    if low in PRONOUNS:
        return False, "代词"
    if CLAUSEISH.match(low):
        return False, "从句式"
    if len(n.split()) > MAX_NAME_WORDS:
        return False, f"超过 {MAX_NAME_WORDS} 词"
    if not re.search(r"[A-Za-z]", n):
        return False, "无字母"
    return True, ""


def should_flip(claim: dict) -> bool:
    """被动式且关系不是状态族 → 建边时交换 subject / object（确定性规则，可审计）。"""
    pn = (claim.get("predicate_normalized") or "").strip()
    if pn in STATEFUL_RELATIONS:
        return False
    surf = claim.get("predicate_surface") or claim.get("predicate") or ""
    return bool(_PASSIVE.search(surf))


# ---------- 端点：来源与待规范列表 ----------

def endpoint_plan(claim: dict, pos: str) -> dict:
    """这个端点怎么处理：直接采用（S4.5 已解且合规）/ 不可用 / 需要 LLM 规范化。

    **S4.5 的解不无条件直通**（2026-09-18 改）：S4.5 的契约是"忠实、可逐字锚定（只删不改）"，
    所以它的解天然可能是句子式长短语（`supervising the agent only when it goes off track`）——
    而 S5 的入图契约是"词条名"。两者冲突时，旧行为是"采用后被 ≤6 词护栏拒掉＝整条丢"；
    现在改为**转交 LLM 压缩**（raw 用 S4.5 的解），溯源链仍完整：
    `concept → derived_from → S4.5 的解 → S4.5 依据（逐字）→ chunk`。
    """
    status = claim.get(f"{pos}_resolution_status")
    resolved = (claim.get(f"{pos}_resolved") or "").strip()
    if status == "resolved" and resolved:
        ok, why = valid_entity_name(resolved)
        if ok:
            return {"kind": "resolved", "text": resolved}
        return {"kind": "normalize", "text": resolved, "from_resolved": True, "why": why}
    if status in ("unresolved", "not_anaphora"):
        return {"kind": "unusable", "text": "", "why": f"指代{status}"}
    raw = (claim.get(pos) or "").strip()
    if not raw:
        return {"kind": "empty", "text": ""}
    if raw.lower().strip(" .") in PRONOUNS:
        return {"kind": "unusable", "text": raw, "why": "光杆代词"}
    return {"kind": "normalize", "text": raw}


def build_targets(claims: list[dict]) -> list[dict]:
    """列出**需要 LLM 规范化**的端点（已解出/不可用/空的不必送）。"""
    out: list[dict] = []
    for ci, c in enumerate(claims):
        if c.get("predicate_status") == "pending":
            continue
        for pos in ("subject", "object"):
            plan = endpoint_plan(c, pos)
            if plan["kind"] == "normalize":
                out.append(
                    {
                        "idx": f"{ci}:{pos}",
                        "claim_idx": ci,
                        "position": pos,
                        "raw": plan["text"],
                        "subject": c.get("subject"),
                        "predicate": c.get("predicate"),
                        "object": c.get("object") or "",
                        "predicate_normalized": c.get("predicate_normalized") or c.get("predicate"),
                    }
                )
    return out


def parse_endpoint_reply(part: dict) -> tuple[dict, dict]:
    """解析端点规范化响应 → (idx → concept|null, idx → derived_from[])。

    `derived_from` 是**结构化溯源字段**（取代原来的自由文本 `note`）：
    概括时列出取自该端点 raw 的词，程序据此做确定性校验（纯集合比较，不解析自由文本）。
    """
    got: dict[str, str | None] = {}
    derived: dict[str, list[str]] = {}
    for r in (part or {}).get("endpoints") or []:
        if not isinstance(r, dict):
            continue
        key = str(r.get("idx") or "").strip()
        if not key:
            continue
        concept = r.get("concept")
        got[key] = concept.strip() if isinstance(concept, str) and concept.strip() else None
        df = r.get("derived_from")
        words: list[str] = []
        if isinstance(df, list):
            words = [str(x).strip() for x in df if isinstance(x, str) and str(x).strip()]
        elif isinstance(df, str) and df.strip():
            words = [df.strip()]
        derived[key] = words[:8]      # 上限 8 个，防跑飞
    return got, derived


async def normalize_endpoints(
    client: DeepSeekLLMClient,
    source_id: str,
    targets: list[dict],
    *,
    batch: int = 80,
) -> dict:
    """调 LLM 把端点短语规范成可点名概念；返回 {map: {idx: concept|null}, derived_from: {idx: [词]}}。"""
    got: dict[str, str | None] = {}
    derived: dict[str, list[str]] = {}
    batches = [targets[i: i + batch] for i in range(0, len(targets), batch)]

    async def ask(chunk: list[dict], label: str) -> None:
        payload = [
            {k: t[k] for k in ("idx", "raw", "subject", "predicate", "object", "predicate_normalized")}
            for t in chunk
        ]
        part, _ = await _chat_json(
            client,
            [
                LLMMessage(role="system", content=PROMPT),
                # 紧凑 JSON：pretty 格式会让输入涨 13–14%（实测），而这些字段不需要给人看
                LLMMessage(role="user", content=json.dumps({"source_id": source_id, "endpoints": payload},
                                                          ensure_ascii=False, separators=(",", ":"))),
            ],
            label=label,
        )
        g, d = parse_endpoint_reply(part)
        got.update(g)
        derived.update(d)

    failed_batches = 0
    for bi, chunk in enumerate(batches, start=1):
        try:
            await ask(chunk, f"S5 {source_id} 端点批{bi}/{len(batches)}")
        except Exception as exc:
            failed_batches += 1
            print(f"  [S5] 第 {bi} 批失败（{exc}）→ 该批端点按'取不出'处理")
        for retry in range(1, 3):                     # 漏条补问（同清洗/归一步的做法）
            missing = [t for t in chunk if t["idx"] not in got]
            if not missing:
                break
            print(f"  [S5] 第 {bi} 批缺 {len(missing)} 条，第 {retry} 次补问")
            try:
                await ask(missing, f"S5 {source_id} 端点批{bi}-补{retry}")
            except Exception as exc:
                print(f"  [S5] 补问失败（{exc}）")
        print(f"  [S5] 批{bi}/{len(batches)}：端点 {len(chunk)}")
    return {"map": got, "derived_from": derived,
            "batches": len(batches), "failed_batches": failed_batches}


RETRY_PROMPT = """你是「端点规范化器（修正轮）」。上一轮你把某些端点取成了**同一断言另一端的词**——这不允许。

对每个端点，只依据它**自己的 raw 文本**重新取一次「可点名的名词概念」：
1. 实词取自本端点 raw；**不得使用同一断言另一端（subject 或 object 里的另一侧）的词**。
2. 可以删冠词 / 所有格 / 纯语法框架 / 从句引导词，但**删掉后所指必须不变**。
3. 只保留必要的限定词（删掉会改变所指的必须保留，纯修饰可删）；**不要把 raw 整段照搬**。
4. 允许概括，但要在 derived_from 里列出取自 raw 的词（非概括时给 []）。
5. 写成术语式名词短语（能当检索键用；去冠词 / 改单复数 / 统一大小写）。
6. **保持 raw 的语言**（本语料是英文）：不要把概念名翻译成其他语言。
7. 只在本端点 raw 里取不出可点名概念时 → null，别硬造。

输出严格 JSON（无多余文字、无代码围栏）：{"endpoints":[{"idx":0,"concept":"…","derived_from":["…"]}]}"""


LONG_RETRY_PROMPT = """你是「端点规范化器（修正轮）」。上一轮你给这些端点写的名字**太长了**——
读起来像一句话或一段描述，而不是一个词条名。请重取一次。

规则：
1. 只依据本端点 raw；**不得从同一断言另一端（subject / object 的另一侧）借词**。
2. **只保留必要的区分性限定词**（删掉会改变所指的必须留）；列举、从句、纯修饰一律去掉。
3. 目标是一个**词条名**——用户会拿它去检索的那个词。**如果写出来还像一句话，就说明还没概括到位**。
4. 概括 / 名词化时在 `derived_from` 里列出取自 raw 的词（非概括给 []）。
5. **保持 raw 的语言**（本语料是英文）：**不要把概念名翻译成中文**——要压缩、不要翻译。
6. 实在取不出词条名 → null，别硬造。

输出严格 JSON（无多余文字、无代码围栏）：{"endpoints":[{"idx":0,"concept":"…","derived_from":["…"]}]}"""


async def fix_cross_endpoint_bleed(
    client: DeepSeekLLMClient,
    source_id: str,
    targets: list[dict],
    cmap: dict,
    derived_map: dict | None = None,
    *,
    batch: int = 40,
) -> dict:
    """对"串味"端点做一次**聚焦重试**（只重问这些端点，不重问全批）。

    为什么重试而不是直接丢：实测错案
      `Every function reachable through any domain on an allowlist | is | an attack surface`
      是**有实质内容的断言**（"经允许域名可达的函数都成了攻击面"），正确形态是
      `function —is→ attack surface`。整条 skip 掉等于丢一条重要关系。

    返回 {fixed: {idx: name}, retried: n, still_bled: [idx], batches: n, failed: n}
    """
    bad = [
        t for t in targets
        if cross_endpoint_bleed(
            cmap.get(t["idx"]) or "",
            t["raw"],
            (t.get("object") if t["position"] == "subject" else t.get("subject")) or "",
        )
    ]
    dv = derived_map if derived_map is not None else {}
    if not bad:
        return {"fixed": {}, "retried": 0, "still_bled": [], "batches": 0, "failed": 0}

    print(f"  [S5] {source_id}：发现 {len(bad)} 个端点串味（借了另一端的词）→ 聚焦重试")
    res = await _retry_endpoints(
        client, source_id, bad, cmap, dv, prompt=RETRY_PROMPT,
        label_prefix=f"S5 {source_id} 串味修正", batch=batch,
        accept=lambda name, t: bool(name) and not cross_endpoint_bleed(
            name, t["raw"], (t.get("object") if t["position"] == "subject" else t.get("subject")) or ""),
    )
    return {"fixed": res["accepted"], "retried": len(bad), "still_bled": res["still"],
            "batches": res["batches"], "failed": res["failed"], "log": res["log"]}


def overlong_targets(targets: list[dict], cmap: dict) -> list[dict]:
    """挑出"名字太长"（超过 MAX_NAME_WORDS 词）的端点——给聚焦重试用。"""
    out = []
    for t in targets:
        name = (cmap.get(t["idx"]) or "").strip()
        if not name:
            continue
        ok, why = valid_entity_name(name)
        if not ok and "超过" in why:
            out.append(t)
    return out


def final_guard(targets: list[dict], cmap: dict) -> dict:
    """两次聚焦重试之后的**确定性终检**：仍不合格的判 null（记账，绝不硬用错名）。

    为什么需要它（2026-09-18 实测踩到）：串味检查跑在"过长压缩"**之前**，
    压缩改写出来的新名字没人再检查——实测压缩把
    `function reachable through a domain on an allowlist` 改成了 `allowlisted domain attack surface`
    （又借了宾语的概念），却一路通过。**护栏必须放在所有改写之后**。

    检查两类：① 名字形态（`valid_entity_name`：代词 / 从句式 / 无字母 / 超过 6 词）；
              ② 串味（借了同一断言另一端的词）。
    """
    dropped: list[dict] = []
    for t in targets:
        name = (cmap.get(t["idx"]) or "").strip()
        if not name:
            continue
        other = (t.get("object") if t["position"] == "subject" else t.get("subject")) or ""
        ok, why = valid_entity_name(name)
        if not ok:
            cmap[t["idx"]] = None
            dropped.append({"idx": t["idx"], "name": name, "kind": "form", "why": why})
        elif cross_endpoint_bleed(name, t["raw"], other):
            cmap[t["idx"]] = None
            dropped.append({"idx": t["idx"], "name": name, "kind": "bleed",
                            "why": "串味（借了同一断言另一端的词）"})
    return {"dropped": dropped}


async def _retry_endpoints(
    client: DeepSeekLLMClient,
    source_id: str,
    bad: list[dict],
    cmap: dict,
    derived_map: dict,
    *,
    prompt: str,
    label_prefix: str,
    accept,
    batch: int = 40,
) -> dict:
    """通用：对一批"不合格"端点**重问一次**，结果写回 cmap / derived_map。

    accept(name, target) → bool 决定这次结果是否可用；不可用的写回 None（该端点判"取不出"），
    绝不硬用错名。返回 {fixed, still, batches, failed, retried}。
    """
    fixed: dict[str, str | None] = {}
    batches = [bad[i: i + batch] for i in range(0, len(bad), batch)]
    failed = 0
    for bi, chunk in enumerate(batches, start=1):
        payload = [
            {k: t[k] for k in ("idx", "raw", "subject", "predicate", "object", "predicate_normalized")}
            for t in chunk
        ]
        try:
            part, _ = await _chat_json(
                client,
                [
                    LLMMessage(role="system", content=prompt),
                    LLMMessage(role="user", content=json.dumps(
                        {"source_id": source_id, "endpoints": payload},
                        ensure_ascii=False, separators=(",", ":"))),
                ],
                label=f"{label_prefix}{bi}/{len(batches)}",
            )
        except Exception as exc:
            failed += 1
            print(f"  [S5] {label_prefix}{bi} 失败（{exc}）")
            continue
        g, d = parse_endpoint_reply(part)
        fixed.update(g)
        derived_map.update(d)

    accepted: dict[str, str] = {}
    still: list[str] = []
    log: list[dict] = []
    for t in bad:
        before = cmap.get(t["idx"]) or ""
        name = fixed.get(t["idx"])
        ok = bool(name) and accept(name, t)
        if ok:
            cmap[t["idx"]] = name
            accepted[t["idx"]] = name
        else:
            cmap[t["idx"]] = None      # 仍不合格 → 该端点判"取不出"（绝不硬用错名）
            still.append(t["idx"])
        log.append({"idx": t["idx"], "raw": t.get("raw", ""),
                    "before": before, "after": name or None, "accepted": ok})
    return {"accepted": accepted, "still": still, "log": log,
            "batches": len(batches), "failed": failed, "retried": len(bad)}


async def fix_overlong_endpoints(
    client: DeepSeekLLMClient,
    source_id: str,
    targets: list[dict],
    cmap: dict,
    derived_map: dict | None = None,
    *,
    batch: int = 40,
) -> dict:
    """对"名字太长"的端点做一次聚焦重试（提示词明确"上次太长、压成词条名"）。"""
    bad = overlong_targets(targets, cmap)
    if not bad:
        return {"fixed": {}, "retried": 0, "still_long": [], "batches": 0, "failed": 0}
    print(f"  [S5] {source_id}：发现 {len(bad)} 个端点名字过长（> {MAX_NAME_WORDS} 词）→ 聚焦重试")
    dv = derived_map if derived_map is not None else {}
    res = await _retry_endpoints(
        client, source_id, bad, cmap, dv, prompt=LONG_RETRY_PROMPT,
        label_prefix=f"S5 {source_id} 过长修正", batch=batch,
        accept=lambda name, t: valid_entity_name(name)[0],
    )
    return {"fixed": res["accepted"], "retried": len(bad), "still_long": res["still"],
            "batches": res["batches"], "failed": res["failed"], "log": res["log"]}
    fixed: dict[str, str | None] = {}
    batches = [bad[i: i + batch] for i in range(0, len(bad), batch)]
    failed = 0
    for bi, chunk in enumerate(batches, start=1):
        payload = [
            {k: t[k] for k in ("idx", "raw", "subject", "predicate", "object", "predicate_normalized")}
            for t in chunk
        ]
        try:
            part, _ = await _chat_json(
                client,
                [
                    LLMMessage(role="system", content=RETRY_PROMPT),
                    LLMMessage(role="user", content=json.dumps(
                        {"source_id": source_id, "endpoints": payload},
                        ensure_ascii=False, separators=(",", ":"))),
                ],
                label=f"S5 {source_id} 串味修正{bi}/{len(batches)}",
            )
        except Exception as exc:
            failed += 1
            print(f"  [S5] 串味修正第 {bi} 批失败（{exc}）")
            continue
        g, d = parse_endpoint_reply(part)
        fixed.update(g)
        dv.update(d)

    still: list[str] = []
    for t in bad:
        name = fixed.get(t["idx"])
        other_raw = (t.get("object") if t["position"] == "subject" else t.get("subject")) or ""
        if name and not cross_endpoint_bleed(name, t["raw"], other_raw):
            cmap[t["idx"]] = name          # 修正成功 → 覆盖原结果
        else:
            cmap[t["idx"]] = None          # 仍串味 → 该端点取不出（记 skipped，不硬用错名）
            still.append(t["idx"])
    return {"fixed": {k: v for k, v in fixed.items() if v}, "retried": len(bad),
            "still_bled": still, "batches": len(batches), "failed": failed}


# ---------- 建边（确定性） ----------

def _resolve_endpoint(
    claim: dict,
    pos: str,
    concept_map: dict,
    claim_idx: int,
    text_words: set[str],
    allowed: set[str],
    derived_map: dict | None = None,
) -> tuple[str | None, str, str, str]:
    """确定这一端最终用什么名字；返回 (名字 或 None, 不通过的原因, 实际试过的名字, 档位)。"""
    plan = endpoint_plan(claim, pos)
    if plan["kind"] == "empty":
        return ((None, "", "", "ok") if pos == "object" else (None, "无主语", "", "ok"))
    if plan["kind"] == "unusable":
        return None, f"{pos}端不可用（{plan['why']}）", plan["text"], "ok"
    name = plan["text"]
    if plan["kind"] == "normalize":
        name = (concept_map.get(f"{claim_idx}:{pos}") or "").strip()
        if not name:
            return None, f"{pos}端取不出可点名概念（原：{plan['text'][:40]}）", plan["text"], "ok"
    ok, why = valid_entity_name(name)
    if not ok:
        # **标记**：把不合格的名字原样写进原因（护栏在程序侧，提示词里不再限制词数/形态）
        return None, f"{pos}端不可点名（{why}）：{name[:60]}", name, "ok"
    tier = "ok"
    if name.lower().strip(" .") not in allowed:
        # 四档判定（A 原文措辞 / B 词形派生 / C 语义概括 / D 拒）
        tier = classify_name_tier(name, plan["text"], (derived_map or {}).get(f"{claim_idx}:{pos}"))
        if tier == "reject":
            # **标记**：把名字写进原因（否则只看得到"疑似自造"、看不到造了什么名）
            return None, f"{pos}端名字疑似自造（无来源词或来源不在本端点 raw）：{name[:60]}", name, tier
    return name, "", name, tier


def assemble_graph(
    claims: list[dict],
    concept_map: dict,
    *,
    text_words: set[str],
    resolved_names: set[str],
    claim_chunk: dict[int, str],
    derived_map: dict | None = None,
) -> dict:
    """确定性建边 + 对账。返回 {entities, edges, audit, name_tiers, generalized}。"""
    allowed = {n.lower().strip(" .") for n in resolved_names}
    entities: dict[str, dict] = {}
    edges: list[dict] = []
    audit: list[dict] = []          # 每条 claim 的处置（建边 / 丢弃 + 原因）
    name_tiers: dict[str, str] = {}      # 端点 idx → 档位（ok / derived / generalized）
    generalized: list[dict] = []         # C 档（语义概括）→ 待抽检清单

    for ci, c in enumerate(claims):
        if c.get("predicate_status") == "pending":
            audit.append({"claim_idx": ci, "status": "skipped", "reason": "谓词待定（未归入受控关系）"})
            continue
        has_obj = bool((c.get("object") or "").strip())
        # 被动式且**有两端**才翻转（一元断言没有可交换的对象）；翻转后 subject 是受事 → 建边方向反过来
        flipped = has_obj and should_flip(c)
        frm_pos, to_pos = ("object", "subject") if flipped else ("subject", "object")
        src_frm, why_f, tried_f, tier_f = _resolve_endpoint(
            c, frm_pos, concept_map, ci, text_words, allowed, derived_map)
        if src_frm is None:
            audit.append({"claim_idx": ci, "status": "skipped", "reason": why_f})
            continue
        src_to, why_t, tried_t = (None, "", "")
        tier_t = "ok"
        if has_obj:
            src_to, why_t, tried_t, tier_t = _resolve_endpoint(
                c, to_pos, concept_map, ci, text_words, allowed, derived_map)
        if why_t:
            audit.append({"claim_idx": ci, "status": "skipped", "reason": why_t})
            continue

        # 护栏③：**两端归一后同名 → 不建边**（否则会建出一条自环）。
        # 实测案例（2026-09-18）：`Every function reachable through any domain on an allowlist | is | an attack surface`
        # 的**主语**被归一成了 `attack surface`（模型把宾语的概念安到了主语头上），于是两端同名。
        # 前面两道护栏拦不住它：①"可点名"查的是**归一后**的名字；②"不造名"查的是**整篇文档**有没有这个词
        # （`attack surface` 当然在文档里）→ 缺的正是这条**端点级**的一致性检查。
        # 处置：不建边 + 记 skipped（可对账）；S6 的自环处理退化为纯兜底。
        if src_to and _fold(src_frm) == _fold(src_to):
            audit.append({
                "claim_idx": ci,
                "status": "skipped",
                "reason": f"两端归一后同名（{src_frm}）——疑似伪 claim 或端点归一取错概念",
            })
            continue

        pred = c.get("predicate_normalized") or c.get("predicate")
        edge = {"from": src_frm, "predicate": pred, "to": src_to or "", "claim_idx": ci}
        if flipped:
            edge["passive_flipped"] = True
        if c.get("predicate"):
            edge["predicate_surface"] = c["predicate"]
        if c.get("polarity"):
            edge["polarity"] = c["polarity"]
        if c.get("roles"):
            edge["roles"] = c["roles"]
        if claim_chunk.get(ci):
            edge["chunk_id"] = claim_chunk[ci]
        edges.append(edge)
        for pos_name, tier in ((frm_pos, tier_f), (to_pos, tier_t)):
            if not pos_name or (pos_name == to_pos and not has_obj):
                continue
            name_tiers[f"{ci}:{pos_name}"] = tier
            if tier == "generalized":
                generalized.append({
                    "idx": f"{ci}:{pos_name}",
                    "raw": (c.get(pos_name) or ""),
                    "concept": (edge["from"] if pos_name == frm_pos else (edge.get("to") or "")),
                    "derived_from": (derived_map or {}).get(f"{ci}:{pos_name}") or [],
                    "claim_idx": ci,
                })
        for name in (src_frm, src_to):
            if name:
                entities.setdefault(name, {"name": name, "type": "concept", "aliases": []})
        audit.append({"claim_idx": ci, "status": "edge", "reason": ""})

    return {
        "entities": list(entities.values()),
        "edges": edges,
        "audit": audit,
        "name_tiers": name_tiers,
        "generalized": generalized,
    }


# ---------- 主入口 ----------

def claim_to_chunk(claims: list[dict], clean_t: str, chunks: list[dict]) -> dict[int, str]:
    """claim_idx → 锚句所在 chunk（供前端算"跨节数"）。"""
    out: dict[int, str] = {}
    for ci, c in enumerate(claims):
        for e in c.get("evidence_texts") or []:
            p = _find_raw(e, clean_t)
            if p is None:
                continue
            hit = next((ch["chunk_id"] for ch in chunks if ch["start_pos"] < p[1] and ch["end_pos"] > p[0]), None)
            if hit:
                out[ci] = hit
                break
    return out


async def assemble(
    client: DeepSeekLLMClient,
    source_id: str,
    claims: list[dict],
    clean_t: str,
    chunks: list[dict],
    *,
    batch: int = 80,
) -> dict:
    """S5 主入口：LLM 规范端点 → 程序建边。返回 {entities, edges, stats, audit, blocked, chunk_sections}。"""
    targets = build_targets(claims)
    print(f"  [S5] {source_id}：claims {len(claims)} → 待规范端点 {len(targets)}")
    nv = await normalize_endpoints(client, source_id, targets, batch=batch)
    bleed = await fix_cross_endpoint_bleed(client, source_id, targets, nv["map"], nv.get("derived_from"))
    longfix = await fix_overlong_endpoints(client, source_id, targets, nv["map"], nv.get("derived_from"))
    guard = final_guard(targets, nv["map"])          # 终检：所有改写之后再统一过一遍

    text_words = set()
    # 注意：**不要用 `_norm`**——它把 en/em dash 也折成 `-`（那是 A4 引文定位的口径），
    # 会把 `environment—high-utility` 粘成一个词，导致词表里没有 `utility`。
    # 这里按 `_fold_text` 的语义折：词内连字符→`-`、破折号→空格。
    for w in re.findall(r"[a-z0-9\-]+", _fold_text(clean_t).lower()):
        text_words.add(w)
        text_words.add(_fold(w))
    resolved_names = {
        c[f"{p}_resolved"].strip()
        for c in claims
        for p in ("subject", "object")
        if c.get(f"{p}_resolved")
    }
    g = assemble_graph(
        claims,
        nv["map"],
        text_words=text_words,
        resolved_names=resolved_names,
        claim_chunk=claim_to_chunk(claims, clean_t, chunks),
        derived_map=nv.get("derived_from"),
    )
    audit = g["audit"]
    skips = [a for a in audit if a["status"] == "skipped"]

    # 护栏命中统计（**标记用**）：护栏全在程序侧，提示词里不再写词数/形态限制。
    def _cat(reason: str) -> str:
        for k in ("超过 6 词", "代词", "从句式", "无字母", "疑似自造", "取不出可点名概念",
                  "指代", "两端归一后同名", "谓词待定"):
            if k in reason:
                return k
        return "其他"

    skip_categories = collections.Counter(_cat(a["reason"]) for a in skips)
    tier_counts = collections.Counter(g.get("name_tiers", {}).values())
    stats = {
        "claims_in": len(claims),
        "claims_with_edge": len(audit) - len(skips),
        "claims_skipped": len(skips),
        "entities": len(g["entities"]),
        "edges": len(g["edges"]),
        "normalize_targets": len(targets),
        "normalize_batches": nv["batches"],
        "normalize_failed_batches": nv["failed_batches"],
        "bleed_retried": bleed["retried"],
        "bleed_fixed": len(bleed["fixed"]),
        "bleed_remaining": len(bleed["still_bled"]),
        "long_retried": longfix["retried"],
        "long_fixed": len(longfix["fixed"]),
        "long_remaining": len(longfix["still_long"]),
        "final_guard_dropped": len(guard["dropped"]),
        "name_tiers": dict(tier_counts),          # ok / derived / generalized（A/B/C 档）
        "generalized": len(g.get("generalized") or []),
    }
    print(
        f"  [S5] {source_id}：建边 {stats['claims_with_edge']} / 未建边 {stats['claims_skipped']}"
        f" → 实体 {stats['entities']}、边 {stats['edges']}"
    )
    return {
        "entities": g["entities"],
        "edges": g["edges"],
        "stats": stats,
        "skipped": skips,                    # 兼容界面：未建边的 claim + 原因
        "audit": audit,                      # 全量对账（每条 claim 一条）
        "skip_categories": dict(skip_categories),   # 护栏命中分类（程序侧标记用）
        "name_tiers": dict(tier_counts),            # 端点名档位（ok / derived / generalized）
        # 端点规范化的**全量映射**（可审计）：raw → concept + 结构化溯源词。
        # 之前这份映射没落盘，导致"看不到 S5 到底把什么规范成了什么"（只能从边反推）。
        "normalize_map": [
            {
                "idx": t["idx"],
                "raw": t["raw"],
                "concept": nv["map"].get(t["idx"]),
                "derived_from": (nv.get("derived_from") or {}).get(t["idx"]) or [],
                "tier": g.get("name_tiers", {}).get(t["idx"], ""),
            }
            for t in targets
        ],
        # C 档（语义概括 + 来源词）：**通过但进待抽检清单**——概括的忠实性只能靠人看
        "generalized": g.get("generalized") or [],
        # 过长重试的全量对照（before/after/accepted）：诊断"提示词是否真的让它概括了"
        "long_retry": longfix.get("log") or [],
        # 终检被拦的名字（所有改写之后再统一检查 → 防止压缩/重试引入新问题）
        "final_guard": guard["dropped"],
        "chunk_sections": {ch["chunk_id"]: (ch.get("section_path") or "") for ch in chunks},
    }
