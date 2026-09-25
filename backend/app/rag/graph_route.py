"""检索侧 · 图路由第一版（第 3 步最小闭环）：**按角色过滤**。

路径：`doc（已点名）+ claim_type → statement（全集）→ evidence_chunks → 注入文本`

为什么先做它（而不是先做"概念入口 + 向量"）：
  · 它**不需要向量入口**——文档已经点名、角色是枚举语义；
  · 它正是**纯向量做不到**的能力："把这篇的局限都列出来"要的是**全集**，不是 top-k；
  · 零向量、零 LLM，风险最小，第一时间能验证"图参与检索"到底有没有增量。

诚实边界：这一版只用 `claim_type` 这条最粗的过滤；概念入口（向量打 concept/statement）、
一跳关系路径、与向量兜底的合并都还没接（第 4 步）。
"""

from __future__ import annotations

KB_ID = "kb-main"
# **防备式上限**（沛东 2026-09-18 定）：图路由抓的是"结构化命中集合"，不是 top-k 抽奖——
# 用向量时代的精细预算（3600）去截它，只会制造"看起来很完整的假完备"（实测：chunks 模式
# 只装 10/21 块＝覆盖 44% 的断言，却给出 4 大类 21 条的完整答案）。
# 这个上限只干两件事：① 防止极端 token 消耗；② 避免接近模型窗口上限（Lost-in-the-middle 加剧）。
# 取值口径：**模型窗口 × 50% 左右**（deepseek-chat 64k 窗口 → 32k），换长窗口模型再调。
DEFAULT_MAX_TOKENS = 32000
# 对比/调试用的老口径（向量式精细预算），只在 mode=quotes / both 的 A-B 实验里用
DEFAULT_BUDGET_TOKENS = 3600
CLAIM_TYPE_LABEL = {"LimitationStatement": "局限", "OutlookStatement": "展望"}

# 确定性触发词表（不做 LLM 路由——决策 7 的口径：入口/路由尽量确定性）。
# 命中的前提还包括"点名了唯一一篇文档"（见 RagBackend.prepare）。
ROLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "LimitationStatement": ("局限", "限制", "不足", "缺点", "风险", "代价", "隐患",
                            "挑战", "弱点", "短板", "失败", "坑", "问题"),
    "OutlookStatement": ("展望", "未来", "下一步", "方向", "计划", "打算", "期待", "前景"),
}


def detect_role(query: str) -> str | None:
    """问题是否在问"某类论述角色"（局限 / 展望）——确定性词表，命中即返回 claim_type。"""
    q = (query or "").lower()
    for ct, words in ROLE_KEYWORDS.items():
        if any(w in q for w in words):
            return ct
    return None
# 预算里还要算上"头部标题 + 每块的来源标注行"——实测踩坑：只算 chunk payload.tokens 时，
# 实际注入比预算多约 10%（头部 + 12 行来源标注没人记账）。这里用确定性 allowance 顶上。
HEADER_ALLOW_TOKENS = 80
PER_CHUNK_ALLOW_TOKENS = 24
QUOTE_SHARE_IN_BOTH = 0.55      # `both` 模式里引文清单的预算占比（剩下给证据块）


def role_chunks(
    source_id: str,
    claim_type: str,
    *,
    compile_store,
    vector_store,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    mode: str = "chunks",
) -> dict:
    """按角色取某文档的全部断言 + 其证据块，并组装注入文本（预算内；超出则上报不静默砍）。

    mode（2026-09-18 加的对比开关，用来回答"只注引文 vs 注入整块"哪个效果好）：
      · `chunks`（默认）：只注证据块，**按 chunk 顺序**（＝原文顺序，保留行文逻辑）
      · `quotes`：只注断言引文清单（每条一句原文，按 claim_idx 顺序）
      · `both`：先给引文清单（保证覆盖全部断言），剩余预算再装块

    **预算口径（2026-09-18 改）**：图路由用 `max_tokens` 防备式上限（默认 32k），
    **不再用向量式的 3600 精细预算**——抓出来的块是强相关的，该整体给模型。
    只有 `mode=quotes/both` 的 A-B 实验还沿用 `budget_tokens`（3600）做对照。
    """
    stmts = compile_store.role_statements(source_id, claim_type)
    chunk_ids: list[str] = []
    seen: set[str] = set()
    for s in stmts:
        for cid in s.get("evidence_chunks") or []:
            if cid not in seen:
                seen.add(cid)
                chunk_ids.append(cid)

    index = {h.get("id"): h for h in vector_store.list_by_document(KB_ID, source_id, limit=5000)}
    limit = max_tokens if mode == "chunks" else budget_tokens
    chunks: list[dict] = []
    used = HEADER_ALLOW_TOKENS
    over_budget: list[str] = []
    missing: list[str] = []
    for cid in chunk_ids:
        hit = index.get(cid)
        if not hit:
            missing.append(cid)
            continue
        payload = hit.get("payload") or {}
        toks = int(payload.get("tokens") or 0)
        if used + toks + PER_CHUNK_ALLOW_TOKENS > limit:
            over_budget.append(cid)
            continue
        used += toks + PER_CHUNK_ALLOW_TOKENS
        chunks.append({
            "chunk_id": cid, "tokens": toks, "text": payload.get("text", ""),
            "section_path": payload.get("section_path", ""),
        })

    label = CLAIM_TYPE_LABEL.get(claim_type, claim_type)
    by_chunk: dict[str, list[str]] = {}
    for s in stmts:
        for cid in s.get("evidence_chunks") or []:
            by_chunk.setdefault(cid, []).append(s["id"])

    def quote_lines(budget: int = budget_tokens) -> tuple[list[str], int]:
        """引文清单（每条断言一行），按 claim_idx 顺序装到预算为止；返回 (行, 用掉的 token)。"""
        out: list[str] = []
        used_t = HEADER_ALLOW_TOKENS
        for s in stmts:
            quote = (s.get("evidence_texts") or [""])[0]
            est = max(8, len(quote) // 4) + 8
            if used_t + est > budget:
                break
            used_t += est
            out.append(f"- [{s['id']}] {s['predicate']}｜{quote}")
        return out, used_t

    def covered(stmt_ids: list[str]) -> int:
        """这批注入覆盖了多少条该角色断言（引文模式按装入条数算；块模式按块承载的断言并集算）。"""
        if not stmt_ids:
            return 0
        ids = {x for sid in stmt_ids for x in by_chunk.get(sid, [])}
        return len(ids)

    if mode == "quotes":
        ql, used_q = quote_lines()
        injection = "\n".join(
            [f"# {source_id} 的{label}（图路由·引文清单：{len(ql)}/{len(stmts)} 条断言）", ""] + ql)
        return _result(source_id, claim_type, stmts, [], injection, len(chunk_ids),
                       used_q - HEADER_ALLOW_TOKENS, over_budget, missing, mode,
                       {"quotes": len(ql), "chunks_used": 0, "statements_covered": len(ql)})

    if mode == "both":
        # 先按比例给引文清单，剩下的预算再装块（不能"引文先吃满"——那样块永远是 0）
        ql, used_q = quote_lines(int(budget_tokens * QUOTE_SHARE_IN_BOTH))
        parts = [f"# {source_id} 的{label}（图路由·引文清单 + 证据块）", "", "## 断言引文清单", *ql, "", "## 证据块"]
        used = used_q
        chunks_both: list[dict] = []
        for ch in chunks:
            if used + ch["tokens"] + PER_CHUNK_ALLOW_TOKENS > budget_tokens:
                continue
            used += ch["tokens"] + PER_CHUNK_ALLOW_TOKENS
            chunks_both.append(ch)
            parts += [f"### {ch['chunk_id']}", ch["text"], ""]
        return _result(source_id, claim_type, stmts, chunks_both, "\n".join(parts), len(chunk_ids),
                       used - HEADER_ALLOW_TOKENS, over_budget, missing, mode,
                       {"quotes": len(ql), "chunks_used": len(chunks_both),
                        "statements_covered": covered([c["chunk_id"] for c in chunks_both]) + len(ql)})

    # 默认：只注证据块，按 chunk 顺序（＝原文顺序）
    lines = [f"# {source_id} 的{label}（图路由：{len(stmts)} 条断言 → {len(chunks)} 个证据块）", ""]
    for ch in chunks:
        src_ids = by_chunk.get(ch["chunk_id"], [])
        lines.append(f"### {ch['chunk_id']}（来自 {len(src_ids)} 条{label}断言：{', '.join(src_ids[:4])}"
                     f"{'…' if len(src_ids) > 4 else ''}）")
        lines.append(ch["text"])
        lines.append("")
    injection = "\n".join(lines)
    return _result(source_id, claim_type, stmts, chunks, injection, len(chunk_ids),
                   used - HEADER_ALLOW_TOKENS, over_budget, missing, mode,
                   {"quotes": 0, "chunks_used": len(chunks),
                    "statements_covered": covered([c["chunk_id"] for c in chunks])})


def _result(source_id, claim_type, stmts, chunks, injection, n_chunk_ids, tokens_used,
            over_budget, missing, mode, extra) -> dict:
    return {
        "source_id": source_id,
        "claim_type": claim_type,
        "statements": stmts,
        "chunks": chunks,
        "injection": injection,
        "trace": {
            "route": "graph_role",                 # 走的哪条路（便于 trace / A-B 对比）
            "mode": mode,
            "statements": len(stmts),
            "chunk_ids": n_chunk_ids,
            "tokens_used": tokens_used,
            "over_budget": over_budget,            # 顶预算没进去的（上报，不静默）
            "missing_in_kb": missing,              # 图里的 chunk 在向量库里找不到（数据不一致）
            **extra,
        },
    }
