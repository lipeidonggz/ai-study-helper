"""S4.5 · 指代消解：把 `this / it / they / these…` 的所指判出来，交下游建图用。

为什么单独一步（不是 Pass 1 / S6）：
  · **Pass 1 做不了**——它的契约是"逐字引文 + 主宾照抄"，而消解要写出原文里不在这个位置的词
    （那会破坏"引文可锚"这条硬约束）；
  · **S6 的"消解"是另一件事**——那是异名消解（VM vs virtual machine，判断两个已有的东西是否同一），
    本步是"从不完整指到完整"，方向相反；
  · **塞进 S5 会让它又变胖**——S5 已承担从句主语归约 + 手段→roles，而"一个 prompt 混太多职责"
    正是 Pass 2 当初被迫拆三步的原因。

三条原则（2026-09-17 定）：
  ① **只补信息、不做取舍**：判不出就标 `unresolved`，丢不丢留给 S5 的建图边界；
  ② **不覆盖原字段**：`subject` / `object` 一字不动（可锚定不破、可回滚），消解结果另写
     `*_resolution` / `*_resolution_evidence` / `*_resolution_status`；只有"整串就是指代词/指代短语"
     的才再写一个可直接替换的 `*_resolved`；
  ③ **只依据给定上下文**：不许用内置知识猜——`evidence` 必须逐字出现在窗口里（程序可校验）。

窗口 ＝ **锚句所在段（整段）+ 上一段尾句**。实测（A5 38 / O2 28 条）所指需回溯 ≥2 句的占
58% / 36%，还有跨段的 → 句级窗口（"锚句 ± 邻句"）**不够**。

TODO（沿用 service.py 那笔）：`_find_raw` / `_chat_json` 目前在 scripts/ 里，这里先 import 复用，
      以后统一搬进 app/compile/。
"""

from __future__ import annotations

import json
import re

from app.agent.llm import DeepSeekLLMClient, LLMMessage
from scripts.compile_slice_b6 import _chat_json, _find_raw, _norm


def evidence_verbatim(evidence: str, window: str) -> bool:
    """依据是否真出自窗口 —— 判据是"**只删不改**"（与谓词清洗护栏同构）：

    把依据与窗口都切成词，依据的词序列必须是窗口词序列的**子序列**（顺序一致、可跳词）。
    · **省略不算编造**：模型常把跨句的所指写成 `第一处 ... 第二处`，也会删掉中间的并列项不打省略号
      ——实测这两种都被"整串子串匹配"误判成编造（A5 4 + 3 条）。删词是合法引用，**改词/凭空造句才是编造**。
    · **空依据不算通过**——那是"结论先行"最容易犯的毛病（A 版 65 条里 6 条空依据）。
    · 词数下限 4：太短的串（一两个常见词）几乎必然"是子序列"，没有鉴别力。
    """
    ev_tokens = re.findall(r"[A-Za-z0-9']+", _norm(evidence or "").lower())
    if len(ev_tokens) < 4:
        return False
    win_tokens = re.findall(r"[A-Za-z0-9']+", _norm(window).lower())
    i = 0
    for tok in ev_tokens:
        while i < len(win_tokens) and win_tokens[i] != tok:
            i += 1
        if i >= len(win_tokens):
            return False
        i += 1
    return True

BARE = {"this", "that", "these", "those", "such", "it", "they", "them", "there"}
HEAD = re.compile(r"^(this|that|these|those|such|it|they|them|there)\b", re.I)
# 出现这些词说明整串更像从句/描述，而不是"指代词 + 名词"的干净短语
CLAUSEISH = re.compile(r"\b(that|which|who|is|are|was|were|be|been|being|can|could|would|should|will|might|may|has|have|had)\b", re.I)


def classify(text: str) -> tuple[str, str] | None:
    """判断一个字段值是不是指代目标 → (kind, anaphor)；不是则 None。

    kind：`bare` 整串就是指代词 / `phrase` 指代词+名词（this approach） /
          `embedded` 指代被夹在更长的串里（it was sandboxed）——这类不生成可直接替换的 *_resolved。
    """
    t = (text or "").strip()
    if not t:
        return None
    m = HEAD.match(t)
    if not m:
        return None
    anaphor = m.group(1).lower()
    words = t.split()
    if len(words) == 1:
        return ("bare", anaphor)
    if len(words) <= 5 and not CLAUSEISH.search(t[len(m.group(0)):].strip()):
        return ("phrase", anaphor)
    return ("embedded", anaphor)


def paragraph_window(claim: dict, clean_t: str, *, max_chars: int = 1500, prev_tail: int = 200) -> dict:
    """切出该 claim 的上下文窗口：锚句所在段（必要时按锚句裁剪）+ 上一段尾句。"""
    ev = next((e for e in (claim.get("evidence_texts") or []) if isinstance(e, str) and e), "")
    pos = _find_raw(ev, clean_t) if ev else None
    if pos is None:
        pos = (0, 1)
    start = clean_t.rfind("\n", 0, pos[0]) + 1
    end = clean_t.find("\n", pos[1])
    if end < 0:
        end = len(clean_t)
    para = clean_t[start:end]
    if len(para) > max_chars:                      # 超长段：围着锚句裁（不丢它前面的所指）
        a = max(0, (pos[0] - start) - int(max_chars * 0.6))
        b = min(len(para), (pos[1] - start) + int(max_chars * 0.25))
        # **按句界对齐**：否则窗口在句子中间切断，模型照抄整句时会"逐字对不上"（实测 2 条被护栏误判成编造）
        ends = [m.end() for m in re.finditer(r"[.!?]\s", para[:a])]
        if ends:
            a = ends[-1]
        nxt = re.search(r"[.!?]\s", para[b:])
        if nxt:
            b = b + nxt.start() + 1
        para = para[a:b]
    prev_start = clean_t.rfind("\n", 0, max(0, start - 1)) + 1
    prev = clean_t[prev_start:start].strip()
    prev_win = _tail_sentences(prev, prev_tail)
    window = (prev_win + "\n" if prev_win else "") + para
    return {"window": window.strip(), "paragraph": para.strip(), "prev_tail": prev_win}


def _tail_sentences(text: str, max_chars: int, keep: int = 2) -> str:
    """取上一段尾部的**最后 keep 个完整句**。

    两个坑都踩过：① 机械截 N 字符会把句子切成半句，模型照抄"整句"时逐字对不上（被护栏误判成编造）；
    ② 只保留最后一句又会**把指代所在的那句丢掉**（实测 A5 `[77]`：`these defenses` 的所指在倒数第二句）。
    """
    if not text:
        return ""
    ends = [m.end() for m in re.finditer(r"[.!?]\s", text)]
    if len(ends) <= keep:
        return text.strip()
    s = text[ends[len(ends) - keep]:].strip()
    if len(s) > max_chars and ends:                      # 太长就退到只剩最后一句
        s = text[ends[-1]:].strip()
    return s


def find_targets(claims: list[dict], clean_t: str) -> list[dict]:
    """列出所有待消解的目标：主语位 + 宾语位（光杆 / 指代短语 / 嵌入式）。"""
    out: list[dict] = []
    for i, c in enumerate(claims):
        for position in ("subject", "object"):
            txt = (c.get(position) or "").strip()
            got = classify(txt)
            if not got:
                continue
            kind, anaphor = got
            win = paragraph_window(c, clean_t)
            out.append(
                {
                    "claim_idx": i,
                    "position": position,
                    "anaphor": anaphor,
                    "kind": kind,
                    "field_text": txt,
                    "subject": c.get("subject"),
                    "predicate": c.get("predicate"),
                    "object": c.get("object") or "",
                    "window": win["window"],
                    "anchor": (c.get("health") or {}).get("anchor") or "",
                }
            )
    return out


_RULES = """你是「指代消解器」。输入是一批待判的指代（每条含 claim_idx、position、anaphor，以及该断言的
subject / predicate / object 与它的上下文窗口 window）。任务：判断 anaphor 在 window 里指代的
是**哪个可点名的名词概念**，只输出 JSON。

# 判据
- **只依据 window**：不要用你自己的知识猜。window 里找不到依据 → 判不出来。
- resolution 要写成**可点名的名词短语**（例：`the model-layer mechanisms`、`credentials`、`the allowlist`），
  不要写成句子、不要照抄整句。
- **宁缺勿错**：错解会把两个概念焊死，比不解危险得多。判不出就 `status: "unresolved"`、`resolution: null`。
- **形式主语不是指代**：`there is …` / `it becomes harder …` 这类 there / it 没有所指对象 → `status: "not_anaphora"`。
- evidence 必须是 window 里**逐字出现**的片段（用来证明你的判断）；给不出逐字依据 → 按判不出处理。

# 输出（严格 JSON；无多余文字、无代码围栏）
三态：`resolved`（给出 resolution）/ `unresolved`（判不出）/ `not_anaphora`（形式主语，无指代）。
"""

# v2 追加两条（V1 实测暴露的：两版都会给"整段描述"硬造一个句子当 resolution）
_RULES_V2 = _RULES + """
# 形态与出口（补）
- resolution 要写成**术语式的名词短语**（术语表里会出现的那种名字，≤6 词）：不要带括号补充、不要把整句搬进来。
- **所指只能描述成"一整件事"**（一个事件 / 一段状态 / 一整个命题，而不是一个名词概念）→ `status: "unresolved"`。
  · 例：`It rained for three days and the bridge collapsed. This was inevitable.` 里的 this 指的是"前一句
    描述的那件事"（一整件事）→ unresolved，而不是把那个句子抄成 resolution。
"""

# A/B 只差**输出字段顺序**（其余一字不动）：
#   A 结论先行：status → resolution → evidence
#   B 依据先行：evidence → status → resolution（先摘原文再下结论，抗"用内置知识猜"）
_SCHEMA_A = """{"results":[{"claim_idx":0,"position":"subject","status":"resolved","resolution":"the three output formats","evidence":"supports three output formats: markdown, HTML, and PDF"}]}"""
_SCHEMA_B = """{"results":[{"claim_idx":0,"position":"subject","evidence":"supports three output formats: markdown, HTML, and PDF","status":"resolved","resolution":"the three output formats"}]}"""

PROMPTS = {
    "A": _RULES + _SCHEMA_A,
    "B": _RULES + _SCHEMA_B,
    "B2": _RULES_V2 + _SCHEMA_B,          # B 的字段顺序（依据先行）+ 形态/出口补充
}

# v3：**防循环**（A5 实测：`This is a direct prompt injection` → resolution 写成
# "a direct prompt injection"，把断言自身的谓词当所指）
_RULES_V3 = _RULES_V2 + """
- **防循环**：resolution 不能与断言自身的 predicate / object **字面重复**。例：断言
  `This is a banned technique`，resolution 写成 `a banned technique` 就是把结论当所指（循环）——
  要么找到它真正指代的概念，要么判 `unresolved`。
"""
PROMPTS["B3"] = _RULES_V3 + _SCHEMA_B

# v4 = v2 的形态/出口 + **收窄版**防循环。为什么要收窄：V3 的措辞让模型把"所指是一整件事"的
# 一律放弃（实测 12 条让路，其中 `these defenses → the model-layer defenses` 这类**正确**消解被误杀）。
# 正确的分界不是"是不是一件事"，而是① 不许与己方谓词/宾语循环 ② 是"一件事"就**概括成名词短语**，概括不出才 unresolved。
_RULES_V4 = _RULES_V2 + """
- **防循环**：resolution 不能与断言自身的 predicate / object **字面重复**。例：断言
  `This is a banned technique`，resolution 写成 `a banned technique` 就是把结论当所指（循环）——
  要么找到它真正指代的概念，要么判 `unresolved`。
- **所指是"一整件事"时，先概括、再放弃**：把它凝成一个**名词短语**（动名词也算，例
  `the per-action approval approach`）；只有当它既不是名词概念、也概括不出一个短语时，才 `unresolved`。
"""
PROMPTS["B4"] = _RULES_V4 + _SCHEMA_B


async def resolve_targets(
    client: DeepSeekLLMClient,
    source_id: str,
    targets: list[dict],
    *,
    variant: str = "B4",          # 基线：依据先行 + 形态/出口 + 收窄防循环（A/B 实验见 scripts/anaphora_ab.py）
    batch: int = 40,
) -> dict:
    """调 LLM 消解一批目标；返回 {results, stats}。不改 claims（回填交给 apply_resolutions）。"""
    system = PROMPTS[variant]
    batches = [targets[i: i + batch] for i in range(0, len(targets), batch)]
    got: dict[tuple[int, str], dict] = {}
    failed_batches = 0

    async def ask(chunk: list[dict], label: str) -> None:
        payload = [
            {
                "claim_idx": t["claim_idx"],
                "position": t["position"],
                "anaphor": t["anaphor"],
                "subject": t["subject"],
                "predicate": t["predicate"],
                "object": t["object"],
                "window": t["window"],
            }
            for t in chunk
        ]
        part, _ = await _chat_json(
            client,
            [
                LLMMessage(role="system", content=system),
                LLMMessage(role="user", content=json.dumps(payload, ensure_ascii=False, indent=1)),
            ],
            label=label,
        )
        for r in part.get("results") or []:
            try:
                key = (int(r.get("claim_idx")), str(r.get("position")))
            except (TypeError, ValueError):
                continue
            got[key] = r

    for bi, chunk in enumerate(batches, start=1):
        try:
            await ask(chunk, f"S4.5 {variant} {source_id} {bi}/{len(batches)}")
        except Exception as exc:                      # 单批失败不阻断：这批目标留待 unresolved
            failed_batches += 1
            print(f"  [S4.5] 第 {bi} 批失败（{exc}）→ 该批目标按未判处理")

    results = []
    for t in targets:
        r = got.get((t["claim_idx"], t["position"])) or {}
        status = str(r.get("status") or "unresolved").strip()
        if status not in ("resolved", "unresolved", "not_anaphora"):
            status = "unresolved"
        resolution = r.get("resolution")
        resolution = resolution.strip() if isinstance(resolution, str) and resolution.strip() else None
        if status == "resolved" and not resolution:
            status = "unresolved"                     # 说 resolved 却没给结果 → 视为判不出
        evidence = str(r.get("evidence") or "")
        results.append(
            {
                **{k: t[k] for k in ("claim_idx", "position", "anaphor", "kind", "field_text")},
                "status": status,
                "resolution": resolution,
                "evidence": evidence,
                "evidence_verbatim": evidence_verbatim(evidence, t["window"]),
                "answered": (t["claim_idx"], t["position"]) in got,
            }
        )
    stats = {
        "targets": len(targets),
        "answered": sum(1 for r in results if r["answered"]),
        "resolved": sum(1 for r in results if r["status"] == "resolved"),
        "unresolved": sum(1 for r in results if r["status"] == "unresolved"),
        "not_anaphora": sum(1 for r in results if r["status"] == "not_anaphora"),
        "evidence_verbatim": sum(1 for r in results if r["evidence_verbatim"]),
        "failed_batches": failed_batches,
        "batches": len(batches),
        "variant": variant,
    }
    return {"results": results, "stats": stats}


def apply_resolutions(claims: list[dict], results: list[dict]) -> dict:
    """把消解结果回填成新字段（**不动** subject / object / evidence_texts）。"""
    n = 0
    for r in results:
        c = claims[r["claim_idx"]]
        pos = r["position"]
        c[f"{pos}_anaphor"] = r["anaphor"]
        c[f"{pos}_resolution_status"] = r["status"]
        if r["resolution"]:
            c[f"{pos}_resolution"] = r["resolution"]
            if r.get("evidence"):
                c[f"{pos}_resolution_evidence"] = r["evidence"]
            if r["kind"] in ("bare", "phrase"):       # 干净可替换的才给 *_resolved
                c[f"{pos}_resolved"] = r["resolution"]
        n += 1
    return {"applied": n}


async def resolve_source(
    client: DeepSeekLLMClient,
    source_id: str,
    claims: list[dict],
    clean_t: str,
    *,
    variant: str = "B4",
    batch: int = 40,
) -> dict:
    """线上入口：检出目标 → 调 LLM → 过依据护栏 → 回填 claims。返回统计 + 清单。"""
    targets = find_targets(claims, clean_t)
    stats = {"targets": 0, "resolved": 0, "unresolved": 0, "not_anaphora": 0, "demoted": 0}
    if not targets:
        return {"stats": stats, "list": []}
    out = await resolve_targets(client, source_id, targets, variant=variant, batch=batch)
    guard = apply_evidence_guard(out["results"], targets)      # 依据非逐字 → 降 unresolved
    apply_resolutions(claims, out["results"])
    stats = {
        "targets": len(targets),
        "resolved": guard["resolved"],
        "unresolved": guard["unresolved"],
        "not_anaphora": guard["not_anaphora"],
        "demoted": guard["demoted"],
        "evidence_verbatim": sum(1 for r in out["results"] if r["evidence_verbatim"]),
        "variant": variant,
    }
    rows = [
        {
            "idx": r["claim_idx"],
            "position": r["position"],
            "anaphor": r["anaphor"],
            "kind": r["kind"],
            "field_text": r["field_text"],
            "status": r["status"],
            "resolution": r["resolution"],
            "evidence": r["evidence"],
            "evidence_verbatim": r["evidence_verbatim"],
        }
        for r in out["results"]
    ]
    return {"stats": stats, "list": rows}


def apply_evidence_guard(results: list[dict], targets: list[dict]) -> dict:
    """程序护栏（确定性）：**依据不是逐字 → 该条降为 unresolved**并记账。

    为什么要有：提示词里只是软性要求"给不出逐字依据就按判不出处理"，软约束不如程序护栏
    （与谓词清洗的"只删不改"护栏同一个套路）。降级只动 status，不动已判出的 resolution——
    留痕供人工复核（`evidence_not_verbatim: True`）。
    """
    win = {(t["claim_idx"], t["position"]): t["window"] for t in targets}
    demoted = []
    for r in results:
        ok = evidence_verbatim(r.get("evidence") or "", win[(r["claim_idx"], r["position"])])
        r["evidence_verbatim"] = ok
        if not ok and r["status"] == "resolved":
            r["status"] = "unresolved"
            r["evidence_not_verbatim"] = True
            demoted.append((r["claim_idx"], r["position"], r.get("resolution")))
    return {
        "demoted": len(demoted),
        "samples": demoted[:5],
        "resolved": sum(1 for r in results if r["status"] == "resolved"),
        "unresolved": sum(1 for r in results if r["status"] == "unresolved"),
        "not_anaphora": sum(1 for r in results if r["status"] == "not_anaphora"),
    }
