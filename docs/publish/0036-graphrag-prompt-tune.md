# 想用 GraphRAG 的 prompt-tune auto 选样？先修 bug 再用

> **30 秒判断**：本篇基于 `graphrag 3.2.0`。`prompt-tune` 在官方文档里的定位是"**可选、但强烈建议跑**"（原文：*This step is optional, though it is highly encouraged*）——用使用者自己的语料反推一份适配的提示词。我在同一份语料、同一条命令下跑了五遍，得到**五个互不相容的领域**：类型表两两相似度最低是 0、最高 0.36，25 个类型里 19 个只出现过一次。往下挖，是两个能定位到行号的代码缺陷：**`auto` 模式的"质心选样"根本没起作用**，以及**few-shot 示例与它自己那段输入对不上**。
>
> **一句话结论**：你从 `prompt-tune` 拿到的那份提示词，不是"从你的语料推断出来的"——`auto` 模式实际只是在语料的一个区间里近乎随机地挑了几块；而那份提示词里的示例，是模型对**另一段文本**的抽取结果。两者都不报错。**要用它，先把这两处修掉（各一行，见 6.3），或者干脆别用 `auto`。**
>
> **适合谁读**：正在用、或准备用 `prompt-tune` 调 GraphRAG 提示词的同学。
>
> **结构速览**：1 节是实验与结果；2 节是四种选块模式 ＋ 完整流程（含"那张实体类型表是怎么来的"）；3–4 节是两个缺陷（含可直接复现的脚本）；5 节解释它们为什么能长期存在；**6 节是"先用还是先修"，含两个 bug 各自的修法**。
>
> **证据标记**：**✅ 实测**（本地脚本／真实调用）· **📄 源码**（`graphrag 3.2.0`）· **📎 官方文档** · **🤔 推断**。

---

## 0. 为什么单看它

`prompt-tune` 在官方文档里的定位是"**可选、但强烈建议跑**"的一步：

> This step is **optional**, though it is **highly encouraged** to run it as it will yield better results when executing an Index Run.

它的作用是：**用你自己的语料，反推一份适配的提示词**——产出六样东西：领域推断、语言检测、persona（角色设定）、**实体类型表**、抽图的few-shot 示例、评分标准。官方默认提示词是给英文通用语料写的，换领域、换语言都会明显变形。

它确实有用——我在一份中文技术语料上量过：**默认四类实体**（`organization / person / geo / event`）会让 `EVENT` 变成一个占 **61%** 的垃圾桶；换成 `prompt-tune` 生成的类型表之后，最大桶降到 **14%**，那些"看起来像结构垃圾"的实体各自找到了合适的归属。

**所以它不是没用——问题在于它不可复现，而且它会静默地把示例搞错。**

**这篇文章要说两个缺陷，但它们的性质完全不同**，先放在开头，免得读到后面意外：

- **缺陷一（第 3 节）是"承诺的能力根本没实现"**：`auto` 模式号称"用嵌入挑最有代表性的样本"，实际等价于"**从语料前 n-subset-max 块里近乎随机地取 k 块**"。这是实打实该修的。
- **缺陷二（第 4 节）是"产物有瑕疵、但影响很小"**：生成的 few-shot 示例与它自己的输入对不上。我专门做了对照实验，**它并不会污染抽取结果**（见 4.4）。

本文的实验规模：一份 51 篇英文新闻语料（取自公开数据集 MultiHop-RAG，按"索引实际读入的文本"计 **124,330 token**，切成 **141 块**——**就是论文 News 数据集用的那份语料**，我们抽了约 9%），命令固定为：

```bash
graphrag prompt-tune --root . --selection-method auto --n-subset-max 32 --k 4
```

跑五遍。下面先看结果，再看原因。

**一个说明（免得被当成"自己配错了才怪工具"）**：官方建议"用脚本提供的默认值"，而默认是 `random` ＋ `n-subset-max=300` ＋ `k=15`；本文用的是 **`auto` ＋ 把 `n-subset-max` 调到 32**——这确实是我们自己选的（当初的理由见第 2 节）。它恰好也是缺陷一暴露得最充分的一组参数，但**换成接近默认的那一组，缺陷一依旧存在**——3.2 证明了"下标错配"与 `n_subset_max` 大小无关，3.3 是对照组实测。

顺带一个成本量级（很多人第一句就会问）：按本文设置跑一次 `prompt-tune`（`k=4`）实测 **10 次调用、8.7 万 token，成本约 ¥0.2**——**它本身很便宜**。真正贵的是它产出的那三份提示词会决定后面整条索引链的行为（几千次调用）。所以"跑一次试试"没有成本顾虑，"**用哪一份**"才有。

---

## 1. 五遍，五个域

每一遍产出的"领域 — 实体类型表"如下（五份原始产物都留在仓库里，可逐字对照复现）：

| # | 推断出的角色 | 类型数 | 实体类型表 |
| --- | --- | --- | --- |
| 1 | 公共财政 / 宏观政策分析师（评分口径里写着 **in the Philippines**） | 6 | `economic_indicator`, `fiscal_policy_measure`, `government_institution`, `public_official`, `tax_revenue_type`, `government_expenditure_program` |
| 2 | 媒体网络 / 商业情报研究员 | 5 | `organization`, `person`, `product`, `event`, `financial_metric` |
| 3 | 经济与政治分析师 | 8 | `organization`, `person`, `government_position`, `economic_metric`, `fiscal_policy`, `sector`, `financial_instrument`, `event` |
| 4 | 技术领域的媒体与商业分析师 | 8 | `company`, `person`, `product`, `platform`, `technology`, `position`, `sports_team`, `sports_league` |
| 5 | 技术与商业分析师 | 7 | `company`, `product`, `technology`, `person`, `government body`, `financial concept`, `legal case` |

三个量化指标：

- **两两 Jaccard 相似度**（交集 ÷ 并集，衡量两张类型表的重合程度）：第 1 遍与其余四次**全是 0.00**；中位约 0.18；最高 0.36（第 4 遍 vs 第 5 遍）；
- 五遍共出现 **25 个不同的实体类型，其中 19 个只出现过一次**；出现三次以上的只有 `person`（4 次）和 `product`（3 次）；
- **评分口径每遍都不同**——而它会被写进社区报告提示词，直接决定报告怎么打分、往哪个方向写。

**第 1 遍那个 "in the Philippines" 是从哪来的？** 我顺着查了一下："Philippines" 出现在 `article-006` 里（一篇 2024 年赤字预算的报道），而**这篇正好落在"前 32 块"的范围内**。也就是说，那一遍抽到的 4 块里包含了它，域就跟着写成了"公共财政 / 菲律宾"——**这不是它读懂了整份语料，而是它只看了那几块。**

这不是"有点抖动"。**同一份语料，你换一天再跑一次，会得到一张几乎不重叠的图。**

---

## 2. 选块的四模式，与选块之后的七次调用

`prompt-tune` 的第一步是**选块**——它从你的语料里挑若干块作为"样本"，后面七次 LLM 调用（领域、语言、persona、评分口径、类型表、示例、报告角色）**全部只基于这几块**。所以这一小步，决定了整份提示词的性格。

```text
语料 ──按 1200 token 切块──> 141 块
                              │
                ①  选块（四种模式：all / top / random / auto）
                              ▼
                    k 个块（本文 k=4；默认 15）
                              │
      ②  七次调用 —— 全部只吃这 k 个块：
            ① 域   ② 语言   ③ persona（只看域，不看语料）
            ④ 评分口径   ⑤ 实体类型表
            ⑥ few-shot 示例（每个块各一次，上限 5）   ⑦ 报告角色
                              ▼
                         三份提示词：
       extract_graph.txt          实体类型表 ＋ few-shot 示例
       summarize_descriptions.txt persona ＋ 固定任务指令
       community_report_graph.txt 报告角色 ＋ 评分口径
```

一共四种方式（📄 [`prompt_tune/types.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/types.py) 的 `DocSelectionType` ＋ [`loader/input.py:76-100`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/loader/input.py#L76-L100)）：

| 模式 | 做法 | 实际喂给 LLM 的量 | 采样范围 | 可复现 |
| --- | --- | --- | --- | --- |
| `all` | **不采样**，用全部块 | 全部（本文语料 141 块 ≈ 15–17 万 token／约 70–80 万 input token） | 全语料 | ✅ |
| `top` | 取**最前面** `limit` 块（默认 15） | 15 块 | 语料开头 | ✅ |
| `random` | 随机抽 `limit` 块（**默认模式**） | 15 块 | 全语料（无偏） | ❌ 无随机种子 |
| `auto` | 先嵌入、按质心选 `k` 块（默认 15） | `k` 块 | 设计意图＝全语料 | ❌ |

逐条说：

**`all`：不采样，全给。** 它有点特殊——`DocSelectionType.ALL` 这个枚举值**除了定义那一行，全仓没有任何引用**（📄 搜 `DocSelectionType.ALL` 只会命中 [`types.py:12`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/types.py#L12)），也就是说 `load_docs_in_chunks` 里**根本没有处理它的分支**，它直接落到"不采样"，把全部块交给 LLM。

**`top`：只读开头。** 取前 `limit` 块。**确定、可复现**，但它只看得见语料的开头——语料是按读取顺序排的，所以你实际是拿"排在最前面的那一两篇"去推断**整个语料**的领域。

**`random`（默认）：范围无偏，但不可复现。** 从全语料随机抽 `limit` 块。采样范围没有系统性偏差，但 `DataFrame.sample()` 没有设随机种子 ⇒ 每次抽的都不一样。

**`auto`：设计上最讲究的那个。** 它分三步：

1. 先从全语料里**随机抽 `n_subset_max` 块**（默认 300）——这批块只用来算嵌入；
2. 把这批块送嵌入模型，算出它们的**质心**，再取**离质心最近的 `k` 块**（默认 15）；
3. 把这 `k` 块作为样本，送进后面那七次调用。

设计意图很清楚：**用嵌入找"最典型"的块，一次覆盖多个语义簇，避免样本被某一个域吃掉。** 相比 `top` 它不该只看开头，相比 `random` 它不该抽到一堆离群点——**这也正是我们当初选它的原因**（上一轮我们用 `top` ＋ 单篇语料，生成的提示词把整个主题写死了）。另外注意 `n_subset_max` 与 `k` **只在 `auto` 下生效**，`--limit` **只在 `random` / `top` 下生效**——两组参数是互斥配对的。

但它有 bug——而且这个 bug 让"质心"这一步变成了**纯粹的装饰**：实际行为等价于"**从语料前 32 块里近乎随机地取 4 块**"。**第 3 节**会讲清楚。

### 2.1 选块之后：七次调用各自"吃什么"

**四种模式只管"选块"这一步**，选完之后的流程四种模式完全一样。这七次调用的输入与产出如下（📄 [`api/prompt_tune.py:107-176`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/api/prompt_tune.py#L107-L176)）：

| #   | 调用                                      | 输入                                  | 产出                   | 产出落到哪个文件                     |
| --- | --------------------------------------- | ----------------------------------- | -------------------- | ---------------------------- |
| 1   | `generate_domain`                       | **样本块**（拼成一个字符串）                    | domain（一句领域描述，自由文本）  | ——（只作后面几步的输入）                |
| 2   | `detect_language`                       | 样本块                                 | language             | 三个文件都用                       |
| 3   | `generate_persona`                      | **只有 domain**（＋一个固定 task 模板）        | persona（3–4 句"专家人设"） | 三个文件都用（作为 system 消息）         |
| 4   | `generate_community_report_rating`      | domain ＋ persona ＋ 样本块              | 0–10 的评分口径           | `community_report_graph.txt` |
| 5   | `generate_entity_types`                 | **persona ＋ domain ＋ 样本块**          | **实体类型表**            | `extract_graph.txt`          |
| 6   | `generate_entity_relationship_examples` | persona ＋ 类型表 ＋ **选中的每个块各一次**（上限 5） | few-shot 示例          | **只有 `extract_graph.txt`**   |
| 7   | `generate_community_reporter_role`      | domain ＋ persona ＋ 样本块              | 报告角色                 | `community_report_graph.txt` |


换句话说：**选块之后，流程不会再回到全量语料，也不会再回到"文档"这一层**——后面所有调用吃的都是那 `k` 个块。四种选块模式的全部影响，就止于"这 `k` 个元素是谁"。

三条依赖关系值得单独指出：

1. **persona 不看语料，只看 domain。** `GENERATE_PERSONA_PROMPT` 里只有一个 `{sample_task}`，而它是用 domain 填出来的固定模板（"识别某社区内部的连接与结构，特别是在 {domain} 领域"）。⇒ **域一旦定错，persona 跟着错。**
2. **类型表看得到 persona 与 domain**，而 persona 又是域的下游 ⇒ **类型表本质上是"域"的下游产物**。
3. **示例是唯一"扇出"的那一步**（选中的每个块一次，上限 `MAX_EXAMPLES = 5`）——也正是**第 4 节**那个缺陷发生的地方。

一句话：**这是一条单源流水线，源头只有一个——"领域"。** 而领域是从那几块样本里自由生成的。

### 2.2 那张实体类型表是怎么来的

类型表就是第 5 步的产出。它的提示词 `ENTITY_TYPE_GENERATION_JSON_PROMPT` 有几条硬要求（📄 [`prompt/entity_types.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/prompt/entity_types.py)）：

- "实体类型**必须与用户任务相关**"；
- "**避免** `other` / `unknown` 这类泛型"；
- "**不要生成冗余或重叠的类型**——比如同时出现 `company` 和 `organization`，只留一个"；
- "数量不重要，**质量优先**"；
- 外加三段示例（组织 / 概念 / 产业各一段）。

输出是结构化的 JSON（`{"entity_types": [...]}`），所以拿到手就是一张干净的列表。

**但这条链的源头不稳**：域是从样本里自由生成的（第 1 步）→ persona 只由域推出来（第 3 步）→ 类型表再由 persona ＋ 域 ＋ 样本生成（第 5 步）。**域抽彩票，类型表就继承这张彩票**——本文开头那五张互不重叠的类型表，就是这么来的。

而且提示词里那条"禁止冗余/重叠"的硬要求，实测也不总被遵守。跑第二遍时拿到的表里有这么几组：

```text
job_cuts        + layoffs          ← 近乎同义
legal_case      + law
technology                          ← 泛型
organization / person               ← 泛型
sports_event / investment_round     ← 很窄的具体类型
```

同一张表里泛型和窄类型混着，粒度是参差的——**而这已经是"四种模式里最正常的一次"了。**

---

## 3. 缺陷一：`auto` 的"质心选样"没有作用

### 3.1 下标错位：32 行的下标套到 141 行上

[`prompt_tune/loader/input.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/loader/input.py)：

```python
# 第 86 行：随机抽 n_subset_max 块去算嵌入（pandas sample，无随机种子）
sampled_text_chunks = chunks_df.sample(n=min(n_subset_max, len(chunks_df)))["text"].tolist()
embedding_results = await run_embed_text(sampled_text_chunks, ...)
embeddings = np.array(embedding_results.embeddings)      # ← 行数 = 抽样抽出来的那些

# 第 100 行：把嵌入算出的下标，用回到【全量】表上
chunks_df = _sample_chunks_from_embeddings(chunks_df, embeddings, k=k)
```

而 `_sample_chunks_from_embeddings` 是：

```python
center = np.mean(embeddings, axis=0)
distances = np.linalg.norm(embeddings - center, axis=1)
nearest_indices = np.argsort(distances)[:k]     # 下标 ∈ [0, n_subset_max-1]
return text_chunks.iloc[nearest_indices]        # 却在【全量】表上取
```

**下标来自"被抽出来的那 32 块"，却被套用到"全量 141 块"的表上。**

### 3.2 那把 `n_subset_max` 调大就没事了吗？也不行

3.1 讲完，最自然的反应是：**那把 `n_subset_max` 调到 ≥ 语料块数，是不是就没事了？**

不行——因为 `DataFrame.sample()` 返回的**不是原顺序的子集，而是随机排列**；即便抽满全量（集合完全相同），顺序也已经变了。

下面先看一个**玩具数据**上的最小验证（8 行数据，第 i 行的"嵌入"就取 i，抽样取满 8 行但顺序被打乱）——真实语料上的结果在下一小节：

```text
原始顺序      : row0 row1 row2 row3 row4 row5 row6 row7
采样返回的顺序: row0 row6 row7 row2 row4 row5 row1 row3   ← 抽满全量，顺序被打乱
embeddings    : 0 6 7 2 4 5 1 3        (按采样顺序排列)
质心          : 3.50
离质心最近的 3 个（采样序列里的位置）: [7, 4, 3]
应该返回      : row3 row4 row2
实际返回      : row7 row4 row3          ← 不一致
```

原因还是那个：`embeddings[j]` 对应的是**打乱后第 j 个**元素，而 `nearest_indices` 是**打乱序列里的位置**，却被拿来索引**原顺序的表**。注意两边的下标都合法（都在 0–7 内）——所以**它不报错，只是静默地取错行**。

一句话：**拿 A 集合的几何形状，去 B 集合里取第几个。** 质心提供的信号被彻底打散。

### 3.3 实测：直接调那个函数

不靠推理，直接调 `load_docs_in_chunks()`（就是 `prompt-tune` 内部用的那个），同一份 141 块语料、`k=4`，跑两组：

```text
=== n_subset_max = 32（我的设置） ===
  run 1: 4 块 -> article-008, 002, 007, 001
  run 2: 4 块 -> article-008, 008, 001, 004
  run 3: 4 块 -> article-003, 005, 009, 006      ← 全部落在 article-001 ~ 009

=== n_subset_max = 300（默认，≥ 语料块数） ===
  run 1: 4 块 -> article-005, 014, 018, 001
  run 2: 4 块 -> article-038, 013, 009, 037
  run 3: 4 块 -> article-009, 038, 041, 010      ← 能到 article-038 / 041
```

**注意对照组的边界**：`n_subset_max=300` 那一组只证明了"**可达范围打开了**"（能取到 article-038/041 这种靠后的文章）——**它并不代表取到的是"离质心最近"的那几块**。

两层效果看得清清楚楚：

| 层次 | 条件 | 后果 |
| --- | --- | --- |
| **一直存在** | 无条件（`sample()` 必然打乱顺序） | 质心挑选失效 ⇒ 结果 ≈ **从某个区间里随机取 k 块** |
| **叠加** | `n_subset_max < 语料块数` | 下标范围被压到 `[0, n_subset_max-1]` ⇒ 只能命中**全量表的前 n_subset_max 行** |

第一组那 141 块里，前 32 块只来自 **9 篇文章**（共 51 篇）。也就是说：**那五遍"从语料推断域"，实际是从 9 篇里的 4 块推断的。** 而 `--k 4` 意味着整份提示词由 4 块决定。

一个旁证：三次里两次抽到 `article-008`（一篇亚马逊 Prime Day 苹果打折导购），而第 4 遍推断出的正是"技术 / 消费电子"域，它的 few-shot 示例也全部来自这篇文章。

### 3.4 顺带把默认值说清楚

这几组参数**互斥配对**，很容易混：

| 参数 | 默认 | 生效范围 |
| --- | --- | --- |
| `--selection-method` | **`random`**（不是 `auto`） | — |
| `--n-subset-max` | 300 | **仅 `auto`** |
| `--k` | 15 | **仅 `auto`** |
| `--limit` | 15 | **仅 `random` / `top`**（`all` 与 `auto` 都不读它） |

所以即便用 `auto` 的**默认值**（`n_subset_max=300`），只要语料块数超过 300，也会踩到"范围被压"这一层——只是方向变成"只看语料开头"。**按读取顺序排在前面的那几篇，决定了你整张图的类型表。**

---

## 4. 缺陷二：few-shot 示例与它自己那段输入对不上

这个缺陷不挑采样方法，**默认的 `random` 也一样中**。

### 4.1 那些示例是怎么"造"出来的

先说一个容易误解的点：`prompt-tune` 产出的提示词里，**示例不是人写的模板，而是"让模型在你自己的语料上先跑一遍抽取"的结果**。

第 6 步 `generate_entity_relationship_examples` 对**每个选中的块**发一次请求（上限 5 个）：

- system 消息 = persona；
- user 消息 = `ENTITY_RELATIONSHIPS_GENERATION_PROMPT`——**这个提示词本身就是一个完整的抽取任务**（"识别所有实体与关系，按 `("entity"<|>名称<|>类型<|>描述)` 格式输出"）；
- 模型的返回被原样存成 `examples[i]`。

然后在索引阶段 `create_extract_graph_prompt` 把示例拼进**抽取提示词**（只有它带示例；摘要与报告提示词都不带），格式是：

```text
Example {n}:

entity_types: [...]
text:
{input_text}          ← docs[i]，第 i 个块（chunk）
------------------------
output:
{output}              ← examples[i]，模型对某个块的抽取结果
```

拼接规则两条：**至少 `min_examples_required`（默认 2）条**；之后只要超出 `max_tokens`（默认 2000）预算就停。

### 4.2 错配是怎么发生的：一个"会继续长大"的消息列表

核心机制一句话：**`build()` 返回的不是"一张消息快照"，而是那个还在往里追加的列表本身。**

第一步第二步**同一个类** `CompletionMessagesBuilder` 上的两个方法（📄 [`graphrag_llm/utils/completion_messages_builder.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag-llm/graphrag_llm/utils/completion_messages_builder.py)）——它内部只有一个 list `self._messages`，所有 `add_*` 方法都往它里面 append。

#### 第一步：`add_user_message` 是往同一个 list 里 append

```python
def add_user_message(self, content, name=None):      # 同文件第 177 行
    self._messages.append(ChatCompletionUserMessageParam(...))
    return self        # ← 返回自己，为了能链式调用
```

#### 第二步：`build()` 返回那个 list，不是副本

```python
def build(self):       # 同文件第 245 行
    return self._messages          # ← 注意：不是 list(self._messages)
```

这两步合起来的效果，三行就能复现：

```python
b  = CompletionMessagesBuilder().add_system_message("SYS")
a1 = b.add_user_message("MSG-1").build()
a2 = b.add_user_message("MSG-2").build()

print(a1 is a2)   # True            ← 两个"快照"是同一个对象
print(a1)         # [SYS, MSG-1, MSG-2]   ← a1 也"长大"了
```

**你以为 `a1` 是"只含 MSG-1 的快照"，其实它是个会继续长大的列表。**

#### 第三步：协程要等 `gather` 才真正执行

回到那段出问题的代码（[`prompt_tune/generator/entity_relationship.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/generator/entity_relationship.py)）：

```python
msg_builder = CompletionMessagesBuilder().add_system_message(persona)   # ← 循环外，只有这一个

tasks = [
    model.completion_async(messages=msg_builder.add_user_message(message).build(), ...)
    for message in messages
]
responses = await asyncio.gather(*tasks)
```

`completion_async(...)` 是协程函数——**调用它只是创建协程对象，函数体要等 `await` / `gather` 才执行**。于是时间线是这样的：

```text
预期（每个任务各拿各的）：
  循环 i=0 → 任务0 的消息 = [SYS, 块0]
  循环 i=1 → 任务1 的消息 = [SYS, 块1]
  … 执行时各自回答自己那一块        ✅

实际发生：
  循环 i=0 → 任务0 拿到的是【同一个 list 的引用】
  循环 i=1 → append(块1) 之后，任务1 拿到的还是【同一个 list】
  …
  循环结束 → 那个 list 已经是 [SYS, 块0, 块1, 块2, 块3, 块4]
  gather 执行 → 5 个任务看到的输入【完全一样】
  ⇒ 模型每次都回答最后一条 ⇒ 5 份输出都是"块4"的抽取结果
```

**为什么偏偏是"最后一条"？** 因为请求在结构上就是一段多轮对话——`[system, 用户1, 用户2, 用户3, 用户4]`，中间**没有任何助手回复**。模型最自然的读法是"用户连着说了几句、最新一句是第 4 条"，于是只回答第 4 条；它不会把五段各自抽一遍再合并，因为提示词里没有任何指令要求它合并（那个提示词始终是"给一段文本、抽它的实体和关系"的单输入句式）。

需要说清的是：**"回答最后一条"是模型行为，不是代码保证。** 换个模型，症状可能变成"把几段混着抽"或"偶发某条对了"，但**缺陷本身不变**——5 个任务看到的输入都是同一个（而且是错的）。我实测的 7 份产物（5 遍 `auto` ＋ 2 份对照）里，观察到的都是"**同一个块的抽取结果被当成多条示例**"；其中只有一份恰好有一个示例的输入与那个块同属一篇文章，所以命中了 25/29——**其余示例全部对不上**。

#### 第四步：配对时错位成型

索引执行实体和关系抽取的提示词`create_extract_graph_prompt` 是按 `docs[i] ↔ examples[i]` 逐条配的，于是：

```text
examples = [块4的结果, 块4的结果, 块4的结果, 块4的结果, 块4的结果]
docs     = [  块0   ,   块1    ,   块2    ,   块3    ,   块4    ]

拼进最终提示词：
  Example 1: text=块0   output=块4的结果   ← 错配
  Example 2: text=块1   output=块4的结果   ← 错配
  …
  Example 5: text=块4   output=块4的结果   ← 只有这条对得上
```

这也解释了 4.3 里那条最反直觉的证据：在我们那份 `auto` 臂的提示词里，**唯一对得上的那个示例，恰好它的输入就是最后一个块**。

修复只要一行：**把 builder 移进每次调用、每个任务各建一个**。上游主索引路径本来就是这么写的（[`graph_extractor.py:86`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/index/operations/extract_graph/graph_extractor.py#L86)，每次调用新建）。

### 4.3 怎么验证：把输出里的实体名，拿回它自己的输入里搜

先讲判定方法，否则下面的数字没法读。`extract_graph.txt` 里每条示例的 output 都是这种格式：

```text
("entity"<|>实体名<|>类型<|>描述) ## ("entity"<|>实体名<|>...)
```

所以验证一步就够：**取这条示例 output 里的所有实体名，逐个去它自己的 `text` 里搜**。名副其实的话命中率应该接近 100%；错配的话会大幅偏低。

结果如下（✅ 实测，脚本 `_check_example_consistency.py`）：

| 提示词             | 示例  | 它的 `text` 来自   | output 里的实体                                               | 命中        |
| --------------- | --- | -------------- | --------------------------------------------------------- | --------- |
| **建索引那份**（默认参数） | 1   | TwitchCon 那篇的块 | `MIKE RYAN`／`THE DAN LEBATARD SHOW`／`INTER MIAMI`／`MIAMI` | **0/4**   |
|                 | 2   | McGregor 那篇的块  | `MIKE RYAN`／`THE DAN LEBATARD SHOW`／`INTER MIAMI`／`MIAMI` | **0/4**   |
| **`auto` 那份**   | 1   | 苹果手表导购的块       | `COWBOYS`／`49ERS`／`NFL`／…                                 | **0/29**  |
|                 | 2   | Cowboys 那篇的块   | `COWBOYS`／`49ERS`／`NFL`／…                                 | **25/29** |

这张表里有两条独立的信息，合起来正好把机制锁死：

**① 两个不同的输入，却给出同一组输出。** 第一份提示词的两个示例，`text` 分别来自 TwitchCon 和 McGregor（两篇毫不相干的文章），但 output 是**一模一样的 4 个 Inter Miami 实体**。⇒ **说明这两次调用的输出不是各自算出来的**，而是同一个东西重复了两遍——与 4.2 的机制预测一致。

**② 那"同一个东西"是谁？** 看第二份：示例里只有第 2 条对得上（25/29），第 1 条完全对不上（0/29）。而第 2 条的 `text` 恰好**与最后一个块同属一篇文章**（都是 Cowboys 那篇，实体名大量重合）。⇒ **说明所有 output 其实都来自最后一个块**——只有它的"同门兄弟"认得出自己。

**③ 这不是偶发。** 我把手头七份 `prompt-tune` 产物都跑了一遍这个检查——五遍 `auto`，加上上面表格里的另外两份：

| 产物 | `auto5-1` | `auto5-2` | `auto5-3` | `auto5-4` | `auto5-5` | `auto` 那份 | 建索引那份 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 示例一致性 | 0% | 0% | 6% | 0% | 0% | **43%** | 0% |

**七份里五份是 0%——一条示例都对不上**；剩下两份一份 6%，一份 43%（就是上面"第 2 条恰好与最后一个块同文"的那一份）。

**但要留意这张表的边界**：它证明的是"**示例被写坏了**"——这是提示词**自身**的一个性质，**还不等于"模型会被带歪"**。后者是另一个问题，得单独做对照实验（下一节），而且——剧透一下——**实测结果是否定的，至少 DeepSeek Flash 4.1 没有被带歪**。

### 4.4 它到底在教什么（以及我敢说到哪一步）

先把它教了什么说清楚。抽取提示词里那段示例长这样：

```text
Example 1:
text: <一段文本>
------------------------
output: <一组实体和关系>
```

而 few-shot 的作用是**用示范来定义"什么算合格输出"**——模型会照着例子的"输入 → 输出"关系去理解任务。我们的例子示范的是：

> 给你**一段**文本，输出**另一段**文本里的实体和关系。

换句话说，**它示范了一种"输入与输出可以不对应"的做法**。而这条提示词的输出会直接决定索引里每个节点和每条边——**它是整条索引链的输入规范**，不是一份随手写的文档。

那么"**输入可以不看**"这个判断，是不是也该落到图上？**我去做了对照实验——结果是负的。**

#### 实验：把示例换掉，看抽取会不会被带歪

同一份语料、同一个类型表、同一批测试块，**只改 `-Examples-` 那一段**：

| 臂 | 示例怎么配 |
| --- | --- |
| **A 现状（错配）** | `text`=块0 → `output`=**另一块的抽取结果**；`text`=块1 → `output`=**同一个**结果（复现 bug 的"重复"特征） |
| **B 正配** | `text`=块0 → `output`=块0 自己的抽取结果；块1 同理 |
| **C 无示例** | 整段 `-Examples-` 删掉 |

然后用同一份提示词去抽 **3 个与示例无关的留出块**，每臂每块跑 3 次，三项指标（✅ 实测，脚本 `_probe_example_pollution.py`）：

```text
  A 错配  : entities= 196  grounded= 184 (93.9%)  leaked=  0 ( 0.0%)
  B 正配  : entities= 199  grounded= 183 (92.0%)  leaked=  0 ( 0.0%)
  C 无示例: entities= 211  grounded= 199 (94.3%)  leaked=  0 ( 0.0%)
```

- **grounded** ＝ 实体名能在该块的输入文本里找到的比例；
- **leaked** ＝ 实体名**不在**输入里、却在**示例的输出**里出现过的比例（也就是"从示例抄来的"）。

**三臂的泄漏率都是 0。** 错配臂那两条示例的输出是 Inter Miami 那篇的实体（`MIKE RYAN` / `INTER MIAMI` / `THE DAN LEBATARD SHOW`），而三个测试块与它毫无关系——**抽出来一条都没抄过去**。grounded 率也基本持平（92–94%），无示例那臂甚至实体最多（211）。

#### 结论：这是"示例无效"，不是"示例有害"

把三种说法分开看，状态一目了然：

| 说法 | 状态 |
| --- | --- |
| 示例的输入与输出**系统性不一致** | ✅ **实测**（4.3 那张命中率表） |
| 原因是代码缺陷（共享 builder） | ✅ **实测 ＋ 源码** |
| **它会让抽取"学歪"、抄示例里的内容** | ❌ **实测为否**（泄漏率 0，grounded 率持平） |

也就是说，这个缺陷的**确定危害**是：**这几条示例作为"示范"是无效的**——它们的输入和输出对不上，等于白占提示词的位置和 token；而**没有证据表明它们会污染抽取内容**。至于别处见过的"报告串味""某个桶特别大"，更可能的解释在别处（比如类型表本身偏窄、语料本身的域分布），不该记在它头上。

顺带一条方法上的提醒（我自己差点栽在这儿）：**"发现缺陷"很容易滑向"认定这个缺陷产生了后果"**——上表最后一行就是这种滑向：听上去合理、机制上也讲得通，但一做对照就站不住。**缺陷的"存在性"和它的"影响面"，是两件要分开验证的事。**

#### 这个实验的局限

1. **样本小**：3 个测试块 × 3 次，而且块的差异远大于臂的差异（同一个块能抽出 55 个实体，也能抽出 3 个）；
2. **只测了一个模型**（DeepSeek），换模型不保证一样；
3. **grounded 只用"实体名是否出现在文本里"作近似**，管不到关系方向、描述是否走样；
4. 反过来说，这个实验用的是**极端错配**（示例输出来自完全不相干的文章）——**连极端情形都没泄漏**，真实情形（同一批语料内部错位）就更不容易。

---

## 5. 为什么这两个缺陷能活这么久

"这么火的微软开源项目，怎么会有这种浅 bug？" 我一开始也不信，于是把暴露面查了一遍。结论是：**它们确实不在核心路径上。**

**① 两个缺陷都在 `prompt-tune` 里，而它是官方标注"开发中"的功能。** [`api/prompt_tune.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/api/prompt_tune.py) 开篇写着：

> WARNING: This API is under development and may undergo changes in future releases. Backwards compatibility is not guaranteed.

**主索引路径是干净的。** [`index/operations/extract_graph/graph_extractor.py:86`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/index/operations/extract_graph/graph_extractor.py#L86)、[`extract_covariates/claim_extractor.py:122`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/index/operations/extract_covariates/claim_extractor.py#L122) 都是**每次调用新建一个 builder**——正是正确写法。全包唯一一处把 builder 提到循环外的，就是 [`entity_relationship.py:41`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/generator/entity_relationship.py#L41) 这一处。

**② 缺陷一的暴露面取决于你怎么用它**：`auto` **不是默认模式**（默认是 `random`），所以多数人碰不到它。但**一旦选了 `auto`，下标错配就是无条件存在的**——3.2 已经证明"把 `n_subset_max` 调大也不行"；`n_subset_max < 语料块数` 只是额外把可选范围压到前 N 行。我们自己"把 32 硬塞进 141 块的语料"，正好两个问题同时中了。

**③ 缺陷二才是真正的漏网之鱼：它不挑参数，但**失败是静默的**。**

- 生成的提示词语法完全正确，不报错；
- 示例长得就是正常的实体元组；
- 唯一的发现方式，是**逐条把示例的输出拿去和它自己的输入比对**——而这正是没人做的事。

顺带一句方法上的教训：**"生成式工具"的质量检查，不能只看它产出的东西"像不像"，要看它产出的东西"对不对得上自己的输入"。**

---

## 6. 所以：先用，还是先修？

两个缺陷的**急迫程度不同**，答案要分开说。

### 6.1 缺陷一：修之前，`auto` 这一档没有可用配置

它不是"结果有偏差"，而是**能力失效**——你以为它在挑代表性样本，它其实在语料前 32 块里近乎随机地取。而且**调参数救不回来**：3.2 已经证明，就算把 `n_subset_max` 调到 ≥ 语料块数，"质心语义"仍然是错的。

所以只有两条路：**不改代码就别用它；愿意动手就修它**（修法见 6.3，改动很小）。

| 替代方案 | 你得到什么 | 代价 |
| --- | --- | --- |
| `random` | 全语料无偏采样 | 每次都换一批，不可复现 |
| `top` | 完全可复现 | 只看语料开头（对文件顺序敏感） |
| `all` | 看得最全、零采样偏差 | 5 次调用各带全部样本（本文语料约 70–80 万 input token） |

### 6.2 缺陷二：可以先不改，但建议改

它的代价是"**示例无效**"，不是"示例有害"——4.4 的对照实验显示它不污染抽取结果，所以不改**不影响图的质量**。

但改掉它也没坏处：示例是花 token 换来的（一次 `prompt-tune` 里 4–5 次调用都花在它身上），而且**换个模型或换份语料，未必还是这个结论**。

### 6.3 两个 bug 各自的修法（改动都很小）

**缺陷一（`auto` 取错块）的修法 · [`prompt_tune/loader/input.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/loader/input.py)（让下标对齐）**

```python
# 现在：把【全量】表传给 helper —— 下标来自采样集，却在全量表上取
chunks_df = _sample_chunks_from_embeddings(chunks_df, embeddings, k=k)

# 应该：把【被采样的那一份】传下去
sampled_df = chunks_df.sample(n=min(n_subset_max, len(chunks_df)))
sampled_text_chunks = sampled_df["text"].tolist()
embeddings = ...                       # 对 sampled_text_chunks 求嵌入
chunks_df = _sample_chunks_from_embeddings(sampled_df, embeddings, k=k)
```

**缺陷二（示例与输入错配）的修法 · [`prompt_tune/generator/entity_relationship.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/generator/entity_relationship.py)（每个任务各建一个 builder）**

```python
# 现在：循环外共用一个 builder，每轮 add_user_message 都往同一个列表里追加
msg_builder = CompletionMessagesBuilder().add_system_message(persona)
tasks = [
    model.completion_async(messages=msg_builder.add_user_message(m).build(), ...)
    for m in messages
]

# 应该：每个任务各用自己的 builder（上游主索引路径就是这么写的）
tasks = [
    model.completion_async(
        messages=CompletionMessagesBuilder().add_system_message(persona)
            .add_user_message(m).build(),
        ...
    )
    for m in messages
]
```


### 6.4 暂时不想动代码的话

**① 针对本文这两个 bug，两件事：**

- **别用 `auto`**（理由见 6.1），换成 6.1 那张表里的某一个；
- **顺手查一下示例对不对得上**：把 `extract_graph.txt` 里每条 `Example` 的 output 实体名，拿去它自己的 text 里搜一遍。命中率低就是错配（本文七份产物里五份是 0%）——**这是唯一能发现缺陷二的方法**。

**② 另外几条体检，跟这两个 bug 无关，但同属「`prompt-tune` 的坑」：**

- **显式给 `--domain` 和 `--language`**：这条对应的其实是**开头那个"五遍五个域"**——它的成因除了第 3 节那个采样 bug，还有"LLM 调用本身没有随机种子"（模型配置里没有 `temperature` / `seed` 字段），后面这条不是 bug、改不掉，只能自己给域来绕；
- **主题有没有写死**：把旧语料的提示词直接搬到新语料，报告会整体跑偏；
- **长度占位符有没有丢**：官方默认的[报告提示词](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompts/index/community_report.py)里有 `Limit the total report length to {max_report_length} words.`，而 `prompt-tune` 会整篇重写提示词，**这类可选约束会被静默丢掉**（我实测三份产物里出现 0 次）；
- **换语料必须重跑并重新检查**：我踩过最贵的一次坑就是"换了语料、没换提示词"，那一轮数据全部作废。

还有一个值得试的方向（🤔 待验证）：既然 few-shot 示例在多数情况下都是系统性错配的，那么**"干脆不带示例"可能不比现在差**。这是个便宜且干净的对照实验。

---

## 7. 小结：三句话

1. **`prompt-tune` 的"域推断"不是从你的语料里读出来的**——在 `auto` 下它是从 4 块（常常只来自一两篇）猜出来的，而且猜的过程不可复现。同一份语料跑五遍，能得到五个几乎不重叠的类型表。
2. **它的示例生成有代码级缺陷**：所有示例其实是**同一个块**的抽取结果，却被逐条配给了**不同块**的原文——**它示范的是"输入与输出可以不对应"**。（缺陷本身实测确凿；不过我做了对照实验：**它并不会把抽取内容带歪**，三臂的"泄漏率"都是 0，见 4.4。所以它是"**无效的示例**"，不是"有害的示例"。）
3. **但它仍然有用**——前提是先处理掉这两个缺陷：**修掉 `auto` 的取块错位（见 6.3）或者干脆别用 `auto`**、别让它替你决定"这是什么领域"（显式给 `--domain`）、跑完做三项检查。这样它就能把默认四类实体那个 61% 的垃圾桶治好。

---

### 下一篇：Dynamic Community Selection

这篇的主角是"调优工具"，下一篇回到检索：GraphRAG 有一条**官方文档几乎没写**的可选路径——它想解决"用户不知道该选哪一层"这个问题。我把它的打分器、剪枝算法、三组实测和一年半的功能史都核了一遍，包括一个"失败反而更贵"的设计。下一篇《[GraphRAG 有条"按问题挑材料"的隐藏路径：文档 0 提及，实测也未必更省](https://github.com/lipeidonggz/ai-study-helper/blob/master/docs/publish/0037-graphrag-dynamic-selection.md)》逐行核这些。

---

## 附：核对方式

- 版本：`graphrag 3.2.0`（Python 3.11），DeepSeek `deepseek-chat` 作补全模型、本地 `multilingual-e5-large` 作嵌入
- 语料：论文 News 数据集的同源子集——51 篇英文新闻（**124,330 token**（按索引实际读入的文本计，含文件标题；正文合计 122,387）/ 141 块，取全量 1,381,153 token 的约 9%），固定种子 42 分层抽样；`subset_manifest.json` 记录每篇的标题、URL、分类与 token
- 主要代码位置（📄，链接钉在 tag `v3.2.0` 上）：[`prompt_tune/loader/input.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/loader/input.py)（选块与 `_sample_chunks_from_embeddings`）、[`prompt_tune/generator/entity_relationship.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/generator/entity_relationship.py)（示例生成）、[`graphrag_llm/utils/completion_messages_builder.py:245`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag-llm/graphrag_llm/utils/completion_messages_builder.py#L245)（`build()` 返回引用）、[`prompt_tune/generator/extract_graph_prompt.py`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/prompt_tune/generator/extract_graph_prompt.py)（示例配对）、[`cli/main.py:278-335`](https://github.com/microsoft/graphrag/blob/v3.2.0/packages/graphrag/graphrag/cli/main.py#L278-L335)（默认值）
- 本文用到的脚本（都在仓库的 [`experiments/graphrag-exp-multihop/`](https://github.com/lipeidonggz/ai-study-helper/tree/master/experiments/graphrag-exp-multihop) 下，点文件名即可看源码）：

| 脚本                                                                                                                                                                                                                                                                                    | 干什么                                                 |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| [`_run_5_tunes.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_run_5_tunes.py)                                                                                                                                                     | 同参跑五遍 prompt-tune，并抓取中间数据                           |
| [`_probe_all_mode.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_probe_all_mode.py)                                                                                                                                               | 四种选块模式各跑一次，打印实际返回了多少块                               |
| [`_probe_auto_selection.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_probe_auto_selection.py)                                                                                                                                   | 直接调 `load_docs_in_chunks`，打印每次真正选中的块属于哪篇文章          |
| [`_verify_permutation_alignment.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_verify_permutation_alignment.py)                                                                                                                   | 3.2 那个最小验证（抽满全量、顺序被打乱，看返回是否仍是"离质心最近"的那几个）           |
| [`_verify_sample_order.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_verify_sample_order.py) / [`_verify_bugs.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_verify_bugs.py) | `sample()` 顺序、`build()` 对象同一性、下标错配三个最小验证            |
| [`_check_example_consistency.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_check_example_consistency.py)                                                                                                                         | 逐条检查示例输出里的实体名是否出现在它自己的输入里                           |
| [`_probe_example_pollution.py`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/graphrag-exp-multihop/_probe_example_pollution.py)                                                                                                                             | 4.4 那个对照实验：同语料同类型表，只换 `-Examples-` 段（错配 / 正配 / 无示例） |

> ⚠️ **脚本是现场快照，不能开箱即跑**——它们读的语料、模型产物、向量库都在 `data/tmp/` 下，而那个目录没有入库（体积大、且混着运行时产物）。要跑需要自备：语料与产出目录、DeepSeek 凭据、本地 embedding 服务、`graphrag 3.2.0` 环境。详见 [`experiments/README.md`](https://github.com/lipeidonggz/ai-study-helper/blob/master/experiments/README.md)。

如果你也在用 `prompt-tune`，欢迎说说两件事：**① 你的"域"是怎么定的**（自己写 `--domain`，还是让它猜）；**② 有没有遇到过生成的提示词跑偏**（主题被写死、类型表偏得离谱）。我手上只有一份语料 ＋ 一个模型，很想知道这些现象在别处是不是一样。
