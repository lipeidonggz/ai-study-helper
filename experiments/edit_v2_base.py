import json
from pathlib import Path

ROOT = Path(r"D:\lida-data\vscode\ai-study-helper")

# 1) rag-cmp-002 金标准：遗漏透明改无条件义务（沛东 2026-09-04 拍板）
cmp = ROOT / "backend" / "eval" / "cases" / "rag" / "rag-cmp-002.json"
d = json.loads(cmp.read_text(encoding="utf-8"))
g = d["annotation"]["golden_answer"]
old = (
    "遗漏透明：回答须让用户知道这不是穷尽目录——若有未展开的重要内容"
    "（如 O2 的某扩展主题、A5 的某模式细节），应明说\"这两篇还有 X 未展开\"，"
    "不得让用户误以为已覆盖全文。"
)
new = (
    "遗漏透明（强制）：本用例扩展主题仅要求各覆盖 ≥2 个、核心之外必有未展开内容——"
    "回答必须主动声明\"两文还有未展开内容\"并至少点名 1 个未展开项"
    "（如 O2 的某扩展主题、A5 的某模式细节）；未声明或未点名即 fail（判 pass 前提之一）。"
)
assert old in g, "golden 遗漏透明旧句未找到"
g2 = g.replace(old, new)
d["annotation"]["golden_answer"] = g2
cmp.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("rag-cmp-002 golden updated, 遗漏透明（强制） in place:", "遗漏透明（强制）" in g2)

# 2) 0024 术语勘误：断言级 -> 声明级（含决策点锚点补一条勘误说明）
note = ROOT / "memory" / "0024-rag-v1-cases.html"
t = note.read_text(encoding="utf-8")
n_old = t.count("断言级")
t = t.replace("断言级", "声明级")
anchor = "【决策点】引用真实性粒度讨论（2026-09-01，沛东关注成本）"
assert anchor in t, "0024 决策点锚点未找到"
annot = "（术语勘误 2026-09-04：原文作“断言级”，业界标准为 claim-level（声明级）/ atomic claims，全文统一为“声明级”）"
t = t.replace(anchor, anchor + annot, 1)
note.write_text(t, encoding="utf-8")
print("0024 术语勘误完成，原“断言级”出现", n_old, "处")

# 3) schema.py 注释同步
sch = ROOT / "backend" / "eval" / "schema.py"
s = sch.read_text(encoding="utf-8")
s2 = s.replace("断言级逐条核对", "声明级逐条核对")
sch.write_text(s2, encoding="utf-8")
print("schema.py 注释同步:", "断言级" not in s2)
