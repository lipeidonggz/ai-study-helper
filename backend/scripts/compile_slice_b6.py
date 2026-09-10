"""B6 · 最小编译切片：真实跑一遍编译层（O2/A5），不写向量库。

为什么做（B6）：
  用真实语料验证编译输出 schema + 编译 prompt 是否可落地——LLM 能否正确抽出
  概念/陈述/边、reify_reason / claim_type 判断是否合理、引文锚定（span→chunk）能否定位到块。

输入/坐标：
  从 MANIFEST 取源 → 复用切块抽取（_file_units）得到"清洗后原文 T"（与 chunk_offsets 同源同坐标）。
  O2/A5 均为单文件、整篇 < 12k token → 一个内容单元（不按小节拆，避免破坏上下文）。

流程：
  清洗后 T → 内容单元（整篇）→ 编译 prompt → DeepSeekLLMClient → 解析编译产物 bundle
  → 引文锚定：逐段归一化 evidence_texts → 在 T 定位起止 → 与 chunk_offsets 重叠并集 → evidence_chunks。

输出：bundle JSON（backend/data/tmp/b6_<source>.json），供人工审质量。不做任何向量入库。
运行：cd backend && .venv\\Scripts\\python.exe -m scripts.compile_slice_b6 --source O2,A5
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from app.agent.llm import DeepSeekLLMClient, LLMMessage
from app.kb.ingest import _file_units
from app.kb.manifest import parse_manifest
from app.storage.sqlite.kb_store import KbStore
from app.storage.sqlite.settings_store import SqliteSettingStore


BACKEND = Path(__file__).resolve().parents[1]  # backend/
REPO = BACKEND.parent                          # 仓库根目录
APP_DB = BACKEND / "data" / "app.db"           # 运行时 DB（backend/data）
KB_DB = BACKEND / "data" / "kb.db"
MANIFEST_PATH = REPO / "data" / "kb-src" / "MANIFEST.md"  # 素材台账（仓库根 data/kb-src）
OUT_DIR = BACKEND / "data" / "tmp"
DEFAULT_MODEL = "deepseek-chat"
PASS2_CLAIM_BUDGET = 40  # Pass2 每批 claim 上限（输出规模 ∝ claims；按"块"累积、块不跨批）


# ---------------------------------------------------------------------------
# 编译系统提示词（基础件，草稿；附字段/概念定义 + 规则 + 示例）
# ---------------------------------------------------------------------------
PASS1_SYSTEM = """你是「知识抽取器」。把输入的「清洗后原文窗口」抽成【原子断言】列表（每条 = 一个 subject–predicate–object 的最小断言，可带角色），只输出严格 JSON。

# 输入
一段清洗后原文（纯段落文本、无编号、可能含多个主题）。

# 输出（严格 JSON；无多余文字、无代码围栏）
{"claims":[{"subject":"…","predicate":"…","object":"…","roles":{"instrument":["…"]},"evidence_texts":["逐字连续引文","…"]}]}
roles 可省略。

# A. 抽取范围
抽：针对某个「可被点名的知识对象」（有名有姓，如 containment / model layer / egress control / Claude Cowork；仅举例，不限于此），说了「可被引证之事」的断言——是什么 / 怎么运作 / 为什么 / 有什么局限 / 未来方向 / 与谁的关系 / 谁主张什么 / 分成哪类 / 属性 / 机制。
跳过：过渡 / 引子 / 钩子；只复述上一句的；无新断言的例子；主体是「我们 / 本文 / 这个改动」这类叙述 / 元主体。
拿不准就抽（宁多勿漏）。

# B. 原子性（一条 = 一个最小断言）
- 复合句（一句话两个意思）→ 拆成两条。例：`X is fast and Y is slow` → `X is fast` ／ `Y is slow`。
- 复合谓词（一个主语、两个动词）→ 拆成两条。例：`X reads and writes files` → `X reads files` ／ `X writes files`。
- 列表（一个位置多个项 A/B/C，主语或宾语皆可）→ 每条一个项；只拆「项的个数」，谓词不变、不新造动词。例：`X reads files, sockets, and env vars` → `X reads files` ／ `X reads sockets` ／ `X reads env vars`。

# C. 字段约定
- subject / object = 有名名词短语（可点名对象）；predicate = 动词。
- subject 取该断言所描述的、最具体、最像话题的核心实体。例：✗ `An important factor is caching` → ✓ `caching is an important factor`。
- 不作 subject：抽象类别 / 属性 / 从句；报告来源 / 元主体（telemetry / we / 本文 / 作者）。
- 动名 / 命题主语必须归约：以 granting / placing / supervising / limiting / having 或 "X that …"、"only when …" 开头的主语，改写为「施事（或核心实体）→ 谓词 → 该命题」。
- 定义 / 归类断言（is / is a / has…）：把被归类 / 被描述的实体放 subject。

# D. 角色（n-ary 关系的限定；写在 roles 里，不占 object）
- 「经由 / 通过 / 借助 / 用 X」这类「手段 / 工具」→ 放进 `roles.instrument`（可多值）；不要塞进 object、不要拆成新条、不要新造动词。
- 没有角色就不要输出 `roles` 字段（不要输出空 `{}`）。
- 例：`X repels attacks via sandboxes and VMs` → 一条：`{subject:X, predicate:repels, object:attacks, roles:{instrument:["sandboxes","VMs"]}}`。

# E. evidence_texts
- 每条 claim 给逐字原文引文数组。
- 每个元素 = 同一段原文内的一段连续文字；含标点、与原文逐字一致；不许拼接、不许改写。
- 跨段（即便相邻段）→ 拆成多个数组元素。
- 某段找不到逐字对应 → 放弃该段；整条都找不到 → 放弃该 claim。
"""


PASS2_SYSTEM = """你是「知识图谱构建器」。把输入【按块组织的原子断言清单】组装成 KG，只输出严格 JSON，不要多余解释、不要代码围栏。不得改断言内容——证据就在每块的 context 原文里。

# 输入
按块组织的断言清单：每个块含 context（该块清洗后原文）+ 该块内的 claims；每条 claim 含 claim_idx（全局下标）、subject / predicate / object（可空）/ roles（可空）。

# 输出（严格 JSON；无多余文字、无代码围栏）
{"entities":[{"name","type":"concept|document","aliases":[]}],
 "terms":[{"label","denotes":"<entity name>","note":""}],
 "statements":[{"claim_idx":0,"claim_type":null,"reify_reasons":["…"],"about":[],"roles":{},"asserted_by":"<doc ref>"}],
 "relations":[{"from","predicate","to","produced_by":"compile|compile-hypothesis","note":""}],
 "data_evidence":[{"claim_idx":N,"evidence_for":0}]}

# A. 术语
- entity（节点）：type ∈ {concept, document}。concept=可被点名的稳定知识对象/主题。
- term：记录某实体另一正式名称；term.label=该名称；term.denotes=规范 entity 名。仅术语漂移/来源差异才建 term（实体 aliases 不建 term）。
- **aliases（别名）**：同一实体的**另一个名字**；判据 = 把原文里的名字换成它、**句意不变**。
  · 可放：单复数 / 大小写 / 连字符 / 冠词 / 缩写与全称（virtual machine ↔ VM）。
  · 不放（各建 entity、用 relation 连）：它**使用 / 包含 / 依赖**的东西（"OS-level sandbox" 用了 "Seatbelt" → Seatbelt 是独立实体、不是别名）、**描述 / 定语**、**相关但非同一对象**的。
  · **别名不得同时又是独立 entity**：若同一名字既出现在某 entity 的 aliases、又作为 entity 出现，只留 entity，把它从别人的 aliases 里去掉或合并。
- statement：被 reify 成节点的断言（rdf:Statement）。
- relation（边）：实体/陈述间的谓词，取值来自受控词表（见 E）。

# B. 结构规范化（核心：借 context 把 Pass 1 的糙结构归约成干净结构）
- 从句/命题主语或宾语（"supervising …"、"whether …"、"how to …"、"X that …"）→ 用 context 归约成其中的有名概念/实体；不得留作 entity 名或 statement 端点。
- 手段/路径（"经由/通过/借助 X"）若落在谓词或宾语里 → 用 context 取出，放进 statement 的 roles.instrument（可多值）。
- entity 名必须是干净名词概念（可点名）；拒绝整句/命题式短语。
- 同义/变体（同一概念多种叫法）→ 合并到同一 canonical entity（别名口径见 A）。

# C. 每条 claim 的归处
→ reify_reasons 非空 → statement；否则 → entity+relation 或 entity 属性。
- statement：reify_reasons 任一命中（见 D）。
- entity+relation：结构关系（partOf/involves/precedes）、机制/定义（X 是 Y 的一种 / X 通过 A 达成 Y）。谓词"经由 A/B/C"复合 → 拆成 X↔A、X↔B、X↔C。
- entity 属性：单属性（X 由两部分组成）→ 挂 entity 属性/标签，不立 statement。
- data/benchmark（含 %/数字/基准结果）→ 不独立立 statement；设 evidence_for=所支撑 statement 的 claim_idx。

# D. reify_reasons（statement 判据，任一命中即立）
{type_filter, argument_endpoint, concept_central}
- type_filter：该断言论述功能是局限/展望（用户按类型过滤）。
- argument_endpoint：该断言需作 support/attack/complement 的端点。
- concept_central：关于枢纽概念、可被独立提问（含估值/影响断言论"X 重要/导致/影响 Y"、"什么是 X"）。

# E. claim_type 与 relation 谓词
- claim_type ∈ {LimitationStatement, OutlookStatement, null}：Limitation=已承认的当前不足/具体翻车教训；Outlook=面向未来演化的威胁/下一步呼吁；以 what's broken vs looking ahead + 判官 a5-e2/a5-e3 为界；其余 null。claim_type 仅当 type_filter 在场时设。
- relation 谓词（受控词表）∈ {partOf, involves, precedes, support, attack, complement, asserts, hasStatement}。估值/影响（causes/affects/important）是 statement 的谓词，不进此表。

# F. statement 字段
claim_idx（回指条目的全局下标）、claim_type、reify_reasons、about（该断言关于的 entity 名）、roles（若归一出手段/路径；否则省略）、asserted_by（文档 ref）。不回抄 subject/predicate/object（程序取自 claim，防输出过大）。
"""

# 去重段两版：strict=从严/grounded（新）；loose=凭常识合并（旧，仅对比用）
_DEDUP_STRICT = """# 去重（判据；宁拆勿错合）
- 形态变体（形；不看上下文即可判）→ **合**：完全同名 / 单复数 / 大小写 / 连字符 / 冠词 / 缩写与全称（VM、VMs → VM）。
- 上下文里的同义（义；**须 context 支持**）→ **合**：context 明确把两者当同一对象（缩写展开 / 明说"也叫…" / 并列定义）。
- **不得仅凭你自己的常识判"同义"**——上下文不支持就**不合并**。
- 不合并：相近但不同的概念（VM vs sandbox）。
- 拿不准 → **不合并**（错误合并比拆更难挽回）。
- 合并后：canonical 取最常用/最正式的（别名口径见 A）。
- relation 的 from/to 用实体名 或 语句 claim_idx；不出现同对象既局限又展望。
"""
_DEDUP_LOOSE = """# 去重
- 同实体合并到同一 name（canonical）；不出现同对象既局限又展望。
- relation 的 from/to 用实体名 或 语句 claim_idx。
"""


def _build_clean_t(sections) -> str:
    """构建"清洗后全文 T"：与 chunk_sections / chunk_offsets 同一坐标（各节段落按序 \\n 连接）。"""
    return "\n".join(p for s in sections for p in s.paragraphs)


def _window_text(text: str, max_chars: int) -> list[str]:
    """按段落（\\n）边界把长文本切成窗口，每窗 ≤ max_chars（超长单段整体保留）。"""
    paras = text.split("\n")
    windows: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for p in paras:
        add = len(p) + (1 if cur else 0)
        if cur and cur_len + add > max_chars:
            windows.append("\n".join(cur))
            cur = [p]
            cur_len = len(p)
        else:
            cur.append(p)
            cur_len += add
    if cur:
        windows.append("\n".join(cur))
    return windows


def _norm(s: str) -> str:
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u00a0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _find_raw(evidence: str, text: str) -> tuple[int, int] | None:
    """在 text 中定位 evidence 的 [start, end)。先精确，再归一化（空白/引号/破折号）。"""
    idx = text.find(evidence)
    if idx >= 0:
        return idx, idx + len(evidence)

    # 归一化折叠：把 text 折叠成一个 collapsed 字符串，并记录每个 collapsed 字符对应的原始起始位置。
    # 重建时再定位 evidence，把 collapsed 下标映射回原始偏移。
    collapsed: list[str] = []
    raw_map: list[int] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c in "\u201c\u201d\u2018\u2019\u2013\u2014\u00a0":
            collapsed.append(_norm(c))
            raw_map.append(i)
            i += 1
        elif c.isspace():
            collapsed.append(" ")
            raw_map.append(i)
            while i < len(text) and text[i].isspace():
                i += 1
        else:
            collapsed.append(c)
            raw_map.append(i)
            i += 1
    ctext = "".join(collapsed)
    nev = _norm(evidence)
    ci = ctext.find(nev)
    if ci < 0:
        return None
    start = raw_map[ci]
    end = raw_map[ci + len(nev) - 1] + 1
    return start, end


def _overlap(lo: int, hi: int, chunks: list[dict]) -> list[str]:
    """[lo, hi) 与各 chunk 的 [start, end) 重叠 → 命中 chunk_id 列表（多对多）。"""
    hits = []
    for ch in chunks:
        if ch["start_pos"] < hi and ch["end_pos"] > lo:
            hits.append(ch["chunk_id"])
    return hits


def _extract_json(content: str) -> dict:
    """从 LLM 输出里剥离代码围栏/多余文本，解析出 JSON 对象。"""
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("LLM 输出中没有 JSON 对象")
    obj = text[start : end + 1]
    try:
        return json.loads(obj)
    except json.JSONDecodeError as e:
        # 容忍"Extra data"（JSON 后有冗余内容）：取第一个完整 JSON 值。
        try:
            val, _ = json.JSONDecoder().raw_decode(text[start:])
            return val
        except Exception:
            pass
        # 常见 LLM 输出问题：字符串内的裸换行未转义、尾逗号。做一次宽松修复再试。
        repaired = _repair_json(obj)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            dbg = OUT_DIR / f"b6_parse_fail_{int(__import__('time').time())}.json"
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            dbg.write_text(obj, encoding="utf-8")
            raise ValueError(f"LLM JSON 仍无法解析（{e}），原始落盘 {dbg}") from e


def _repair_json(s: str) -> str:
    """宽松修复：把双引号字符串内的裸换行转义、去尾逗号（容忍 LLM 大块 JSON 的不规范）。"""
    out: list[str] = []
    in_str = False
    esc = False
    for ch in s:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ch in "\n\r":
            out.append("\\n")
            continue
        out.append(ch)
    repaired = "".join(out)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def _a4(bundle: dict, clean_t: str, chunks: list[dict], source_id: str, file_idx: int) -> dict:
    """引文锚定：对每条 statement，逐段定位 evidence_texts，取并集得 evidence_chunks。"""
    for st in bundle.get("statements", []):
        quotes = st.get("evidence_texts")
        if isinstance(quotes, str):  # 兼容旧版单字符串输出
            quotes = [quotes]
        if not (isinstance(quotes, list) and quotes):
            old = st.get("evidence_text")
            quotes = [old] if isinstance(old, str) and old else []
        quotes = [q for q in quotes if isinstance(q, str) and q]
        if not quotes:
            st["clean_ref"] = {"source_id": source_id, "file_idx": file_idx, "start_pos": None, "end_pos": None}
            st["anchor"] = "section"
            st["evidence_chunks"] = []
            st["a4_note"] = "无 evidence_texts"
            continue

        ranges: list[tuple[int, int]] = []
        failed: list[str] = []
        for q in quotes:
            pos = _find_raw(q, clean_t)
            if pos is None:
                failed.append(q[:28])
            else:
                ranges.append(pos)

        if not ranges:
            st["clean_ref"] = {"source_id": source_id, "file_idx": file_idx, "start_pos": None, "end_pos": None}
            st["anchor"] = "section"
            st["evidence_chunks"] = []
            st["a4_note"] = "未定位到任何分段（全部降级 section 弱锚）：" + "; ".join(failed)
        else:
            st["clean_ref"] = {"source_id": source_id, "file_idx": file_idx, "start_pos": min(r[0] for r in ranges), "end_pos": max(r[1] for r in ranges)}
            st["anchor"] = None
            hits: set[str] = set()
            for r in ranges:
                hits.update(_overlap(r[0], r[1], chunks))
            st["evidence_chunks"] = sorted(hits)
            ext = ("；分段降级:" + "; ".join(failed)) if failed else ""
            if st["evidence_chunks"]:
                st["a4_note"] = "OK" + ext
            else:
                st["a4_note"] = "定位到但未重叠任何 chunk（检查偏移空间）" + ext
    return bundle


def _build_pass2_blocks(claims: list[dict], clean_t: str, chunks: list[dict]) -> list[dict]:
    """按"锚句所在块"把 claims 分组为 Pass 2 输入块。

    block = {chunk_id, context(块文本), claims:[{claim_idx, subject, predicate, object, roles?}]}。
    每个块文本只出现一次（去重）；claim 不带短锚（块文本已含）。未定位的 claim 归入孤立块。
    """
    order = {ch["chunk_id"]: ch["start_pos"] for ch in chunks}
    blocks: dict[str, dict] = {}
    orphan: dict = {"chunk_id": "_orphan", "context": "", "claims": []}
    for ci, c in enumerate(claims):
        cid: str | None = None
        for e in c.get("evidence_texts", []):
            p = _find_raw(e, clean_t)
            if p is None:
                continue
            for ch in chunks:
                if ch["start_pos"] < p[1] and ch["end_pos"] > p[0]:
                    cid = ch["chunk_id"]
                    break
            if cid:
                break
        if cid and cid not in blocks:
            ch = next(x for x in chunks if x["chunk_id"] == cid)
            blocks[cid] = {"chunk_id": cid, "context": clean_t[ch["start_pos"]:ch["end_pos"]], "claims": []}
        blk = blocks[cid] if cid else orphan
        item = {"claim_idx": ci, "subject": c.get("subject"), "predicate": c.get("predicate"), "object": c.get("object")}
        if c.get("roles"):
            item["roles"] = c["roles"]
        blk["claims"].append(item)
    out = sorted(blocks.values(), key=lambda b: order.get(b["chunk_id"], 10 ** 9))
    if orphan["claims"]:
        out.append(orphan)
    return out


async def _compile_source(
    source_id: str,
    api_key: str,
    model: str,
    token_limit: int = 12000,
    pass1_window_chars: int = 5000,
    only_pass1: bool = False,
    tag: str = "",
    pass2_from: str = "",
    dedup: str = "strict",
) -> dict | None:
    """对单个源做整篇编译（一个内容单元），返回 bundle（含引文锚定结果）；源缺失返回 None。"""
    manifest = parse_manifest(MANIFEST_PATH)
    src = next((s for s in manifest if s.source_id == source_id), None)
    if src is None:
        print(f"[B6] 源 {source_id} 不在 manifest（{MANIFEST_PATH}）")
        return None

    units = _file_units(src)  # [(file_title, sections)]
    if not units:
        print(f"[B6] {source_id}: 未切出任何内容")
        return None
    if len(units) > 1:
        print(f"[B6] {source_id}: 多文件源（{len(units)} 文件），本切片仅编译第 0 个文件")

    file_title, sections = units[0]
    clean_t = _build_clean_t(sections)
    if len(clean_t) > token_limit * 3.5:  # 粗略：字符约为 token 的 3.5 倍，超限则提醒
        print(f"[B6] {source_id}: 内容单元约 {len(clean_t)} 字符，可能超 {token_limit} token，请检查")

    kb = KbStore(KB_DB)
    chunks = [c for c in kb.list_chunk_offsets(source_id) if c.get("file_idx", 0) == 0]

    content_unit = {
        "source_id": source_id,
        "file_idx": 0,
        "section_path": "/".join([s.path for s in sections if s.path]),
        "clean_start": 0,
        "clean_end": len(clean_t),
        "text": clean_t,
    }
    client = DeepSeekLLMClient(api_key=api_key, model=model)

    if pass2_from:
        cf = OUT_DIR / f"b6_claims_{source_id}_{pass2_from}.json"
        claims: list[dict] = json.loads(cf.read_text(encoding="utf-8"))
        print(f"[B6] {source_id}: Pass2-only，载入 claims {len(claims)} 条（{cf.name}）")
    else:
        # Pass 1：按窗口抽原子断言 + 逐字短锚（不判类型；窗口小 → 输出不超限 + 省输出 token）
        windows = _window_text(clean_t, pass1_window_chars)
        claims = []
        for wi, w in enumerate(windows):
            u1 = (
                f"内容单元窗口：source_id={source_id}；file_idx=0；window={wi + 1}/{len(windows)}；"
                f"section_path={content_unit['section_path']}。\n下面是该窗口清洗后原文，请抽原子断言：\n\n" + w
            )
            r1 = await client.chat([LLMMessage(role="system", content=PASS1_SYSTEM), LLMMessage(role="user", content=u1)])
            claims.extend(_extract_json(r1.content).get("claims", []))
        print(f"[B6] {source_id}: Pass1 窗口 {len(windows)} 个 / 输入 {len(clean_t)} 字符 / claims {len(claims)} 条")
        # B8 结构校验/归一：去空 roles、剔除缺必填字段(subject/predicate)的 claim
        _before = len(claims)
        norm: list[dict] = []
        for c in claims:
            if not c.get("roles"):
                c.pop("roles", None)  # 空 roles 不落
            if c.get("subject") and c.get("predicate"):  # object 可选（一元断言）
                norm.append(c)
        dropped = _before - len(norm)
        claims = norm
        if dropped:
            print(f"[B6] {source_id}: 结构校验剔除 {dropped} 条缺必填字段的 claim（剩余 {len(claims)}）")
        suffix = f"_{tag}" if tag else ""
        claims_file = OUT_DIR / f"b6_claims_{source_id}{suffix}.json"
        claims_file.write_text(json.dumps(claims, ensure_ascii=False, indent=2), encoding="utf-8")
        if only_pass1:
            print(f"[B6] {source_id}: 仅 Pass1，claims 落盘 {claims_file}")
            return None

    # Pass 2：分类 + 结构规范化 + 建图——按"块"组输入（每块 context + 该块 claims），分批保输出上限
    blocks = _build_pass2_blocks(claims, clean_t, chunks)
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_n = 0
    for blk in blocks:
        n = len(blk["claims"])
        if cur and cur_n + n > PASS2_CLAIM_BUDGET:
            batches.append(cur)
            cur, cur_n = [], 0
        cur.append(blk)
        cur_n += n
    if cur:
        batches.append(cur)
    print(f"[B6] {source_id}: Pass2 输入 {len(blocks)} 块 / {len(claims)} claims / {len(batches)} 批")
    p2_system = PASS2_SYSTEM + "\n" + (_DEDUP_STRICT if dedup == "strict" else _DEDUP_LOOSE)
    merged: dict = {"entities": [], "terms": [], "statements": [], "relations": [], "data_evidence": []}
    p2_in = 0
    p2_out = 0
    for batch in batches:
        user2 = (
            f"source_id={source_id}；asserted_by={source_id}。\n按块组织的断言清单：\n"
            + json.dumps(batch, ensure_ascii=False, indent=2)
        )
        p2_in += len(user2)
        r2 = await client.chat([LLMMessage(role="system", content=p2_system), LLMMessage(role="user", content=user2)])
        p2_out += len(r2.content)
        part = _extract_json(r2.content)
        merged["statements"].extend(part.get("statements", []))  # claim_idx 已是全局下标
        merged["data_evidence"].extend(part.get("data_evidence", []))
        merged["entities"].extend(part.get("entities", []))
        merged["terms"].extend(part.get("terms", []))
        merged["relations"].extend(part.get("relations", []))
    print(f"[B6] {source_id}: Pass2 字符 输入={p2_in} 输出={p2_out} 比例 in:out={p2_in / max(1, p2_out):.1f}:1")
    # 实体按 name 去重
    seen: set[str] = set()
    ents: list[dict] = []
    for e in merged["entities"]:
        n = e.get("name")
        if n and n not in seen:
            seen.add(n)
            ents.append(e)
    merged["entities"] = ents
    bundle = merged
    # 用 Pass 1 清单补回 subject/predicate/object/evidence（Pass 2 只出分类）
    for st in bundle.get("statements", []):
        idx = st.get("claim_idx")
        if isinstance(idx, int) and 0 <= idx < len(claims):
            c = claims[idx]
            st["subject"] = c["subject"]
            st["predicate"] = c["predicate"]
            st["object"] = c.get("object")  # object 可选（一元断言）
            st["evidence_texts"] = c.get("evidence_texts", [])
            if c.get("roles"):
                st["roles"] = c["roles"]
    # 数据/基准断言并入其支撑 statement 的证据（不独立立节点）
    for de in bundle.get("data_evidence", []):
        fi = de.get("evidence_for")
        di = de.get("claim_idx")
        if isinstance(fi, int) and isinstance(di, int) and 0 <= di < len(claims):
            target = next((s for s in bundle.get("statements", []) if s.get("claim_idx") == fi), None)
            if target is not None:
                for ev in claims[di].get("evidence_texts", []):
                    if ev not in target.get("evidence_texts", []):
                        target.setdefault("evidence_texts", []).append(ev)
    bundle["content_unit"] = content_unit
    bundle["source_id"] = source_id
    bundle = _a4(bundle, clean_t, chunks, source_id, 0)

    # 文档元数据由程序填（LLM 不该给 url/作者/日期）
    for e in bundle.get("entities", []):
        if e.get("type") == "document":
            e.setdefault("metadata_source", "program-filled-from-manifest")
    return bundle


async def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="B6 最小编译切片：O2/A5 编译层真实跑批（不向量入库）")
    parser.add_argument("--source", default="O2,A5", help="逗号分隔的源 id")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--token-limit", type=int, default=12000)
    parser.add_argument("--pass1-window-chars", type=int, default=5000)
    parser.add_argument("--only-pass1", action="store_true", help="只跑 Pass 1 并落盘 claims，不做 Pass 2")
    parser.add_argument("--tag", default="", help="输出文件后缀标签（区分多遍跑批）")
    parser.add_argument("--pass2-from", default="", help="Pass2-only：从 b6_claims_<source>_<tag>.json 载入 claims，跳过 Pass 1")
    parser.add_argument("--dedup", default="strict", choices=["strict", "loose"], help="Pass2 去重法：strict(从严/grounded) / loose(凭常识，旧)")
    args = parser.parse_args()

    settings = SqliteSettingStore(APP_DB).get_llm_settings()
    if not settings.api_key:
        sys.exit("[B6] 未配置大模型 API Key（设置面板配置），无法编译")
    model = args.model or settings.model or DEFAULT_MODEL

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = {}
    for sid in [s.strip() for s in args.source.split(",") if s.strip()]:
        bundle = await _compile_source(
            sid, settings.api_key, model, args.token_limit, args.pass1_window_chars, args.only_pass1, args.tag, args.pass2_from, args.dedup
        )
        if bundle is None:
            continue
        suffix = f"_{args.tag}" if args.tag else ""
        out = OUT_DIR / f"b6_{sid}{suffix}.json"
        out.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
        results[sid] = str(out)
        stmts = bundle.get("statements", [])
        n_ok = sum(1 for s in stmts if s.get("a4_note") == "OK")
        print(
            f"[B6] {sid}: 实体 {len(bundle.get('entities', []))} / 陈述 {len(stmts)} / 关系 {len(bundle.get('relations', []))} "
            f"/ 引文锚定定位成功 {n_ok} → {out}"
        )
    if not results:
        sys.exit("[B6] 没有产出 bundle")


if __name__ == "__main__":
    asyncio.run(main())
