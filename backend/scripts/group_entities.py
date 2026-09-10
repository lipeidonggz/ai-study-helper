"""实验：把实体按"临时实体卡"嵌入后分组，看分组质量与规模（不调用大模型）。

用法：
  cd backend && .venv\\Scripts\\python.exe -m scripts.group_entities --in <bundle.json> [--union ...]
  （可加 --show 3 看前几个组的成员）

看三件事：
  1. 已知为真的对（VM/virtual machine、NIST 两条、user oversight capacity…）是否落进同一组
  2. 组数是否可控（几十组量级）
  3. 组内噪声占比（"仅相关、非同指"的成员有多少——它们允许进组，最终由大模型否掉）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compile.merge import merge_entities, provenance_for  # noqa: E402
from app.compile.merge_semantic import (  # noqa: E402
    build_cards,
    group_by_similarity,
    normalize_brackets,
)
from app.storage.fastembed_store import FastEmbedEmbedder  # noqa: E402
from app.storage.sqlite.kb_store import KbStore  # noqa: E402

KB_DB = Path(__file__).resolve().parents[1] / "data" / "kb.db"
MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models"

# 已知"应为同指"的对（召回探针）
TRUE_PAIRS = [
    ("VM", "virtual machine"),
    ("EDR", "endpoint detection and response"),
    ("NIST AI agent identity and authorization project", "NIST's project on AI agent identity and authorization"),
    ("user oversight capacity", "user's capacity for oversight"),
    ("agent security posture", "agent-specific security posture"),
]
# 已知"绝不能合"的对（允许同组，但要计数；最终由大模型否掉）
FALSE_PAIRS = [
    ("Claude Code", "Claude Cowork"),
    ("AI", "agent identity"),
    ("containment", "isolation"),
    ("MCP", "MCP server"),
    ("sandbox", "container"),
    ("egress control", "egress proxy"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="实体卡向量分组实验")
    parser.add_argument("--in", dest="inputs", action="append", required=True)
    parser.add_argument("--union", action="store_true")
    parser.add_argument("--thresholds", default="0.90,0.92,0.94,0.96")
    parser.add_argument("--show", type=int, default=0, help="打印前 N 个组的成员")
    args = parser.parse_args()

    paths = [Path(p) if Path(p).is_absolute() else Path.cwd() / p for p in args.inputs]
    entities, edges, cu, sid = [], [], {}, ""
    for p in paths if args.union else paths[:1]:
        b = json.loads(p.read_text(encoding="utf-8"))
        entities += b.get("entities", [])
        edges += b.get("edges", [])
        cu = cu or (b.get("content_unit") or {})
        sid = sid or (b.get("source_id") or "")

    prov = {}
    if cu.get("text") and sid and KB_DB.exists():
        chunks = [c for c in KbStore(KB_DB).list_chunk_offsets(sid) if c.get("file_idx", 0) == 0]
        names = sorted({e["name"].strip() for e in entities if e.get("name")})
        prov = provenance_for(names, cu["text"], chunks)

    l1 = merge_entities(entities, edges, provenance=prov)
    renamed, _ = normalize_brackets(l1["entities"])
    l1 = merge_entities(renamed, l1["edges"], provenance=prov)
    cards = build_cards(l1["entities"], l1["edges"])
    texts = [c["card"] for c in cards]
    print(f"实体 {len(cards)} 个（① 原始 {len(entities)} → 形合并后）")
    print("卡片示例：")
    for c in cards[:3]:
        print(f"  {c['card']}")

    emb = FastEmbedEmbedder(cache_dir=str(MODEL_DIR))
    vectors = emb.embed(texts, batch_size=32)

    names = [c["name"] for c in cards]
    pos = {n: i for i, n in enumerate(names)}

    def same_group(groups, a: str, b: str) -> bool:
        if a not in pos or b not in pos:
            return False
        ia, ib = pos[a], pos[b]
        return any(ia in g and ib in g for g in groups)

    for th in [float(x) for x in args.thresholds.split(",")]:
        groups = group_by_similarity(vectors, th)
        multi = [g for g in groups if len(g) > 1]
        hit = [f"{a} ↔ {b}" for a, b in TRUE_PAIRS if same_group(groups, a, b)]
        miss = [f"{a} ↔ {b}" for a, b in TRUE_PAIRS if not same_group(groups, a, b)]
        noise = [f"{a} ↔ {b}" for a, b in FALSE_PAIRS if same_group(groups, a, b)]
        print(f"\n=== 阈值 {th:.2f} ===")
        print(
            f"  组数 {len(groups)}（成员>1 的组 {len(multi)}）| 最大组 {len(groups[0])} 个成员"
            f" | 进组的实体 {sum(len(g) for g in multi)}"
        )
        print(f"  已知同指命中 {len(hit)}/{len(TRUE_PAIRS)}" + (f"，漏 {miss}" if miss else ""))
        print(f"  仅相关对同组（允许，待大模型否掉）：{len(noise)}" + (f" → {noise}" if noise else ""))
        if args.show:
            for g in multi[: args.show]:
                print("   · " + "  |  ".join(names[i] for i in g))


if __name__ == "__main__":
    main()
