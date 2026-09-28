"""把 GraphRAG 默认的抽取提示词原样导出到 prompts/extract_graph.txt，
供我们改语言要求（走官方 `extract_graph.prompt: <文件路径>` 机制，不改 site-packages）。

导出时**不做任何格式化**：文件内容即模板本体（含 {entity_types} 等占位符），
这样后续只改一行、其余字节不变。
"""

from __future__ import annotations

from pathlib import Path

from graphrag.prompts.index.extract_graph import GRAPH_EXTRACTION_PROMPT

ROOT = Path(__file__).resolve().parent
out = ROOT / "prompts" / "extract_graph.txt"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(GRAPH_EXTRACTION_PROMPT, encoding="utf-8")

print(f"写出 {out}")
print(f"  字符数 {len(GRAPH_EXTRACTION_PROMPT):,} · 行数 {GRAPH_EXTRACTION_PROMPT.count(chr(10)) + 1}")
print("  含占位符:", [p for p in ("{entity_types}", "{input_text}", "{{") if p in GRAPH_EXTRACTION_PROMPT])
for i, line in enumerate(GRAPH_EXTRACTION_PROMPT.split("\n")[:30], 1):
    if "English" in line:
        print(f"  ← 语言要求在第 {i} 行: {line.strip()}")
