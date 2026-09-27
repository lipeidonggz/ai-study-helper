# GraphRAG 的 Global Search：文档让你"选一层"，代码把 ≤N 全给了

> **30 秒判断**：你在用 GraphRAG 的全局检索（`global_search`），或者正打算用它回答"这批资料整体讲了什么"这类问题。这篇把它**默认模式（非自适应）**的五步流程拆到代码级，并说清三个会直接影响你结果的地方：材料其实不是"某一层"、"重要性分"不是同一把尺、以及那个让用户填层号的旋钮本身就不该存在。
>
> **一句话结论**：Global Search 的本质是"**把社区报告全量扫一遍、由 map 各自出要点、再由 reduce 拼成综述**"。它的成本主要由"要扫多少报告"决定，而**这个数量被层号间接控制**——偏偏层号在实现里既不是"某一层"（是 `level <= N` 的全部），又因为一个上游空操作失去了原本的粒度保证。
>
> **适合谁读**：在用 GraphRAG（或类似"预生成摘要 ＋ map-reduce"方案）做全局问答的同学。不需要读过原论文，但要知道什么是社区报告。
>
> **阅读成本**：约 20 分钟。1 节是一屏流程；2 节讲材料选择（含一个上游 bug 的来龙去脉）；3–5 节是三步执行；6–7 节是容易踩的机制与参数；8 节是三条边界与替代路线。
>
> **证据标记**：**✅ 实测**（本地探针／官方 notebook 输出）· **📄 源码**（读 graphrag 3.2.0 与官方仓库）· **📎 官方文档／论文** · **🤔 推断**。

---

## 0. 为什么单独看它

GraphRAG 的四种检索模式里，只有 Global Search 能干一件事：**回答"跨整个语料"的问题**。Local Search 是从一组命中实体出发看局部邻域，Basic Search 是纯向量找相似片段，它们都给不出"这批资料整体有哪些主题"。

**它为什么需要单独一套机制？因为"全局问题"根本不是检索问题。** "这批资料里有哪些主要主题"这种问法，向量检索无从下手——top-k 的前提是"能挑出若干条最相关的记录"，而"整体讲了什么"的**相关性无法定义在任意单条记录上**。论文把这层说得很直白（📎 原文）：

> RAG fails on global questions directed at an entire text corpus, such as "What are the main themes in the dataset?", since this is inherently a **query-focused summarization** (QFS) task, rather than an explicit retrieval task.

另一条现成的路也不行：**直接对源文本做摘要**——论文里那个对比条件 TS（把同一套 map-reduce 直接作用在源文本上）恰恰是**最贵的一条**，token 就是它的 100% 基准。

于是 GraphRAG 的解法是**在两者之间插一层"中间粒度"**：索引期先把语料压成一批**社区报告**，查询期不再去捞原文，而是对这批准摘要做一次 map-reduce（每批出要点 → 汇总成一篇）。这就是它省 token 的来源——根层社区摘要只需要 TS 的 2–3%。

**那"用户为什么必须自己填社区层级"？** 因为"压到多粗"是一个绕不开的权衡，而这个权衡取决于**用户想要什么**，不是系统能替他猜的：

| 层号 | 社区形态 | 材料特征 | 适合的问题 |
| --- | --- | --- | --- |
| **低（C0 = 根）** | 社区少而大 | 粗、便宜、偏全景 | "整体有哪些主题" |
| **高** | 社区多而小 | 细、贵（每次要扫 `level <= N` 的全部报告） | "某个子话题具体怎么做的" |

默认路径下系统**不做这个判断**——它把这个旋钮直接交给调用方，而且是**必填**（2.3 节给出 API、notebook、配置三处证据）。至于"让用户填层号"这个设计本身好不好，8.1 节会说清它的问题。

这条链上还有几个地方会直接影响你看到的结果，而且**不看代码是不知道的**：材料到底是哪几份、分数是怎么打的、筛选发生在哪一步、引用能追到多细。下面按执行顺序讲。

代码路径：`structured_search/global_search/{search.py, community_context.py}`；两段提示词在 `prompts/query/global_search_{map,reduce}_system_prompt.py`。

---

## 1. 五步，一屏看完

| 步骤 | 做什么 | 成本 | 信息损耗 |
| --- | --- | --- | --- |
| **第 0 步 · 选材料** | 从 `communities` / `community_reports` 两张表里取出"报告集合"（**当前版本 = `level <= N` 的全部报告**） | 纯本地 | 无 |
| **第 1 步 · 装配上下文** | 先用**固定种子的随机方法把报告打散**，再按 token 预算切批（**每批 = 一次 map 调用**） | 批数决定 map 次数 | **无**（只是切块，不丢内容） |
| **第 2 步 · Map** | 每批独立产出"一列点 ＋ 每点 0–100 的重要性分" | **主要成本**（批数 × 一次调用） | **有**：每批输出受 `max_length` 限制（默认 1000 词）⇒ 一批承载越多，压缩越狠、细节丢得越多 |
| **第 3 步 · Reduce** | 丢掉 0 分、按分降序把点填进新窗口，生成最终答案 | 一次调用 | **有**：按 `max_data_tokens` 累加，**超了就 break** ⇒ 低分点被整条丢弃 |
| **第 4 步 · 返回** | 答案 ＋ 报告级引用（`[Data: Reports (id)]`） | — | 无 |

一句话记住分工：**map 负责"从每批里摘要点并打分"，reduce 负责"在有限窗口里挑最重要的点拼成一篇"**。真正的筛选发生在两处——map 的打分，和 reduce 的按分截断。

另外注意上表最后两列：**这条链是有损的，而且两处损耗性质不同**——map 是"批内压缩"（摘要天然有损，长度上限让它更狠），reduce 是"整条丢弃"（低分点直接不进最终窗口）。第 0、1 步本身不丢内容。

---

## 2. 第 0 步：材料是怎么选的

这一步就定死了后面拿什么当材料。**输入是两张表，输出是"报告列表"**（函数是 `read_indexer_reports`）。

| 表 | 一行是什么 | 关键列 | `title` 是什么 |
| --- | --- | --- | --- |
| `communities`（层级结构） | 一个社区 | `community / level / parent / children / entity_ids / size` | **`"Community N"`**（索引期直接拼字符串，N = 社区 id） |
| `community_reports` | 一个社区一份 LM 报告 | `community / level / summary / full_content / rank / findings` | **LM 生成的报告标题** |

默认模式（`dynamic_community_selection=False`）下，代码只做四件事：

1. **把社区表 explode 成"社区 × 成员实体 id"的长表**——这是"实体"唯一的出场；
2. **两张表各按 `level <= N` 砍一刀**（`N` = 外部必填的 `community_level`）；
3. **按 `title` 分组、取 `community` 最大值**——代码注释写着 `# perform community level roll up`，docstring 说它是"每个实体只留它所属的最深社区"。**但这一步在当前版本是空操作**（下一小节说为什么）；
4. **用这组 community id 与报告表 inner merge** ⇒ 输出报告集合（不是实体集合、也不是社区集合）。

### 2.1 那个"空操作"：roll-up 本来在干什么

先说它**原本**的作用：roll-up ＝ "把同一实体的多行压成一行，只留它所属的最深社区"，目的是**让每个实体恰好由一份报告代表**。

为什么需要它？社区是**层级**结构，一个实体会同时出现在各层的祖先社区里。实体 A 可能同时属于 `C5`(L0)、`C20`(L1)、`C77`(L2)；若不处理，把 `level <= 2` 的报告全喂给模型，**同一批内容会被多层报告重复代表**——模型看到三份讲同一件事的报告，还会以为它更重要。

roll-up 就是来解决这个"重复代表"的。

**而它现在失效了。** 失效的原因只有**一行实质差别**：分组那一行没变，**变的是传给它的表**。两版对照（📄 源码，链接见文末附录）：

**v1.x（roll-up 生效）**——入参是**实体级**的 `final_nodes` 表，`title` = **实体名**：

```python
def read_indexer_reports(
    final_community_reports: pd.DataFrame,
    final_nodes: pd.DataFrame,        # ← 实体 × 社区的长表（title = 实体名）
    community_level: int | None,
    ...
):
    nodes_df = final_nodes
    ...
    if not dynamic_community_selection:
        # perform community level roll up
        nodes_df = nodes_df.groupby(["title"]).agg({"community": "max"}).reset_index()
        filtered_community_df = nodes_df["community"].drop_duplicates()
        reports_df = reports_df.merge(filtered_community_df, on="community", how="inner")
```

**v2.0.0 起（roll-up 变成空操作）**——入参换成**社区表**，`title` = **`"Community N"`**：

```python
def read_indexer_reports(
    final_community_reports: pd.DataFrame,
    final_communities: pd.DataFrame,  # ← 换成社区表
    community_level: int | None,
    ...
):
    nodes_df = final_communities.explode("entity_ids")   # ← 唯一实质改动
    ...
    if not dynamic_community_selection:
        # perform community level roll up
        nodes_df = nodes_df.groupby(["title"]).agg({"community": "max"}).reset_index()
        filtered_community_df = nodes_df["community"].drop_duplicates()
        reports_df = reports_df.merge(filtered_community_df, on="community", how="inner")
```

`groupby(["title"])` 那一行**两版一字不差**；变的是 `title` 的含义：旧版里它是**实体名** ⇒ "同一实体的多行合并、取最深社区"（roll-up 生效）；新版里它是 **`"Community N"`**（社区表的 title 在索引期被改写成了编号，每个社区本来就唯一）⇒ **分组等于什么都没做**，最终就等价于"把 `level <= N` 的报告全给出去"。

坏点在 **v2.0.0**（PR #1674，2025-02-07），v3 只是继承；**v1.x 不受影响**。

一个本地探针能把它演清楚（✅ 实测：用合成层级复刻那个函数，喂两种 title 口径）：

```text
合成层级：C5{A,B,C} L0 / C6{D} L0
          C20{A,B} C21{C} C22{D} L1
          C77{A} C78{B} L2          （满足"子社区完整划分父社区"）

community_level = 1
  title = "Community N"（当前版本） → 5 份报告 [5,6,20,21,22]  ← 就是 "level ≤ 1 的全部"
  title = 实体名（旧版本）          → 3 份报告（每个实体只留最深那个）
```

**结论：这个参数在当前版本的实际语义 = `level <= N` 的全部报告**，不是"只取第 N 层"、也不是"每个实体取最深社区"。

这件事我们提交给了上游：**[microsoft/graphrag#2573](https://github.com/microsoft/graphrag/issues/2573)**（📎）。提交时的口径是：**不主张"应该按最深社区取"**（那是可争的设计取舍），而是指出**代码、注释、文档三者互相矛盾**——这一条不可争；"哪种行为才对"留给维护者，并给出三个方向：A 用一行恢复 roll-up（`groupby("entity_ids")`）、B 严格单层（`level == N`）、C 保持 `<= N` 但改文档与 docstring。

### 2.2 于是这个参数的实际语义

- **= "报告集合 = `level <= N` 的全体报告"**，外加一条几乎恒真的约束（报告所属社区必须能在社区表里找到对应行）。
- **`community_level = None`**：不过滤 ⇒ **全部社区、所有层**的报告。
- **它不是"选第 N 层"**：API docstring 写 "the community level to search at"、官方 notebook 注释写 "higher value means more fine-grained"——**说法都是"那一层"，实现是"≤N 的所有层"**，而且偏差方向是**"给得比说的多"**（把更粗的层全塞了进来）。
- **模型不知道自己在读哪一层**：`read_community_reports` 只映射 `id / title / community / summary / full_content / rank / attributes`——**`level` 没进对象**，上下文表头里也没有 level 列。这条后面会变成一个"放大器"（见 4.2）。

### 2.3 层从哪来、层的高低意味着什么

**层不是自适应选的，是外部必填**：Python API 里 `community_level: int | None` 是必填参数（无默认值）；官方 notebook 直接写常量 `COMMUNITY_LEVEL = 2` 再拿去过滤；配置 `GlobalSearchDefaults` 里**没有"默认层"**（只有 `max_context_tokens = 12_000`、`dynamic_search_max_level = 2`）。⇒ **不是自适应，是"外部指定 ＋ 全量使用该层及更浅层"。**

**层的高低**对应什么，见第 0 节那张表；这里补一条更要紧的：**选层是个裸超参**——原论文 §4 专门做实验比较"哪层最好"，结论是 C0 明显差、C1–C3 在各数据集与指标上互有胜负、**没有通用答案**（📎）。论文需要专门比出这个结论，本身就说明没有默认答案。

---

## 3. 第 1 步：装配上下文（它决定 map 分几批）

顺序是：**算社区权重 → 按 rank 筛 → 打乱 → 分批 → 批内排序成表 →（可选）前置对话历史**。

- **算社区权重**（仅当传了 entities）：遍历实体 → 取 `community_ids` → 把 `text_unit_ids` 累加到对应社区 ⇒ 权重 = **该社区内所有实体关联的去重 text unit 数**，再按最大值归一化到 0–1，写进报告 attributes（默认名 `occurrence weight`）。这就是官方 notebook 里那句 "community weights for context ranking" 的实义。
- **按 `rank` 筛**：`report.rank >= min_community_rank`（默认 0 ⇒ 全过）。⚠️ 这个 `rank` 是**报告自身的重要性分**（索引期由社区报告提示词打的），**不是查询相关度**——它的作用是"让你直接砍掉低重要性报告"来省 token。
- **打乱**（**不是可选项，是写死的载荷步骤**）：`random.seed(86)` ＋ shuffle ⇒ **有固定种子的伪随机，同输入可复现**。构造器 `context_builder_params` 里 `shuffle_data: True` 是硬编码的（`query/factory.py`），公共 API 与配置**都没有这个开关** ⇒ 走 API／CLI／notebook 一律恒开；只有直接调底层 `build_community_context` 才谈得上关掉。
  **为什么要打乱**：破坏"批次划分"与"任何让报告互相关联的维度"之间的对齐。真实的"顺序 ↔ 相关"来源有两类：① **社区表天然顺序**（id 随深度递增、同父子的子社区相邻 ⇒ 同主题的报告天然挨着）；② 同源文档／同一次入库批次。打散让**每一批都尽量是语料的横切面**，而不是"这一批全是同一主题"。
  **关掉会怎样**（如果直接调底层函数）：报告保留输入顺序 ≈ 社区 id 顺序 ⇒ 每一批变成"连续一段 id"＝**同一层 ＋ 同一子树扎堆**，正好与上面的意图相反——map 的批内打分更不可比，reduce 拿到的还是系统性不同质的材料。一个反证：**local search 的上下文构造反而显式传 `shuffle_data=False`**（它要的是"围绕命中实体"的局部上下文，不需要横切面）——同一参数在两条链上取值相反，说明这是有意的设计选择。
- **分批**：逐条累加 token，超 `max_context_tokens`（类默认 8000，官方 notebook 传 12000）就切批。`GlobalCommunityContext` 硬编码 `single_batch=False`（函数默认是 True，这里被覆盖）⇒ 走这条路**一定是多批**。这一步**不丢信息**，只是切块——真正会丢的是 reduce。
- **批内排序成表**：切批时按 `[occurrence weight, rank]` **降序**排，输出 `|` 分隔的 CSV（表头 ≈ `id|title|…attributes…|summary 或 content|rank`）⇒ **批间打散、批内有序**，两个机制同时存在：让体量／重要性更高的报告在批内靠前（影响模型的阅读与注意力），同时让结构化表带上 `id` 好做引用。
- **对话历史**（默认最多 5 轮、只用 user 轮）前置到每一批之前，支持多轮指代，且保证各批看到同样的上文。

---

## 4. 第 2 步：Map（并行，一批一次调用）

这是全量扫的廉价实现：**不做检索，让每一批自己摘出重要点并打分**，把"筛选"推迟到 reduce。

- 提示词：`system = MAP_SYSTEM_PROMPT`（模板变量只有 `{context_data}` 与 `{max_length}`）、`user = query`；**强制 JSON 输出**（`response_format_json_object=True`）。
  **一个容易误读的点**：`MAP_SYSTEM_PROMPT` 通篇在说"回答用户的问题"（*"…responds to **the user's question**"*、*"…how important the point is in **answering** the user's question"*），**但它本身并不包含那个问题**——问题是作为 **user 消息**单独传进来的（📄 `add_system_message(prompt).add_user_message(query)`）。所以"map 到底看不看得到用户问题"的答案是：**看得到，只是走 user 通道，不在 system 模板里**。reduce 阶段同理，也是同一个写法。
- 产出解析成 `points: [{description, score}]` → 归一成 `{answer, score}`；并发由信号量控制（`concurrent_coroutines=32`），让**延迟与批数脱钩**。
- **失败静默降级**：JSON 解析失败、或抛异常 ⇒ 该批只产出 `{answer:"", score:0}`，**只打 warning 日志**。好处是不阻塞整条链（其余批照常出结果）；代价是**答案会缺一块而没有显式提示**——正是最该警惕的"静默失败"形态。

### 4.1 提示词在要求什么

结构是 `---Role---`（"helpful assistant"，只谈提供的表）→ `---Goal---` → `---Data tables---` → **把 Goal 整段再重复一遍**（"贴身重申"，代价是 prompt 变长）。约束里有七条要点（📎 原文）：

1. 只用表里的数据做主要上下文；
2. 不知道就说不知道、**不要编**；
3. 每条要点必须有 **Description ＋ 0–100 的整数重要度分**，且明确"**'I don't know' 型回答必须 0 分**"；
4. 要点要带引用 `[Data: Reports (report ids)]`，**单条最多 5 个 id**、其余写 `+more`，id 取表格里的 id（不是行号）；
5. **无证据不得写入**；
6. 保留原意与情态动词；
7. 输出严格 JSON。

三处值得注意：

- **打分语义是"对回答用户问题的有用性"（与 query 相对）**——和社区报告的 `rank`（与 query 无关）**不是一回事**；
- **0 分有明确定义**（"I don't know" ⇒ 0），正好和第 3 步"丢掉 0 分"衔接；
- **map 阶段永远禁止编造**：`allow_general_knowledge` 的注入点在 **reduce**，不在 map。

### 4.2 两个追问

**追问一：跨批打的分，算不算同一把尺？**

不算。每批是**独立调用**：**问题它看得到，但材料只有自己那一批**——所以打出的 0–100 是"在这批材料里的相对重要性"（锚在批内），不是跨批可比的绝对分。加详细的评分标准能解决**档位语义**（"80 分大概指什么"），解决不了**批内相对锚定**（"这批里最相关的那条才 60 分"）。

更稳的思路是：**别让跨批分数单独承担排序职责。** 比如把 0 分当二值筛选（要不要）、再加粗分档、把"谁更重要"的比较尽量推到 reduce（那是**同一次上下文里的全局比较**）。

**追问二：宏观问题 ＋ 细层，重要性判得准吗？**

**判得出，但那不是"匹配"而是"归纳"**——模型要把细节升维到主题。这是 LLM 的强项，但它把一个**局部观察**转成了**全局判断**，于是有三个硬约束：

1. **它在信息不足时做全局判断**："这条细节在整个体系里重不重要"是**相对性**问题（取决于别的材料有没有覆盖它），而 map 只看到一批 ⇒ **用局部可见代理全局重要性**。这是 global search 的结构性破绽：**筛选发生在局部，而"重要性"是全局属性**；reduce 虽然在后一步做了全局合成，但**筛选已经在前一步发生了**（该丢的已经丢了）。
2. **层级差越大越不可比，而且有个写在代码里的放大器**：`community_level` 按文档是"选一层"、实现却是把 `level <= N` 的各层报告全塞进来（**混深度是默认状态**）；更糟的是 `level` **没被读进上下文对象**（见 2.2）⇒ **模型连"手上这份是粗层还是细层"都不知道**。
3. **宏观问题恰好是"局部最判不准"的那类**：点状问题里相关材料自明；宏观问题的"重要性"取决于"整体是否已有别的材料说过同一件事"，甚至取决于"这些点加起来能不能答上" ⇒ **map 的分数对宏观问题信息量最低，而那正是最需要它的时候**。（信息检索里这叫 granularity / abstraction mismatch。）

按性价比的缓解办法：

| 办法 | 说明 |
| --- | --- |
| **① 选层与问题形状匹配** | 宏观问题本就该选**粗层**（层低 = 粗且便宜，正好对上）；用细层答宏观问题是自己制造的错配 |
| **② 把 `level` 塞进上下文表** | 让模型至少知道自己在看哪一层；成本 ≈ 0，最便宜的改进 |
| **③ 别让分数独自承担筛选** | 0 分做二值筛选 ＋ 粗分档 ＋ 多留点，把全局比较推到 reduce |
| **④ 多层混给** | 概览层 ＋ 细节层一起进上下文——DRIFT 大致就是这个思路 |

---

## 5. 第 3 步：Reduce（一次调用出最终答案）

1. 汇总所有批的 points，每条标上"来自第几个 analyst（批次号）"——这是最终答案里**唯一残留的来源线索**（批次还没细到社区／原文）；
2. **丢掉 score = 0**；
3. 若一条不剩且 `allow_general_knowledge=False` ⇒ **直接返回固定的 `NO_DATA_ANSWER`（"我不知道"），零 LLM 调用**（这种情况**零成本**）；
4. 其余**按 score 降序**逐条格式化，**累加到 `max_data_tokens` 为止、超了就 break** ⇒ **这里的截断会真的丢点**。这是整条链里真正"筛选"的一步，代价是低分点被直接丢弃，而丢多少取决于预算大小；
5. 用 `REDUCE_SYSTEM_PROMPT` 流式生成（`{report_data}` / `{response_type}` / `{max_length}` 默认 2000）；若开了 `allow_general_knowledge`，末尾追加"可引入通用知识"指令——官方注释明说 **"may increase hallucinations"**。

**引用这一级到底给到什么**：reduce 提示词明确要求 "preserve all the data references previously included in the analysts' reports" ⇒ **引用有，但只到"报告"这一级、而且没有校验**；要回原文还得再走两跳（报告 → 实体／关系／claim → text unit），单条引用还有"最多 5 个 id ＋ `+more`"的抽样上限。

---

## 6. 四个容易忽略但要点清的机制

| 机制 | 事实 | 含义 |
| --- | --- | --- |
| 随机是**伪随机** | `random.seed(86)`（可传） | 同输入可复现，不是每次不一样 |
| **两处截断，性质不同** | 装配阶段按 `max_context_tokens` 切批（**不丢**，只是分多批）；reduce 阶段按 `max_data_tokens` 截断（**整条丢点**） | "切块"不等于"丢"：装配只是分批；真正**丢掉整条内容**的是 reduce。而 map 的损耗是另一种——**批内压缩**（受 `max_length` 限制），它不丢"条"，但会让每条变短、细节变少 |
| **两个 max_length** | map 默认 1000、reduce 默认 2000 | 都是"要求模型输出多长"，不是窗口大小 |
| **空结果兜底** | 全 0 分 ⇒ `NO_DATA_ANSWER`，不调 LLM | 与 `allow_general_knowledge` 是两条互斥的路 |

---

## 7. 参数表（官方 notebook 的实测值）

**上下文侧**：`use_community_summary=False`（用**完整报告**而非短摘要）· `shuffle_data=True` · `include_community_rank=True` · `min_community_rank=0` · `include_community_weight=True` · `normalize_community_weight=True` · `max_context_tokens=12_000` · `context_name="Reports"`。

**调用侧**：map `max_tokens=1000` / `temperature=0.0`；reduce `max_tokens=2000` / `temperature=0.0`；`max_data_tokens=12_000`（注释：模型窗口 8k 时建议设 5000）· `allow_general_knowledge=False` · `json_mode=False` · `concurrent_coroutines=32` · `response_type="multiple paragraphs"`（也可 prioritized list / single paragraph / multi-page report）。

---

## 8. 三条边界：怎么用才不踩坑

### 8.1 选层这个旋钮，不该让用户填

前面说"选层是个裸超参"，这里把它的**结构性问题**说透。

**问题**：顶层社区天然大小不一、深度也不齐。设两个顶层社区，一个最深到 L2、一个只到 L1：用户指定 `N=2` 或 `N=1` 时**他想表达的粒度本该不同**，可第二个顶层社区两次都只能给出它那个 L1 子社区（那就是它的最细可得）。

**这不是理论担心，可以量化。** 拿上游 issue **[#1650](https://github.com/microsoft/graphrag/issues/1650)** 里那位用户报的真实分布（社区数 / 平均规模）——L0 **27 个 · ~120 节点**、L1 更多更小——按"深分支 / 浅分支"各取一个看：

| 分支 | `N=0` | `N=1` | `N=2` |
| --- | --- | --- | --- |
| **深分支 T1**（L0 40 个实体 → L1 四个各 10 → L2 十六个各 ~2.5） | 1 份（整块 40） | 4 份（各 10） | 16 份（各 ~2.5） |
| **浅分支 T2**（L0 8 个实体，没再细分） | 1 份（整块 8） | **1 份（同一份）** | **1 份（同一份）** |
| **后果** | `N=1 → N=2` 的"变细"**只作用在深分支上**，浅分支纹丝不动；而进到同一个上下文表里的报告，规模能从"2.5 个实体"跨到"40 个实体"——**模型还看不到 `level`** | | |

也就是说：**用户用一个一维的层号，去表达一个二维的东西**（相关性 × 可得深度）；信息必然有损，用户表达不准、系统实现不准都是这个压缩的后果。

**三条替代路线**：

1. **查询驱动的树遍历**——就是下面 8.2 那条（按 query 相关度剪枝），它恰好绕开"层号 ≠ 粒度"这个错配；
2. **把旋钮换成"资源口径"**：目标报告数 / token 预算 / "覆盖多少比例的 text unit"。这类量**用户有直觉**（"我要便宜一点 / 全一点"），**系统也能自己做好**（按预算在每棵子树上分）；
3. **至少把"最细可得"说清楚**：如果一个主题在库里只被粗层覆盖，正确行为是"给已有最细的 ＋ 明确声明这是最细可得"，而不是假装它就该这么粗。

一个必须承认的边界（🤔）：**粒度其实是"问题与语料共同决定"的**，不只是问题决定的。就算 query 判得再准，如果某棵子树没长下去，也拿不到细报告。

顺带一个术语提醒：`roll up` 在数仓里通常指"向上一层汇总"（明细 → 汇总），这里的用法借的是它 **groupby 聚合、多行压一行** 的形状义。正是"名字对得上、代码形状也对得上、只有语义被换掉了"这个组合，让这个空操作藏了很久。

### 8.2 另一条路：Dynamic Community Selection

开 `dynamic_community_selection=True` ⇒ 不再"用固定层的全部报告"，改为**自顶向下按 query 相关度剪枝社区树**。

- **此时 `community_level` 的语义变成"cap（最多下探到哪一层）"**——API docstring 原文："you can still provide community_level [to] cap the maximum level to search"；
- 配置里的 `dynamic_search_max_level`（默认 **2**）是这条路径的兜底上限；
- **输出是"一组可能跨层的社区"，不是一个层**（父相关就留、子也相关就继续留，`keep_parent` 控制是否连父一起保留）；
- 代价：**每个候选社区一次 LLM 打分调用**，且**剪枝剪错会漏召回**。

**这里有个容易踩的坑**：两条路径下这个参数**都是"深度上限"，不是"等值过滤"**。默认路径下它之所以看起来像"等值"，恰恰是因为原本靠 roll-up 兜住了粒度——而 roll-up 现在失效了。**别把它当成 `level == N` 用。**

### 8.3 已知代价

- **引用只到"报告"级、且未经校验**（要回原文还得再走两跳，见第 5 节）；
- **随机分批导致中间答案追溯不到社区／原文**（点只标了"来自第几个 analyst"，批次号没细到社区）；
- **增量场景下同主题重复计入**，会让"全面性"看起来更好（社区报告按批次累加，没有跨批重聚）。

---

## 9. 小结：三句话

1. **它的本质是"全量扫报告 ＋ map 摘点 ＋ reduce 拼"**——不做检索，成本由"要扫多少报告"决定。
2. **材料比文档说的多**：`community_level = N` 实际给的是 `level <= N` 的全部报告（上游空操作，已提 issue）；**模型还不知道自己在读哪一层**。
3. **它最薄弱的一环是"在局部判断全局重要性"**：宏观问题恰好最容易在这里失真——所以别用细层答宏观问题，也别让跨批分数独自承担排序。

如果只记一条：**用 Global Search 之前，先想清楚"要它扫多少报告"**——那个数字由层号间接决定，而层号既不符合直觉、也不保证粒度。

---

## 附：核对方式

- 版本：`graphrag 3.2.0`（Python 3.11）；代码路径与提示词按官方 `main` 分支核对（钉在提交 `769542fb`）
- 主要核对位置（📄）：`query/structured_search/global_search/{search.py, community_context.py}`、`query/indexer_adapters.py`（`read_indexer_reports` / `read_community_reports`）、`prompts/query/global_search_{map,reduce}_system_prompt.py`、`config/defaults.py`（`GlobalSearchDefaults`）
- 2.1 那两版代码的原文（可自行比对）：**v1.2.2** [graphrag/query/indexer_adapters.py](https://github.com/microsoft/graphrag/blob/v1.2.2/graphrag/query/indexer_adapters.py) ／ **v3.2.0** [packages/graphrag/graphrag/query/indexer_adapters.py](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/indexer_adapters.py)
- 官方材料（📎）：原论文 [*From Local to Global: A Graph RAG Approach to Query-Focused Summarization*](https://arxiv.org/abs/2404.16130)（§3.1.6 与 §4）、官方 global search notebook、issue [#2573](https://github.com/microsoft/graphrag/issues/2573) 与 [#1650](https://github.com/microsoft/graphrag/issues/1650)
- 本地探针：`data/tmp/_graphrag_rollup_probe.py`（用合成层级复刻 `read_indexer_reports`，比对两种 `title` 口径 ⇒ 证明当前版本退化为 `level <= N`）
- 本文的核对脚本与笔记都在开源仓库：<https://github.com/lipeidonggz/ai-study-helper>

如果你在用 Global Search 做过实际调参（比如 C0–C3 的选择依据），欢迎在评论区说说你的场景和结论——如果再多几个"哪层最好"的实测样本，第 8.1 节那张表就能从"结构性问题"变成"该怎么选"的经验。
