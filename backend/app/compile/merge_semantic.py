"""Pass 2-② 第二刀（L3 义合并）· P1：候选生成（确定性，不调 LLM）。

为什么需要（memory/0028 考古层第二十三/二十四段）：
  L1 只管"字形"（单复数/大小写/所有格/连字符），够不着"形状不同但同指"：
  缩写↔全称、括号限定、改写/语序、同义异名。这些要靠**语义判断**（L3，LLM 带上下文）。
  但 N² 不能全问 LLM——P1 先用确定性信号把候选**收窄**成个位数~几十对。

信号（取并集，同一对可多信号命中）：
  · bracket  ：`X (Y)` 与 `X`——**确定性规则**（括号是限定语，X (Y) ≡ X，且 X (Y) ≠ Y）
  · acronym  ：`VM` ↔ `virtual machine`（词首字母匹配；**只是候选**，AI↔agent identity 是反例）
  · rewrite  ：token 集合高度重合但**互不包含**（抓改写/语序，如 NIST 那两条、
               `user oversight capacity` ↔ `user's capacity for oversight`）——**这是同指候选**
  · subset   ：一方 token 被另一方包含（`Claude` vs `Claude Code`、`MCP` vs `MCP server`）
               —— **单独分桶，不进合并候选**：这类绝大多数是上下位/从属，不是同一个东西
               （要让 LLM 判断"是不是同指"，它会忍不住把 Claude Code 并进 Claude）
  · neighbor ：共享 ≥2 个非枢纽邻居 —— 也只作**关联留档**（实测噪声大：
               `Slack`↔`api.anthropic.com` 只因同段出现；关联 ≠ 同指）

**共现不做候选源**（实测教训）：chunk 级共现太宽——A5 里它一下吐出 2289 对，
几乎"同块出现过"的都在里面，等于没收窄。共现改为**候选上的备注**（这对是否在
同一块共现过），供 L3 判定时参考。

P1 的产出只用于**人工过目**：确认候选质量后，P2 才把候选交给 LLM 带上下文判 same/different。
"""

from __future__ import annotations

import re

from app.compile.merge import form_key

_BRACKET = re.compile(r"^(?P<outer>.*?)\s*\((?P<inner>[^()]*)\)\s*$")


def split_bracket(name: str) -> tuple[str, str] | None:
    """把 `X (Y)` 拆成 (X, Y)；不是括号结尾则返回 None。"""
    m = _BRACKET.match((name or "").strip())
    if not m:
        return None
    outer, inner = m.group("outer").strip(), m.group("inner").strip()
    return (outer, inner) if outer and inner else None


def normalize_brackets(entities: list[dict]) -> tuple[list[dict], list[dict]]:
    """括号确定性规则：**外层形式也作为实体存在时**，`X (Y)` 归一为 X（原名进 aliases）。

    为什么不无条件剥括号：`Ephemeral container (claude.ai)` 的外层在语料里根本不存在
    （0 次字面出现），剥掉等于丢掉限定语——那属于"无证据改名"，本项目的红线。
    只有"X 确实另有独立实体"才说明括号只是限定，可确定合并。
    """
    by_key: dict[str, list[str]] = {}
    for e in entities:
        by_key.setdefault(form_key(e.get("name", "")), []).append(e.get("name", ""))

    out: list[dict] = []
    log: list[dict] = []
    for e in entities:
        name = (e.get("name") or "").strip()
        parts = split_bracket(name)
        if not parts:
            out.append(e)
            continue
        outer, inner = parts
        key = form_key(outer)
        others = [n for n in by_key.get(key, []) if n != name]
        if not others:
            out.append(e)  # 留作 L3 候选（外层不存在 → 不用确定规则）
            continue
        ne = dict(e)
        ne["name"] = outer
        ne["aliases"] = sorted({*(e.get("aliases") or []), name})
        ne["merge"] = [*(e.get("merge") or []), {"method": "bracket", "from": name}]
        out.append(ne)
        log.append({"from": name, "to": outer, "inner": inner, "with": sorted(others)})
    return out, log


# 英文虚词：比较 token 集合时忽略（`user's capacity for oversight` 里的 's / for 不该算差异）
STOPWORDS = {
    "a", "an", "the", "of", "for", "on", "in", "to", "and", "or", "with", "by",
    "s", "t", "is", "are", "be", "into", "from", "at", "as",
}


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z0-9]+", (name or "").lower()) if t and t not in STOPWORDS]


def is_acronym_of(short: str, long: str) -> bool:
    """short 是 long 的首字母缩写？（两边都要求形状像"缩写 / 多词短语"）"""
    s = re.sub(r"[^A-Za-z0-9]", "", short or "")
    if not (2 <= len(s) <= 6 and s.isupper()):
        return False
    toks = _tokens(long)
    if len(toks) < 2:
        return False
    return "".join(t[0] for t in toks) == s.lower()


def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


CANDIDATE_SIGNALS = ("bracket", "acronym", "overlap", "subset", "neighbor")


def collect_candidates(
    entities: list[dict],
    edges: list[dict],
    provenance: dict[str, set[str]] | None = None,
    *,
    overlap_threshold: float = 0.6,
    hub_degree: int = 8,
) -> dict[str, list[dict]]:
    """生成 L3 候选。返回 {"merge": [...合并候选...], "subsumption": [...上下位/从属对...]}。

    entities 应为 **L1 合并后**的实体；subsumption 只做记录，不交给 LLM 判合并。
    """
    prov = provenance or {}
    names = [e.get("name", "") for e in entities]
    by_name = {e.get("name", ""): e for e in entities}
    ids = {e.get("name", ""): e.get("id") or e.get("name", "") for e in entities}

    # 邻居表（边在 L1 后已 remap 成 id，故按 id 建表）
    neigh: dict[str, set[str]] = {}
    for ed in edges:
        f, t = ids.get(ed.get("from", "")), ids.get(ed.get("to", ""))
        f = f or (ed.get("from") or None)
        t = t or (ed.get("to") or None)
        if f and t and f != t:
            neigh.setdefault(f, set()).add(t)
            neigh.setdefault(t, set()).add(f)
    degree = {ids[n]: len(neigh.get(ids[n], ())) for n in names}

    found: dict[tuple[str, str], dict] = {}

    def add(a: str, b: str, signal: str, **extra) -> None:
        if a == b:
            return
        key = tuple(sorted((a, b)))
        item = found.setdefault(key, {"a": key[0], "b": key[1], "signals": [], "detail": {}})
        if signal not in item["signals"]:
            item["signals"].append(signal)
        if extra:
            item["detail"].update(extra)

    for e in entities:
        n = e.get("name", "")
        parts = split_bracket(n)
        if parts:
            add(n, parts[0], "bracket", inner=parts[1])  # 外层不存在的括号实体
        for other in names:
            if other == n:
                continue
            if is_acronym_of(n, other) or is_acronym_of(other, n):
                add(n, other, "acronym")
            ov = _token_overlap(n, other)
            sub = _token_subset(n, other)
            if ov >= overlap_threshold and not sub:
                add(n, other, "rewrite", overlap=round(ov, 2))
            elif sub:
                add(n, other, "subset")

    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            shared = {x for x in neigh.get(ids[a], set()) if x in neigh.get(ids[b], set())}
            useful = sorted(x for x in shared if degree.get(x, 0) <= hub_degree)
            if len(useful) >= 2:
                add(a, b, "neighbor", shared_neighbors=useful[:6], n_shared=len(useful))

    out: list[dict] = []
    for item in found.values():
        a, b = item["a"], item["b"]
        item["counts"] = {a: _count(by_name.get(a)), b: _count(by_name.get(b))}
        item["chunks"] = {a: sorted(prov.get(a, ())), b: sorted(prov.get(b, ()))}
        shared_chunks = sorted(set(prov.get(a, ())) & set(prov.get(b, ())))
        if shared_chunks:  # 共现只作备注
            item["detail"]["cooccur"] = shared_chunks
        out.append(item)
    out.sort(key=lambda d: (-len(d["signals"]), d["a"], d["b"]))
    merge = [d for d in out if any(s in ("bracket", "acronym", "rewrite") for s in d["signals"])]
    subsump = [d for d in out if d not in merge and "subset" in d["signals"]]
    assoc = [d for d in out if d not in merge and d not in subsump]
    return {"merge": merge, "subsumption": subsump, "association": assoc}


def _token_subset(a: str, b: str) -> bool:
    """一方 token 是另一方的**真**子集——抓上下位/从属名（`MCP` ⊂ `MCP server`）。"""
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb or ta == tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return small < big and len(small) >= 1 and len(small) <= 2


def _count(entity: dict | None) -> int:
    if not entity:
        return 0
    return 1 + sum(int(m.get("count", 1)) for m in (entity.get("merge") or []))


# ---------------------------------------------------------------------------
# 向量分组：把"临时实体卡"嵌入后按相似度聚成组（候选来源，不调用大模型）
# ---------------------------------------------------------------------------

def build_cards(
    entities: list[dict],
    edges: list[dict],
    *,
    max_neighbors: int = 10,
) -> list[dict]:
    """给每个实体拼一张**临时实体卡**：名字 + 别名 + 邻居实体名（都按字母排序）。

    卡片只用于候选阶段计算，**不落库**（实体仍然不加 description 字段）。
    排序是为了消掉"顺序"这个变量——实测顺序扰动会把相似度拉到 0.999 级别，
    虽然淹不掉信号，但固定顺序后直接归零，没有理由留着。
    """
    id2name = {e.get("id") or e.get("name", ""): e.get("name", "") for e in entities}
    weight: dict[str, dict[str, int]] = {e.get("id") or e.get("name", ""): {} for e in entities}
    for ed in edges:
        f = id2name.get(ed.get("from", ""), ed.get("from", ""))
        t = id2name.get(ed.get("to", ""), ed.get("to", ""))
        if not f or not t or f == t:
            continue
        weight.setdefault(f, {})[t] = weight.setdefault(f, {}).get(t, 0) + 1
        weight.setdefault(t, {})[f] = weight.setdefault(t, {}).get(f, 0) + 1

    cards: list[dict] = []
    for e in entities:
        eid = e.get("id") or e.get("name", "")
        name = e.get("name", "")
        aliases = sorted({a for a in (e.get("aliases") or []) if a and a != name})
        nbrs = sorted(weight.get(eid, {}).items(), key=lambda kv: (-kv[1], kv[0]))
        nbr_names = sorted(n for n, _ in nbrs[:max_neighbors])
        parts = [name]
        if aliases:
            parts.append("aliases: " + ", ".join(aliases))
        if nbr_names:
            parts.append("neighbors: " + ", ".join(nbr_names))
        cards.append({"id": eid, "name": name, "card": " | ".join(parts)})
    return cards


def group_by_similarity(vectors: list[list[float]], threshold: float) -> list[list[int]]:
    """按余弦相似度分组：相似度 ≥ 阈值即视为同组（连通分量）。返回下标分组列表。"""
    import numpy as np

    m = np.asarray(vectors, dtype=float)
    m = m / np.clip(np.linalg.norm(m, axis=1, keepdims=True), 1e-12, None)
    sim = m @ m.T
    np.fill_diagonal(sim, -1.0)

    n = len(vectors)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in zip(*np.where(np.triu(sim >= threshold, 1))):
        ri, rj = find(int(i)), find(int(j))
        if ri != rj:
            parent[rj] = ri

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


# ---------------------------------------------------------------------------
# L3 的**结构证据**（确定性，2026-09-17 定）
# ---------------------------------------------------------------------------
#
# 背景（memory/0028 考古层第二十五段 + 第四十段后的复测）：
#   · 当初（第二十五段）想用"同谓词 + 同宾语 → 同指候选"，实测被否，归因是"图太薄"；
#   · 2026-09-17 复查发现还有第二层原因：**信号与目标错位**——别名分裂时边也跟着分裂，
#     真同指对（VM ↔ virtual machine、EDR ↔ 全称）**共享 0 个具体关系对**；
#     而"同类不同实例"（MCP server / plugin / web search tool 都 provides content）共享满。
#   故本信号的正确位置是：**L3 判定的证据（加分项）+ 否决倾向（减分项）**，不是候选源。


def _profile(edges: list[dict]) -> dict[str, set[tuple[str, str, str]]]:
    """实体 → 具体关系轮廓：{(方向, 谓词, 另一端的实体 id)}。"""
    prof: dict[str, set[tuple[str, str, str]]] = {}
    for e in edges:
        f, t, p = e.get("from"), e.get("to"), e.get("predicate")
        if not f or not p:
            continue
        prof.setdefault(f, set()).add(("out", p, t or ""))
        if t:
            prof.setdefault(t, set()).add(("in", p, f))
    return prof


def structure_evidence(
    entities: list[dict],
    edges: list[dict],
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], dict]:
    """给每对候选算结构证据（确定性、不调 LLM）。

    返回 {pair: {shared, conflict, degree_a, degree_b, shared_pairs, conflict_pairs}}：
      · **shared**  = 两者共享的「同一方向 + 同一谓词 + 同一端点」对（同一物的两个名字常连同样的东西）
      · **conflict** = 两者在同一方向 + 同一谓词下**各自指向不同端点**的对数（更像两个不同物）
      · shared ≥ 2 才提示"结构支持"（2026-09-10 的教训：1 条边撞上就是 100% 相似，没有证明力）
    """
    name_to_id = {}
    for e in entities:
        name_to_id[e.get("name") or ""] = e.get("id")
        for a in e.get("aliases") or []:
            name_to_id[a] = e.get("id")
    prof = _profile(edges)

    def pid(x: str) -> str:
        return name_to_id.get(x) or x

    out: dict[tuple[str, str], dict] = {}
    for a, b in pairs:
        pa, pb = prof.get(pid(a), set()), prof.get(pid(b), set())
        shared = sorted(pa & pb)
        # conflict：同方向 + 同谓词，但另一端不同
        conflict = 0
        by_key_a: dict[tuple[str, str], set[str]] = {}
        by_key_b: dict[tuple[str, str], set[str]] = {}
        for d, p, o in pa:
            by_key_a.setdefault((d, p), set()).add(o)
        for d, p, o in pb:
            by_key_b.setdefault((d, p), set()).add(o)
        for k in by_key_a.keys() & by_key_b.keys():
            if by_key_a[k] ^ by_key_b[k]:      # 同方向 + 同谓词，另一端不同
                conflict += 1
        deg_a, deg_b = len(pa), len(pb)
        out[(a, b)] = {
            "shared": len(shared),
            "conflict": conflict,
            "degree_a": deg_a,
            "degree_b": deg_b,
            "shared_pairs": ["·".join(x) for x in shared[:6]],
            # 否决倾向只给"两边都有一定规模、且同谓词下指向不同端点"的情形——
            # 否则"一个只有 1 条边的实体"随便撞上就成冲突，等于噪声（2026-09-10 的教训）。
            "veto_candidate": bool(conflict >= 2 and min(deg_a, deg_b) >= 3),
        }
    return out


# ---------------------------------------------------------------------------
# L3 判定（LLM 带证据判 same / different）——**提示词是基础件，改动需人工过目**
# ---------------------------------------------------------------------------
#
# 关于"拿不准判 different"（沛东 2026-09-18 定的两条原则之一，理由留在注释、不写进提示词）：
#   实体合并是**结构性动作**——合错了会污染下游全体（所有引用该实体的边都错）且不可逆，
#   而漏合只是少一次优化 → 故这里**从严（宁可漏、不可错）**。
#   对照：claim_type 那类**索引性标签**相反，**从宽（宁可多、不可漏）**——见 classify.py 注释。

L3_PROMPT = """你是「实体同指判定器」。输入是一批**候选对**（A / B），每对附：
  · 候选信号（acronym = 词首字母匹配；rewrite = 词集高度重合但互不包含；bracket = 括号限定）
  · 各自代表边（谓词 + 另一端 + **逐字原文引文**）——引文是最终依据
  · 各自在原文里的出处块

任务：对每一对判 same / different，并给出**逐字原文依据**。

判据（从严）：
1. **same**：仅当两者在原文里**指同一个事物**——只是叫法不同（缩写与全称、括号补充、改写或语序差异、冠词 / 所有格 / 单复数差异）。
2. **different**：以下情形一律判 different——
   · 上下位 / 包含 / 从属（一者是另一者的一类、一部分、一个实例）
   · 一者是另一者的**属性、度量、约束或状态**（不是那个事物本身）
   · **同一角色的不同实例**（功能相同但不是同一个东西）
   · 一方比另一方**多了会改变所指的限定词**（否定、范围、条件、程度、序数等）
3. **拿不准判 different**。
4. 依据必须**逐字**出现在上面给出的引文里；写不出逐字依据 → 判 different。
5. 不要用你自己的世界知识补全（只依据原文）。
6. `canonical`（same 时的规范名）必须用**原文的语言**写（本语料是英文），不要翻译。

输出（严格 JSON，无多余文字、无代码围栏）：
{"results":[{"id":0,"verdict":"same","reason":"一句话理由","evidence":"逐字原文片段","canonical":"same 时给推荐规范名：取更完整、更常见的那个"}]}
id 必须原样回抄；verdict 只允许 same / different。"""


def _edge_line(e: dict, claims: list[dict], side: str = "out", id2name: dict[str, str] | None = None) -> str:
    """一条代表边的一行描述（含原文引文）。

    方向**始终**按 from —谓词→ to 渲染（side 只用于挑选"离本实体最近的那条"，
    不能拿来翻转箭头——否则入边会把两端渲染成同一个名字）。
    端点一律渲染成**名字**：id 对 LLM 没有意义。
    """
    mapping = id2name or {}

    def nm(x: str | None) -> str:
        return mapping.get(x, x) if x else "∅"

    frm = nm(e.get("from")) if e.get("from") else "∅"
    to = nm(e.get("to"))
    idx = e.get("claim_idx")
    quote = ""
    if isinstance(idx, int) and 0 <= idx < len(claims):
        evs = claims[idx].get("evidence_texts") or []
        quote = (evs[0] if evs else "")[:160]
    return f"{frm} —{e.get('predicate')}→ {to} ｜原文：{quote}"


def build_l3_user(
    entities: list[dict],
    edges: list[dict],
    pairs: list[tuple[str, str]],
    claims: list[dict],
    evidence: dict[tuple[str, str], dict] | None = None,
    *,
    max_edges: int = 3,
) -> tuple[str, dict[int, list[str]]]:
    """组装 L3 的 user 输入。返回 (文本, {配对序号: 该对可用的引文池})。

    引文池用于**逐字依据护栏**：模型给的 evidence 必须能在池子里原样找到。
    """
    ev = evidence or {}
    name_to_id = {}
    id2name: dict[str, str] = {}
    for e in entities:
        name_to_id[e.get("name") or ""] = e.get("id")
        if e.get("id"):
            id2name[e["id"]] = e.get("name") or e["id"]
        for a in e.get("aliases") or []:
            name_to_id[a] = e.get("id")
    by_id: dict[str, list[dict]] = {}
    for e in edges:
        by_id.setdefault(e.get("from"), []).append(e)
        if e.get("to"):
            by_id.setdefault(e["to"], []).append(e)

    lines: list[str] = []
    pools: dict[int, list[str]] = {}
    for i, (a, b) in enumerate(pairs):
        info = ev.get((a, b), {})
        lines.append(f'[{i}] A = "{a}"　B = "{b}"')
        if info.get("signals"):
            lines.append(f"  候选信号：{', '.join(info['signals'])}")
        pool: list[str] = []
        for label, name in (("A", a), ("B", b)):
            eid = name_to_id.get(name)
            rel = [e for e in by_id.get(eid or "", [])]
            if not rel:
                lines.append(f"  {label} 的代表边：（无）")
                continue
            lines.append(f"  {label} 的代表边：")
            for e in rel[:max_edges]:
                line = _edge_line(e, claims, "out", id2name)
                lines.append("    - " + line)
                if "｜原文：" in line:
                    q = line.split("｜原文：", 1)[1].strip()
                    if q:
                        pool.append(q)
        # 同一条边可能同时挂在 A/B 两侧（A→B），引文池去重（保序）
        pools[i] = list(dict.fromkeys(pool))
    return "\n".join(lines), pools


def parse_l3(
    raw: str | dict,
    pools: dict[int, list[str]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """解析 L3 响应并做**逐字依据护栏**：依据不在引文池里 → 降级 different（记账）。

    与 S4.5 的「只删不改」同源纪律：模型可以省，但不能编。
    """
    import json as _json

    if isinstance(raw, dict):
        data = raw
    else:
        text = (raw or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") :] if "{" in text else text
        data = _json.loads(text)
    items = data.get("results") if isinstance(data, dict) else data
    out: list[dict] = []
    demoted: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        rec = {
            "id": it.get("id"),
            "verdict": (it.get("verdict") or "").strip().lower(),
            "reason": (it.get("reason") or "").strip(),
            "evidence": (it.get("evidence") or "").strip(),
            "canonical": (it.get("canonical") or "").strip() or None,
        }
        if rec["verdict"] not in ("same", "different"):
            rec["verdict"] = "different"
            rec["reason"] = rec["reason"] or "verdict 非法 → 保守判 different"
        if rec["verdict"] == "same":
            pool = (pools or {}).get(rec["id"]) if isinstance(rec["id"], int) else None
            ok = bool(rec["evidence"]) and (
                pool is None or any(rec["evidence"] in q or q in rec["evidence"] for q in pool)
            )
            if not ok:
                demoted.append(dict(rec, demoted_from="same", why="依据不逐字/缺失"))
                rec["verdict"] = "different"
                rec["reason"] = (rec["reason"] + "｜依据护栏降级") if rec["reason"] else "依据护栏降级"
        out.append(rec)
    return out, demoted


def split_by_veto(
    pairs: list[tuple[str, str]],
    evidence: dict[tuple[str, str], dict],
) -> tuple[list[int], list[dict]]:
    """**确定性前置**：结构上命中否决的候选对直接判 different，不送 LLM。

    为什么放在这里而不是写进提示词（2026-09-18 沛东指出的问题）：
      "基于结构证据的否定"是**确定性操作**（同方向 + 同谓词、端点不同、两边都有规模），
      交给 LLM 既没必要、又会污染它的语义判断。确定性的事由程序做，LLM 只做语义判断。

    返回 (送 LLM 的下标列表, 被否决的记录)。否决口径见 `structure_evidence.veto_candidate`：
    conflict ≥ 2 且两边度数都 ≥ 3——否则"只有 1 条边的实体"随便撞上就成冲突（噪声）。
    """
    keep: list[int] = []
    vetoed: list[dict] = []
    for i, pair in enumerate(pairs):
        info = evidence.get(pair, {})
        if info.get("veto_candidate"):
            vetoed.append({
                "id": i,
                "a": pair[0],
                "b": pair[1],
                "verdict": "different",
                "by": "structure_veto",
                "reason": f"结构否决：同谓词下端点不同 {info.get('conflict', 0)} 处，"
                          f"两边度数 {info.get('degree_a', 0)}/{info.get('degree_b', 0)}",
                "evidence": "",
                "canonical": None,
            })
        else:
            keep.append(i)
    return keep, vetoed


async def judge_pairs(
    client,
    entities: list[dict],
    edges: list[dict],
    pairs: list[tuple[str, str]],
    claims: list[dict],
    evidence: dict[tuple[str, str], dict] | None = None,
) -> dict:
    """L3 判定编排：**确定性否决先用**，剩下的才送 LLM；逐字依据护栏兜底。

    返回 {"results": [...], "vetoed": [...], "demoted": [...], "skipped_ids": [...], "raw": str}
    —— 每个候选对都必须有结论（same / different / 否决 / 降级），不许静默。
    """
    from app.agent.llm import LLMMessage
    from scripts.compile_slice_b6 import _chat_json

    ev = evidence or {}
    keep_ids, vetoed = split_by_veto(pairs, ev)
    results: list[dict] = []
    demoted: list[dict] = []
    raw = ""
    error = ""
    if keep_ids:
        sub_ev = {}
        for i in keep_ids:
            a, b = pairs[i]
            sub_ev[(a, b)] = ev.get((a, b), {})
        text, pools = build_l3_user(entities, edges, [pairs[i] for i in keep_ids], claims, sub_ev)
        try:
            obj, raw = await _chat_json(
                client,
                [LLMMessage(role="system", content=L3_PROMPT), LLMMessage(role="user", content=text)],
                label="S6-L3",
            )
        except Exception as exc:      # 调用失败 → 全部候选留待下次（不猜、不静默丢）
            error = f"{type(exc).__name__}: {exc}"
            obj = {"results": []}
        recs, demoted = parse_l3(obj, pools)
        # 把子编号（0..n-1）映回原下标
        for r in recs:
            if isinstance(r.get("id"), int) and 0 <= r["id"] < len(keep_ids):
                r["id"] = keep_ids[r["id"]]
        for d in demoted:
            if isinstance(d.get("id"), int) and 0 <= d["id"] < len(keep_ids):
                d["id"] = keep_ids[d["id"]]
        results = recs
    answered = {r["id"] for r in results if isinstance(r.get("id"), int)} | {
        v["id"] for v in vetoed
    }
    skipped_ids = [i for i in range(len(pairs)) if i not in answered]
    return {
        "results": sorted(results + vetoed, key=lambda r: r.get("id") or 0),
        "vetoed": vetoed,
        "demoted": demoted,
        "skipped_ids": skipped_ids,
        "error": error,
        "raw": raw,
    }
