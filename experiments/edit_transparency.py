import json
from pathlib import Path

p = Path(r"D:\lida-data\vscode\ai-study-helper\backend\eval\cases\rag\rag-cmp-002.json")
d = json.loads(p.read_text(encoding="utf-8"))
g = d["annotation"]["golden_answer"]
old = (
    "遗漏透明（强制）：本用例扩展主题仅要求各覆盖 ≥2 个、核心之外必有未展开内容——"
    "回答必须主动声明\"两文还有未展开内容\"并至少点名 1 个未展开项"
    "（如 O2 的某扩展主题、A5 的某模式细节）；未声明或未点名即 fail（判 pass 前提之一）。"
)
new = (
    "遗漏透明（条件强制）：回答若未覆盖两侧全部扩展主题，必须主动声明\"两文还有未展开内容\""
    "并至少点名 1 个未展开项（未点名即视为未声明）；两侧扩展主题已全部覆盖时无此义务，"
    "如实收口即可，不得编造不存在的遗漏。判官须结合回答实际覆盖的扩展主题判断本条是否适用。"
)
assert old in g, "old transparency sentence not found"
g2 = g.replace(old, new)
d["annotation"]["golden_answer"] = g2
p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("updated:", "条件强制" in g2 and "必有未展开" not in g2)
