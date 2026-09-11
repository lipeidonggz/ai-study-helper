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

# 红线阈值（经验值；随语料可调）
RED_LINES = {
    "anchor_rate": 0.95,       # 引文可锚率（**只看写了引文的那些**）：低于此 → 不过（幻觉探针）
    "evidence_missing": 0.20,  # 完全没写引文的占比：高于此 → 不过（模型省字段/输出被截断）
    "meta_subject": 0.10,      # 元主语占比（改写，不丢）：高于此 → 不过
    "clause_subject": 0.02,    # 从句式主语占比（丢弃）：高于此 → 不过（实测 0.3–0.6%）
    "predicate_dirty": 0.25,   # 谓词不干净（≥4 词 或 含并列，标记）：高于此 → 不过
}

META_SUBJECTS = {
    "we", "our", "us", "this", "that", "it", "they", "there", "these", "those",
    "the author", "the paper", "the post", "本文", "作者",
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

    动作三类：**丢弃**（证据不可锚 / 从句式主语）、**确定性改写**（元主语 → 文档主体）、
    **标记**（谓词需清洗 / 否定存疑 / 主语不在锚句）。
    """
    subject_fallback = doc_subject or default_doc_subject(source_id)
    n = len(claims)
    kept: list[dict] = []
    dropped: list[dict] = []
    rewritten: list[dict] = []
    marked: list[dict] = []
    anchor_ok = n_meta = n_clause = n_dirty = n_neg = n_no_ev = 0

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

        # ② 元主语 → 改写为文档主体（不丢）
        if subj.lower() in META_SUBJECTS:
            n_meta += 1
            rewrites.append({"field": "subject", "from": subj, "to": subject_fallback})
            c["subject"] = subject_fallback

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
            c["health"] = {"marks": marks, "rewrites": rewrites}
            if marks:
                marked.append({"idx": i, "subject": subj, "predicate": pred, "marks": marks})
            if rewrites:
                rewritten.append({"idx": i, "rewrites": rewrites})
        if reasons_drop:
            dropped.append({"idx": i, "subject": subj, "predicate": pred, "reasons": reasons_drop})
        else:
            kept.append(c)

    ratios = {
        "anchor_rate": anchor_ok / max(1, n - n_no_ev),   # 只看写了引文的
        "evidence_missing": n_no_ev / n if n else 0.0,
        "meta_subject": n_meta / n if n else 0.0,
        "clause_subject": n_clause / n if n else 0.0,
        "predicate_dirty": n_dirty / n if n else 0.0,
    }
    verdict = {}
    for k, v in ratios.items():
        th = RED_LINES[k]
        verdict[k] = {"value": round(v, 4), "threshold": th, "pass": (v >= th) if k == "anchor_rate" else (v <= th)}

    # 召回按**体检后**的 claim 算：丢弃导致漏召回会被抓出来
    recall = recall_check(kept, clean_t, source_id) if gold else None
    if recall:
        verdict["recall_must"] = {
            "value": round(recall["must_hit"] / max(1, recall["must_total"]), 4),
            "threshold": 1.0,
            "pass": recall["pass"],
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
            "② 元主语改写": {"count": n_meta, "rate": round(ratios["meta_subject"], 4), "to": subject_fallback},
            "③ 从句式主语丢弃": {"count": n_clause, "rate": round(ratios["clause_subject"], 4)},
            "④ 谓词需清洗（标记）": {"count": n_dirty, "rate": round(ratios["predicate_dirty"], 4)},
            "⑤ 否定存疑（标记，不设红线）": {"count": n_neg},
        },
        "verdict": verdict,
        "red_line_pass": all(v["pass"] for v in verdict.values()),
        "recall": recall if recall else {"executed": False, "note": "未执行（未提供召回金标准）"},
        "dropped": dropped,
        "rewritten": rewritten,
        "marked": marked,
    }
    return kept, report
