# GraphRAG 论文没写的两个检索模式：Local 与 DRIFT 拆解，以及一份使用判断

> **30 秒判断**：本篇基于 `graphrag 3.2.0`，全部结论来自读代码，不做新实验。先说一件可能意外的事：**GraphRAG 那篇论文全文没有出现过 "local search"，也没有出现过 "drift"**——论文声明的贡献只有全局感知（社区层级 ＋ map-reduce），这两个模式是实现和文档里的东西。而这恰恰是本篇要评的东西：当"设计依据"不在论文里，判断标准就只能换一条——**它的声明和实现对得上吗？它丢掉的东西你能看见吗？**
>
> **一句话结论**：**两个模式的骨架都是对的**——Local 把"全局视角（社区报告）＋ 中观结构（实体/关系）＋ 细节证据（原文块）"放进同一个窗口，DRIFT 在此之上加了"用答案生成新问题"的迭代；**但控制面都没做完**：Local 的入口没有阈值也没有重排、三源预算是一刀切的固定配额、裁掉了什么默认不记录；DRIFT 没有中途判停、问题条数写死、去重靠巧合、还有一个参数同时管两件事。**当机制参考可以，当可依赖组件还不够。**
>
> **适合谁读**：正在给 RAG 系统设计"多来源上下文装配"或"多轮检索"的同学；以及想知道 GraphRAG 除 Global 之外还有什么可用的人。
>
> **结构速览**：0 节说清楚"为什么这两个模式没有论文可依"；1–2 节是 Local 的机制与评价；3–4 节是 DRIFT 的机制与评价；5 节是一份"什么时候能用"的判断表；6 节是把它们改成生产可用要动的最小清单。
>
> **证据标记**：**📄 源码**（`graphrag 3.2.0`，链接挂在文内）· **📎 官方文档** · **🤔 推断**（会明确标出）。

---

## 0. 先说一件可能意外的事：这两个模式论文里没有

判断一个检索模式"能不能依赖"，常规做法是先看它的论文怎么论证。这两个模式看不了：

**GraphRAG 那篇论文（[From Local to Global](https://arxiv.org/abs/2404.16130)，2404.16130）里，"local search" 出现 0 次，"drift" 出现 0 次。** 论文自己声明的贡献是"对整份语料做全局感知查询"——用 LLM 建实体图、用层次社区检测切分、为每个社区预生成摘要，查询时对这些摘要做 map-reduce。也就是说，**Local 和 DRIFT 是产品与仓库里的东西，不是论文的贡献**。

这不是小事。它意味着两件事：

1. **引用时不能写"Local Search 出自 GraphRAG 论文"**——只能写"出自 GraphRAG 的实现与文档"。
2. **评价标准也得换**。论文模式的评价是"方法是否成立、指标是否显著"；实现模式的评价只能是工程口径：**声明的语义和实现的语义对不对得上、失败和裁剪是否可见、参数是否可标定**（也就是我们常说的两条：可对账、可复现）。

本文就按这个标准来。先讲 Local，再讲 DRIFT，最后给判断。

---

## 1. Local Search 的机制：一次装配，链上只有一次生成调用

官方文档对它的定位是一句话：**Local search 把知识图里的相关数据和原始文档的文本块合起来生成答案**（📎 [Query Overview](https://microsoft.github.io/graphrag/query/overview/)，原文：*generates answers by combining relevant data from the AI-extracted knowledge-graph with text chunks of the raw documents*），适合"需要理解文档里某个具体实体"的问题。

读完代码，它的完整链路只有五步，**全程只有两次模型调用：一次查询嵌入（把问题变成向量）＋ 一次答案生成**。

**第一步：找入口实体。** 这一问要从哪些实体出发？做法是把这个查询**在实体向量库里做相似检索**。三个细节值得记：

- **有会话历史时，先把最近 5 轮用户提问拼进当前查询**（📄 [`context_builder/conversation_history.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/context_builder/conversation_history.py)：倒着遍历、只收用户角色、取满 5 条即停），拼出来是"当前查询 ＋ 历史提问（最新在前）"。
- **检索用的是"实体标题 : 描述"的向量，不是名字匹配**：索引期为每个实体嵌入的文本是 `"{title}:{description}"`（📄 [`data_model/row_transformers.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/data_model/row_transformers.py) 的 `transform_entity_row_for_embedding`）。所以它做的是**描述级语义命中**，不要求实体名出现在你的问题里。
- **取回数量＝`top_k_entities`(默认 10) × `oversample_scaler`(2) ＝ 20，且没有相似度阈值**（📄 [`context_builder/entity_extraction.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/context_builder/entity_extraction.py)）。乘 `oversample_scaler` 的原始注释是 `# oversample to account for excluded entities`——它是给"被排除掉的实体"留余量；而返回值不会再截回 10 条，所以**默认情况下实际进入装配的是 20 个实体**。

**第二步：把上下文预算切成三份。** 剩余的 token 预算按三个比例分配（📄 [`local_search/mixed_context.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/structured_search/local_search/mixed_context.py) 的 `build_context`）。默认 `max_context_tokens` 是 12000，切成：

| 来源 | 比例 | 默认额度 |
| --- | --- | --- |
| 社区报告 | `community_prop` 0.15 | ≈1800 token |
| 实体 ＋ 关系 ＋ 协变量 | `1 − 0.15 − 0.5` | ≈4200 token |
| 原始文本块 | `text_unit_prop` 0.5 | ≈6000 token |

⚠ 一个容易读错的地方：函数签名里写的默认值是 `community_prop=0.25`、`max_context_tokens=8000`，但**运行时生效的是配置文件那一层**——`query/factory.py` 会把 `local_search.*` 逐项塞进装配参数（📄 [`query/factory.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/factory.py)），而配置类里的默认是 `0.15` 与 `12000`（📄 [`config/defaults.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/config/defaults.py)）。**同一份代码两层默认值不一致，读数要认准哪一层生效。**

**第三步：这三份材料的候选，都是从第一步那 20 个实体派生出来的。** 这是这个模式最要紧的一层结构：

- **社区报告的候选**＝命中实体所属的那些社区（用每个实体记着的社区归属，统计"哪个社区被命中的实体最多"）；
- **实体 / 关系 / 协变量的候选**＝命中实体本身，加上与它们**直接相连**的关系（协变量按"主体是命中实体"筛）；
- **原始文本块的候选**＝命中实体出现过的那些文本单元。

**第四步：三类材料各自按规则排序，装进自己的额度。**

- **社区报告的排序**：按 `(命中实体数, 社区 rank)` 降序排，再按额度装；默认 `use_community_summary=False`，所以**塞进去的是报告全文，不是摘要**（📄 [`context_builder/community_context.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/context_builder/community_context.py)，那句 `report.summary if use_community_summary else report.full_content` 的三元表达式）。
- **实体 / 关系 / 协变量**：实体先全部成表（带 `rank` 列），然后**逐个实体往前加**它相关的关系（默认 `top_k_relationships=10`，排序键是 `rank`，不是度数），**一旦累计超限就整体回退到上一个可行状态**（📄 `_build_local_context`，日志里那句 `reverting to previous context state`）。
- **原始文本块的排序**：同 id 去重后，按"**该实体在命中列表里的顺序，以及该块里与命中实体相关的关系条数**"排——**越靠前的实体，它名下的块越优先**（📄 `_build_text_unit_context`）。

换句话说，**实体列表是唯一入口，三类材料都是它的下游**——入口选错，三类材料会一起错。这正是第 2 节里"入口没有阈值、也没有重排"值得单列成一条缺点的原因。

**第五步：一次 LLM 出答案。** 装配好的上下文进 system 提示词，用户问题进 user 消息（📄 [`local_search/search.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/structured_search/local_search/search.py)）。

**顺带一个对你可能有用的开关**：`return_candidate_context=True` 时，三个来源都会把"候选全集"返回，并给每条打一个 `in_context` 标记——**可以直接量出"装进去了多少、丢了多少"**。注意它默认是 `False`（📄 `query/factory.py`）。

---

## 2. Local Search 的评价：三源混合是对的，控制面是缺的

### 2.1 先说对的地方

**第一，三源分工这个思路本身，明显强于纯 top-k 向量检索。** 向量检索给你的只有"最相似的细节"，没有全局视角、也没有中观结构。而这里三类材料恰好覆盖三个层次：

- **社区报告＝全局视角**（这一段材料在整个语料里的位置和主题归属）；
- **实体 / 关系表＝中观结构**（谁和谁有关、通过什么关系）；
- **原始文本块＝细节证据**（可以逐字引用的原文）。

这三层放进同一个窗口互补，是实打实比"再调调 top-k"更进一步的思路。

**第二，"谁占多少"是显式比例，这是优点。** 大多数 RAG 系统的占比是"捞回来塞进去"涌现出来的——不可调、不可说。这里是写在配置里、可以改、可以读的。

**第三，原文块拿最大头（0.5）方向正确。** 它与"原文块才是事实源，图只是索引"这个口径一致——答案要能回锚到原文，证据就该占最多预算。

### 2.2 学术来路：这个思路有前作，但要引对

**三源混合本身没有一篇专门论文**——它是 GraphRAG 的实现产物。但它的想法散在三支脉络里，引用时应该引这三支：

| 脉络 | 代表工作 | 与本文的关系 |
| --- | --- | --- |
| 层次化摘要树检索 | **RAPTOR**（Recursive Abstractive Processing for Tree-Organized Retrieval，[2401.18059](https://arxiv.org/abs/2401.18059)） | **GraphRAG 论文自己把这条引为最近的前作**（论文 2.1 节原文：*similar to other approaches that use hierarchical indexing to create summaries*）；递归聚类＋摘要建树，检索落在不同层级——正是"宏观／中观／细节"在论文层面的对应物，而且比 GraphRAG 更早 |
| 图作索引、落回原文 | HippoRAG（[2405.14831](https://arxiv.org/abs/2405.14831)）、HippoRAG 2（[2502.14802](https://arxiv.org/abs/2502.14802)）、KAG（[2409.13731](https://arxiv.org/abs/2409.13731)）、LinearRAG（[2510.10114](https://arxiv.org/abs/2510.10114)）、EcphoryRAG（[2510.08958](https://arxiv.org/abs/2510.08958)） | 共同点是"用结构化索引做路由、最后落回段落"，与"实体/关系表 ＋ 文本块"的分工同构 |
| 双层检索 | LightRAG（HKUDS，EMNLP 2025） | 低层用实体/关系、高层用主题关键词，两级检索——"中观 ＋ 宏观"的直译 |

所以准确的引用口径是：**三源混合在 GraphRAG 的实现里被具体化，其学术对应物分散在这三支；没有一篇专讲"三源同窗混合 ＋ 固定 token 配比"**——这也解释了为什么那套比例是拍出来的：它没有论文级的标定依据。

### 2.3 再说缺的地方

**① 三源预算把"与目标有关"的量钉死在参数里。** 问题是什么形状，要到查询期才知道：一个纯事实问题（"X 的额度是多少"）其实想要几乎全部预算给原文块；一个全局问题想要更多社区报告。而一个固定比例对所有问题一视同仁。这跟"报告里必须写 5–10 条 findings"是同一类毛病——**凡是该由目标决定的量，一旦出现在参数里，就一定会在别的目标上失效**。

**② 三份预算互不"让额度"。** 三个 `int(max_context_tokens × prop)` 是各自独立的：装不满的社区位不会转给原文块，而答案在报告里的问题也只有那 1800 token 可用——**两个方向的浪费同时存在**。

**③ 裁剪的单位和配额的单位不是一回事，而且默认不记录。** 那 4200 token 是"按实体逐个加、超了整体回退"，所以丢的是**一整个实体连同它的关系与协变量**；丢了什么，除非手动打开对账开关，否则**没有任何输出**。

**④ "token 当统一货币"隐含"三类内容同质"的假设。** 0.15 × 12000 ＝ 1800 token 的社区额度，在真实语料上意味着什么？我们量过的社区报告长度是 **0.7k–2.9k token**（新闻语料偏短、笔记类语料偏长）——也就是说，**这个额度常常只够塞 1–2 份报告**，"社区上下文"会退化成"一份报告的全文"。

而问题不在体裁，在**每 token 承载的东西不一样**。社区报告是有结构的，但它是**叙述型**的：标题、摘要、评分与理由，加上 5–10 条要点，每条要点是一句短标题紧随一段解释文字；我们此前还量过，其中约 **15% 的字符**是 `[Data: …]` 这类引用标记（根层更高）——也就是说，同样一段文字里，**真正可枚举的"条目"只有几条**。而实体表／关系表是**一行一条**：一行一个实体或一条关系，字段对齐、彼此独立。拿同一个 token 配额去分这两类内容，等于假设"**一段解释文字**"和"**一行断言**"一样贵。

**⑤ 多轮会话的处理方式偏粗：把历史拼进检索输入。** 上一节说过，它把"当前查询 ＋ 最近 5 轮提问"拼成一段直接做向量检索。工程上更通行的做法是**先把历史压成一句自足的查询**（condensation／query rewriting），再拿这一句去做检索，然后**在结果层做融合与重排**（多路召回 ＋ 倒数排名融合），而不是在输入层把多句话粘在一起——因为稠密检索器就是在单句查询上训练的，拼接会把向量拉向"整场对话的平均话题"。此外还应该：按需改写（问题自足就不改写）、用重排器对候选重排、历史按相关性选取或滚动摘要（而不是固定取 k 轮），并给历史单列一条 token 预算。

**⑥ 入口没有阈值也没有重排。** 向量检索取回 20 个实体就直接进入装配——没有相似度门槛，也没有交叉编码器重排。"能跑通"和"能信"之间差的往往就是这一步。

---

## 3. DRIFT Search 的机制：播种、迭代、汇总

官方文档给它的定位是：**用社区信息扩展 local search 的起点**，"把这些社区洞见细化成一串后续问题"，从而"检索并使用更丰富的事实"（📎 Query Overview，原文：*providing a more comprehensive option for local search*）。

代码里的它，是"**多次 local search ＋ 一个调度循环**"的外壳。三个阶段：

### 3.1 阶段一：播种（把 query 变成一批"中间答案 ＋ 后续问题"）

四步，共 6 次 LLM 调用：

1. **HyDE 式扩展查询**：先让模型"为这个查询写一个假想答案"，而提示词里要求的格式模板**是从你的社区报告里随机抽一份全文**（`secrets.choice`）——这份全文**只当结构模板用，不参与回答**（📄 [`primer.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/structured_search/drift_search/primer.py)）。1 次调用。
2. **选报告**：用扩展后的查询向量，与所有社区报告全文的嵌入算余弦相似度，取最相似的 **20** 份（`drift_k_followups`）。
3. **五折并行出中间答案**：这 20 份折成 `primer_folds`(默认 5) 折，每折一次 LLM，产出 `{intermediate_answer, score, follow_up_queries}`。5 次调用。
4. **汇总成一个初始问题**：中间答案拼起来、分数取平均、后续问题全部入队。

> 术语统一一下：**队列里每一项我们都叫"问题"**——它不是只有一句问句，而是带着"问题文本 ＋ 是否已回答 ＋ 答案 ＋ 得分 ＋ 它派生出的新问题"这一组状态（代码里这个类型叫 `DriftAction`）。后面出现的"未完成的问题"就是"还没被检索回答过的那几项"。

**这一折的提示词在要求什么**（📄 [`prompts/query/drift_search_system_prompt.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompts/query/drift_search_system_prompt.py)）：写一份 2000 字符左右、markdown 格式的"中间答案"；给这份中间答案自评一个 0–100 的聚焦度分数；再给**至少 5 条**后续问题，并明确要求"不要问复合问题"。拼进去的社区报告是**裸拼接**（没有 id、没有标题、没有分隔标记）——对比 Local 那边是"表格化 ＋ 表头 ＋ id"，这里模型根本不知道哪段是哪份报告，也回锚不了。

### 3.2 阶段二：迭代（每轮最多 20 个问题，各跑一次 Local）

主循环是 `while epochs < n_depth`（默认 3 轮）：每轮从"未完成的问题"里取 20 个（`drift_k_followups`），**每个问题都当作一次查询去调用 Local Search**，返回的 JSON 被解析成 `{response, score, follow_up_queries}`，新问题继续入队（📄 [`drift_search/search.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/structured_search/drift_search/search.py)）。

⚠ 这里 Local 的预算被**换过一套**：`local_search_text_unit_prop=0.9`、`local_search_community_prop=0.1`（对比 Local 独立使用时的 0.5 / 0.15）——**比独立使用时更偏向原始文本块**（📄 `config/defaults.py` 的 `DriftSearchDefaults`）。

### 3.3 阶段三：汇总（reduce）

这一步由代码里的一个开关控制，**默认开启**（`reduce: bool = True`）：开启时，把队列里所有**已回答问题的答案**汇总成一份综合回答（温度为 0）；关掉它，返回的就不是一段回答，而是那份"问题 → 答案"的**原始集合**。

但要留意：**这个开关在常规入口（命令行、高层 API）和配置里都没有暴露**——想关掉只能直接调用引擎，所以实际用到的永远是"开"（流式入口内部也是先拿到原始集合，再流式地把这一步做完）。**另外这一步没有输出上限**（`reduce_max_completion_tokens=None`）。

### 3.4 成本量级：次数有硬上限，但"最贵"要看 token

| 阶段 | LLM 调用 |
| --- | --- |
| HyDE 扩展 | 1 |
| 播种（5 折并行） | 5 |
| 主循环（3 轮 × 最多 20 个问题） | ≤60 |
| 汇总 | 1 |
| **合计** | **上限约 67 次/查询** |

**但这个数字只能说明"调用次数"，不能直接当成本结论。** 四个模式的成本结构并不一样：

- **Basic 与 Local** 是"一次中等输入"（Local 的输入上限约 12000 token）；
- **Global** 是把 `≤N` 层的社区报告**全文**扫一遍（按约 12000 token 一批），**批数随报告总量增长**，再加一次汇总；
- **DRIFT** 是"一次播种（读 20 份报告的**全文**）＋ 最多 60 次局部检索（每次上限约 12000 token）＋ 一次汇总"。

所以正确的说法是：**DRIFT 的调用次数有硬上限，而 Global 的次数随语料增长**——在报告很少的小语料上，DRIFT 相对更贵；在报告很多的大语料上，Global 会追上来。**谁最贵取决于语料规模与参数，不做实测不能下这个结论**（本文只核结构，没有量 DRIFT 的账单）。

顺带把官方那句引准确：drift_search 文档的原话是 *"a method that **balances computational costs with quality outcomes**"*（平衡算力成本与质量产出）——它讲的是**族内定位**（比 global 省、比 local 全）。拿"调用次数"去反驳它，本身就换了它没说的判据。

### 3.5 两个"不可复现"环节（都来自代码）

1. **HyDE 的模板是随机抽的**：`secrets.choice`（无种子）⇒ 同一个问题跑两次，扩展文本可能不同 ⇒ **选进播种的那 20 份报告可能不同**。
2. **主循环里那个"排序"其实是洗牌**：`rank_incomplete_actions()` 在不传打分器时会走 `random.shuffle`（📄 [`drift_search/state.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/structured_search/drift_search/state.py) 里那行注释 `# shuffle the list if no scorer`），而主循环正是不传打分器调用的 ⇒ **每轮"做哪 20 个问题"是随机挑的**；那个 `score` 在默认路径下只被记录，不参与选择。

再加上底层模型没有随机种子配置——**"同一个问题两次结果不同"是它的设计内行为，不是偶发噪声。**

### 3.6 两处"控制面"缺陷

**其一，两个死参数。** `drift_search.data_max_tokens` 与 `drift_search.concurrency` 只出现在配置默认值（📄 `config/defaults.py`）和配置模型（📄 [`config/models/drift_search_config.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/config/models/drift_search_config.py)）里，**查询侧没有任何代码读它们**——对照 `global_search.data_max_tokens` 在 `query/factory.py` 是被消费的。也就是说，**在配置文件里设这两个键不会生效**。

**其二，提示词的可配置性是不对称的。** `drift_search_system_prompt`（借 Local 时用的）与 `drift_reduce_prompt` 会被写进生成的配置文件、**可以改**；而**播种阶段的提示词是硬编码的**（`primer.py` 里直接引用常量）⇒ **能改的两个阶段不是决定走向的那个，真正决定"中间答案与后续问题长什么样"的播种阶段反而改不了**，而它还用随机模板抽报告。

---

## 4. DRIFT 的评价：流程骨架对，流程控制糙

### 4.1 先说对的地方

**它把 Global 的"覆盖面"和 Local 的"细节"接在了一起，再加一层迭代。** Global 的问题是"把全库报告扫一遍、但没有细节"，Local 的问题是"细节准、但起点窄"。DRIFT 用社区报告当起点（覆盖）、用循环里的一串问题去深挖（细节）、最后汇总——**"全局起点 → 局部证据 → 汇总回答"这个骨架是完整的**，比任何单一模式都完整。这也是它被称为"local search 的综合版"的由来。

### 4.2 两处需要说精确（免得理解错）

- **"分层"其实是一次跨层跳转。** 报告池来自用户填的 `community_level`（和 Global、Local 用的是同一个 `≤N` 过滤），primer 在里面按相似度取 20 份——**这 20 份是跨层的，不保证深浅**。所以它是"社区报告层 → 实体/关系/原文层"的一次跳转，不是逐层下钻；**深度仍由用户填的层号框定**。
- **"迭代"迭代的是问题链，不是证据集合。** 每轮是若干个平行的 Local 检索，**上一轮的材料不会传给下一轮**；跨轮传递的只有问题文本（新问题是模型看完自己那份答案后生成的）。证据直到最后的汇总阶段才被放到一起。

### 4.3 再说糙的地方

**① 没有中途判停。** 主循环就是"跑满 n_depth 轮"，唯一的自然终止是"没有未完成的问题"。**最值得说的一句是：判停信号其实已经现成**——每个问题都生成了 `score`（0–100），播种阶段也算过平均分，**两个分数都没被用来决定"够了没有"**；也没有类似 LLM-Wiki 那种"预算 / 连续空搜索"的停止条件。**原料齐了，线没接。**

**② 问题条数写死，而且会溢出。** 提示词要求每折至少 5 条后续问题，5 折合起来 **≥25 条**，而主循环每轮只取 20 条 ⇒ **第一轮就必然溢出**，多出来的问题要么排到后面轮次、要么永远轮不到（选择是随机洗牌）。更麻烦的是"至少 5 条"只有下限没有上限，而每条后续问题的答案又能再产出"最多 20 条"新问题 ⇒ **候选池按 20×20 的速度膨胀，绝大多数问题永远不会被执行——而且没有任何"未执行 / 被丢弃"的记录**。

**③ 去重靠巧合。** 每一项的身份就是它的**问题文本**（📄 [`drift_search/action.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/query/structured_search/drift_search/action.py) 的 `__hash__`/`__eq__` 都按这行问句比），所以措辞**完全相同**的问题会被合并成一个；**差一个字就各跑一次检索**。队列本身没有任何归一化或显式去重（代码注释还写着"assumes queries are unique"）。

**④ 一个旋钮管两件不相干的事。** `drift_k_followups`（默认 20）**既决定播种阶段取多少份社区报告，又决定主循环每轮做多少个问题**。这两个数字在语义上没有任何关系，却被绑成同一个参数。

---

## 5. 一份使用判断

| | Local Search | DRIFT Search |
| --- | --- | --- |
| 适合的问题 | 点型：某个实体/概念的具体信息 | 混合型：既要全局方向、又要具体事实 |
| 成本 | 2 次模型调用（1 次查询嵌入 ＋ 1 次生成，输入上限约 12k token）；**装配不花钱，但决定账单** | 上限约 67 次 LLM 调用/查询（成本在"次数"） |
| 可复现性 | 装配是确定性的，随机性只来自模型采样 | **自带两个随机环节**（模板抽样 ＋ 轮内洗牌） |
| 现在就能用的部分 | 三源混合的骨架、可配置的比例、候选对账开关 | 三阶段的流程骨架、"用答案生成新问题"的迭代思路 |
| 必须先自己补的部分 | 入口阈值与重排；按问题形状分预算；打开裁剪对账；多轮改"改写 ＋ 按需" | 中途判停；问题条数的上限与去重；拆开那个双义参数；未执行问题的清单 |

**三条可以直接拿去用的判断：**

1. **把 Local 当"多来源上下文装配"的参考实现**——它的三类来源划分是对的，直接借鉴；但预算机制要换成"按问题形状路由 ＋ 顺序装满即停 ＋ 余量顺延"，并且一定要把"装了什么、丢了什么"打印出来。
2. **DRIFT 现在不建议作为默认路径**——它的成本上界最高（次数有硬上限、输入又是报告全文），还带两个随机环节；如果要用，先接判停和去重，否则你等于在花几十次调用的钱，去跑一个连"该不该继续"都没判的循环。
3. **两个模式都别当"可依赖组件"**，先当"机制参考"。它们的共同问题是**控制面缺失**：阈值、比例、判停、对账这些"让它可被信任"的部分，要么没有、要么没接上。

---

### 5.1 顺带一份通用清单：怎么判断一个检索组件能不能依赖

写到这里应该能看出，Local 和 DRIFT 的问题不是孤例——它们属于同一族"研究性代码"的典型症状。把这一路核下来的东西归成**九类**，并按**功能缺口 ／ 质量属性 ／ 语义契约**标注，可以当一份通用自检清单用（每一类都标了本文或本系列里的实例）：

| # | 病灶 | 类别 | 实例 |
| --- | --- | --- | --- |
| 1 | **声明 ≠ 实现，而且没人被通知** | 语义契约 | Global 模式"取最深一层"在重构后变成空操作，文档却仍写"选第 N 层"（详见《[那个让用户填的社区层级参数，为什么在 GraphRAG 里根本没法用？](https://github.com/lipeidonggz/ai-study-helper/blob/master/docs/publish/0035-graphrag-global-search.md)》）；社区报告的"长度上限"被 prompt-tune 静默丢掉（详见《[论文说根层摘要只要原文的 2.6%，我实测是 126%](https://github.com/lipeidonggz/ai-study-helper/blob/master/docs/publish/0038-graphrag-report-length.md)》） |
| 2 | **留了口子没人用／死代码** | 质量属性：可维护性 | Local 的 `exclude_entity_names` 在常规路径（CLI／高层 API）里根本传不进来；DRIFT 的 `data_max_tokens`、`concurrency` 在查询侧没有任何读取点 |
| 3 | **默认值分层不一致** | 质量属性：可配置性 | Local 的 `community_prop` 在配置里是 0.15、在函数签名里是 0.25；`max_context_tokens` 一处 12000、一处 8000 |
| 4 | **失败静默、指标打错层** | 质量属性：可观测性 | 报告生成失败但流水线报成功、退出码 0；"失败调用数"只统计调用层，解析层的失败不进这个数；**成本统计也漏项**——Local 的装配环节明明调了一次查询嵌入，统计却报 0（结果对象里的计数是默认值），而 DRIFT 的同一环节报了 1 次，两个模式口径不一致 |
| 5 | **不可复现，而且没有旋钮** | 质量属性：可复现性 | DRIFT 的模板随机抽 ＋ 轮内洗牌（3.5）；prompt-tune 的采样没有随机种子，底层模型配置里连 `temperature`／`seed` 字段都没有（详见《[想用 GraphRAG 的 prompt-tune auto 选样？先修 bug 再用](https://github.com/lipeidonggz/ai-study-helper/blob/master/docs/publish/0036-graphrag-prompt-tune.md)》） |
| 6 | **拍脑袋常数** | 质量属性：可标定性 | 报告必须写 5–10 条要点；社区切分的实体数上限 10；层号必须由用户填；本文里的三份预算 0.5／0.15／0.35 与 DRIFT 的"至少 5 条" |
| 7 | **文档承诺与实现细节对不上** | 语义契约 | DRIFT 官方定位是"平衡算力成本与质量产出"，但它的成本上界最高、且没有任何判停（3.4）；反过来，也不能用"调用次数"去判它的成本——那是个错位的判据 |
| 8 | **只在 notebook 里"验证过"** | 质量属性：可测试性 | 官方 notebook 打印出的异常值（社区 id 前后都是 10）被当成正常输出，直到有人去核代码 |
| 9 | **该有的能力没有** | **功能缺口** | DRIFT 没有中途判停、问题条数写死且无上限（4.3）；Local 的入口没有阈值也没有重排、多轮不做查询改写（2.3） |

**把这张表换个说法**：第 1、7 条是**语义契约**问题（声明是一份契约，却没有探针去核对它）；第 2–6、8 条全是**质量属性**（可维护性、可配置性、可观测性、可复现性、可标定性、可测试性）；第 9 条是**功能缺口**（该有的能力没做）。换句话说：**这套实现把功能面做得很细（抽图、社区、报告都有讲究），而"质量属性"（-ilities）基本没被当成需求**——成本尤其明显，它既是质量属性（性能效率），又是本篇的主线（见 3.4）。

这不是"某个团队粗心"，而是**激励结构**：论文奖励方法有效，不奖励可观测、可配置、可维护；研究阶段的预算自然全押在功能与指标上。所以"验证假设"和"交付组件"是两件事——一旦把它当产品基座，被推迟的质量属性就变成债务。

**于是本篇的总判据可以写成三问**（判断一个检索组件能不能依赖，不看代码风格——研究代码糙很正常，看的是**质量属性有没有被当成需求**）：

1. **声明能不能被机器核对？**（契约——文档说的"取最深一层""长度上限""会排除某些实体"，有没有一条可执行的检查在盯它）
2. **失败与丢弃能不能被看见？**（可观测——失败静默、指标打错层、裁剪不记录、成本漏记，这些在本文里全出现过）
3. **参数能不能由部署约束导出？**（可标定／可配置——预算、阈值、条数，是从"模型窗口 − 输出预留 − 输入长度"推出来的，还是拍的）

这三问落到工程上是同一件事：**把质量属性写成可执行的检查**——软件工程里管这叫 fitness function（适应度函数）。下一节每条改动，都是把这三问里的某一问答上。

---

## 6. 如果要改，改动都很小

（标签：**【功能缺口】**＝该有的能力没有；**【可观测】／【可配置·可标定】／【可维护】**＝质量属性；**【策略】**＝设计取舍。）

**Local：** ① **【功能缺口】** 入口加相似度阈值（或至少加一次重排）；② **【策略】** 预算从"固定三份"改成"按优先级顺序装、装满即停、余量顺延"；③ **【功能缺口】** 先按问题形状路由，给主导来源一个大额度；④ **【功能缺口】** 设"证据保底"（至少 n 条原文块）；⑤ **【可观测】** 打开对账输出，把候选 / 进窗口 / 丢弃三项计数落盘；⑥ **【功能缺口】** 多轮改成"改写 ＋ 按需改写 ＋ 结果层融合重排"；⑦ **【可配置·可标定】** 参数由部署约束导出（模型窗口 − 输出预留 − 问题长度 ⇒ 上下文预算），别手工对。

**DRIFT：** ① **【功能缺口】** 用现成的 `score` ＋ 一条"充分性"判据做显式判停；② **【功能缺口 ＋ 可配置】** 问题条数改成"按需 ＋ 上限"，并做显式去重与归一化；③ **【可配置】** 把两个 20 拆成两个参数；④ **【可观测】** 未执行的后续问题落一份"未覆盖清单"；⑤ **【可配置】** 给汇总阶段加输出上限；⑥ **【可配置】** 把播种阶段的提示词也导出来（现在只有另外两个可配）。

**两边共同的：【可观测】** 把这些"声明"变成**可对账的探针**——声明"这里会排除某些实体"，就该有地方能看见它排了谁；声明"预算 1800 token 给社区报告"，就该能看见实际塞了几份、挤掉了哪些。**这就是把质量属性写成可执行的检查（fitness function）**，也是对上面三问里第 1、2 问的回答。

---

### 下一篇：一篇 GraphRAG 的总结

这一系列到这里拆完了八篇。下一篇不再拆单个模式，而是把它们收拢成一篇总结，回答两个问题：**GraphRAG 到底基于什么假设、把事情做成了什么**，以及**它的适用范围究竟在哪**——被拆开看时各自都还成立的结论，合起来是什么形状。见《[GraphRAG 到底基于什么假设，适用范围又窄在哪](https://github.com/lipeidonggz/ai-study-helper/blob/master/docs/publish/0041-graphrag-summary.md)》。

---

## 附：核对方式

- 版本：`graphrag 3.2.0`（Python 3.11）；全部结论来自读源码，本文不做新实验
- 主要代码位置（📄）：`query/context_builder/{entity_extraction,conversation_history,community_context,local_context,source_context}.py`、`query/structured_search/local_search/{search,mixed_context}.py`、`query/structured_search/drift_search/{search,primer,action,state,drift_context}.py`、`prompts/query/drift_search_system_prompt.py`、`config/defaults.py`、`config/models/drift_search_config.py`、`query/factory.py`、`index/workflows/generate_text_embeddings.py`、`data_model/row_transformers.py`
- 论文与文档（📎）：[From Local to Global](https://arxiv.org/abs/2404.16130)（其 2.1 节把 RAPTOR 引为最近的前作）；官方 [Query Overview](https://microsoft.github.io/graphrag/query/overview/)
- 学术来路：RAPTOR [2401.18059](https://arxiv.org/abs/2401.18059)、HippoRAG [2405.14831](https://arxiv.org/abs/2405.14831)／HippoRAG 2 [2502.14802](https://arxiv.org/abs/2502.14802)、KAG [2409.13731](https://arxiv.org/abs/2409.13731)、LinearRAG [2510.10114](https://arxiv.org/abs/2510.10114)、EcphoryRAG [2510.08958](https://arxiv.org/abs/2510.08958)、LightRAG（HKUDS，EMNLP 2025）
- 文中"社区报告长度 0.7k–2.9k token"来自我们此前的语料实测；本文未引入新数据
- 本文的完整记录与代码：<https://github.com/lipeidonggz/ai-study-helper>

如果你也用过 Local 或 DRIFT，欢迎把你那边的**实际调用次数**和**上下文里三类材料的实际占比**贴到评论区——我这篇只做机制梳理，很想知道真实语料上"1800 token 塞得下几份报告"。
