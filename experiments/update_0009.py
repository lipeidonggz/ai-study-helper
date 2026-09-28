"""0009 权威副本：追加 RAG 检索机制升级条目 + 同步块数。"""

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

NEW_LI = (
    "        <li><strong>RAG v1.0 检索机制升级与评测驱动调校（2026-09-03~04，阶段 2）</strong>："
    "入库——叶子节标题进 chunk（文本三段式 = 来源行 + 叶子节标题 + 正文；只拼叶子不拼全路径、"
    "单段路径不拼、切块按整文件最坏叶子预留 token），四源重入库 260 块（A5 24 / O2 19 / O1 21 / OW1 196），"
    "own-002 答案块 dense rank 23 → 前 4；问答侧 workflow——点名单源/多源 → 每源宽候选（dense top-25）"
    "→ 组级门控 → 注入预算取证据块集（无位置窗口、无单核心，覆盖/预算信号进 trace）；"
    "BM25（rank_bm25 + jieba 内存索引，节标题拼入可检索文本）与本地 rerank（fastembed cross-encoder，"
    "jina-reranker-v2-base-multilingual）已实现为实验对照、默认 dense-only（本地 CPU rerank 实测约 3 pairs/s "
    "不可在线用，见 0025）；VectorStore 增加全量列举（list_all）；工具隔离——rag 模式 registry_for_mode "
    "剔除 note_add/get/search（note 空库结果覆盖注入的 own-002 修复）；评测——cmp-001 金标准重写为演进感知版"
    "（两文处于 harness 术语未收敛窗口期 + 可确证对比，修正\"时间线污染\"后见之明）；新增基准用例集首条 "
    "rag-cmp-002（用户完整性导向：核心主题全覆盖 + 扩展主题≥2 + 遗漏透明，tag=benchmark、must_pass=true）；"
    "判官提示词新增清单型金标准结构化核对与从严覆盖判定（点名关键概念或不可混淆等价描述，泛化不算）；"
    "验证——Run#107 B4 全量 10 条 20/20（a5-005 修复）、own-002 note 过滤后 20/20（Run#108）、"
    "cmp-001 新金标准 20/20（Run#109）、rag-cmp-002 判官从严 15/20（预期红，驱动检索升级）；"
    "判官自由裁决一致性（LLM judge 二合一执行不稳）与 faithfulness 抽样审计记待办下迭代；"
    "测试 146 全绿（新增 workflow 机制化、注册表按模式过滤、入库节标题单测）</li>"
)


def main() -> None:
    path = Path("memory/0009-current-features.html")
    text = path.read_text(encoding="utf-8")
    old_block_count = "已入库 O1/O2/A5/OW1 共 254 块"
    assert old_block_count in text
    text = text.replace(
        old_block_count,
        "已入库 O1/O2/A5/OW1 共 254 块（2026-09-04 叶子节标题进 chunk 重入库后 260 块）",
        1,
    )
    anchor = "（新增 manifest/切块/抽取/规则/Qdrant/点名检测/判官联动单测）</li>"
    assert anchor in text
    text = text.replace(anchor, anchor + "\n" + NEW_LI, 1)
    path.write_text(text, encoding="utf-8")
    print("0009 updated")


if __name__ == "__main__":
    main()
