"""编译层体检（质量门）：线上流程的必选一步，红线不过 → 该篇编译失败。

设计依据（memory/0028 考古层第三十段）：
  · 体检只做三件**确定性**的事——**丢弃 / 确定性改写 / 标记**；
    凡需要语义判断的修改（剥谓词修饰、拆并列、合并异名）一律不在这里做。
  · 只给高置信项设红线；低置信项（如"否定丢失"实测 9% 命中但基本是误报）只标记。
  · 报告必须带**被丢弃/被标记的样本清单**——教训：正则写错时，只有看清单才发现误伤。
"""

from __future__ import annotations

import json
import re

from app.kb.manifest import parse_manifest
from scripts.analyze_pass1_recall import GOLD_DIR, _para_index
from scripts.compile_slice_b6 import MANIFEST_PATH, _anchor_window, _find_raw

# 指标阈值（经验值；随语料可调）。**红线只有两条**——其余是告警线。
#
# 口径（2026-09-17 定）：红线＝**不可恢复**的问题，过了才能进下一步；凡能被程序确定性
# 兜底（改写/丢弃/留给下游）的，都只是告警 + 动作，不该把整篇判死。
#   · recall_must  漏了关键内容 → 不可恢复（金标准；没提供则跳过）
#   · anchor_rate  在编引文（幻觉探针）→ 不可恢复
# 告警线（不过也不拦，供人发现"抽取在退化"）：
#   · evidence_missing / self_reference / deictic_subject / clause_subject / predicate_dirty
RED_LINES = {
    "recall_must": 1.0,
    "anchor_rate": 0.95,       # 只统计写了引文的那些
}
WARN_LINES = {
    "evidence_missing": 0.20,  # 完全没写引文的占比（丢掉即可）
    "self_reference": 0.20,    # 自称（we/our/us）→ 确定性改写为文档主体
    "deictic_subject": 0.15,   # 指代（this/it/they…）→ **保留 + 标记**，交下游「指代消解」
    "clause_subject": 0.02,    # 从句式主语占比（丢弃）
    "predicate_dirty": 0.25,   # 谓词不干净（≥4 词 或 含并列）→ 交清洗步
}
THRESHOLDS = {**RED_LINES, **WARN_LINES}

# 自称：报告主体的自称。所指**确定**（＝文档主体）→ 确定性改写，语义等价。
SELF_REFERENCE = {"we", "our", "us", "the author", "the paper", "the post", "本文", "作者"}
# 指代：代词 / 指示词。所指**不确定**（要看上下文）→ **不改写也不丢弃**：
# 保留原 surface（保住可锚定），打标记 + `needs_resolution`，交下游「指代消解」步。
# 为什么不能在这一步丢：丢在这里＝下游永远拿不到原料，消解无从做起（前置有损不可逆）。
DEICTIC = {"this", "that", "it", "they", "there", "these", "those"}
# 指标说明（给界面渲染成表：指标名 / 取值 / 阈值 / 是否红线 / 动作 / 为什么需要）
CHECK_SPEC: dict[str, dict] = {
    "recall_must": {
        "name": "召回（must 项）",
        "action": "红线",
        "note": "人工金标准里 must=true 的核心断言，其所在段落是否被某条 claim 的证据覆盖；漏一条即不过。"
                "没提供金标准时跳过（报告标注「未执行」）。",
    },
    "anchor_rate": {
        "name": "引文可锚率",
        "action": "红线",
        "note": "**只统计写了引文的 claim**：引文能在原文里逐字定位的比例。低于阈值＝模型在编引文，"
                "所以它是红线（同时是幻觉探针）。",
    },
    "evidence_missing": {
        "name": "没写引文占比",
        "action": "丢弃（告警线）",
        "note": "claim 完全没给引文的比例（模型省字段或输出被截断）。这类**丢掉即可**、不涉及真假，"
                "故阈值宽松。",
    },
    "self_reference": {
        "name": "自称占比（we/our/us）",
        "action": "确定性改写（告警线）",
        "note": "主语是报告主体自称（we / our / us / 本文 / 作者）。所指**确定**＝文档主体，"
                "所以程序**改写成文档主体**（语义等价、不丢信息）。比例高多半是文体（实践复盘文），"
                "但过高（>阈值）说明抽取在泛泛而谈。",
    },
    "deictic_subject": {
        "name": "指代占比（this/it/they）",
        "action": "保留+标记（告警线）",
        "note": "主语是代词 / 指示词（this / it / that / they…），**所指不确定**——改成文档主体会造错"
                "（`this enables progressive disclosure` 里的 this 不是作者），丢掉又会让下游无从消解。"
                "故：**原样保留 + 打 `needs_resolution` 标记**，交下游「指代消解」步判所指并记依据。"
                "这里的比例＝待消解队列的长度，不是错误率。",
    },
    "clause_subject": {
        "name": "从句式主语占比",
        "action": "丢弃（告警线）",
        "note": "主语是 how to… / granting… / that…（≥3 词）这类从句式短语，不是干净概念 → **直接丢弃**；"
                "比例高说明抽取质量在退化。",
    },
    "predicate_dirty": {
        "name": "谓词需清洗占比",
        "action": "标记（交归一步，告警线）",
        "note": "谓词 ≥4 词或含并列（修饰/从句被揉进了谓词，如 `grows large enough that`）。**只标记不丢**，"
                "交归一步清洗成 predicate_clean；比例高会拖累谓词归一的一致性。",
    },
}
CLAUSE_SUBJECT = re.compile(r"^(how to|whether|when|why|that|which|only when|granting|having)\b", re.I)
# 限定词开头的短名词短语（`that token`）不是从句 → 要求 ≥3 词
CLAUSE_NEEDS_LEN3 = {"that", "which", "who"}
NEG = re.compile(r"\b(not|never|no|neither|nor)\b|n't\b", re.I)
CONJ = re.compile(r"\b(and|or)\b", re.I)


def _words(s: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z\-]*", s or "")


def default_doc_subject(source_id: str) -> str:
    """文档主体兜底推导：manifest 名字首段；若是匿名化标签（如「A社」）则退到 URL 域名。"""
    try:
        src = next(s for s in parse_manifest(MANIFEST_PATH) if s.source_id == source_id)
        head = re.split(r"[:：\-—|·]", getattr(src, "name", "") or "")[0].strip()
        if re.match(r"^[A-Za-z]?社$", head) or len(head) <= 2:
            m = re.search(r"https?://(?:www\.)?([^/]+)", getattr(src, "url", "") or "")
            if m:
                return m.group(1).split(".")[0].capitalize()
        return head or source_id
    except Exception:
        return source_id


def recall_check(claims: list[dict], clean_t: str, source: str) -> dict | None:
    """召回（红线，可选）：金标准锚句所在段是否被某条 claim 的证据覆盖。

    命名约定：eval/compile/<source 小写>_recall_gold.json；文件不存在返回 None（报告标注"未执行"）。
    """
    gold_path = GOLD_DIR / f"{source.lower()}_recall_gold.json"
    if not gold_path.exists():
        return None
    items = (json.loads(gold_path.read_text(encoding="utf-8")).get("items")) or []
    hit = must_hit = must_total = 0
    missing: list[str] = []
    for it in items:
        pos = _find_raw(it["anchor_text"], clean_t)
        if not pos:
            continue
        para = _para_index(clean_t, pos[0])
        ok = any(
            _para_index(clean_t, p[0]) == para
            for c in claims
            for p in (_find_raw(e, clean_t) for e in (c.get("evidence_texts") or []))
            if p
        )
        if ok:
            hit += 1
            if it.get("must"):
                must_hit += 1
        elif it.get("must"):
            missing.append(it["id"])
        if it.get("must"):
            must_total += 1
    return {
        "executed": True,
        "gold": gold_path.name,
        "items": len(items),
        "hit": hit,
        "must_hit": must_hit,
        "must_total": must_total,
        "missing_must": missing,
        "pass": must_hit == must_total,
    }


def run_health_check(
    claims: list[dict],
    clean_t: str,
    source_id: str,
    *,
    doc_subject: str = "",
    gold: bool = True,
) -> tuple[list[dict], dict]:
    """体检：返回 (过滤后的 claim 列表, 报告)。

    动作三类：**丢弃**（证据不可锚 / 从句式主语）、**确定性改写**（自称 → 文档主体）、
    **标记**（指代待消解 / 谓词需清洗 / 否定存疑 / 主语不在锚句）。
    指代（this/it/they…）**既不改写也不丢弃**——保留原样并打 needs_resolution，
    交下游「指代消解」步（丢在这里＝下游永远拿不到原料）。
    """
    subject_fallback = doc_subject or default_doc_subject(source_id)
    n = len(claims)
    kept: list[dict] = []
    dropped: list[dict] = []
    rewritten: list[dict] = []
    marked: list[dict] = []
    anchor_ok = n_self = n_deictic = n_clause = n_dirty = n_neg = n_no_ev = 0

    for i, raw in enumerate(claims):
        c = dict(raw)
        reasons_drop: list[str] = []
        marks: list[str] = []
        rewrites: list[dict] = []

        # ① 引文可锚（红线；不可锚 → 丢弃）。分两种，别混：
        #    · 没写引文（模型省字段/输出被截断）→ evidence_missing（次要，丢掉即可）
        #    · 写了引文但定位不到（多半是编造）→ anchor_rate（严重，幻觉探针）
        quotes = [e for e in (c.get("evidence_texts") or []) if isinstance(e, str) and e]
        if not quotes:
            n_no_ev += 1
            reasons_drop.append("没有引文")
        elif all(_find_raw(q, clean_t) for q in quotes):
            anchor_ok += 1
        else:
            reasons_drop.append("引文定位失败")

        subj = (c.get("subject") or "").strip()
        win = _anchor_window(c, clean_t)

        # ② 自称（we/our/us/本文/作者）→ 改写为文档主体（所指确定，语义等价）
        if subj.lower() in SELF_REFERENCE:
            n_self += 1
            rewrites.append({"field": "subject", "from": subj, "to": subject_fallback})
            c["subject"] = subject_fallback

        # ②b 指代（this/it/they…）→ **保留 + 标记**（所指不确定；改写会造错、丢掉会让下游无从消解）
        elif subj.lower() in DEICTIC:
            n_deictic += 1
            c["subject_anaphora"] = subj            # 原文 surface 原样留着（可锚定不破）
            c["needs_resolution"] = True            # 下游「指代消解」步的输入标记
            marks.append("指代主语（待消解）")

        # ③ 从句式主语 → 丢弃
        m_clause = CLAUSE_SUBJECT.match(subj)
        if m_clause and not (m_clause.group(1).lower() in CLAUSE_NEEDS_LEN3 and len(subj.split()) < 3):
            n_clause += 1
            reasons_drop.append("主语是从句式")

        # ④ 谓词不干净（≥4 词 或 含并列）→ 标记（交归一步清洗）
        pred = c.get("predicate") or ""
        if len(pred.split()) >= 4 or CONJ.search(pred):
            n_dirty += 1
            marks.append("谓词需清洗")

        # ⑤ 低置信项：只标记，不设红线
        if NEG.search(win["anchor_sentence"]) and not c.get("polarity"):
            n_neg += 1
            marks.append("否定存疑")
        sj = {w.lower() for w in _words(subj)}
        if sj and not (sj & {w.lower() for w in _words(win["anchor_sentence"] + " " + win["context_before"])}):
            marks.append("主语不在锚句（可能指代/需邻句）")

        if marks or rewrites:
            c["health"] = {
                "marks": marks,
                "rewrites": rewrites,
                "anchor": win["anchor_sentence"],
                "src_idx": i,          # 在体检**输入**列表里的下标（供下游回填归一结果）
            }
            if marks:
                marked.append(
                    {
                        "idx": i,
                        "subject": c.get("subject") or subj,
                        "predicate": pred,
                        "object": c.get("object") or "",
                        "anchor": win["anchor_sentence"],
                        "marks": marks,
                    }
                )
            if rewrites:
                rewritten.append({"idx": i, "rewrites": rewrites})
        if reasons_drop:
            dropped.append(
                {
                    "idx": i,
                    "subject": c.get("subject") or subj,
                    "predicate": pred,
                    "object": c.get("object") or "",
                    "anchor": win["anchor_sentence"],
                    "reasons": reasons_drop,
                }
            )
        else:
            kept.append(c)

    ratios = {
        "anchor_rate": anchor_ok / max(1, n - n_no_ev),   # 只看写了引文的
        "evidence_missing": n_no_ev / n if n else 0.0,
        "self_reference": n_self / n if n else 0.0,
        "deictic_subject": n_deictic / n if n else 0.0,
        "clause_subject": n_clause / n if n else 0.0,
        "predicate_dirty": n_dirty / n if n else 0.0,
    }
    verdict = {}
    for k, v in ratios.items():
        th = THRESHOLDS[k]
        verdict[k] = {
            "value": round(v, 4),
            "threshold": th,
            "pass": (v >= th) if k == "anchor_rate" else (v <= th),
            "red_line": k in RED_LINES,          # 只有 recall_must / anchor_rate 为 True
        }

    # 召回按**体检后**的 claim 算：丢弃导致漏召回会被抓出来
    recall = recall_check(kept, clean_t, source_id) if gold else None
    if recall:
        verdict["recall_must"] = {
            "value": round(recall["must_hit"] / max(1, recall["must_total"]), 4),
            "threshold": 1.0,
            "pass": recall["pass"],
            "red_line": True,
        }

    report = {
        "source": source_id,
        "doc_subject": subject_fallback,
        "claims_in": n,
        "claims_out": len(kept),
        "checks": {
            "① 引文可锚（写了引文的）": {"ok": anchor_ok, "of": n - n_no_ev, "rate": round(ratios["anchor_rate"], 4),
                                        "定位失败": sum(1 for d in dropped if "引文定位失败" in d["reasons"])},
            "①b 没写引文（丢弃）": {"count": n_no_ev, "rate": round(ratios["evidence_missing"], 4)},
            "② 自称改写为文档主体": {"count": n_self, "rate": round(ratios["self_reference"], 4), "to": subject_fallback},
            "②b 指代保留待消解": {"count": n_deictic, "rate": round(ratios["deictic_subject"], 4)},
            "③ 从句式主语丢弃": {"count": n_clause, "rate": round(ratios["clause_subject"], 4)},
            "④ 谓词需清洗（标记）": {"count": n_dirty, "rate": round(ratios["predicate_dirty"], 4)},
            "⑤ 否定存疑（标记，不设红线）": {"count": n_neg},
        },
        "verdict": verdict,
        "recall": recall if recall else {"executed": False, "note": "未执行（未提供召回金标准）"},
        # 红线只由 red_line=True 的项决定；告警项不过也不拦
        "red_line_pass": all(v["pass"] for v in verdict.values() if v.get("red_line")),
        "dropped": dropped,
        "rewritten": rewritten,
        "marked": marked,
    }
    # 指标表（界面渲染用）：名称 / 取值 / 阈值 / 是否红线 / 动作 / 说明
    order = [
        "recall_must", "anchor_rate",                                   # 红线（不可恢复）
        "evidence_missing", "self_reference", "deictic_subject",        # 告警 + 动作
        "clause_subject", "predicate_dirty",
    ]
    report["check_spec"] = [
        {
            "key": k,
            "name": CHECK_SPEC[k]["name"],
            "value": verdict[k]["value"],
            "threshold": verdict[k]["threshold"],
            "pass": verdict[k]["pass"],
            "red_line": bool(verdict[k].get("red_line")),
            "action": CHECK_SPEC[k]["action"],
            "note": CHECK_SPEC[k]["note"],
        }
        for k in order
        if k in verdict
    ]
    return kept, report
