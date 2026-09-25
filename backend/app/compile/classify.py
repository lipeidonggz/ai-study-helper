"""S7 · 分类（Pass 2③）：**statement 组装 + 归属边 + reify 标记**（claim_type 赋值见下一步）。

设计口径（架构文档 C4 / D 节 / E 节 schema / 决策 9，2026-09-18 与沛东确认）：
  · **范围 = 全部 claim，不是"全部边"**：A5 394 条 claim 里只有 314 条建了边，剩下 80 条
    （端点取不出概念：属性值 / 从句式 / 指代未解）**同样建 statement**——它们仍是"作者说了的话"，
    只是不挂概念、挂在文档上。这正是 C2 给 `summary` 类定的处置，推广到全部"未建边"claim。
  · **statement 是索引项，不是事实源**：它必须能回到原文（`evidence_chunks`），
    所以"每条 claim ↔ 一个 statement"是**构造保证**（可对账、无丢无增）。
  · 本步（S7-1 / S7-2）纯程序；`claim_type` 赋值（LLM）与 `reify_reasons` 标注随后接。

产物（对齐 E 节 schema）：
  statements[]：{id, claim_idx, subject(concept ref|null), object(concept ref|null),
                 predicate, roles?, polarity?, claim_type?, reify_reasons[],
                 evidence_texts[], evidence_chunks[], asserted_by(doc ref), about[]}
  edges[]：doc —asserts→ statement；statement —groundedIn→ chunk；concept —hasStatement→ statement
"""

from __future__ import annotations

import json

from app.compile.assemble import claim_to_chunk

# ---------------------------------------------------------------------------
# S7-3 · claim_type 判定（LLM）——**提示词是基础件，改动需人工过目**
# ---------------------------------------------------------------------------
#
# 只判两类（架构文档 D 节已定稿）：LimitationStatement / OutlookStatement；其余留 null。
# 为什么要它：全量 statement 解决了"每条断言可寻址"，但没有解决"394 条里哪些是局限"——
# 而"按角色过滤"（各文的局限 / 展望）需要一条**可枚举**的键；靠向量排序做这件事，
# 在"列出全部局限"这种枚举语义上不保证完备（正是图谱立项要解决的问题）。
# 为什么"宁多勿漏"（沛东纠正，2026-09-18）：这两类是**索引性标签**，不是结构性动作——
#   漏标＝该断言在"按角色过滤"里彻底不可见、下游无从发现；多标只是多一条噪声（下游读到逐字引文能自行判断）。
#   对照：实体合并 / 谓词归一 / 边方向属**结构性动作**，错了会污染下游全体且不可逆，故那里从严（宁可漏、不可错）。

CLAIM_TYPE_PROMPT = """你是「论述角色判定器」。输入是一批**断言**（每条含 id、主语 / 谓词 / 宾语，
以及**逐字原文引文**）。任务：判断每条断言是否属于下面两类**论述角色**之一；不属于就给 null。

- LimitationStatement（局限）：作者在陈述某事物的不足、风险、失败、代价、边界，或"做不到 / 尚未解决"。
- OutlookStatement（展望）：作者在陈述将来会怎样、打算做什么、下一步、期待，或"待解决的问题"。

判据（从严）：
1. 只依据**引文**判断，不要用你自己的知识补全。
2. 只判**作者自己的论断**：引述他人观点、概念定义、方法 / 机制描述、纯事实陈述，都不属于这两类 → null。
3. **宁多勿漏**：只要引文确实在讲"不足 / 风险 / 代价 / 边界 / 做不到"（或"将来 / 计划 / 待解决"），
   即使不够典型、或不确定作者是否在强调它，也**要标**；完全属于第 2 条那些排除类的才给 null。
4. 依据必须**逐字**出现在引文里；写不出逐字依据 → null。
5. reason 用原文的语言写。

输出（严格 JSON，无多余文字、无代码围栏）：
{"results":[{"id":0,"claim_type":"LimitationStatement","reason":"一句话理由","evidence":"逐字原文片段"}]}
id 必须原样回抄；claim_type 只允许 LimitationStatement / OutlookStatement / null 三种取值。"""


def build_claim_type_user(statements: list[dict], claims: list[dict]) -> tuple[str, dict[str, list[str]]]:
    """组装 S7-3 的 user 输入。返回 (文本, {statement_id: 该条可用的引文池})。

    引文池用于**逐字依据护栏**：模型给的 evidence 必须能在该条自己的引文里找到。
    """
    payload = []
    pools: dict[str, list[str]] = {}
    for st in statements:
        c = claims[st["claim_idx"]] if 0 <= st["claim_idx"] < len(claims) else {}
        quotes = [q for q in (c.get("evidence_texts") or []) if isinstance(q, str) and q.strip()]
        payload.append({
            "id": st["id"],
            "subject": c.get("subject") or "",
            "predicate": c.get("predicate") or "",
            "object": c.get("object") or "",
            "evidence": quotes[0] if quotes else "",
        })
        pools[st["id"]] = quotes
    return json.dumps({"claims": payload}, ensure_ascii=False, separators=(",", ":")), pools


CLAIM_TYPES = ("LimitationStatement", "OutlookStatement")


async def classify_claim_types(
    client,
    statements: list[dict],
    claims: list[dict],
    *,
    batch: int = 50,
) -> dict:
    """S7-3：批量判 `claim_type`（LLM），依据护栏兜底。

    返回 {by_id: {st_id: {claim_type, reason, evidence}}, demoted: [...], stats: {...}}。
    漏条补问一次（同 S5 / 归一步的做法）；补不回的留 null 并记账——**不猜**。
    """
    from app.agent.llm import LLMMessage
    from scripts.compile_slice_b6 import _chat_json

    by_id: dict[str, dict] = {}
    demoted_all: list[dict] = []
    batches = [statements[i: i + batch] for i in range(0, len(statements), batch)]
    failed = 0

    async def ask(sts: list[dict], label: str) -> None:
        text, pools = build_claim_type_user(sts, claims)
        obj, _raw = await _chat_json(
            client,
            [LLMMessage(role="system", content=CLAIM_TYPE_PROMPT),
             LLMMessage(role="user", content=text)],
            label=label,
        )
        recs, demoted = parse_claim_type(obj, pools)
        for r in recs:
            if r["id"]:
                by_id[r["id"]] = r
        demoted_all.extend(demoted)

    for bi, chunk in enumerate(batches, start=1):
        try:
            await ask(chunk, f"S7 {len(statements)} 条 claim_type 批{bi}/{len(batches)}")
        except Exception as exc:
            failed += 1
            print(f"  [S7] 第 {bi} 批失败（{exc}）→ 该批留 null")
        for retry in range(1, 3):
            missing = [s for s in chunk if s["id"] not in by_id]
            if not missing:
                break
            print(f"  [S7] 批{bi} 缺 {len(missing)} 条，第 {retry} 次补问")
            try:
                await ask(missing, f"S7 补问{bi}-{retry}")
            except Exception as exc:
                print(f"  [S7] 补问失败（{exc}）")

    n_limit = sum(1 for r in by_id.values() if r["claim_type"] == "LimitationStatement")
    n_out = sum(1 for r in by_id.values() if r["claim_type"] == "OutlookStatement")
    return {
        "by_id": by_id,
        "demoted": demoted_all,
        "stats": {
            "statements": len(statements),
            "answered": len(by_id),
            "limitation": n_limit,
            "outlook": n_out,
            "null": sum(1 for r in by_id.values() if not r["claim_type"]),
            "demoted": len(demoted_all),
            "batches": len(batches),
            "failed_batches": failed,
        },
    }


def parse_claim_type(raw: str | dict, pools: dict[str, list[str]] | None = None) -> tuple[list[dict], list[dict]]:
    """解析 S7-3 响应并做**逐字依据护栏**：依据不在该条引文里 → 置 null（记账）。

    与 S4.5 / S6-B 同源纪律：模型可以判 null（漏），但不能编依据（错标）。
    """
    data = raw if isinstance(raw, dict) else json.loads((raw or "").strip().strip("`") or "{}")
    items = data.get("results") if isinstance(data, dict) else data
    out: list[dict] = []
    demoted: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        sid = str(it.get("id") or "").strip()
        ct = it.get("claim_type")
        ct = ct.strip() if isinstance(ct, str) else None
        rec = {
            "id": sid,
            "claim_type": ct if ct in CLAIM_TYPES else None,
            "reason": (it.get("reason") or "").strip(),
            "evidence": (it.get("evidence") or "").strip(),
        }
        if rec["claim_type"]:
            pool = (pools or {}).get(sid) or []
            ok = bool(rec["evidence"]) and any(
                rec["evidence"] in q or q in rec["evidence"] for q in pool
            )
            if not ok:
                demoted.append(dict(rec, demoted_from=rec["claim_type"], why="依据不逐字/缺失"))
                rec["claim_type"] = None
        out.append(rec)
    return out, demoted


def build_statements(
    claims: list[dict],
    merged: dict,
    clean_t: str,
    chunks: list[dict],
    *,
    source_id: str,
    id_prefix: str = "st",
) -> dict:
    """S7-1 / S7-2：每条 claim → statement + 归属边（程序，确定性）。"""
    edges_by_claim: dict[int, dict] = {}
    for e in merged.get("edges") or []:
        idx = e.get("claim_idx")
        if isinstance(idx, int):
            edges_by_claim[idx] = e

    ent_by_id = {e.get("id"): e for e in (merged.get("entities") or [])}
    claim_chunk = claim_to_chunk(claims, clean_t, chunks)
    doc_id = f"doc:{source_id}"

    statements: list[dict] = []
    out_edges: list[dict] = []
    for i, c in enumerate(claims):
        sid = f"{id_prefix}{i + 1:04d}"
        e = edges_by_claim.get(i)
        subject_id = e.get("from") if e else None
        object_id = (e.get("to") or None) if e else None
        about = [x for x in (subject_id, object_id) if x and x in ent_by_id]
        chunk_id = claim_chunk.get(i) or (e.get("chunk_id") if e else None)

        st = {
            "id": sid,
            "claim_idx": i,
            "subject": subject_id,                     # 概念 ref；端点取不出 → None（挂 doc）
            "object": object_id,
            "predicate": c.get("predicate_normalized") or c.get("predicate") or "",
            "predicate_surface": c.get("predicate") or "",
            "polarity": c.get("polarity"),
            "roles": c.get("roles"),
            "claim_type": None,                        # S7-3（LLM）填
            "reify_reasons": [],                       # S7-4 填
            "evidence_texts": c.get("evidence_texts") or [],
            "evidence_chunks": [],          # S8 逐段锚定后填（多对多）
            "naive_chunk": chunk_id,        # S7 的粗映射（单块），保留作对照
            "asserted_by": doc_id,
            "about": about,
            "has_edge": bool(e),
        }
        statements.append(st)

        # 骨架边（E 节命名）：doc → asserts → statement；concept → hasStatement → statement；
        # statement → groundedIn → chunk
        out_edges.append({"from": doc_id, "predicate": "asserts", "to": sid,
                          "produced_by": "compile"})
        for cid in about:
            out_edges.append({"from": cid, "predicate": "hasStatement", "to": sid,
                              "produced_by": "compile"})
        # groundedIn 边在 S8 逐段锚定之后统一生成（见下方）

    # ---- S8 · 引文锚定（A4）：逐段定位 evidence_texts → 并集得 evidence_chunks ----
    # 逻辑复用 scripts/compile_slice_b6.py 的 `_a4`（脚本里已跑通、实测定位 100%）——
    # app 复用 scripts 的工具函数是本项目已声明的单一来源做法（见 service.py 的 TODO）。
    from scripts.compile_slice_b6 import _a4

    _a4({"statements": statements}, clean_t, chunks, source_id, 0)   # file_idx=0（S0 多文件未做）
    for st in statements:
        for cid in st.get("evidence_chunks") or []:
            out_edges.append({"from": st["id"], "predicate": "groundedIn", "to": f"chunk:{cid}",
                              "produced_by": "auto"})

    n = len(statements)
    stats = {
        "statements": n,
        "with_concept": sum(1 for s in statements if s["about"]),
        "doc_only": sum(1 for s in statements if not s["about"]),      # 无概念端点 → 挂 doc
        "with_chunk": sum(1 for s in statements if s["evidence_chunks"]),
        "with_edge": sum(1 for s in statements if s["has_edge"]),
        "edges": len(out_edges),
        "asserts": sum(1 for e in out_edges if e["predicate"] == "asserts"),
        "has_statement": sum(1 for e in out_edges if e["predicate"] == "hasStatement"),
        "grounded_in": sum(1 for e in out_edges if e["predicate"] == "groundedIn"),
        # S8 锚定统计：逐段定位成功的 statement / 降级到 section 的 / 命中的 chunk 数
        "anchored": sum(1 for s in statements if s.get("evidence_chunks")),
        "section_level": sum(1 for s in statements if s.get("anchor") == "section"),
        "evidence_chunks": sum(len(s.get("evidence_chunks") or []) for s in statements),
        "multi_chunk": sum(1 for s in statements if len(s.get("evidence_chunks") or []) > 1),
    }
    return {
        "statements": statements,
        "edges": out_edges,
        "doc": {"id": doc_id, "type": "document", "ref": source_id},
        "stats": stats,
    }
