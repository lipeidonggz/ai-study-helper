"""混装语料实验（A5 英文 ＋ 掘金中文）的准备脚本。

与单篇实验的差别：要从**两个 HTML 源**分别导出文本到 input/（A5 用我们的抽取器，
掘金用之前存下的原始 HTML 页）。

settings.yaml 里：
- 提示词挂钩**可选**（ASH_PROMPT_DIR 设了才加）—— 因为 prompt-tune 生成提示词之前
  那些文件还不存在，配置校验会读文件而失败；
- 补 embedding_models 占位块（prompt-tune 无条件构造嵌入客户端需要它）。
"""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
PROMPT_DIR = os.environ.get("ASH_PROMPT_DIR", "")
PROMPT_BLOCK = (
    f"""
# 提示词挂钩（ASH_PROMPT_DIR={PROMPT_DIR}）
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

_CHROME = re.compile(r"(阅读|点赞|人赞同|收藏|评论\s*\d|发布于|关注|扫码|微信)")


def load_chunker():
    path = REPO / "backend" / "app" / "kb" / "chunker.py"
    spec = importlib.util.spec_from_file_location("ash_chunker", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_doc(html_path: Path, out_name: str, fallback_title: str, drop_chrome: bool) -> None:
    chunker = load_chunker()
    html = html_path.read_text(encoding="utf-8", errors="ignore")
    title = (BeautifulSoup(html, "html.parser").title.string or fallback_title).strip()
    title = re.sub(r"\s*[-|]\s*掘金\s*$", "", title)
    sections = chunker.extract_sections_html_auto(html)

    lines: list[str] = [f"# {title}", ""]
    for sec in sections:
        if sec.path:
            lines.append(f"## {sec.path}")
            lines.append("")
        for para in sec.paragraphs:
            text = re.sub(r"\s+", " ", para).strip()
            if not text:
                continue
            if drop_chrome and len(text) < 90 and _CHROME.search(text):
                continue
            lines.append(text)
            lines.append("")

    body = "\n".join(lines).strip() + "\n"
    out = ROOT / "input" / out_name
    out.write_text(body, encoding="utf-8")
    zh = len(re.findall(r"[\u4e00-\u9fff]", body))
    print(f"写出 {out.relative_to(REPO)}  字符 {len(body):,}（中文 {zh:,}）")


SETTINGS = """\
# 混装语料实验（2026-09-25）——A5 英文 ＋ 掘金中文
completion_models:
  default_completion_model:
    model_provider: deepseek
    model: deepseek-chat
    auth_method: api_key
    api_key: ${DEEPSEEK_API_KEY}
    call_args:
      timeout: 180
      num_retries: 2

# prompt-tune 会无条件构造嵌入客户端，缺这块直接 ValueError ⇒ 占位即可（top/random 不会真调用）
embedding_models:
  default_embedding_model:
    model_provider: openai
    model: text-embedding-3-large
    auth_method: api_key
    api_key: not-used-placeholder

concurrent_requests: 4
__PROMPT_BLOCK__
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


def main() -> None:
    write_doc(
        REPO / "data" / "kb-src" / "articles" / "anthropic" / "05-how-we-contain-claude.html",
        "a5.txt",
        "A5",
        drop_chrome=False,
    )
    write_doc(REPO / "data" / "tmp" / "juejin-raw.html", "juejin.txt", "掘金文章", drop_chrome=True)

    out = ROOT / "settings.yaml"
    out.write_text(SETTINGS.replace("__PROMPT_BLOCK__", PROMPT_BLOCK), encoding="utf-8")
    print(f"\n写出 {out.relative_to(REPO)}   提示词: {PROMPT_DIR or '（GraphRAG 原版）'}")

    try:
        from graphrag.config.load_config import load_config
    except Exception:  # noqa: BLE001
        from graphrag.config import load_config  # type: ignore

    cfg = load_config(ROOT)
    print("  自校验 OK · chunking", cfg.chunking.size, "/", cfg.chunking.overlap)
    print("  workflows:", cfg.workflows)
    if PROMPT_DIR:
        rp = cfg.community_reports.resolved_prompts().graph_prompt
        print("  报告提示词首行:", rp.strip().splitlines()[0][:70])


if __name__ == "__main__":
    main()
