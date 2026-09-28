"""用 git 903c7ea 的过程版 0025 重建重构稿的考古层（现场细节 + 经验教训档案 + 草案档案）。"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PROC = Path("data/tmp/0025-proc.html").read_text(encoding="utf-16")
NEW = Path("memory/0025-rag-v1-implementation.html").read_text(encoding="utf-8")


def main() -> None:
    # —— 从过程版切出"现场细节"（讨论历程 + 阶段 A-E）——
    site_start = PROC.find("<h3>讨论历程")
    site_end = PROC.find("<h2>产出物与落点")
    assert site_start > 0 and site_end > site_start
    site_html = PROC[site_start:site_end].rstrip()

    # —— 从过程版切出"实践与经验教训" ol（24 条）——
    less_start = PROC.find("<h2>实践与经验教训")
    less_next = PROC.find("<h2>", less_start + 10)
    less_section = PROC[less_start:less_next]
    ol_start = less_section.find("<ol>")
    ol_end = less_section.find("</ol>") + len("</ol>")
    assert ol_start > 0 and ol_end > ol_start
    lessons_ol = less_section[ol_start:ol_end]

    # —— 组装新考古层 section ——
    arch = (
        '    <section class="card">\n'
        '      <h2>考古层 · 推导现场与档案（自愿深挖；主体层结论是怎么被想出来、试出来、错出来的）</h2>\n'
        "      <p>本层面向想真正掌握方法论、而不只是拿走结论的读者。阅读顺序建议：先按下面的决策链时间线走一遍"
        "（每一条都标了驱动它的评测证据与【决策点】），需要细节再展开“经验教训档案”与“草案档案”折叠。</p>\n"
        "      <h3>决策链时间线（阶段 A-E，复原现场用）</h3>\n"
        f"{site_html}\n"
        "      <h3>经验教训档案（24 条完整案例，折叠）</h3>\n"
        "      <details>\n"
        "        <summary>展开：实践与经验教训（每条一个可复用的坑与解法）</summary>\n"
        f"{lessons_ol}\n"
        "      </details>\n"
        "      <h3>草案档案（被推翻的方案，折叠）</h3>\n"
        "      <details>\n"
        "        <summary>展开：被推翻的草案与弯路（同样是评测证据的产物）</summary>\n"
        "        <ul>\n"
        "          <li><strong>每源配额 3</strong>（Run#104 18/20）——把 top-k 搬进每个源，局部解无效，被覆盖模型取代。</li>\n"
        "          <li><strong>证据组 = 核心 + section 邻近窗口</strong>——隐含定义探针 o2-001 一击证伪：概念散布全文时位置邻近必漏（位置连续性假设不成立）。</li>\n"
        "          <li><strong>BM25 当默认召回</strong>——own-002 答案块 rank23 曾归因于它，量化确认根因是节标题不进检索（输入侧）→ 降级为实验对照。</li>\n"
        "          <li><strong>本地 rerank 上生产</strong>——CPU 实测约 3 pairs/s，在线路径否决；API rerank 为备选。</li>\n"
        "          <li><strong>锋利版阈值 4+4 / 全覆盖</strong>——工程视角的错误二分，被用户视角（完整可信不误判）否决。</li>\n"
        "          <li><strong>判官加“从严条款”靠提示词</strong>——自由裁决仍不一致（判官自违反从严条款），转结构化裁决（下迭代）。</li>\n"
        "        </ul>\n"
        "      </details>\n"
        "    </section>"
    )

    # —— 替换重构稿里的旧考古层 section ——
    old_start = NEW.rfind('    <section class="card">\n      <h2>考古层')
    assert old_start > 0
    old_end = NEW.find("    </section>", old_start) + len("    </section>")
    new_text = NEW[:old_start] + arch + "\n\n" + NEW[old_end:]
    Path("memory/0025-rag-v1-implementation.html").write_text(new_text, encoding="utf-8")
    print("rebuilt; arch chars:", len(arch))


if __name__ == "__main__":
    main()
