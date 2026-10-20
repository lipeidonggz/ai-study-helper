> **30 秒判断**：技术选型不是从“最近哪个框架最火”开始，而是从已经锁定的产品和架构约束往前推：Windows x64、普通非技术用户、不装 Docker／数据库／本地模型、云端双 API Key、约 10 万 block、多轮与多 Agent 很可能到来。最终基线是 **Tauri 2 ＋ Vue 3／TypeScript ＋ Python／FastAPI ＋ SQLite ＋ LanceDB ＋ LangGraph**；DeepSeek 走 OpenAI Python SDK，千问 embedding 走 DashScope SDK。
>
> 核心结论：**框架负责它真正擅长的那一层，领域事实自己掌握。LangGraph 管图编排，模型 SDK 管传输，SQLite／LanceDB 管数据，产品自己的代码管工具、预算、记忆、trace 和评测。**
>
> **适合谁读**：准备把 RAG／Agent 原型做成 Windows 桌面产品的人；正在纠结 Tauri／Electron、Python／Java／.NET、LangChain／LangGraph、Qdrant／LanceDB 的人；以及想知道“先有架构，再做技术选型”到底怎么落地的人。
>
> **系列位置**：本文属于[《文档学习问答 Agent 系列》](https://juejin.cn/column/7697101748126007305)，是当前设计阶段的最后一篇。前文依次完成需求、金标准、存储、检索、入库与评测；本篇把那些约束翻译成可交付的软件组合。
>
> **结构速览**：§0 为什么技术选型必须最后做；§1 硬约束；§2 总体架构；§3 桌面与前端；§4 后端语言；§5 数据与索引；§6 入库与模型 SDK；§7 Agent 框架；§8 评测台；§9 打包与安全；§10 验证边界；§11 一页速查。
>
> **证据标记**：**📎 官方文档**＝接口、能力与容量口径；**🤔 推断**＝由已确认约束推出的工程判断。选型没有靠现有代码倒推，模型名与 SDK 口径在 2026-10-20 核对。

**先交代几个名词**

- **桌面壳**：管理窗口、安装、更新和内部进程的应用外壳。
- **sidecar**：由桌面壳拉起的内部后端进程。
- **嵌入式数据库**：作为库随应用运行，不需要用户另装服务的数据库。
- **SSE**：服务器向浏览器单向推送事件的流式协议。
- **ANN**：近似最近邻向量索引；用召回率换查询速度。
- **ModelGateway**：产品内部统一模型接口，屏蔽不同供应商 SDK 的返回、错误和流式差异。

* * *

## 0. 为什么技术选型必须最后做

如果一开始就问“选 Tauri 还是 Electron”“Python 还是 Java”，得到的答案只会是偏好。

前面的设计先锁定了真正会改变选型的东西：

- 用户装的是产品，不是开发环境；
- 数据必须本地、不可变、可复现；
- 检索需要 block 元数据、dense、关键词和范围过滤；
- 入库需要 HTML DOM、版本、manifest 和后台任务；
- Agent 需要显式预算、工具协议和 trace；
- 评测台需要复用同一个 Agent 核心，却不能污染产品数据；
- 下一版确定多轮，且多 Agent 很可能出现。

技术选型不是再发明一次架构，而是为这些结构找最省心的实现。

* * *

## 1. 先摊开硬约束

| 约束 | 取值 | 对技术的要求 |
| --- | --- | --- |
| 用户 | 普通非技术用户 | 安装、首启、更新不能依赖命令行 |
| 平台 | Windows x64 | 优先验证一条安装链，不背跨平台负担 |
| 形态 | 桌面应用 | 外壳管理内部后端、窗口与生命周期 |
| 本地依赖 | 不装 Docker／数据库服务 | 存储和索引必须嵌入式或随包分发 |
| 模型 | 只用云端 API | 不承担 GPU、本地推理和模型分发 |
| 凭证 | 用户分别填对话与 embedding Key | Windows 安全存储；日志和 trace 不含 Key |
| 网络 | 只监听本机 | 不开放局域网／远程访问 |
| 规模 | 约 10 万 block／1,000 篇文档 | 不需要先上分布式检索 |
| 演进 | 下一版多轮，多 Agent 很可能 | 编排层必须支持状态、分支和 checkpoint |
| 质量 | trace、评测、回归、可回滚 | 版本和运行现场必须能落在本地 |

这些约束里，“普通用户不装 Docker”直接排除了服务器型向量库作为正式运行依赖；“多 Agent 很可能”则显著提高了图编排框架的价值。

* * *

## 2. 总体架构：一个产品，一个独立评测工具

```text
Windows x64
├─ 产品桌面壳（Tauri 2）
│  ├─ Vue 3 Web UI
│  └─ Python sidecar
│     ├─ FastAPI / SSE
│     ├─ LangGraph AgentRuntime
│     ├─ 5 个检索工具
│     ├─ HTML 入库
│     ├─ ModelGateway
│     ├─ SQLite 主数据
│     └─ LanceDB 派生索引
│
├─ 独立评测台（Tauri + Vue + Python）
│  ├─ ExecutionRun / EvaluationRevision
│  ├─ 独立 eval.db / traces
│  └─ 只读冻结 collection 快照
│
└─ 外部云 API
   ├─ DeepSeek：OpenAI Python SDK
   └─ 千问 embedding：DashScope SDK
```

产品与评测台共享代码和契约，但运行成两个入口、两个进程、两个数据根。评测结果不能写进正常产品会话；产品当前数据也不能被评测静默修改。

* * *

## 3. 桌面壳与前端

### 3.1 桌面壳：Tauri 2

比较过三种路线：

| 方案 | 优势 | 主要问题 |
| --- | --- | --- |
| Tauri 2 | 体积小、使用系统 WebView2、sidecar／更新／窗口能力完整 | 需要验证 Rust 壳与 Python sidecar 的联合打包 |
| Electron | 生态成熟、打包路径稳定 | 再嵌 Python 时同时维护 Chromium、Node、Python 三套运行时 |
| pywebview | Python 单运行时、壳层简单 | 更新、诊断、进程隔离和桌面能力较弱 |

首版选 Tauri。Rust 只承担壳层，不重写业务后端。

启动流程：

1. Tauri 拉起隐藏的 Python sidecar；
2. sidecar 绑定随机 `127.0.0.1` 端口；
3. 每次启动生成会话令牌；
4. 健康检查通过后显示主界面；
5. 应用退出时优雅关闭并清理残留进程。

本地 HTML 文件通过 Tauri 原生文件选择器取得路径，再交给后端。Web 前端不能任意读取磁盘。

### 3.2 前端：Vue 3

选择 **Vue 3＋TypeScript＋Vite**：

- 流式聊天、任务进度、结果矩阵和 trace 树没有 React 专属能力；
- Vue 模板与响应式状态更轻；
- Vue Query 管服务端状态；
- Pinia 管面板、筛选和临时 UI 状态；
- Vue Router 组织产品与评测页面。

接口使用 REST＋SSE：

- 普通操作走短请求；
- 回答 token、工具事件和任务进度走单向流；
- 不需要 WebSocket 的双向复杂度；
- 每条事件带 `trace_id`、序号与 schema 版本。

OpenAPI 从 FastAPI／Pydantic 生成 TypeScript 类型，减少前后端手写重复。

* * *

## 4. 后端语言：为什么最终选 Python

Java、.NET、Node 都能实现这个产品。这里不是“LangGraph 只支持 Python，所以别选别的”，而是比较完整工作负载后的综合结论。

| 语言 | 优势 | 本项目的主要成本 |
| --- | --- | --- |
| Python | HTML／文本、LangGraph、评测、模型 SDK 生态集中 | sidecar 打包、冷启动、类型纪律 |
| Java | 并发、类型、JVM 运维、SQLite/JDBC 成熟 | 文本与评测生态不如 Python；图编排需验证等价能力 |
| .NET | Windows 原生交付、桌面集成强 | 要么替换 Python 文本与 Agent 生态，要么仍带 sidecar |
| Node | 前后端同语言、LangGraph.js | 中文文本、评测与原生依赖维护 |

最终选 Python 的决定性因素是组合优势，而不是单点：

- `lxml`／readability 处理 HTML；
- LangGraph 原生图编排与 checkpoint；
- 评测实验与 provider SDK 密集；
- FastAPI＋Pydantic 可以把工具契约、SSE 与数据校验统一。

已知代价直接承担：Python 由 PyInstaller 随应用打包，用户不安装 Python。只有遇到 Windows 打包失败、关键库不可用、LangGraph 恢复语义不满足或性能突破硬件基线时，才重新评估语言。

* * *

## 5. 数据与索引

### 5.1 SQLite 是主数据，LanceDB 是派生索引

首版采用：

```text
SQLite
  = 文档 / 版本 / 章节 / block / manifest / 任务 / 会话 / 活动快照

LanceDB
  = dense 向量 + BM25/FTS 派生索引

文件系统
  = 原始 HTML / 抽取中间产物 / trace JSONL / artifacts / 备份
```

SQLite 负责短事务、身份关系与活动指针；LanceDB 可以随时从 block 重建。

为什么选 LanceDB：

- 嵌入式，不需要用户安装服务；
- 同一套表支持向量与全文检索；
- 表版本适合不可变快照；
- Python 侧可直接运行。

### 5.2 首版容量：先按 10 万 block

产品估算：

```text
约 1,000 篇文档 × 每篇约 100 block
≈ 10 万 block
```

10 万条 1024 维 float32 向量约 410MB。加上正文、FTS、SQLite、artifact 和多份快照，仍在 4 核／8GB／SSD 的可控范围。

LanceDB 官方 FAQ 的口径是：

- 约 10 万条数据可以不建向量索引；
- OSS 单节点可以较舒适地处理百万级向量；
- 数亿向量／TB 级建议 Enterprise。

因此首版明确不建 ANN 向量索引：

- dense 使用精确扫描；
- 关键词使用 BM25/FTS；
- 超过 10 万 block 或真实首字延迟不达标，再建 ANN；
- 100 万以上不进入首版性能承诺。

这省掉一层索引构建、召回参数和增量维护复杂度。

* * *

## 6. 入库与模型 SDK

### 6.1 HTML 技术栈

```text
URL 获取：httpx + 自定义 SSRF guard
文件：pathlib + SHA-256 + charset-normalizer
DOM：lxml + readability-lxml
结构：自有语义遍历
切块：自有 splitter + 固定 sizing estimator
```

不执行 JavaScript。正文提取、章节树和切块都保持确定性；不调用模型猜章节。

### 6.2 DeepSeek：没有独立官方 SDK

DeepSeek [官方首次调用文档](https://api-docs.deepseek.com/zh-cn/)明确：API 兼容 OpenAI／Anthropic，官方示例使用对应 SDK。当前 Flash 模型名是 `deepseek-flash`。

因此选：

```python
from openai import OpenAI

client = OpenAI(
    api_key=...,
    base_url="https://api.deepseek.com",
)
```

采用 OpenAI SDK 不等于采用 OpenAI 模型。Provider、base URL 和 model ID 都由产品配置固定，并写入 trace。

### 6.3 千问 embedding：使用官方 DashScope SDK

阿里云百炼同时提供 DashScope 与 OpenAI-compatible 两种调用。官方文档说明 DashScope 提供更多独有能力，并提供官方 Python SDK。

本项目选 DashScope：

```python
dashscope.TextEmbedding.call(
    model="qwen3.7-text-embedding",
    input=batch,
    dimension=1024,
    output_type="dense",
)
```

官方参数：1024 默认维度、单批最多 20 条、单行上限 128k token。详见[向量化文档](https://help.aliyun.com/zh/model-studio/embedding)。

### 6.4 为什么还要一层 ModelGateway

不是为了重写 HTTP，而是因为业务层不该直接理解两家 SDK 的差异。

```python
class ModelGateway(Protocol):
    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[ChatEvent]: ...
    async def embed(self, request: EmbedRequest) -> EmbedResponse: ...
```

Gateway 统一：

- 流式事件；
- 首 token 时间；
- usage 与 request ID；
- 错误分类；
- 重试边界；
- 实际 model ID；
- 测试用 fake provider。

SDK 自动重试默认关闭。否则一次用户可见失败背后可能已经发生多次计费请求，而预算和 trace 只看到一次。

* * *

## 7. Agent 框架：为什么最后选 LangGraph

### 7.1 第一轮判断：单 Agent 时，自研更简单

最初的设计前提是：

- 单轮；
- 单 Agent；
- 一个 ReAct 循环；
- 五个工具；
- 显式 Action／Observation。

在这个前提下，框架会额外拥有 prompt、memory、retry、tool calling 和 tracing 的控制权，容易形成两套事实源。自研小循环反而更容易守住预算和错误语义。

### 7.2 前提变化：多轮确定，多 Agent 很可能

下一版确定进入多轮，而且多 Agent 很可能成为产品方向。复杂度随即变成：

- 多节点；
- 条件路由；
- 证据不足回退；
- Agent 交接；
- 长任务 checkpoint；
- 暂停恢复；
- 人工介入；
- 按图路径诊断。

这时继续坚持自研，实际上是在逐步重写一套更弱的状态图运行时。

### 7.3 最终分工

```text
LangGraph：
  节点、状态图、条件边、子图、checkpoint、暂停恢复

产品自有：
  AgentRuntime 接口、工具真实实现、预算准入、错误语义、
  collection 快照、引用校验、ConversationStore、MemoryPolicy、trace
```

首版可以把单轮 Agent 放在一个小图里；多轮和多 Agent 到来后加节点与边，而不是换掉整个运行框架。

### 7.4 LangChain 只用需要的公共件

LangChain 解决模型、embedding、文档、工具和向量库之间的接入与组合重复。但本产品已经有自己的 block、Retriever、Memory、预算和评测事实源。

因此：

- LangGraph 图编排：采用；
- LangChain Core 消息／工具 schema：按需使用；
- 高层 Chain／AgentExecutor：不用；
- Retriever／VectorStore 抽象：不作为主路径；
- LangChain Memory：不作为事实源；
- LangSmith：不是运行前提。

“不是运行前提”的意思是：没有外部 tracing 账号、断网或 SaaS 故障时，问答、入库、评测和本地 trace 仍然正常。外部平台只能接收可选导出。

* * *

## 8. 评测台技术

评测台是独立开发工具：

- 独立 Tauri 窗口；
- Vue 3 Web UI；
- 独立 Python sidecar；
- 独立 SQLite 数据根；
- 只监听 `127.0.0.1`；
- 不做 CLI、远程访问和首版导入／导出。

核心对象：

```text
ExecutionRun
  └─ Attempt

EvaluationRevision
  ├─ Judgment
  ├─ effective judgment binding
  ├─ aggregate
  └─ report projection
```

执行调度用 `asyncio + bounded semaphore + SQLite 状态`：

- 质量评测允许并发；
- 延迟评测强制串行；
- 达到预算后暂停；
- 失败 attempt 可替代；
- 封存后不再追加。

程序 checker 使用 Python 受控注册表，数据库只存 ID、参数和版本，不存可执行代码。LLM judge 首版每个语义项只跑一次，并明确不宣称已测量判官抖动。

* * *

## 9. 打包、更新与安全

| 项 | 首版方案 |
| --- | --- |
| Python | PyInstaller one-folder sidecar |
| 安装器 | Tauri NSIS，按用户安装 |
| WebView | Windows 自带 WebView2；安装器准备 bootstrapper |
| Key | Windows Credential Manager |
| 更新 | 签名更新，用户确认后安装 |
| 回滚 | 更新前备份；迁移失败恢复上一版本 |
| 网络 | 只监听 `127.0.0.1`；外部只连模型 API |
| trace | 本地 JSONL＋SQLite 投影；外部分析可选 |

“普通用户安装”是硬验收：

- 不安装 Python、Node、Docker 或数据库；
- 不需要手工启动服务；
- 错误 Key、断网和限流都有可理解状态；
- 应用退出后内部后端不残留；
- 两个 Key 不出现在配置、日志、trace、备份或诊断包。

* * *

## 10. 真正需要验证什么

设计完成不等于选型完成。落地前有三项硬验证：

### 10.1 Tauri＋Python sidecar

验证：

- 无控制台窗口；
- 健康检查；
- 崩溃有限重启；
- 应用退出无残留；
- 更新后旧 sidecar 不残留。

失败备选：Electron＋同一 Python 核心。

### 10.2 LanceDB 在 Windows 的实际表现

用真实 HTML 产物验证：

- 官方 wheel 可离线打包；
- 10 万 block 构建、冷启动和查询；
- dense＋FTS 排序稳定；
- 产品与评测双进程只读安全；
- 索引损坏可重建。

失败备选：SQLite＋Qdrant local。

### 10.3 HTML 提纯

验证：

- 标题／章节／表格／代码样本可核对；
- 导航、页脚和推荐阅读不混入；
- JS 空壳能明确失败；
- 表格拆行保留 caption 与表头。

这些验证只验证实现风险，不重新讨论已经确认的产品结构。

* * *

## 11. 一页速查

**总体**：Tauri 2＋Vue 3／TypeScript＋Python／FastAPI＋SQLite＋LanceDB＋LangGraph。

**模型**：DeepSeek 用 OpenAI Python SDK；Qwen embedding 用官方 DashScope SDK；上面只有薄 ModelGateway。

**Agent**：多轮与多 Agent 很可能出现，所以 LangGraph 管图，产品代码管工具、预算、记忆事实、trace 与评测。

**数据**：SQLite 主数据，LanceDB 保存 dense 向量并建 BM25/FTS；10 万 block 首版基线不建 ANN，dense 走精确扫描。

**评测**：独立 Tauri＋Vue＋Python＋SQLite；ExecutionRun 与 EvaluationRevision 分开。

**部署**：PyInstaller sidecar＋Tauri NSIS；普通用户不装开发组件；Key 进系统凭证库；只监听回环。

**一句话**：**先让架构决定技术，再让框架只负责它真正擅长的那一层。**

* * *

**上一篇**：《文档问答 Agent 的评测台：从跑分页到可诊断、可回归的系统》（审核中）

**完整设计记录**：[0060 技术选型（HTML 源文件）](https://github.com/lipeidonggz/ai-study-helper/blob/master/memory/0060-technology-selection.html)

**官方依据**：[DeepSeek API](https://api-docs.deepseek.com/zh-cn/) · [阿里云百炼 Embedding](https://help.aliyun.com/zh/model-studio/embedding) · [LanceDB OSS FAQ](https://docs.lancedb.com/faq/faq-oss)

* * *

**前置阅读**：

1. 《[Agent 开发为什么要测试驱动升级版](https://juejin.cn/post/7696916617436889115)》
2. 《[需求定义四步](https://juejin.cn/post/7697089423960719396)》
3. 《[金标准：什么算对、怎么判](https://juejin.cn/post/7697089423961014308)》
4. 《[由检索需求反推存储](https://juejin.cn/post/7697269670269321225)》
5. 《[Agentic RAG 检索侧设计](https://juejin.cn/post/7697829509392121865)》
6. 《[Agentic RAG 能检索还不够：从质量属性反推错误、预算、Trace 与版本管理](https://juejin.cn/post/7698247718677839898)》
7. 《[从 HTML 到可检索、可复现的快照](<./0055-从 HTML 到可检索、可复现的快照.md>)》
8. 《文档问答 Agent 的评测台：从跑分页到可诊断、可回归的系统》（审核中）
