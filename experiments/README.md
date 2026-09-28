# experiments/ —— 实验脚本快照

这里是 GraphRAG 精读与 RAG 评测过程中写的探针、分析与复现脚本。

它们原先放在 `data/tmp/` 下，而那个目录被 `.gitignore` 排除（里面有语料、模型产物、向量库），
于是对外稿里那句"复现脚本都在开源仓库"一度是**不成立**的。这个目录就是把它补上：
**只收脚本，不收数据**。

## 目录对应

| 目录 | 是哪一轮实验 |
| --- | --- |
| `graphrag-exp/` | 环境与总控（索引 runner、prompt-tune runner、本地 embedding 服务等） |
| `graphrag-exp-multihop/` | 51 篇英文新闻子集 —— 对外稿 **0036**（prompt-tune）与 **0039**（压缩比）的实验现场 |
| `graphrag-exp-multihop-autodomain/`、`…-selfconsistent/` | 同上的 A1 / A2 对照臂 |
| `graphrag-exp-eval/` | 14 篇中文技术笔记语料 —— 对外稿 **0035**（Global Search）与 **0038**（报告长度）的数据来源 |
| `graphrag-exp-dynamic/` | Dynamic Community Selection 的小语料实验（0037） |
| `graphrag-exp-juejin/`、`graphrag-exp-mixed/`、`graphrag-exp-update*/` | 更早的实验轮次（索引 / 增量索引） |
| 根目录下的 `*.py` | 散装的单次探针，例如 `_graphrag_rollup_probe.py`、`qc_*.py` |

## ⚠️ 它们**不能开箱即跑**

这些是现场快照，不是产品化的工具。想跑起来，至少需要自己准备四样东西：

1. **语料与产出**：脚本默认从同目录的 `input/`、`output/`、`prompts-tuned*/` 读写，
   而这些**没有入库**（体积大，且混着运行时产物）；
2. **模型凭据**：脚本从 `backend/data/app.db` 读（那个文件同样不入库），
   或在环境变量 `DEEPSEEK_API_KEY` 里给；
3. **本地 embedding 服务**：`_embed_server.py` 需要单独的 venv 与模型缓存（`intfloat/multilingual-e5-large`）；
4. **graphrag 环境**：Python 3.11 + `graphrag 3.2.0`。

所以请把它们当作"**我们当时是怎么测的**"的阅读材料，而不是可执行工具。
每篇文章正文里贴出的结论，都能在这些脚本里找到对应的那一支。

## 入库前的检查

拷贝前扫过一遍：`sk-` 形态的密钥 **0 处**、硬编码的 `api_key` / `password` 字面量 **0 处**。
脚本里的 key 一律走"运行时读 `app.db` → 注入子进程环境变量"这条路，不落盘。
