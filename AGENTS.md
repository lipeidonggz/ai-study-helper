# ai-study-helper 项目核心约定

本文件是 Codex 在本项目中的持久记忆核心，每次会话开始都会自动读取。请严格遵守以下约定。

## 项目核心目标

基于本目录一步步构建和完善一个产品，练手 AI Agent 设计开发相关的知识与技能。构建过程本身就是学习过程，产品只是载体。

## 两条铁律

1. **过程记录一律用 HTML**：产品需求讨论、架构设计讨论、对比测试结果等所有值得记录的内容，一律以 HTML 格式记录到 `memory/` 目录，禁止用 Markdown 作为记录载体。
2. **客观专业的讨论角色**：始终以客观、专业的身份与用户讨论产品需求、架构设计、具体技术，给出依据和取舍，而不是一味迎合。

## 记录规范

- 记录存放于 `memory/` 目录，HTML 格式，共享样式 `memory/assets/style.css`。
- `memory/index.html` 是记忆主页和记录索引，每次新增记录后必须同步更新。
- 文件名规范：`NNNN-短描述.html`（NNNN 为会话序号）。
- 触发时机：当出现值得记录的内容（需求讨论、架构决策、对比测试结果等）时，主动提醒用户确认后记录；用户明确要求记录时直接记录。
- 记忆体系：本文件（AGENTS.md）与 `memory/` 目录共同构成项目级持久记忆；Codex 系统级自动记忆（`~/.codex/memories_*.sqlite`）由系统自动管理，无需手动写入。

## 提交规范（重要）

- 每次 git commit 的说明必须包含两部分：标题（本次改动一句话）+ 正文（当前项目主要功能摘要）。
- 功能摘要以 `memory/0009-current-features.html` 为权威副本：功能有增删时先更新该记录，再复制进 commit 正文。
- 敏感信息（API Key 等）绝不允许出现在 commit 或记录中。

## 当前状态

- 阶段：阶段 1 进行中——LLM 配置与真实调用链路已跑通（memory/0006）；当日进度见 memory/0007-session-progress.html
- 技术基线：Vue 3 + TS 前端；Python + FastAPI 后端；SSE 流式；SQLite + Qdrant 本地存储；存储接口抽象（Port + Adapter）；本地 embedding
- 代码导读：主处理流程阅读路线见 memory/0008-code-reading-guide.html
- 下一步：建立 golden set 评测基线（120 条用例 + 最小评测 runner）→ 获取并固定《三国演义》txt → 阶段 1 验证点跑批
