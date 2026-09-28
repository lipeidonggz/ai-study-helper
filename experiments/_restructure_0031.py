"""0031 重构：把 Global Search 拆成"机制正文 + 考古层"。

做法 = 显式行区间搬运（考古层与其余模式**逐字保留**）＋ 新写的机制正文段落（另存文件）。
不做任何正则改写，避免动到内容；跑完打印对账（搬运了哪些块、砍掉了哪些块）。
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(r"D:\lida-data\vscode\ai-study-helper")
SRC = REPO / "memory" / "0031-graphrag-search-modes.html"
TMP = REPO / "data" / "tmp"


def read(name: str) -> str:
    return (TMP / name).read_text(encoding="utf-8")


def main() -> None:
    lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)
    n = len(lines)

    def seg(a: int, b: int) -> str:
        """1-based inclusive 行区间。"""
        return "".join(lines[a - 1 : b])

    parts: list[tuple[str, str]] = [
        ("A  头部+速查+第二部分标题", seg(1, 85)),
        ("B  新：机制正文头（一屏流程＋第0步＋层从哪来/高低）", read("_0031_new_head.html")),
        ("C  原：第1步/第2步/map提示词导读", seg(306, 331)),
        ("D  原：第3步/第4步", seg(366, 375)),
        ("E  原：四个容易忽略但要点清的机制", seg(376, 385)),
        ("F  新：参数取值（合并原 386-388 与 420-421）", read("_0031_new_params.html")),
        ("G  原：追问·跨批分数不可比", seg(332, 347)),
        ("H  原：追问·宏观问题＋细层", seg(348, 365)),
        ("I  原：追一问·社区权重为什么查询期重算", seg(389, 399)),
        ("J  新：边界小节标题＋引言", read("_0031_new_boundary.html")),
        ("K  原：更根本的一层（设计批评＋量化＋三条替代路线）", seg(152, 169)),
        ("L  原：可选第二条路径 Dynamic Community Selection", seg(422, 429)),
        ("M  原：已知代价（跨记录）", seg(430, 432)),
        ("N  新：考古层标题＋引言", read("_0031_new_arch.html")),
        ("O1 原：⚠️ 撤旧结论的更正块", seg(104, 104)),
        ("O2 原：先补一课 / 沛东的修正 / 等价刻画", seg(106, 151)),
        ("P1 原：版本线 / 有意还是 bug", seg(170, 203)),
        ("P2 原：关键补证→候选修复→已提交 issue→探针实测→于是语义", seg(205, 305)),
        ("Q  原：其余模式＋待问清单＋页脚", seg(433, n)),
    ]

    dropped = [
        ("砍：旧 h3 Global基本模式 ＋ 旧第0步 ＋ ⚠️之外的收尾", 86, 103),
        ("砍：空标题（只有 h4、无内容）", 105, 105),
        ("砍：空标题（只有 h4、无内容）", 204, 204),
        ("砍：旧 h3 选层与自适应 ＋ 其前四小节（内容已并入 B/F）", 400, 421),
    ]

    out: list[str] = []
    print(f"源文件 {n} 行 / {sum(len(x) for x in lines)} 字符\n")
    print("=== 组装 ===")
    for name, text in parts:
        out.append(text)
        print(f"  + {len(text):>7} 字符  {name}")
    print("\n=== 砍掉（只报不并） ===")
    for name, a, b in dropped:
        t = seg(a, b)
        print(f"  - {len(t):>7} 字符  {name}   行{a}-{b}")
        first = next((x.strip()[:70] for x in lines[a - 1 : b] if x.strip().startswith("<h")), "")
        if first:
            print(f"            首标题：{first}")

    result = "".join(out)
    print(f"\n输出 {result.count(chr(10)) + 1} 行 / {len(result)} 字符")

    # 断言：关键块确实在
    for must in (
        "第二部分 · 逐模式细节",
        "一屏流程（五步）",
        "第 1 步 · 装配上下文",
        "map 提示词导读",
        "第 3 步 · Reduce",
        "四个容易忽略但要点清的机制",
        "参数取值（notebook 实测）",
        "这条链的边界",
        "更根本的一层",
        "可选第二条路径：Dynamic Community Selection",
        "已知代价（跨记录）",
        'id="arch-global"',
        "先补一课",
        "版本线",
        "已提交（2026-09-25",
        "于是：默认模式的实际语义与边界",
        "Local Search",
        "第三部分 · 待问清单",
        "</html>",
    ):
        assert must in result, f"缺少关键内容：{must}"
    print("断言通过：19 项关键内容都在")

    # 备份 + 就地写回
    backup = TMP / "_0031_before_restructure.html"
    backup.write_text(SRC.read_text(encoding="utf-8"), encoding="utf-8")
    SRC.write_text(result, encoding="utf-8")
    print(f"已备份原文 → {backup.name}")
    print(f"已写回 {SRC}")


if __name__ == "__main__":
    main()
