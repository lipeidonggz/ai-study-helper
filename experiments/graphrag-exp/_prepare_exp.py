"""实验准备：A5 → 纯文本输入 + 最小 settings.yaml + 配置自校验。

设计取舍（2026-09-25）：
- 输入用**我们自己的** `app.kb.chunker.extract_sections_html_auto` 把 HTML 洗成纯文本
  （去 nav/footer/订阅噪声、保留小节标题），而不是把 228KB 原始 HTML 丢给
  GraphRAG 的 markitdown 转换器——少一个变量，且文本量已知（≈8.2k token）。
- settings.yaml 里 api_key 写 `${DEEPSEEK_API_KEY}`，**真实密钥只从环境变量进来**，
  绝不落文件、绝不打印。
- 本脚本不调用任何 LLM，也不联网。
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent  # data/tmp/graphrag-exp -> data/tmp -> data -> 仓库根
A5_HTML = REPO / "data" / "kb-src" / "articles" / "anthropic" / "05-how-we-contain-claude.html"

# 提示词目录（可选）：默认空 ＝ 用 GraphRAG 原版提示词；
# 设 ASH_PROMPT_DIR=prompts-juejin-tuned 就用"为掘金文章调出来的那套"跑 A5（跨语料移植实验）
PROMPT_DIR = os.environ.get("ASH_PROMPT_DIR", "")
PROMPT_BLOCK = (
    f"""
# 提示词挂钩（来自 ASH_PROMPT_DIR={PROMPT_DIR}）
extract_graph:
  prompt: {PROMPT_DIR}/extract_graph.txt
summarize_descriptions:
  prompt: {PROMPT_DIR}/summarize_descriptions.txt
community_reports:
  graph_prompt: {PROMPT_DIR}/community_report_graph.txt
"""
    if PROMPT_DIR
    else ""
)


def load_chunker():
    """按文件路径直接加载我们的 chunker（它只依赖 stdlib + bs4）。"""
    path = REPO / "backend" / "app" / "kb" / "chunker.py"
    spec = importlib.util.spec_from_file_location("ash_chunker", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def export_a5_text() -> str:
    chunker = load_chunker()
    html = A5_HTML.read_text(encoding="utf-8", errors="ignore")
    sections = chunker.extract_sections_html_auto(html)

    lines: list[str] = ["# How we contain Claude across products", ""]
    for sec in sections:
        if sec.path:
            lines.append(f"## {sec.path}")
            lines.append("")
        for para in sec.paragraphs:
            text = re.sub(r"\s+", " ", para).strip()
            if text:
                lines.append(text)
                lines.append("")

    text = "\n".join(lines).strip() + "\n"
    out = ROOT / "input" / "a5.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"写出 {out.relative_to(REPO)}")
    print(f"  sections   : {len(sections)}")
    print(f"  chars      : {len(text):,}")
    print(f"  rough tokens: ~{int(len(text) / 3.6):,}")
    print(f"  前 160 字符: {text[:160]!r}")
    return text


SETTINGS = """\
# 最小实验配置（2026-09-25）——只跑到"社区报告"为止
# 依据：本地已安装的 graphrag 3.2.0 的 config/defaults.py 与 index/workflows 注册表

completion_models:
  default_completion_model:
    model_provider: deepseek
    model: deepseek-chat
    auth_method: api_key
    api_key: ${DEEPSEEK_API_KEY}
    # 2026-09-25 加了超时：第一次重跑时并发 25 + 无超时 ⇒ 卡在 summarize 步骤 8 分钟无进展
    call_args:
      timeout: 180
      num_retries: 2
__PROMPT_BLOCK__

# 并发降到 4：默认 25 会把 DeepSeek 打急（第一次重跑就是这么卡住的）
concurrent_requests: 4

# 注意：不写 input 块 —— 默认 type 就是 text，会读 input/ 下的文本文件。
# 踩过的坑（2026-09-25）：GraphRAG 用 string.Template 做环境变量替换，
# 于是 settings.yaml 里**任何字面美元符号都会破坏解析，连注释里的也算**：
#   · 给 file_pattern 写正则的结尾锚  → Invalid placeholder in string
#   · 在注释里写出占位符的完整写法    → Environment variable not found

# 只列我们真正要的步骤 ⇒ 两个 embedding 工作流被跳过
# （create_final_text_units / generate_text_embeddings），
# 因此 embedding_models 整块都不需要。
workflows:
  - load_input_documents
  - create_base_text_units
  - create_final_documents
  - extract_graph
  - finalize_graph
  - create_communities
  - create_community_reports

cache:
  type: json
  storage:
    type: file
    base_dir: cache

reporting:
  type: file
  base_dir: logs
"""


def write_settings() -> Path:
    out = ROOT / "settings.yaml"
    out.write_text(SETTINGS.replace("__PROMPT_BLOCK__", PROMPT_BLOCK), encoding="utf-8")
    print(f"\n写出 {out.relative_to(REPO)}")
    print(f"  提示词: {PROMPT_DIR or '（GraphRAG 原版）'}")
    return out


def validate_settings() -> None:
    """用 graphrag 自己的配置加载器解析一遍，确认字段名/枚举值合法。"""
    print("\n=== 配置自校验（不调 LLM、不联网）")
    try:
        from graphrag.config.load_config import load_config
    except Exception:  # noqa: BLE001
        from graphrag.config import load_config  # type: ignore

    cfg = load_config(ROOT)
    print("  chunking        :", cfg.chunking.size, "/", cfg.chunking.overlap, cfg.chunking.encoding_model)
    print("  cluster_graph   : max_cluster_size =", cfg.cluster_graph.max_cluster_size)
    print("  workflows       :", cfg.workflows)
    print("  extract_graph   : entity_types =", cfg.extract_graph.entity_types,
          "max_gleanings =", cfg.extract_graph.max_gleanings)
    print("  community_reports: max_length =", cfg.community_reports.max_length,
          "max_input_length =", cfg.community_reports.max_input_length)
    for key, model in cfg.completion_models.items():
        key_state = "<set>" if getattr(model, "api_key", None) else "<empty>"
        print(f"  completion_models[{key}]: provider={model.model_provider} model={model.model} api_key={key_state}")


if __name__ == "__main__":
    export_a5_text()
    write_settings()
    validate_settings()
