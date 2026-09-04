"""每源入库范围（文件选择规则，glob 相对素材根路径）。

为什么放代码而不是 MANIFEST：data/ 目录整体 gitignore，规则放代码才能随提交沉淀；
MANIFEST 记录"有什么素材"，这里记录"素材的哪些文件进库"。

背景（2026-09-02 抽查）：教程仓库里混杂大量非正文文件——
T1 正文只有 book/ 13 个章节，仓库其余 1383 个 md 是 README 多语言（9 种）与杂项；
T2 有 7 种语言目录，只取中文主体 + 英文原文。
"""

import re
from pathlib import Path

from app.kb.manifest import SourceDoc

_KB_ROOT = Path(__file__).resolve().parents[3]  # 仓库根目录

_ALLOWED_SUFFIX = {".md", ".mdx", ".html", ".pdf"}
_DEFAULT_PATTERNS = ("**/*.md", "**/*.mdx", "**/*.html", "**/*.pdf")

# 规则为空/缺省 = 默认全收（仅 .md/.mdx/.html/.pdf）
SOURCE_RULES: dict[str, list[str]] = {
    "T1": ["book/*.md"],  # 只收正文章节；README 多语言与仓库杂项不收
    "T2": ["units/zh-CN/**/*.mdx", "units/en/**/*.mdx"],  # 中文主 + 英文原文
    "T3": ["[0-9]*/**/*.md"],  # 只收 1-5 章正文
}

# T1 书内文件顺序（2026-09-04 体检发现）：glob 字母序会把 afterword(后记) 排到 introduction 前；
# 书结构 = introduction → chapter1..N → afterword → reference-answers
_T1_FILE_ORDER = {"introduction.md": 0, "afterword.md": 900, "reference-answers.md": 901}

# 每源"来源行"标签（作者/平台 · 标题）：模糊指代检索的锚点（方案 1，2026-09-02 定）。
# 人工维护、随代码提交沉淀；找不到时回退到 MANIFEST 名称（去掉尾部年份括号）。
SOURCE_LABELS: dict[str, str] = {
    "T1": "李博杰《深入理解 AI Agent：设计原理与工程实践》",
    "T2": "Hugging Face Agents Course",
    "T3": "吴恩达 Agentic AI（Datawhale 整理）",
    "A1": "Anthropic · Building Effective Agents",
    "A2": "Anthropic · How we built our multi-agent research system",
    "A3": "Anthropic · Effective context engineering for AI agents",
    "A4": "Anthropic · Effective harnesses for long-running agents",
    "A5": "Anthropic · How we contain Claude across products",
    "A6": "Anthropic · Claude Code Best Practices",
    "O1": "OpenAI · A Practical Guide to Building Agents",
    "O2": "OpenAI · Harness engineering",
    "O3": "OpenAI · New tools for building agents",
    "P1": "Lilian Weng · LLM Powered Autonomous Agents",
    "P2": "Lilian Weng · Harness Engineering for Self-Improvement",
    "P3": "Simon Willison · The Year in LLMs（2025）",
    "P4": "Chip Huyen · Agents",
    "P5": "Eugene Yan · Patterns for Building LLM-based Systems & Products",
    "P6": "Hamel · Your AI Product Needs Evals",
    "P7": "Hamel · AI Evals FAQ",
    "B1": "The Batch #322 · Disciplined Evals and Error Analysis",
    "B2": "The Batch #324 · Improve Agentic Performance with Evals, Part 2",
    "D1": "美团 · WOWService 技术报告",
    "D2": "美团 · VitaBench 评测基准",
    "D3": "火山引擎 · Agent Kit",
    "D4": "阿里云 · 多 Agent 生产落地：协作税（未采集）",
    "D5": "阿里云 · Agent 安全防护实战（未采集）",
    "D6": "阿里云 · Agent Infra 长什么样",
    "D7": "腾讯 · 汤道生谈 harness",
    "OW1": "项目自有笔记",
    "PAPER": "arXiv 学术论文（未采集）",
}


def collect_source_files(source: SourceDoc) -> list[Path]:
    """按规则展开素材路径为实际文件清单（去重、排序、限定允许的后缀）。"""
    files: set[Path] = set()
    for path in source.paths:
        p = Path(path)
        if not p.is_absolute():
            p = _KB_ROOT / p
        if not p.exists():
            continue
        if p.is_file():
            if p.suffix.lower() in _ALLOWED_SUFFIX:
                files.add(p)
            continue
        patterns = SOURCE_RULES.get(source.source_id) or list(_DEFAULT_PATTERNS)
        for pattern in patterns:
            for f in p.glob(pattern):
                if f.is_file() and f.suffix.lower() in _ALLOWED_SUFFIX:
                    files.add(f)
    return sorted(files, key=lambda f: _source_file_key(source.source_id, f))


def _source_file_key(source_id: str, path: Path) -> tuple[int, str]:
    """源内文件排序键：默认按文件名；T1 按书结构（章节数字自然序）。"""
    name = path.name.lower()
    if source_id == "T1":
        fixed = _T1_FILE_ORDER.get(name)
        if fixed is not None:
            return (fixed, name)
        m = re.match(r"chapter(\d+)\.md$", name)
        if m:
            return (int(m.group(1)), name)
    return (1000, name)


def source_label(source: SourceDoc) -> str:
    """来源行标签：优先人工维护的 SOURCE_LABELS，回退 MANIFEST 名称（去掉尾部年份括号）。"""
    label = SOURCE_LABELS.get(source.source_id)
    if label:
        return label
    cleaned = re.sub(r"[（(]\d{4}[-年].*?[）)]$", "", source.name).strip()
    return cleaned or source.source_id
