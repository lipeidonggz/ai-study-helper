# 那个让用户填的社区层级参数，为什么在 GraphRAG 里根本没法用？

> **30 秒判断**：本篇基于 `graphrag 3.2.0`（截至发稿为**最新发布版**，并对照官方 `main` 分支核对）。你在用它的全局检索（`global_search`），或者正打算用它回答"这批资料整体讲了什么"这类问题——这篇把它**默认模式（非自适应）**的五步流程拆到代码级，说清三处会直接影响你结果的地方：**材料比文档说的多**（实际是 `level <= N` 的全部报告，不是"某一层"）、**两处损耗是静默的**（map 的批内压缩、reduce 的按分截断）、以及**让用户填层号这个旋钮本身在结构上就做不到**。
>
> **一句话结论**：Global Search 不做检索——它把选出来的社区报告**全量**扫一遍，由 map 各自出要点、reduce 再拼成综述，所以成本几乎只由"要扫多少份报告"决定。麻烦在于**唯一能控制这个数量的旋钮（层号）本身是失准的**：它不等于"只取某一层"，也不保证"调大就更细"。
>
> **适合谁读**：在用 GraphRAG（或类似"预生成摘要 ＋ map-reduce"方案）做全局问答的同学。不需要读过原论文，但要知道什么是社区报告。
>
> **阅读成本**：约 15 分钟。1 节是一屏流程；2 节讲材料选择（含一个上游 bug 的来龙去脉）；3–5 节是三步执行；6 节是选层这个旋钮的结构性问题与另一条路；7 节是参数表（查阅用）；最后是小结与下一篇的引子。
>
> **证据标记**：**✅ 实测**（本地探针／官方 notebook 输出）· **📄 源码**（读 `graphrag 3.2.0` 与官方仓库）· **📎 官方文档／论文** · **🤔 推断**。

---

## 0. 为什么单独看它

GraphRAG 的四种检索模式（Local / Global / DRIFT / Basic）里，只有 Global Search 能干一件事：**回答"跨整个语料"的问题**。Local Search 是从一组命中实体出发看局部邻域，Basic Search 是纯向量找相似片段，它们都给不出"这批资料整体有哪些主题"。

**它为什么需要单独一套机制？因为"全局问题"根本不是检索问题。** "这批资料里有哪些主要主题"这种问法，向量检索无从下手——top-k 的前提是"能挑出若干条最相关的记录"，而"整体讲了什么"的**相关性无法定义在任意单条记录上**。论文把这层说得很直白（📎 原文）：

> RAG fails on global questions directed at an entire text corpus, such as "What are the main themes in the dataset?", since this is inherently a **query-focused summarization** (QFS) task, rather than an explicit retrieval task.

另一条现成的路也不行：**直接对源文本做摘要**——论文里那个对比条件 TS（把同一套 map-reduce 直接作用在源文本上）恰恰是**最贵的一条**，token 就是它的 100% 基准。

于是 GraphRAG 的解法是**在两者之间插一层"中间粒度"**：索引期先把语料压成一批**社区报告**，查询期不再去捞原文，而是对这批准摘要做一次 map-reduce（每批出要点 → 汇总成一篇）。这就是它省 token 的来源——根层社区摘要只需要 TS 的 2–3%。

一句话背景（不熟悉 GraphRAG 索引的同学）：索引期它先从原文抽出一张**实体关系图**，再用社区发现把图切成若干层的**社区**——每个社区就是一组强关联实体的集合；然后让 LLM 给每个社区写一份**社区报告**。查询期拿去当"材料"的，就是这批报告。

**那"用户为什么必须自己填社区层级"？** 因为"压到多粗"是一个绕不开的权衡，而这个权衡取决于**用户想要什么**，不是系统能替他猜的：

| 层号 | 社区形态 | 材料特征 | 适合的问题 |
| --- | --- | --- | --- |
| **低（L0 = 根）** | 社区少而大 | 粗、便宜、偏全景 | "整体有哪些主题" |
| **高** | 社区多而小 | 细、贵（每次要扫 `level <= N` 的全部报告） | "某个子话题具体怎么做的" |

默认路径下系统**不做这个判断**——它把这个旋钮交给调用方，各入口给的默认还不一样（Python API 必传但可传 `None`、CLI 默认 `2`，见 2.3）。至于"让用户填层号"这个设计本身好不好，6.1 节会说清它的问题。

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

用一张图把这条链串起来：

```text
索引期   源文档 ──> 实体关系图 ──> 社区（分层）──> 社区报告 × M
                                                     │
查询期   选材料（level ≤ N）──> 打散·切批 ──> map × 批数 ──> reduce ──> 答案
                                            （每批：出要点＋打分）  （按分拼一篇）
```

一句话记住分工：**map 负责"从每批里摘要点并打分"，reduce 负责"在有限窗口里挑最重要的点拼成一篇"**。真正的筛选发生在两处——map 的打分，和 reduce 的按分截断。

另外注意上表最后两列：**这条链是有损的，而且两处损耗性质不同**——map 是"批内压缩"（摘要天然有损，长度上限让它更狠），reduce 是"整条丢弃"（低分点直接不进最终窗口）。第 0、1 步本身不丢内容。

---

## 2. 第 0 步：材料是怎么选的

这一步就定死了后面拿什么当材料。**输入是两张表，输出是"报告列表"**。

| 表 | 一行是什么 | 关键列 | `title` 是什么 |
| --- | --- | --- | --- |
| `communities`（层级结构） | 一个社区 | `community / level / parent / children / entity_ids / size` | **`"Community N"`**（索引期直接拼字符串，N = 社区 id） |
| `community_reports` | 一个社区一份 LM 报告 | `community / level / summary / full_content / rank / findings` | **LM 生成的报告标题** |

默认模式（`dynamic_community_selection=False`）下，代码只做四件事：

1. **先把社区表展开成"社区 × 成员实体"的长表**——这是"实体"唯一的出场；
2. **两张表各按 `level <= N` 砍一刀**（`N` = 调用方给的 `community_level`）；
3. **再按 `title` 分组、每个 title 只留一个社区**——它的本意是"每个实体只留它所属的最深社区"（也就是 roll-up），**但在当前版本里是个空操作**（下一小节说为什么）；
4. **最后用留下的社区去报告表里取报告** ⇒ 输出就是报告集合（不是实体集合、也不是社区集合）。

### 2.1 那个"空操作"：roll-up 本来在干什么

先说它**原本**的作用：roll-up ＝ "把同一实体的多行压成一行，只留它所属的最深社区"，目的是**让每个实体恰好由一份报告代表**。

为什么需要它？社区是**层级**结构，一个实体会同时出现在各层的祖先社区里。实体 A 可能同时属于 `C5`(L0)、`C20`(L1)、`C77`(L2)；若不处理，把 `level <= 2` 的报告全喂给模型，**同一批内容会被多层报告重复代表**——模型看到三份讲同一件事的报告，还会以为它更重要。

roll-up 就是来解决这个"重复代表"的。

**而它现在失效了。** 失效的原因只有**一行实质差别**：分组那一行没变，**变的是传给它的表**。两版对照（📄 源码，链接见文末附录）：

**`v1.x`（roll-up 生效）**——入参是**实体级**的 `final_nodes` 表，`title` = **实体名**：

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

**`v2.0.0` 起（roll-up 变成空操作）**——入参换成**社区表**，`title` = **`"Community N"`**：

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

坏点出现在 **`v2.0.0`**（[PR #1674](https://github.com/microsoft/graphrag/pull/1674)，2025-02-07），**`v3`** 只是继承；**`v1.x` 不受影响**。

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

这件事我们提交给了上游：**[microsoft/graphrag#2573](https://github.com/microsoft/graphrag/issues/2573)**（📎）。

### 2.2 于是这个参数的实际语义

- **= "报告集合 = `level <= N` 的全体报告"**。
- **`community_level = None`**：不过滤 ⇒ **全部社区、所有层**的报告。
- **它不是"选第 N 层"**：API docstring 写的是 "the community level to search at"、notebook 注释写的是 "higher value means more fine-grained"——**说法都是"那一层"，实现是"≤N 的所有层"**，偏差方向是**"给得比说的多"**（把更粗的层也塞了进来）。
- **模型不知道自己读的是哪一层**：级别没有被读进上下文对象，喂给模型的表里也没有这一列。这条后面会变成一个"放大器"（见 4.2）。

### 2.3 层从哪来：三个入口、三套默认

**它不由系统自适应决定，而是外部传进来的一个参数**——而且"默认值"是各入口自己给的，还不一样：

| 入口 | 默认 |
| --- | --- |
| Python API | **必传**（但可以传 `None` ＝ 不过滤、全层） |
| CLI / 官方 notebook | **2**（不是"全层"） |
| 配置 | 没有这一项 |

所以准确的说法是：**"用哪一层"没有人替你决定**——要么接受所在入口的默认值，要么自己给。

**层的高低**对应什么，见第 0 节那张表；这里补一条更要紧的：**选层是个没有任何先验的普通超参**——原论文 §4 专门做实验比较"哪层最好"，结论是 **L0** 明显差、**L1–L3** 在各数据集与指标上互有胜负、**没有通用答案**（📎；论文里把层级记作 C0–C3，与本文的 L0–L3 一一对应）。论文需要专门比出这个结论，本身就说明没有默认答案。

---

## 3. 第 1 步：装配上下文（它决定 map 分几批）

顺序是：**算社区权重 → 按 rank 筛 → 打乱 → 分批 → 批内排序成表 →（可选）前置对话历史**。

- **算社区权重**：权重 = 该社区内所有实体关联的**去重文本块数**（由 `entities` 反推），归一化后写进报告的属性（默认名 `occurrence weight`），当批内排序的第一关键字——它衡量的是**社区体量，不是相关性**；而且只在内存里算、不落盘，所以每次查询都会重算。
- **按 `rank` 筛**：默认阈值 0（等于不筛）。注意 `rank` 是**报告自身的重要性分**（索引期由报告提示词打的），**不是查询相关度**——这个阈值的作用只是让你能砍掉低重要性报告来省 token。
- **打散**：先按固定种子把报告随机打散，再切批——固定种子意味着**同输入可复现**。**为什么**：报告在表里是按社区 id 排的（id 随深度递增、同父子的子社区相邻），直接切批会让"某一批全是同一层、同一子树"，批与批之间不可比（map 需要基于批内数据打分，见后续）；打散后**每一批都尽量是语料的横切面**。
- **切批**：逐条累加 token，超预算（默认 12000 token）就切下一批。
- **批内排序**：每批内部再按「权重 → rank」降序排 ⇒ **批间打散、批内有序**：体量／重要性更高的报告在批内靠前（影响模型的阅读与注意力），同时成表时带上 `id`，好让要点能引用。
- **对话历史**：默认把最近 5 轮用户提问前置到每一批之前——支持多轮指代，且各批看到同样的上文。

---

## 4. 第 2 步：Map（并行，一批一次调用）

这是全量扫的廉价实现：**不做检索，让每一批自己摘出重要点并打分**，把"筛选"推迟到 reduce。

- **输入组装**：系统提示词里只有报告表和要求长度，**用户问题是单独一条 user 消息**——所以 LLM 一定看得到问题（提示词通篇在说"回答用户的问题"，但它自己并不包含问题，只看提示词文件容易误读）；输出**强制 JSON**。
- **产出**：每批独立产出「一列点 ＋ 每点 0–100 的分数」；批与批并行（并发数取 `concurrent_requests`：**库默认 25**，官方 notebook 设 32），让**延迟与批数脱钩**。
- **输出有长度上限（静默压缩）**：每批的输出被限制在默认 **1000 词**（`map_max_length`，提示词里写的就是"限制在 1000 词"）。一批里值得说的点越多，就越要挤掉一部分——而模型**既不会说"我压掉了哪几条"，也不会因此报错或打 0 分**。所以这里是**概率性的、不显式的丢信息**：和第 1 节表格里"有损"那栏对应，也和下面这条"静默降级"是两种不同的静默——一个丢"条内的细节"，一个丢"整批"。
- **失败静默降级**：JSON 解析失败、或抛异常 ⇒ 该批只产出空答案 ＋ 0 分，**只打 warning 日志**。好处是不阻塞整条链（其余批照常出结果）；代价是**答案会缺一块而没有显式提示**——正是最该警惕的"静默失败"形态。

### 4.1 提示词在要求什么

它的角色设定是"只谈提供的表"，而且**把要点约束写了两遍**（这个技巧叫"贴身重申"，代价是提示词变长）。约束里主要有七条（📎 原文）：

1. 只用表里的数据做主要上下文；
2. 不知道就说不知道、**不要编**；
3. 每条要点必须有 **Description ＋ 0–100 的整数重要度分**，且明确"**'I don't know' 型回答必须 0 分**"；
4. 要点要带引用 `[Data: Reports (report ids)]`，**单条最多 5 个 id**、其余写 `+more`，id 取表格里的 id（不是行号）；
5. **无证据不得写入**；
6. 保留原意与情态动词；
7. 输出严格 JSON。

三处值得注意：

- **打分语义是"对回答用户问题的有用性"**——和社区报告的 `rank`**不是一回事**；
- **0 分有明确定义**（"I don't know" ⇒ 0），正好和 **reduce** 步"丢掉 0 分"衔接；
- **map 阶段永远禁止编造**：`allow_general_knowledge` 的注入点在 **reduce**，不在 map。

### 4.2 两个追问

**追问一：跨批打的分，算不算同一把尺？**

不算。每批是**独立调用**：**问题它看得到，但材料只有自己那一批**——所以打出的 0–100 是"在这批材料里的相对重要性"（锚在批内），不是跨批可比的绝对分。加详细的评分标准能解决**档位语义**（"80 分大概指什么"），解决不了**批内相对锚定**（"这批里最相关的那条才 60 分"）。这也是装配上下文步骤打散报告的原因。

**追问二：宏观问题 ＋ 细层，重要性判得准吗？**

**判得出，但那不是"匹配"而是"归纳"**——模型要把细节升维到主题。这是 LLM 的强项，代价是它只能在**信息不足时做全局判断**；而这是 map-reduce 的**固有属性**（上下文窗口有限，就只能分批看），不是这套实现额外多出来的缺陷——GraphRAG 也做了两种缓解：**打散**让每批尽量是语料的横切面、**把用户问题带进打分**。剩下的三条边界值得知道：

1. **筛选发生在局部，而"重要性"是全局属性**："这条细节在整个体系里重不重要"是**相对性**问题（取决于别的材料有没有覆盖它），而 map 只看到一批 ⇒ **用局部可见代理全局重要性**；reduce 虽然在后一步做了全局合成，但**该丢的在 map 那步已经丢了**，全局比较来得太晚。
2. **层级差越大越不可比，而且有个写在代码里的放大器**：`community_level` 按文档是"选一层"、实现却是把 `level <= N` 的各层报告全塞进来（**混深度是默认状态**）；更糟的是 `level` **没被读进上下文对象** ⇒ **模型连"手上这份是粗层还是细层"都不知道**。
3. **宏观问题恰好是"局部最判不准"的那类**：点状问题里相关材料自明；宏观问题的"重要性"取决于"整体是否已有别的材料说过同一件事"，甚至取决于"这些点加起来能不能答上" ⇒ **map 的分数对宏观问题信息量最低，而那正是最需要它的时候**。（信息检索里这叫 granularity / abstraction mismatch。）

---

## 5. 第 3 步：Reduce（一次调用出最终答案）

1. 汇总所有批的点。代码只给每条加一个**批号标签**（`----Analyst N----`）和它的分数——而这个标签**不会出现在最终答案里**（reduce 提示词明确要求不要提 analyst）；真正残留的来源线索是点文本里自带的 `[Data: Reports (id)]`，其中 id 就是社区号（map 被要求写进 description、reduce 被要求原样保留）；
2. **丢掉 0 分**（0 分的定义就是"我不知道"，见 4.1）；
3. 一条不剩 ⇒ **直接返回固定的"我不知道"，不调模型**（这种情况**零成本**，也符合"不要编"的原则）；
4. 其余**按分降序**填进新窗口，**填到预算为止——超出的低分点直接丢弃**。这是整条链里真正"筛选"的一步，代价是丢多少取决于预算大小；
5. 生成最终回答。这里还有个开关：开了 `allow_general_knowledge` 就允许引入通用知识——官方注释明说 **"may increase hallucinations"**。

**第 4 步 · 返回：引用这一级到底给到什么**——reduce 提示词明确要求 "preserve all the data references previously included in the analysts' reports" ⇒ **引用有，落在"报告"这一级**（报告 id 就是社区号，所以等于到了社区）；但它是**模型自己写的、没有被校验**——map 没写就没有，写错也照样往下传。要回原文还得再走两跳（报告 → 实体／关系／claim（从原文抽出的断言）→ text unit（索引期切出的文本块）），单条引用还有"最多 5 个 id ＋ `+more`"的抽样上限。

---

## 6. 选层这件事：结构性错配，和另一条路

### 6.1 选层这个旋钮，不该让用户填

前面说"选层是个没有任何先验的超参"，这里把它的**结构性问题**说透。

**问题**：顶层社区天然大小不一、深度也不齐。设两个顶层社区，一个最深到 L2、一个只到 L1：用户指定 `N=2` 或 `N=1` 时**他想表达的粒度本该不同**，可第二个顶层社区两次都只能给出它那个 L1 子社区（那就是它的最细可得）。

**这不是理论担心，可以量化。** 上游 issue **[#1650](https://github.com/microsoft/graphrag/issues/1650)** 里那位用户报过真实分布（L0 **27 个 · ~120 节点**、L1 更多更小）。按这个分布的两种形状各取一个看（**下表的数字是示意，用来说明机制，不是 issue 的原始数据**）：

| 分支 | `N=0` | `N=1` | `N=2` |
| --- | --- | --- | --- |
| **深分支（示意）**：L0 40 个实体 → L1 四个各 10 → L2 十六个各 ~2.5 | 1 份（整块 40） | 4 份（各 10） | 16 份（各 ~2.5） |
| **浅分支（示意）**：L0 8 个实体，没再细分 | 1 份（整块 8） | **1 份（同一份）** | **1 份（同一份）** |

**后果**：`N=1 → N=2` 的"变细"**只作用在深分支上**，浅分支纹丝不动；而进到同一个上下文表里的报告，规模能从"2.5 个实体"跨到"40 个实体"——**模型还看不到 `level`**。

也就是说：**判断从来没有消失，只是被压进了一个一维层号**——"这块材料该看多细"本该由 query × 可得深度共同决定，可调用方手里只有一个标量可用；用户"表达不准"、系统"实现不准"，都是这个压缩的后果。

**三条替代路线**：

1. **查询驱动的树遍历**——就是下面 6.2 那条（按 query 相关度剪枝）：它把"**相关性**"这半边交给 query，**能到多深仍然由语料决定**（浅分支自然停在自己的深度上，不用谁去猜——也就是紧跟其后那段"边界"讲的事）；
2. **把旋钮换成"资源口径"**：目标报告数 / token 预算 / "覆盖多少比例的 text unit"。这类量**用户有直觉**（"我要便宜一点 / 全一点"），**系统也能自己做好**（按预算在每棵子树上分）；
3. **至少把"最细可得"说清楚**：如果一个主题在库里只被粗层覆盖，正确行为是"给已有最细的 ＋ 明确声明这是最细可得"，而不是假装它就该这么粗。

一个必须承认的边界（🤔）：**粒度其实是"问题与语料共同决定"的**，不只是问题决定的。就算 query 判得再准，如果某棵子树没长下去，也拿不到细报告。

### 6.2 另一条路：Dynamic Community Selection

开 `dynamic_community_selection=True` ⇒ 不再"用固定层的全部报告"，改为**自顶向下按 query 相关度剪枝社区树**。

- **此时 `community_level` 的语义变成"cap（最多下探到哪一层）"**——API docstring 原文："you can still provide community_level [to] cap the maximum level to search"；
- 配置里的 `dynamic_search_max_level`（默认 **2**）是这条路径的兜底上限；
- **输出是"一组可能跨层的社区"，不是一个层**（父相关就留、子也相关就继续留，`keep_parent` 控制是否连父一起保留）；
- 代价：**每个候选社区一次 LLM 打分调用**，且**剪枝剪错会漏召回**。

---

## 7. 参数表（官方 notebook 用的参数值）

**上下文侧**：`use_community_summary=False`（用**完整报告**而非短摘要）· `shuffle_data=True` · `include_community_rank=True` · `min_community_rank=0` · `include_community_weight=True` · `normalize_community_weight=True` · `max_context_tokens=12_000` · `context_name="Reports"`。

**调用侧**：map `max_tokens=1000` / `temperature=0.0`；reduce `max_tokens=2000` / `temperature=0.0`；`max_data_tokens=12_000`（注释：模型窗口 8k 时建议设 5000）· `allow_general_knowledge=False` · `json_mode=False` · `concurrent_coroutines=32` · `response_type="multiple paragraphs"`（也可 prioritized list / single paragraph / multi-page report）。

**一个例外**：`shuffle_data` 在 `query/factory.py` 里是**写死的 `True`**——标准入口（CLI 与 `graphrag.api`）没有开关，只有绕过 factory 自己构造 `GlobalSearch` 才改得动。

---

## 8. 小结：三句话

1. **它的本质是"全量扫报告 ＋ map 摘点 ＋ reduce 拼"**——不做检索，成本由"要扫多少报告"决定。
2. **进模型的"材料"比官方文档说的多**：`community_level = N` 实际给的是 `level <= N` 的全部报告（上游空操作，已提 issue）——不只是你要的那一层，**更粗的层也一并塞了进去**；而模型还不知道自己手上这份是粗层还是细层。
3. **最要紧的一条：那个层号参数几乎没法用**——好的系统不该把"用哪一层"这个决策交给用户；而且即便用户有心，它在**结构上**也做不到（一维层号 ≠ "相关性 × 可得深度"）。真要控制粒度，应该换成资源口径（报告数 / token / 覆盖比例），或者让系统按 query 自己选——后者就是 6.2 那条路。


### 下一篇：Dynamic Community Selection

这篇拆的是**默认模式**。6.2 里还有另一条路只给了轮廓：**Dynamic Community Selection**——不再是"用固定层的全部报告"，而是**自顶向下按 query 相关度剪枝社区树**，此时同名参数的含义变成"最多下探到哪一层"，代价是每个候选社区一次 LLM 打分调用、且剪错会漏召回。

它值得单独拆，因为它正面回应了 6.1 的那个结论——"**层号 ≠ 粒度**"。但"换一个更聪明的选法"是否就解决了？还有一串没核：打分提示词具体怎么问、阈值怎么定、`keep_parent` 到底影响什么、成本落在哪一步、以及"剪错漏召回"以什么形态出现。

下一篇逐行核这些。

---

## 附：核对方式

- 版本：`graphrag 3.2.0`（Python 3.11）；代码路径与提示词按官方 `main` 分支核对（钉在提交 `769542fb`）
- 主要核对位置（📄）：`query/structured_search/global_search/{search.py, community_context.py}`、`query/indexer_adapters.py`（`read_indexer_reports` / `read_community_reports`）、`prompts/query/global_search_{map,reduce}_system_prompt.py`、`config/defaults.py`（`GlobalSearchDefaults`）
- 2.1 那两版代码的原文（可自行比对）：`v1.2.2` [graphrag/query/indexer_adapters.py](https://github.com/microsoft/graphrag/blob/v1.2.2/graphrag/query/indexer_adapters.py) ／ `v3.2.0` [packages/graphrag/graphrag/query/indexer_adapters.py](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/indexer_adapters.py)
- 官方材料（📎）：原论文 [*From Local to Global: A Graph RAG Approach to Query-Focused Summarization*](https://arxiv.org/abs/2404.16130)（§3.1.6 与 §4）、官方 global search notebook、issue [#2573](https://github.com/microsoft/graphrag/issues/2573) 与 [#1650](https://github.com/microsoft/graphrag/issues/1650)
- 本地探针：[`_graphrag_rollup_probe.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/_graphrag_rollup_probe.py)（用合成层级复刻 `read_indexer_reports`，比对两种 `title` 口径 ⇒ 证明当前版本退化为 `level <= N`）——它和本文其它核对脚本都在仓库的 [`experiments/`](https://github.com/lipeidonggz/ai-study-helper/tree/master/experiments) 下
- ⚠️ 这些脚本是**现场快照、不能开箱即跑**（语料与模型产物没入库），用途是让读者核对文中的每一处结论；详见 [`experiments/README.md`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/README.md)

如果你在用 Global Search 做过实际调参（比如 L0–L3 的选择依据），欢迎在评论区说说你的场景和结论——如果再多几个"哪层最好"的实测样本，第 6.1 节那张表就能从"结构性问题"变成"该怎么选"的经验。
