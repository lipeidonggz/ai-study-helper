import pandas as pd
from graphrag.config.defaults import DEFAULT_ENTITY_TYPES
print("配置里的 entity_types（我们用默认值）:", DEFAULT_ENTITY_TYPES)
print("插进提示词的形态:", "[" + ",".join(DEFAULT_ENTITY_TYPES) + "]")
e = pd.read_parquet("data/tmp/graphrag-exp-juejin/output/entities.parquet")
print("\nv4 实体类型分布:")
print(e["type"].value_counts().to_string())
print("\nEVENT 桶里的实体（按 degree 降序，前 20）:")
ev = e[e["type"] == "EVENT"].sort_values("degree", ascending=False)
for _, r in ev.head(20).iterrows():
    print(f"   {r['title'][:42]:<44} deg={r['degree']}")
