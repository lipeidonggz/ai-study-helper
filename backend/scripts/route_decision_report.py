"""影子模式数据：把一批真实问题过一遍通道判定，看 L1/L2/L3 的分布与降级情况。

只加载 embedder，不碰 Qdrant（后端可以继续跑）。
L3 默认关闭；`ROUTE_DECISION_LLM=1` 时会真调一次 LLM（需要网络与已配置的 Key）。
"""
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, ".")

from app.rag.route_decision import decide_route  # noqa: E402
from app.storage.sqlite.settings_store import SqliteSettingStore  # noqa: E402

# 验证集：每条带一个"预期形状 / 类别"的人工标注（AI 标、沛东可改）。
# 形状：point→vector、line→relation、area→enum；unsure＝判不出来。
CASES: list[tuple[str, str]] = [
    # —— 面（area / enum）：要一片内容的全部 ——
    ("A5 这篇文章有哪些局限？", "enum"),
    ("这些文章都提到了哪些局限？", "enum"),
    ("作者承认了哪些不足", "enum"),
    ("OpenAI 在使用他们的智能体自主编码时，为了保证可控，都怎么做的", "enum"),
    ("A5 这篇文章主要讲了什么", "enum"),          # 概述型：要覆盖整篇
    ("怎么把数据库迁移到新集群", "enum"),          # 流程型：要的是完整步骤集合（沛东 2026-09-21 定：步骤集合不是点）
    # —— 线（line / relation）：两个事物之间的连接 ——
    ("A5 和 O2 的局限对比", "relation"),
    ("这两篇在展望上有什么不同", "relation"),
    ("A5 里 VM 和沙箱是什么关系", "relation"),
    # —— 点（point / vector）：一处内容 ——
    ("A5 里提到的 17% 那个数字是怎么回事", "vector"),
    ("Claude Code 的 hook 是怎么执行的", "vector"),
    ("containment 是什么意思", "vector"),
    ("为什么要做出口管控", "vector"),
    ("什么是权限提示疲劳", "vector"),
    ("Agent 是啥", "vector"),
    # —— 该判不出来：含糊 / 超范围 ——
    ("嗯这个吧", "unsure"),
    ("帮我看看这个", "unsure"),
    ("这个问题怎么解决", "unsure"),               # 没有指代对象，判不出来是对的
    ("今天天气怎么样", "unsure"),                 # 超出知识库范围
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    judge = None
    if os.environ.get("ROUTE_DECISION_LLM") and os.environ.get("ROUTE_DECISION_LLM") != "0":
        cfg = SqliteSettingStore(Path("data") / "app.db").get_llm_settings()
        if cfg.api_key:
            client = httpx.Client(timeout=60)

            def judge(system: str, user: str) -> dict:
                resp = client.post(
                    "https://api.deepseek.com/chat/completions",
                    headers={"Authorization": f"Bearer {cfg.api_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": cfg.model or "deepseek-chat",
                        "temperature": 0,
                        "messages": [{"role": "system", "content": system},
                                     {"role": "user", "content": user}],
                    },
                )
                return json.loads(resp.json()["choices"][0]["message"]["content"])

    print(f"L3 = {'开（真调 LLM）' if judge else '关'}")
    print(f"{'结果':<6}{'期望':<10}{'最终':<8}{'判定者':<10}{'need':<10} 问题")
    print("-" * 120)
    tally: dict[str, int] = {}
    needs: dict[str, int] = {}
    ok = 0
    for q, expected in CASES:
        d = decide_route(q, judge=judge)
        tally[d.decided_by] = tally.get(d.decided_by, 0) + 1
        needs[str(d.need)] = needs.get(str(d.need), 0) + 1
        hit = (str(d.need) == expected)
        ok += int(hit)
        print(f"{'✓' if hit else '✗':<6}{expected:<10}{d.route:<8}{d.decided_by:<10}"
              f"{str(d.need):<10} {q}")
        if d.decided_by == "llm":
            print(f"{'':<44}└ L3 理由：{d.evidence}")

    print(f"\n命中预期：{ok}/{len(CASES)}")
    print("判定者分布：", tally)
    print("need 分布：", needs)


if __name__ == "__main__":
    main()
