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
