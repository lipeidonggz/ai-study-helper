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

import json
import re

from app.agent.llm import DeepSeekLLMClient, LLMMessage
from app.compile.merge import form_key
from scripts.compile_slice_b6 import _chat_json, _find_raw, _norm

PRONOUNS = {"this", "that", "these", "those", "such", "it", "they", "them", "there", "we", "our", "us"}
CLAUSEISH = re.compile(r"^(how to|whether|when|why|that|which|who|what)\b", re.I)
MAX_NAME_WORDS = 6
STOPWORDS = {"a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "with", "by", "at", "as", "its"}

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

# 怎么转
- **取中心概念、剥掉修饰**：`a small team of just three engineers` → `engineer`；
  `the tools, abstractions, and internal structure required to make progress` → `tooling`；
  `building containment for claude.ai, Claude Code, and Cowork` → `containment`。
- **从句 / 描写里若有可点名的概念，取那个概念**（不要整段判 null）：
  `how to tune the cache size` → `cache size`；`an example of a service that failed` → `service`。
  只有当整段**一个可点名概念都取不出来**时才给 null。
- **归一到规范名词短语**：去冠词 / 改单复数 / 统一大小写，写成**术语式名词短语**（≤6 词）。
- **取不出干净概念 → null**：整段是命题 / 从句（`what's held up, what's broken, and what we've learned…`）、
  纯动作或纯描述（`the exact same effect`）→ concept 给 null，别硬造。
- **不要引入 raw 里没有的概念**（不许用你自己的知识补全）；拿不准就给 null。

# 输出（严格 JSON；无多余文字、无代码围栏）
{"endpoints":[{"idx":0,"concept":"…","note":"…"}]}
concept 为 null 表示"这段端点取不出可点名的概念"；note 可省略。idx 必须原样回抄。
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
    words = [w for w in re.findall(r"[a-z0-9\-]+", name.lower()) if w not in STOPWORDS]
    if not words:
        return False
    return all(w in text_words or _fold(w) in text_words for w in words)


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
    """这个端点怎么处理：直接采用（S4.5 已解）/ 不可用 / 需要 LLM 规范化。"""
    status = claim.get(f"{pos}_resolution_status")
    resolved = (claim.get(f"{pos}_resolved") or "").strip()
    if status == "resolved" and resolved:
        return {"kind": "resolved", "text": resolved}
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


async def normalize_endpoints(
    client: DeepSeekLLMClient,
    source_id: str,
    targets: list[dict],
    *,
    batch: int = 80,
) -> dict:
    """调 LLM 把端点短语规范成可点名概念；返回 {idx: concept|null}。"""
    got: dict[str, str | None] = {}
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
        for r in part.get("endpoints") or []:
            key = str(r.get("idx") or "").strip()
            if not key:
                continue
            concept = r.get("concept")
            got[key] = concept.strip() if isinstance(concept, str) and concept.strip() else None

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
    return {"map": got, "batches": len(batches), "failed_batches": failed_batches}


# ---------- 建边（确定性） ----------

def _resolve_endpoint(
    claim: dict,
    pos: str,
    concept_map: dict,
    claim_idx: int,
    text_words: set[str],
    allowed: set[str],
) -> tuple[str | None, str, str]:
    """确定这一端最终用什么名字；返回 (名字 或 None, 不通过的原因, 实际试过的名字)。"""
    plan = endpoint_plan(claim, pos)
    if plan["kind"] == "empty":
        return ((None, "", "") if pos == "object" else (None, "无主语", ""))
    if plan["kind"] == "unusable":
        return None, f"{pos}端不可用（{plan['why']}）", plan["text"]
    name = plan["text"]
    if plan["kind"] == "normalize":
        name = (concept_map.get(f"{claim_idx}:{pos}") or "").strip()
        if not name:
            return None, f"{pos}端取不出可点名概念（原：{plan['text'][:40]}）", plan["text"]
    ok, why = valid_entity_name(name)
    if not ok:
        return None, f"{pos}端不可点名（{why}）", name
    if not (name.lower().strip(" .") in allowed or _name_grounded(name, text_words)):
        return None, f"{pos}端名字在原文里找不到（疑似自造）", name
    return name, "", name


def assemble_graph(
    claims: list[dict],
    concept_map: dict,
    *,
    text_words: set[str],
    resolved_names: set[str],
    claim_chunk: dict[int, str],
) -> dict:
    """确定性建边 + 对账。返回 {entities, edges, audit, blocked}。"""
    allowed = {n.lower().strip(" .") for n in resolved_names}
    entities: dict[str, dict] = {}
    edges: list[dict] = []
    audit: list[dict] = []          # 每条 claim 的处置（建边 / 丢弃 + 原因）

    for ci, c in enumerate(claims):
        if c.get("predicate_status") == "pending":
            audit.append({"claim_idx": ci, "status": "skipped", "reason": "谓词待定（未归入受控关系）"})
            continue
        has_obj = bool((c.get("object") or "").strip())
        # 被动式且**有两端**才翻转（一元断言没有可交换的对象）；翻转后 subject 是受事 → 建边方向反过来
        flipped = has_obj and should_flip(c)
        frm_pos, to_pos = ("object", "subject") if flipped else ("subject", "object")
        src_frm, why_f, tried_f = _resolve_endpoint(c, frm_pos, concept_map, ci, text_words, allowed)
        if src_frm is None:
            audit.append({"claim_idx": ci, "status": "skipped", "reason": why_f})
            continue
        src_to, why_t, tried_t = (None, "", "")
        if has_obj:
            src_to, why_t, tried_t = _resolve_endpoint(c, to_pos, concept_map, ci, text_words, allowed)
        if why_t:
            audit.append({"claim_idx": ci, "status": "skipped", "reason": why_t})
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
        for name in (src_frm, src_to):
            if name:
                entities.setdefault(name, {"name": name, "type": "concept", "aliases": []})
        audit.append({"claim_idx": ci, "status": "edge", "reason": ""})

    return {
        "entities": list(entities.values()),
        "edges": edges,
        "audit": audit,
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

    text_words = set()
    for w in re.findall(r"[a-z0-9\-]+", _norm(clean_t).lower()):
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
    )
    audit = g["audit"]
    skips = [a for a in audit if a["status"] == "skipped"]
    stats = {
        "claims_in": len(claims),
        "claims_with_edge": len(audit) - len(skips),
        "claims_skipped": len(skips),
        "entities": len(g["entities"]),
        "edges": len(g["edges"]),
        "normalize_targets": len(targets),
        "normalize_batches": nv["batches"],
        "normalize_failed_batches": nv["failed_batches"],
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
        "chunk_sections": {ch["chunk_id"]: (ch.get("section_path") or "") for ch in chunks},
    }
