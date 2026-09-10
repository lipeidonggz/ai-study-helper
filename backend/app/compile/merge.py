"""Pass 2-② 第一刀：形合并（确定性命名归一，不调 LLM）。

为什么需要（memory/0028 考古层第二十一段）：
  Pass 2-① 抽实体时"不去重"——同一对象会以多种写法落成多条记录
  （单复数 / 大小写 / 冠词 / 所有格 / 连字符），跨批更是必然重复。
  实测 A5：4 遍并集 303 个实体名里 21 对单复数、8 个所有格名、
  3 个带括号注释、2 个从句式名（另一类问题）。

本刀只做"形可直合"：
  规范化 key（小写 / 去冠词 / 去所有格 / 连字符折叠为空格 / 词尾单复数折叠）
  → 同 key 归一组 → 组内选 canonical，其余进 aliases（**原名一律保留**，
  故检索两个方向都能命中）。

本刀**不做**（留给后面的刀）：
  · 语义合并（缩写/全称、同义异名）→ 第二刀（候选收窄 + LLM 带上下文判）
  · 括号注释（`Sealed VM (Claude Cowork)` 的括号可能是必要限定）→ 第二刀候选
  · 跨文章异名消解 → 后置
  · aliases 反污染校验 → 第四刀

安全边界：
  1. 只有"同一 key 下出现 ≥2 个**不同**写法"才合并——不做无证据改名
     （如只有 `agents` 出现时不会被改成 `agent`）；
  2. 词尾折叠只作用于**最后一个词**（名词短语的中心词）；
  3. `SINGULAR_KEEP` 兜住"以 s 结尾但本身是单数"的常见词（https/tls/dns…），
     防止 `https → http` 这类错合；再加"必须两形并存"的约束，误伤面极小。
"""

from __future__ import annotations

import re

ARTICLES = {"the", "a", "an"}

# 以 s 结尾、但本身是单数/缩写/不可数的词：禁止去尾 s
SINGULAR_KEEP = {
    "https", "tls", "dns", "cms", "aws", "ios", "devops", "mlops",
    "news", "series", "species", "bus", "gas", "alias", "atlas", "canvas", "chaos",
    # -is 结尾的单数名词（不列出来的会按常规去尾 s，只在"两形并存"时才会合并 → 风险有限）
    "analysis", "basis", "crisis", "thesis", "hypothesis", "axis", "emphasis",
    "synthesis", "diagnosis",
}

# 不规则复数 / 拉丁-希腊复数：模式规则抓不到，必须显式映射（复数 → 单数）。
# 只放"无歧义"的；有歧义的一律不放（宁漏合不误合）：
#   · media（medium 既是"媒介"也是"中等/介质"）、data（datum 极罕见但语义不同）→ 故意不收
IRREGULAR_PLURALS = {
    "children": "child", "people": "person", "men": "man", "women": "woman",
    "feet": "foot", "teeth": "tooth", "geese": "goose", "mice": "mouse",
    "indices": "index", "matrices": "matrix", "vertices": "vertex",
    "appendices": "appendix", "analyses": "analysis", "theses": "thesis",
    "hypotheses": "hypothesis", "crises": "crisis", "axes": "axis",
    "diagnoses": "diagnosis", "parentheses": "parenthesis", "ellipses": "ellipsis",
    "criteria": "criterion", "phenomena": "phenomenon",
    "curricula": "curriculum", "formulae": "formula", "formulas": "formula",
    "cacti": "cactus", "fungi": "fungus", "nuclei": "nucleus", "radii": "radius",
    "stimuli": "stimulus", "syllabi": "syllabus", "alumni": "alumnus",
    "antennae": "antenna", "larvae": "larva", "vertebrae": "vertebra",
    "genera": "genus", "corpora": "corpus", "spectra": "spectrum",
    "memoranda": "memorandum", "strata": "stratum", "codices": "codex",
}

# 单复数同形（本身既是单数也是复数）：禁止去尾 s，否则会把"另一个词"合进来。
# 例：means（手段）不该被折成 mean（均值）；species/series/sheep/aircraft… 同理。
UNCHANGED_PLURALS = {
    "means", "species", "series", "sheep", "deer", "fish", "aircraft", "spacecraft",
    "hovercraft", "offspring", "salmon", "trout", "swine", "bison", "moose",
    "barracks", "crossroads", "headquarters", "gallows", "innings",
}

# 复数形态本身是另一个常见名词：同样禁止去尾 s。
# 例：customs（海关）≠ custom（习俗/定制）；works（工厂/作品集）、premises（场所/前提）同理。
PLURAL_IS_OTHER_NOUN = {
    "customs", "works", "premises", "ethics", "politics", "economics",
    "physics", "mathematics", "statistics", "mechanics",
}

# 一律不做单复数折叠的词表（两类的并集 + 以 s 结尾的单数/缩写/不可数词）
NO_FOLD = SINGULAR_KEEP | UNCHANGED_PLURALS | PLURAL_IS_OTHER_NOUN

_POS = re.compile(r"['\u2019]s\b", flags=re.I)  # 所有格：Claude Code's → Claude Code
_APOS = re.compile(r"['\u2019]")
_DASH = re.compile(r"[\u2010-\u2015\-_/]")  # 各种连字符 + 下划线 + 斜杠 → 空格
_WS = re.compile(r"\s+")
_SEP = re.compile(r"[\s/\\_\u2010-\u2015\-]+")
_PATHISH = re.compile(r"[/\\~]")


def _singular(word: str) -> str:
    """英文名词短语中心词的单复数折叠（保守版）。"""
    w = word
    if w in IRREGULAR_PLURALS:
        return IRREGULAR_PLURALS[w]
    if len(w) <= 2 or w in NO_FOLD:
        return w
    if w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ss", "us")):
        return w
    if w.endswith("es") and w[:-2].endswith(("ss", "x", "z", "ch", "sh")):
        return w[:-2]
    if w.endswith("s"):
        return w[:-1]
    return w


def form_key(name: str) -> str:
    """形归一 key：小写 / 去冠词 / 去所有格 / 连字符折叠 / 中心词单复数折叠。

    注意：key 只用于"判定是否同形"，**不落库**；落库的是 canonical 原名。
    """
    tokens = _norm_tokens(name)
    if not tokens:
        return ""
    tokens[-1] = _fold_head(name, tokens[-1])
    return " ".join(tokens)


def _norm_tokens(name: str) -> list[str]:
    """形归一的"折前"分词：去引号 → 去所有格 → 连字符折叠 → 小写 → 去前导冠词。"""
    s = (name or "").strip()
    s = s.replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = _POS.sub("", s)  # Claude Code's auto mode → Claude Code auto mode
    s = _APOS.sub("", s)
    s = _DASH.sub(" ", s)  # read-only → read only
    s = _WS.sub(" ", s).strip()
    s = s.lower()
    tokens = [t for t in s.split(" ") if t]
    while tokens and tokens[0] in ARTICLES:
        tokens.pop(0)  # 去前导冠词：the sandbox → sandbox
    return tokens


def _skip_singular(raw: str, head: str) -> bool:
    """不该做单复数折叠的中心词：路径/文件名、全大写缩写、含非字母字符的词。

    实测教训：`~/.aws/credentials` 被折成 `~/.aws/credential`、`HCS → hc`——
    路径与缩写不是英文名词短语，不能按名词单复数处理。
    """
    if _PATHISH.search(raw or ""):
        return True
    raw_head = _SEP.split((raw or "").strip())[-1].strip("'\u2019") if (raw or "").strip() else ""
    if len(raw_head) >= 2 and raw_head.isupper():  # HCS / AWS；注意 VMs、APIs 不算全大写
        return True
    return bool(re.search(r"[^a-z]", head))  # 含数字/点号等


def _fold_head(raw: str, head: str) -> str:
    return head if _skip_singular(raw, head) else _singular(head)


def _plural_penalty(name: str) -> int:
    """0 = 该名字本身已是（中心词的）单数形，1 = 是复数形。用于 canonical 选型。"""
    tokens = _norm_tokens(name)
    if not tokens:
        return 0
    return 0 if _fold_head(name, tokens[-1]) == tokens[-1] else 1


def _pick_canonical(variants: list[str], counts: dict[str, int]) -> str:
    """组内选规范名：次数多 → 单数形优先 → 更短 → 无撇号 → 带连字符 → 字典序。"""
    return sorted(
        variants,
        key=lambda n: (-counts[n], _plural_penalty(n), len(n), n.count("'"), -n.count("-"), n),
    )[0]


def merge_confidence(
    variants: list[str],
    sets: dict[str, set[str]],
) -> tuple[str, set[str]]:
    """L2 置信度（**不否决合并**，只给结论打标）：

      · 两形都有出处且**相交** → high（同一物两种写法，共现是强证据）
      · 两形都有出处但**不相交** → low（可能是同一物分布在不同章节，也可能同形异义
        → 合并照做，但进"待抽检清单"。理由：不共现是弱证据，不能当否决票）
      · 只有一形（或没有）有出处 → unknown（LLM 做了表面归一，如 read-only → read only，
        不是可疑信号，不进清单）
    """
    with_prov = [v for v in variants if sets.get(v)]
    if len(with_prov) < 2:
        return "unknown", set()
    shared = set.intersection(*[sets[v] for v in with_prov])
    return ("high", shared) if shared else ("low", set())


def merge_entities(
    entities: list[dict],
    edges: list[dict],
    *,
    id_prefix: str = "ent",
    provenance: dict[str, set[str]] | None = None,
) -> dict:
    """对 ① 的产物做形合并：实体归一 + 边 remap + 自环丢弃，返回新 bundle + audit。

    实体 id 用确定性序号（便于跨遍对比）；真实入库时换成无业务含义的唯一 id。
    provenance（名字 → 出处 chunk 集合）可选：给了就启用 L2「共现才合并」。
    """
    groups: dict[str, list[dict]] = {}
    for e in entities:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        groups.setdefault(form_key(name), []).append(e)

    out_entities: list[dict] = []
    name_to_id: dict[str, str] = {}  # 原名 → canonical id（含别名）
    key_to_id: dict[str, str] = {}  # 形归一 key → canonical id（兜住大小写/单复数引用）
    merge_log: list[dict] = []
    dup_log: list[dict] = []
    review_log: list[dict] = []
    eid_n = 0

    ordered = sorted(groups.items(), key=lambda kv: kv[0])
    for key, recs in ordered:
        counts: dict[str, int] = {}
        for r in recs:
            counts[r["name"].strip()] = counts.get(r["name"].strip(), 0) + 1
        variants = sorted(counts)
        sets = {v: set((provenance or {}).get(v) or ()) for v in variants}
        conf, shared = merge_confidence(variants, sets)

        eid_n += 1
        eid = f"{id_prefix}{eid_n:04d}"
        canonical = _pick_canonical(variants, counts)
        others = [v for v in variants if v != canonical]

        aliases = set(others)
        for r in recs:
            for a in r.get("aliases") or []:  # ① 顺手给的别名先并进来（第四刀再校验）
                if isinstance(a, str) and a.strip() and a.strip() != canonical:
                    aliases.add(a.strip())

        types = [r.get("type") for r in recs if r.get("type")]
        etype = next((r.get("type") for r in recs if r["name"].strip() == canonical and r.get("type")), None)
        if not etype and types:
            etype = max(set(types), key=types.count)

        ent: dict = {"id": eid, "name": canonical, "type": etype or "concept", "aliases": sorted(aliases)}
        if others:
            ent["merge"] = [{"method": "form", "from": v, "count": counts[v]} for v in others]
            log: dict = {"canonical": canonical, "key": key, "variants": {v: counts[v] for v in variants}}
            if provenance:
                log["shared_chunks"] = sorted(shared)
                log["confidence"] = conf
                ent["merge_confidence"] = conf
            merge_log.append(log)
            if conf == "low":  # 已合并，但两形出处不共现 → 进"待抽检清单"
                review_log.append(
                    {
                        "key": key,
                        "canonical": canonical,
                        "variants": {v: counts[v] for v in variants},
                        "chunks": {v: sorted(sets[v]) for v in variants},
                    }
                )
        elif len(recs) > 1:  # 同名重复（跨批同形）：不算"形合并"，但要记一笔
            dup_log.append({"name": canonical, "records": len(recs)})
        out_entities.append(ent)
        for v in variants:
            name_to_id[v] = eid
        key_to_id[key] = eid

    def _resolve(nm: str) -> str | None:
        """边端点归一：先精确名，再形归一 key（`External attackers` → `external attackers`）。"""
        return name_to_id.get(nm) or key_to_id.get(form_key(nm))

    out_edges: list[dict] = []
    self_loops: list[dict] = []
    dangling: list[str] = []
    for e in edges:
        src = (e.get("from") or "").strip()
        dst = (e.get("to") or "").strip()
        sid = _resolve(src) if src else None
        did = _resolve(dst) if dst else None
        if src and sid is None:
            dangling.append(src)
        if dst and did is None:
            dangling.append(dst)
        if sid and did and sid == did:
            self_loops.append({"from": src, "predicate": e.get("predicate"), "to": dst, "claim_idx": e.get("claim_idx")})
            continue
        ne = dict(e)
        if sid:
            ne["from"] = sid
        if did:
            ne["to"] = did
        roles = e.get("roles")
        if isinstance(roles, dict) and roles:
            ne["roles"] = {
                k: [name_to_id.get(str(v).strip(), v) for v in (vs if isinstance(vs, list) else [vs])]
                if isinstance(vs, (list, str))
                else vs
                for k, vs in roles.items()
            }
        out_edges.append(ne)

    audit = {
        "entities_in": len(entities),
        "entities_out": len(out_entities),
        "edges_in": len(edges),
        "edges_out": len(out_edges),
        "form_merged_groups": len(merge_log),
        "form_merged_names": sum(len(m["variants"]) - 1 for m in merge_log),
        "exact_dup_groups": len(dup_log),
        "exact_dup_records": sum(d["records"] - 1 for d in dup_log),
        "low_confidence_merges": len(review_log),
        "self_loops_dropped": len(self_loops),
        "dangling_endpoints": sorted(set(dangling)),
    }
    return {
        "entities": out_entities,
        "edges": out_edges,
        "merge_log": merge_log,
        "dup_log": dup_log,
        "review_log": review_log,
        "audit": audit,
    }


# ---------------------------------------------------------------------------
# 审计辅助（不参与合并）：找出第一刀**漏合**的疑似单复数对
# ---------------------------------------------------------------------------

def _alnum_lower(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _name_pattern(name: str) -> re.Pattern[str] | None:
    """把实体名编成"容忍排版差异"的匹配式（空格→\\s+、连字符/引号宽容），并加词边界。"""
    n = (name or "").strip()
    if not n:
        return None
    parts: list[str] = []
    for ch in n:
        if ch.isspace():
            parts.append(r"\s+")
        elif ch in "-\u2010\u2011\u2012\u2013\u2014\u2015":
            parts.append(r"[\-\u2010-\u2015]")
        elif ch in "'\u2019":
            parts.append(r"['\u2019]")
        else:
            parts.append(re.escape(ch))
    body = "".join(parts)
    # 词边界用 lookaround 而非 \b（名字可能以连字符等非词字符结尾）
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.I)


def occurrence_chunks(name: str, clean_t: str, chunks: list[dict]) -> set[str]:
    """名字在原文里**字面出现**的 chunk 集合（L2 的"出处"）。

    从原文确定性推导，不依赖 LLM 自报——LLM 报 claim_idx 会漏报/编造，
    而"这个名字在哪些块里出现过"是可以直接从文本算出来的。
    """
    pat = _name_pattern(name)
    if pat is None or not clean_t:
        return set()
    hits: set[str] = set()
    for m in pat.finditer(clean_t):
        lo, hi = m.start(), m.end()
        for ch in chunks:
            if ch["start_pos"] < hi and ch["end_pos"] > lo:
                hits.add(ch["chunk_id"])
    return hits


def provenance_for(names: list[str], clean_t: str, chunks: list[dict]) -> dict[str, set[str]]:
    """批量算出处：名字 → chunk 集合。"""
    return {n: occurrence_chunks(n, clean_t, chunks) for n in names if n}


def plural_candidates(name: str) -> set[str]:
    """一个名字"可能的单数/复数形态"（审计用；故意放宽，宁可多报不漏报）。"""
    w = _alnum_lower(name)
    cands = {w}
    if not w:
        return cands
    cands.add(w + "s")          # 反向：单数 → 复数
    cands.add(w + "es")
    if w.endswith("y"):
        cands.add(w[:-1] + "ies")
    if len(w) > 2 and w.endswith("s"):
        cands.add(w[:-1])       # 正向：复数 → 单数
        cands.add(w[:-2])
    if w.endswith("ies") and len(w) > 3:
        cands.add(w[:-3] + "y")
    if w in IRREGULAR_PLURALS:
        sing = IRREGULAR_PLURALS[w]
        cands.add(sing)
        cands.add(sing + "s")
    return {c for c in cands if c}


def missed_plural_pairs(entities: list[dict]) -> list[tuple[str, str]]:
    """审计：两个**实体名**"疑似互为单复数"、但落在**不同实体**上 → 漏合候选。

    在**已合并**的实体表上跑；只看实体名（别名冲突另见 alias_collisions）。
    """
    owner: dict[str, str] = {}   # 名字(原样) → 所属实体 id
    for e in entities:
        n = e.get("name", "")
        if n:
            owner[n] = e.get("id") or n

    by_alnum: dict[str, list[str]] = {}
    for n in owner:
        by_alnum.setdefault(_alnum_lower(n), []).append(n)

    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for n in owner:
        for c in plural_candidates(n):
            if c == _alnum_lower(n):
                continue
            for other in by_alnum.get(c, []):
                if owner[other] == owner[n]:
                    continue
                pair = tuple(sorted({n, other}))  # type: ignore[assignment]
                if pair in seen:
                    continue
                seen.add(pair)
                out.append(pair)
    return sorted(out)


def alias_collisions(entities: list[dict]) -> list[tuple[str, list[str]]]:
    """审计：同一个"名字/别名"被挂到多个实体上 → 别名冲突（第四刀反污染的输入）。"""
    owners: dict[str, set[str]] = {}
    for e in entities:
        eid = e.get("id") or e.get("name", "")
        for n in [e.get("name", "")] + list(e.get("aliases") or []):
            if n:
                owners.setdefault(n, set()).add(eid)
    return sorted((n, sorted(ids)) for n, ids in owners.items() if len(ids) > 1)
