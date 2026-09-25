"""检索通道判据（方案 D 级联：L1 词表 → L2 语义路由 → L3 LLM → 默认向量）。

判据（沛东 2026-09-21 定稿）：**答案的形状决定通道**——
  · **point**（答案是资料库里的一处内容）→ **vector**：向量唯一的能力就是"按相似度选点"；
  · **line**（答案是两个事物之间的连接）→ **relation**（图）；
  · **area**（答案是资料库里的一片内容）→ **enum**（图）。
  依据：**向量检索必然要套 top-k，不论 k 是几**——输出恒为"点集"，给不出线或面。
  `unsure` 不是兜底垃圾，而是**产品需要增强的清单**：它记的正是"用户确实在问、图答不了"的那批。

为什么这样排：**向量是默认路径**，判定问的是"要不要升级到图"，而不是二选一；三类各自对应执行层的一条
查询（enum＝按条件过滤；relation＝以问题里的锚点取连接，两事物 join 与跨源同主题对照共用这条路径）。

为什么不用"用户会不会觉得不完整"当判据（第一版的失败）：那是反事实，模型没法评估，实测六条里六条全判
point、理由句式高度同构（"只需…即可、无需穷尽…"）——**塌陷成"最小充分"这个恒真命题**。改成"要的是不是
某一处内容"之后，判的是**问题的指向**，而不是"够不够"。

三级的分工（沛东 2026-09-20 定）：
  · L1 只保留"要全"一侧，命中即定；**不做"要序"侧**——ORDER 词一旦命中就是单侧短路，
    而"怎么 / 如何 / 是什么"这类词经常出现在要全的问句里（例："都怎么做的"），
    按"宁可漏不可错"，删掉整个 ORDER 侧，代价只是多走一次 L2（几乎零成本）。
  · L2 是**加速层不是判定层**：它的阈值语义只有"置信度够不够跳过 L3"，用**单一对称阈值**，
    不掺"哪个通道更值得信任"的偏好。判成向量也不代表可以放松——那会破坏完备性承诺。
  · L3 只在 L2 分不清时调；**"判不出来"给独立的第三个出口 unsure**，不塞给某一类，
    否则"有多少判不出来"这件事永远看不见。

**2026-09-21 变更：L2（语义路由）已裁出流水线。**
原因是三分类之后 margin 整体变小——实测真实 query 的最大 margin 只有 0.0232，全部低于阈值 0.025，
**L2 在整批样本上一条都没判**（改三类之前也只在 19 条里命中过 1 条，且给不出类别）。
裁掉它还顺带省掉决策路径那一次 query embedding（约 50ms），现在这条路径在没有 L3 时几乎是零成本。
`SemanticRouter` / `UTTERANCES` / `get_router` **保留在文件里备用**（标定脚本
`scripts/calibrate_route_threshold.py` 也一并保留，它是"为什么裁掉"的证据），但流水线不再调用它们。

影子模式（第一阶段）：本模块的输出只进 trace，**不改变实际检索行为**，用于收集判定分布。

L3 的接线说明：本模块只接受一个**同步**的 judge 可调用对象（`judge(system, user) -> dict`），
不直接依赖 agent 那侧的异步 LLMClient——`prepare()` 是同步链路，不为它引入事件循环。
启用 L3 时（`ROUTE_DECISION_LLM=1`）由调用方提供一个同步适配器（直连 HTTP 或线程池包装）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------- L1：词表
# 只保留"要全"一侧（理由见模块 docstring）。词表按**图能做的两件事**分成两组，
# 命中时顺带给出类别（enum / relation），便于执行层与 trace 使用。
# 逐个词都过了"宁可漏不可错"：
# 已删——怎么 / 如何（"都怎么做的"）、主要（"主要原因是什么"）、比较（可作副词）、
# 具体（信号弱且易冲突）、裸"区别 / 不同 / 差别"（可作定语或动词）、裸"关系"（"没关系"）；
# 未收——单字"都"（强集合信号但单字风险高，等影子模式看漏判率再定）。
FULL_PHRASES: dict[str, tuple[str, ...]] = {
    # 枚举：把某一类内容全部取来
    "enum": (
        "都有哪些", "有哪些", "包括哪些", "都包括",
        "哪些", "所有", "全部", "全都", "列举", "罗列", "还有什么",
    ),
    # 关系：事物之间的关系或对照（两事物 join 与跨源同主题对照共用这条路径）
    "relation": (
        "有什么区别", "之间的区别", "有什么不同", "有何不同", "有什么差异", "有何差异",
        "对比", "异同",
        "什么关系", "有什么关系", "之间的关系",
    ),
}
# 供测试与兼容使用：全部短语的扁平集合
ALL_FULL_PHRASES: tuple[str, ...] = tuple(p for g in FULL_PHRASES.values() for p in g)

# ---------------------------------------------------------------- L2：示例话语（已裁出流水线，保留备用）
# 2026-09-21 裁掉的理由见模块 docstring：三分类后 margin 整体变小，阈值 0.025 下一条都不判。
# 保留是为了将来"决策路径需要一次廉价预筛"时能直接捡回来。
# 基础件：AI 起草、沛东审过（2026-09-20 首版二分类；2026-09-21 按"L2 也出三类"改为三形状）。
# 刻意**不使用本语料的源名**（A5 / O2）——按项目纪律，基础件里不放本语料示例。
UTTERANCES: dict[str, tuple[str, ...]] = {
    # 面：一片内容的全部（含"步骤集合"——沛东 2026-09-21 定："步骤集合当然不是点"）
    "area": (
        "这篇文章有哪些局限",
        "作者承认了哪些不足",
        "都提到了哪些风险",
        "帮我列一下相关的做法",
        "有哪些坑",
        "这篇文章讲了什么",
        "要完成这件事需要哪些步骤",
        "这类问题涉及哪些方面",
    ),
    # 线：两个事物之间的连接
    "line": (
        "这两篇在展望上有什么不同",
        "这两篇的观点异同",
        "这两个概念之间是什么关系",
        "这两个做法有什么区别",
        "哪个因素导致了哪个结果",
        "这两篇文章有没有矛盾",
    ),
    # 点：一处内容
    "point": (
        "那个数字是怎么回事",
        "这个机制是怎么执行的",
        "某个术语是什么意思",
        "为什么要做这个限制",
        "这里面放了什么",
        "为什么会出现这个现象",
        "这个参数的默认值是多少",
    ),
}

# L2 的 margin 下限：由 scripts/calibrate_route_threshold.py 的留一法标定。
# 初值取类质心留一法分布的低分位（≈p25）——宁可漏到 L3，不可错判；待影子模式的真实分布微调。
DEFAULT_MARGIN_THRESHOLD = 0.025

# 判不出来时记一条待人工看的日志（追加式 jsonl，与编译侧台账同一风格）。
# 这份台账只该收**真实用户 query**——pytest 跑评测用例、离线探针都会调 prepare()，
# 必须把它们挡在外面，否则队列一开跑就被污染（2026-09-20 实测踩到：一次全量测试灌进 17 条）。
REVIEW_LOG = Path("data/route_decision_review.jsonl")
# 全部判定的分布日志：回答"用户实际在问哪类结构问题"（enum / relation / vector / unsure 的比例）
DIST_LOG = Path("data/route_decision_log.jsonl")

# ---------------------------------------------------------------- L3：提示词
# 基础件：全文经沛东 2026-09-20 审过。
# 三处刻意设计：① 不用"检索通道判定器"这类没有上下文的系统术语，改成描述"要帮忙做的那个决定"；
# ② 两类只给判断依据、不给例子（例子会被当成全集，遇到不在例子里的问法就退缩）；
# ③ "判不出来"独立成第三个出口，不塞给 vector。
# 2026-09-21 补：point 与 area 改用"装进一段 / 需要多段凑起来"分界。起因是"怎么把数据库迁移到新集群"
# 被判成 point（理由"一处操作说明"）——沛东裁定"步骤集合当然不是点"。这里刻意**不引入 token 阈值**：
# 让模型估"要多少 token"等于让它估自己看不到的东西（同一个坑在第三次迭代已踩过），而"能不能一段说清"
# 是可感的形状；token 数留给执行层当预算参数（chunk ≈340、向量预算 3600）。
L3_SYSTEM = """一个学习助手需要先从资料库里取一部分原文片段，再交给模型回答问题。
请判断这个问题的答案是什么形状，只输出一行 JSON。

三种形状：

- "point"：答案是能装进一段的内容，例如一个解释、一个数值、一段说明。
- "line"：答案是两个事物之间的连接，例如它们是什么关系、有什么不同、谁影响谁。
- "area"：答案是资料库里的一片内容，需要多段凑起来——例如某一类东西的全部、
  某一篇文章的整体、一整套做法或步骤。

判断不了、或者三种都套不上，输出 "unsure"，不要勉强归类。

只输出 JSON，不要任何其它文字：
{"need": "point" 或 "line" 或 "area" 或 "unsure", "reason": "一句话说明依据"}"""

# 形状 → 检索通道：点状只能用 top-k 选点（向量）；线（连接）与面（一片）都不是点集，
# 向量给不出这两种输出形状（详细论证见 memory/0028 本轮讨论）。"面"的两种形态
# （按条件取全 / 范围内全量）留给执行层分。
SHAPE_TO_NEED = {"point": "vector", "line": "relation", "area": "enum"}
NEED_TO_SHAPE = {"vector": "point", "relation": "line", "enum": "area"}
NEED_TO_ROUTE = {"vector": "vector", "enum": "graph", "relation": "graph"}


@dataclass(frozen=True)
class RouteDecision:
    """一次通道判定。route 只有两档；"全"的两种形态（图筛选 / 全文直入）留给第二个问题。"""

    route: str                      # "graph" | "vector"
    decided_by: str                 # "lexicon" | "semantic" | "llm" | "default"
    need: str | None = None         # "enum" | "relation" | None（L2 只判到"不是向量"，分不出是哪类）
    shape: str | None = None        # L3 判出来的形状："point" | "line" | "area" | "unsure"
    evidence: str = ""              # 命中的词 / 命中的示例话语 / LLM 给的理由
    margin: float | None = None     # L2 的 top1-top2（只用于判断要不要降级）
    degraded: list[str] = field(default_factory=list)
    needs_review: bool = False      # 判不出来时落一条待人工看的日志
    elapsed_ms: float = 0.0

    def trace_data(self) -> dict:
        return {
            "route": self.route,
            "decided_by": self.decided_by,
            "need": self.need,
            "shape": self.shape,
            "evidence": self.evidence,
            "margin": round(self.margin, 4) if self.margin is not None else None,
            "degraded": list(self.degraded),
            "needs_review": self.needs_review,
            "elapsed_ms": self.elapsed_ms,
        }


def match_full_phrases(query: str) -> tuple[str, list[str]] | None:
    """L1：最长优先、命中后消耗。返回 (类别, 命中的词)；未命中返回 None。"""
    text = query or ""
    pairs = sorted(
        ((group, p) for group, group_phrases in FULL_PHRASES.items() for p in group_phrases),
        key=lambda gp: len(gp[1]),
        reverse=True,
    )
    hits: list[str] = []
    groups: list[str] = []
    for group, phrase in pairs:
        if phrase in text:
            hits.append(phrase)
            groups.append(group)
            text = text.replace(phrase, "\u0000")   # 消耗掉，短词不再重复命中
    if not hits:
        return None
    # 多个类别都命中时，取（等长优先）更早命中的那个；这里简单地以首个命中为准
    return groups[0], hits


class SemanticRouter:
    """L2（**已裁出流水线**）：按**类质心**做的三分类（point / line / area，相对比较、不是阈值判定）。

    为什么不是"最近单条话语"：留一法实测三种实现——最近话语自分类 11/15、类质心 **15/15**、
    类描述句 6/15（见 `scripts/calibrate_route_threshold.py`）。
    最近话语的做法会让"帮我列一下相关的做法"这类被对面类的某条话语吸走（margin −0.044）；
    类质心把同类话语的噪声平均掉，15 条全部正确且 margin 中位数翻倍（0.020 → 0.042）。
    类描述句最差——它跟"问句"的语义距离太远，且两条描述互相干扰。

    2026-09-21 从二分类改为三形状：原版只答"是不是点"，于是 L2 判成图侧时给不出是线还是面
    （`need=None`），执行层拿不到可直接使用的类别。现在 L2 与 L3 说的是同一种语言（形状）。
    """

    def __init__(self, labels: list[str], texts: list[str], matrix: np.ndarray) -> None:
        self._labels = labels
        self._texts = texts
        self._matrix = matrix
        self._protos: dict[str, np.ndarray] = {}
        for label in UTTERANCES:
            idxs = [i for i, l in enumerate(labels) if l == label]
            centroid = matrix[idxs].mean(axis=0)
            self._protos[label] = centroid / (np.linalg.norm(centroid) + 1e-9)

    @classmethod
    def build(cls, embedder) -> "SemanticRouter":
        labels: list[str] = []
        texts: list[str] = []
        for label, group in UTTERANCES.items():
            for u in group:
                labels.append(label)
                texts.append(u)
        vecs = np.array(embedder.embed(texts, is_query=True), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        return cls(labels, texts, vecs)

    def classify(self, query_vec: np.ndarray) -> tuple[str, float, str]:
        """返回 (形状, margin, 证据)。margin = 最高分质心与次高分之差；证据＝获胜类里最贴近的那条话语。"""
        scores = {label: float(query_vec @ proto) for label, proto in self._protos.items()}
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        top_label, top_score = ranked[0]
        margin = top_score - ranked[1][1]
        idxs = [i for i, l in enumerate(self._labels) if l == top_label]
        best_i = max(idxs, key=lambda i: float(self._matrix[i] @ query_vec))
        return top_label, margin, self._texts[best_i]


def _llm_enabled() -> bool:
    """L3 开关：**默认开**（2026-09-21 起）。

    为什么默认开：判定接成"闸门"之后（`route == vector` 且判定明确时才拦下图通道），
    没有 L3 的话 L1 只在命中词表时有意见、其余全落 default＝不干预，闸门等于没接。
    代价是一次 LLM 调用（实测中位 745ms、prompt 182 / completion 36 token）。
    若觉得延迟不划算，设 `ROUTE_DECISION_LLM=0` 即回到"只记录不干预"的行为。
    """
    return os.environ.get("ROUTE_DECISION_LLM", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


# 类质心只跟"嵌入模型"有关，与请求无关；而 RagBackend 是**每次请求新建**的，
# 缓存放实例上等于没缓存（每条 query 都要重编 15 条话语）。故按模型名做模块级缓存。
_ROUTER_CACHE: dict[str, SemanticRouter] = {}


def get_router(embedder) -> SemanticRouter:
    key = getattr(embedder, "model_name", None) or f"id:{id(embedder)}"
    router = _ROUTER_CACHE.get(key)
    if router is None:
        router = SemanticRouter.build(embedder)
        _ROUTER_CACHE[key] = router
    return router


def build_http_judge(
    api_key: str,
    model: str,
    *,
    base_url: str = "https://api.deepseek.com",
    timeout: float = 30.0,
):
    """构造 L3 的**同步** judge（直连 HTTP，OpenAI 兼容协议）。

    为什么要同步：`RagBackend.prepare()` 是同步链路，不为它引入事件循环。
    已知代价：它会在事件循环里阻塞约 745ms（实测中位）——单用户开发环境可接受；
    将来并发放大时，应改成 `prepare_async` + `asyncio.to_thread`（TODO）。
    连接复用：httpx.Client 建在闭包里，跨调用复用（否则每次都要重新 TLS 握手）。
    """
    import httpx

    client = httpx.Client(timeout=timeout)

    def judge(system: str, user: str) -> dict:
        resp = client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "temperature": 0,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        return json.loads(content)

    return judge


def decide_route(
    query: str,
    *,
    judge=None,
) -> RouteDecision:
    """L1 → L3 → 默认（向量）。任何异常都不得影响检索主流程。

    L2（语义路由）已于 2026-09-21 裁出流水线（理由见模块 docstring）；因此这里不再需要
    embedder，决策路径在没有 L3 时是零成本。
    """
    start = time.perf_counter()

    def _done(route, by, evidence="", margin=None, degraded=None) -> RouteDecision:
        deg = list(degraded or [])
        return RouteDecision(
            route=route,
            decided_by=by,
            need=need,
            shape=shape,
            evidence=evidence,
            margin=margin,
            degraded=deg,
            # 台账收两类：unsure（产品增强清单）与 L3 出错（运维问题，别让它静默）
            needs_review=(need == "unsure" or any(g.startswith("llm:error") for g in deg)),
            elapsed_ms=round((time.perf_counter() - start) * 1000, 1),
        )

    # ---- L1 ----
    need: str | None = None
    shape: str | None = None
    hits = match_full_phrases(query)
    if hits:
        need, hit_words = hits
        shape = NEED_TO_SHAPE[need]
        return _done("graph", "lexicon", evidence="命中词：" + " / ".join(hit_words))

    # ---- L3 ----
    margin: float | None = None
    degraded: list[str] = []
    if judge is None or not _llm_enabled():
        degraded.append("llm:unavailable" if judge is None else "llm:disabled")
        return _done("vector", "default", evidence="；".join(degraded),
                     margin=margin, degraded=degraded)
    try:
        raw = judge(L3_SYSTEM, f"问题：{query}")
        shape = str(raw.get("need", "")).strip()
        reason = str(raw.get("reason", "")).strip()
    except Exception as exc:  # noqa: BLE001
        degraded.append(f"llm:error:{type(exc).__name__}")
        return _done("vector", "default", evidence="；".join(degraded),
                     margin=margin, degraded=degraded)
    if shape in SHAPE_TO_NEED:
        need = SHAPE_TO_NEED[shape]
        return _done(NEED_TO_ROUTE[need], "llm", evidence=reason,
                     margin=margin, degraded=degraded)
    degraded.append("llm:unsure")
    need = "unsure"
    return _done("vector", "default", evidence=reason or "L3 判不出来",
                 margin=margin, degraded=degraded)


def record_for_review(query: str, decision: RouteDecision, *, path: Path | None = None) -> None:
    """unsure / L3 出错 → 落一条台账（追加式 jsonl）。**这就是"产品需要增强的清单"**。"""
    if not decision.needs_review:
        return
    target = _resolve_log_path(path, "ROUTE_REVIEW_LOG", REVIEW_LOG)
    if target is None:
        return
    _append(target, query, decision)


def record_distribution(query: str, decision: RouteDecision, *, path: Path | None = None) -> None:
    """全部判定 → 落一条分布日志：用来回答"用户实际在问哪类结构问题"。"""
    target = _resolve_log_path(path, "ROUTE_DIST_LOG", DIST_LOG)
    if target is None:
        return
    _append(target, query, decision)


def _resolve_log_path(path: Path | None, env_key: str, default: Path) -> Path | None:
    """测试与离线探针不得污染台账（显式传 path 的调用不受影响）。"""
    if path is not None:
        return path
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path(os.environ.get(env_key) or default)


def _append(target: Path, query: str, decision: RouteDecision) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "query": query,
            **decision.trace_data(),
        }
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
