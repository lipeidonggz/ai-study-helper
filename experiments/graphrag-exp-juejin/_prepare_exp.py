"""实验准备（第二篇：掘金文章）→ 纯文本输入 + 最小 settings.yaml + 配置自校验。

设计取舍（2026-09-25）：**与 A5 那轮保持完全一致**，唯一差别是输入文档——
这样两篇的结果可比（虽然本轮不要求对照）。
- 输入用我们自己的 `app.kb.chunker.extract_sections_html_auto` 洗成纯文本，保留小节标题；
- 额外一步：丢掉站点元信息行（"xx 阅读 xx 点赞 xx 人赞同"这类），那是掘金的页面装饰；
- settings.yaml 里 api_key 写占位符，**真实密钥只从环境变量进来**，绝不落文件、绝不打印；
- 本脚本不调用任何 LLM，也不联网。
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent  # data/tmp/graphrag-exp -> data/tmp -> data -> 仓库根
SRC_HTML = REPO / "data" / "tmp" / "juejin-raw.html"

# 提示词目录可切换：默认用我们手改的那套；ASH_PROMPT_DIR=prompts-tuned 用 prompt-tune 生成的那套
PROMPT_DIR = os.environ.get("ASH_PROMPT_DIR", "prompts")

# 掘金页面装饰行：只有站点元信息，进图只会污染实体（日期/数字/作者）
_CHROME = re.compile(r"(阅读|点赞|人赞同|收藏|评论\s*\d|发布于|关注|扫码|微信)")


def load_chunker():
    """按文件路径直接加载我们的 chunker（它只依赖 stdlib + bs4）。"""
    path = REPO / "backend" / "app" / "kb" / "chunker.py"
    spec = importlib.util.spec_from_file_location("ash_chunker", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def export_text() -> str:
    chunker = load_chunker()
    html = SRC_HTML.read_text(encoding="utf-8", errors="ignore")
    title = (BeautifulSoup(html, "html.parser").title.string or "untitled").strip()
    title = re.sub(r"\s*[-|]\s*掘金\s*$", "", title)  # 去掉站点后缀
    sections = chunker.extract_sections_html_auto(html)

    lines: list[str] = [f"# {title}", ""]
    dropped = 0
    for sec in sections:
        if sec.path:
            lines.append(f"## {sec.path}")
            lines.append("")
        for para in sec.paragraphs:
            text = re.sub(r"\s+", " ", para).strip()
            if not text:
                continue
            if len(text) < 90 and _CHROME.search(text):
                dropped += 1
                continue
            lines.append(text)
            lines.append("")

    text = "\n".join(lines).strip() + "\n"
    out = ROOT / "input" / "doc.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"标题: {title}")
    print(f"写出 {out.relative_to(REPO)}")
    print(f"  sections   : {len(sections)}")
    print(f"  丢弃装饰行 : {dropped}")
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

# 并发降到 4：默认 25 会把 DeepSeek 打急（第一次重跑就是这么卡住的）
concurrent_requests: 4

# 注意：不写 input 块 —— 默认 type 就是 text，会读 input/ 下的文本文件。
# 踩过的坑（2026-09-25）：GraphRAG 用 string.Template 做环境变量替换，
# 于是 settings.yaml 里**任何字面美元符号都会破坏解析，连注释里的也算**：
#   · 给 file_pattern 写正则的结尾锚  → Invalid placeholder in string
#   · 在注释里写出占位符的完整写法    → Environment variable not found

# 2026-09-25：默认抽取提示词第 25 行要求 "Return output in English" ⇒ 图与报告全英文。
# 这里换成自定义提示词（**只改了那一句语言要求**，其余字节不变），让它跟随原文语言。
extract_graph:
  prompt: __PROMPT_DIR__/extract_graph.txt

# 2026-09-25：实体描述摘要这一步也接上（两套目录里都有同名文件，便于整体切换）
summarize_descriptions:
  prompt: __PROMPT_DIR__/summarize_descriptions.txt

# 2026-09-25：prompt-tune 会在构造嵌入客户端时**无条件**调用
# get_embedding_model_config("default_embedding_model")，缺 key 直接抛
# ValueError（"set the embedding_models configuration"）。
# 我们索引时跳过了所有 embedding 工作流，所以之前故意没写这块；
# 这里补一个**占位**配置：top/random 选法不会真调用它（只有 auto 会）。
embedding_models:
  default_embedding_model:
    model_provider: openai
    model: text-embedding-3-large
    auth_method: api_key
    api_key: not-used-placeholder

# 2026-09-25：报告提示词**原样翻译成中文**（不改语义）——目标是把"报告语言"
# 从"模型默认回落英文"改成稳定中文。走官方 community_reports.graph_prompt 钩子。
community_reports:
  graph_prompt: __PROMPT_DIR__/community_report_graph.txt

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
    out.write_text(SETTINGS.replace("__PROMPT_DIR__", PROMPT_DIR), encoding="utf-8")
    print(f"\n写出 {out.relative_to(REPO)}")
    print(f"  提示词目录: {PROMPT_DIR}")
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
    resolved = cfg.extract_graph.resolved_prompts().extraction_prompt
    lang_line = next(
        (ln.strip() for ln in resolved.split("\n") if "Return output in" in ln), "(找不到语言要求那行)"
    )
    print(f"  extract_graph.prompt = {cfg.extract_graph.prompt}")
    print(f"  -> 实际生效的语言要求: {lang_line}")
    # 判据：默认提示词里写的是 "Return output in English"；出现别的话术就说明用的是自定义版
    is_custom = "Return output in English" not in resolved
    print(f"  -> 用的是自定义提示词? {'是' if is_custom else '否（仍是默认英文）'}")
    rp = cfg.community_reports.resolved_prompts().graph_prompt
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in rp)
    print(f"  community_reports.graph_prompt = {cfg.community_reports.graph_prompt}")
    print(f"  -> 报告提示词首行: {rp.strip().splitlines()[0][:80]}")
    print(f"  -> 报告提示词含中文? {'是' if has_cjk else '否（仍是英文）'}")
    print("  community_reports: max_length =", cfg.community_reports.max_length,
          "max_input_length =", cfg.community_reports.max_input_length)
    for key, model in cfg.completion_models.items():
        key_state = "<set>" if getattr(model, "api_key", None) else "<empty>"
        print(f"  completion_models[{key}]: provider={model.model_provider} model={model.model} api_key={key_state}")


if __name__ == "__main__":
    export_text()
    write_settings()
    validate_settings()
