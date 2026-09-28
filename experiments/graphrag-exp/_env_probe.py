"""环境探针：直接读**已安装**的 graphrag 3.2.0，核对写 settings.yaml 需要的字段。

为什么不用读 GitHub：线上 main 可能与装到的 3.2.0 有差异；写配置前先跟本地实物对齐。
只读，无副作用。
"""

from __future__ import annotations

import inspect


def show(title: str) -> None:
    print(f"\n===== {title}")


import graphrag

show("版本")
print("graphrag", getattr(graphrag, "__version__", "?"))
for mod in ("graphrag_llm", "graphrag_vectors", "graphrag_storage", "graphrag_chunking"):
    try:
        m = __import__(mod)
        print(f"{mod:<20} {getattr(m, '__version__', '?')}")
    except Exception as exc:  # noqa: BLE001
        print(f"{mod:<20} 导入失败: {exc}")

show("graspologic_native")
import graspologic_native as gn

print("OK ->", [n for n in dir(gn) if not n.startswith("_")][:8])

show("配置默认值（本地实物）")
from graphrag.config import defaults as d

print("DEFAULT_ENTITY_TYPES   :", d.DEFAULT_ENTITY_TYPES)
print("DEFAULT_COMPLETION_MODEL:", d.DEFAULT_COMPLETION_MODEL)
print("DEFAULT_EMBEDDING_MODEL:", d.DEFAULT_EMBEDDING_MODEL)
for cls_name in ("ChunkingDefaults", "ClusterGraphDefaults", "CommunityReportDefaults"):
    cls = getattr(d, cls_name, None)
    if cls is None:
        continue
    try:
        inst = cls()
    except Exception:  # 有的 dataclass 需要参数
        inst = None
    if inst is not None:
        print(
            f"{cls_name:<24}:",
            {k: getattr(inst, k) for k in vars(inst)},
        )
    else:
        print(f"{cls_name:<24}:", inspect.signature(cls))

show("GraphRagConfig 顶层字段")
from graphrag.config.models.graph_rag_config import GraphRagConfig

fields = GraphRagConfig.model_fields
for name, info in fields.items():
    req = "必填" if info.is_required() else f"可选 default={info.default!r}"
    print(f"  {name:<26} {req}")

show("已注册的工作流名（决定 workflows 列表能写什么）")
from graphrag.index.workflows import PipelineFactory

names = sorted(PipelineFactory().keys()) if hasattr(PipelineFactory(), "keys") else None
print("PipelineFactory 类型:", type(PipelineFactory))
for attr in ("_registry", "registry", "_workflows", "workflows"):
    got = getattr(PipelineFactory, attr, None)
    if isinstance(got, dict):
        print(f"  from {attr}: {sorted(got)}")
        break
else:
    print("  未找到注册表属性，打印可用属性:", [a for a in dir(PipelineFactory) if not a.startswith("__")])
