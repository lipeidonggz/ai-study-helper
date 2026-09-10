"""编译层（Compile）：把"清洗后原文"编译成对象图（概念 / 具体化陈述 / 边）。

流水线（见 docs/rag-storage-structure.html ④ 与 memory/0028 考古层第十九~二十一段）：
  Pass 1      抽取原子断言 + 逐字短锚
  Pass 2-①    按块抽实体 + 边（不去重、不分类）
  Pass 2-②    归并/消解（形合并 → 义合并 → 边 remap → 收尾清污）
  Pass 2-③    分类（reify_reasons + claim_type）
  A4          引文锚定（evidence_texts → chunk）
"""
