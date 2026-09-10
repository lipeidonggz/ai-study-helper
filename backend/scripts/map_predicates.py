"""把现有全部表面谓词映射到受控关系表（草案）——看表够不够、粒度对不对。

用法：
  cd backend && .venv\\Scripts\\python.exe -m scripts.map_predicates [--union 默认开]

输出：
  1. 覆盖：多少条边能落进表（按边数加权），多少条 unmapped / split_needed
  2. 每条关系各收多少种谓词、多少条边（看粒度是否合适、有没有空关系）
  3. unmapped 清单（这些是"该新增关系"还是"该拆句子"）
  4. 模型给出的"表缺口"建议
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.llm import DeepSeekLLMClient, LLMMessage  # noqa: E402
from app.compile.merge import merge_entities, provenance_for  # noqa: E402
from app.compile.merge_semantic import normalize_brackets  # noqa: E402
from app.compile.relations import RELATIONS  # noqa: E402
from app.storage.sqlite.kb_store import KbStore  # noqa: E402
from app.storage.sqlite.settings_store import SqliteSettingStore  # noqa: E402
from scripts.compile_slice_b6 import _chat_json  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
APP_DB = BACKEND / "data" / "app.db"
KB_DB = BACKEND / "data" / "kb.db"
TMP = BACKEND / "data" / "tmp"
DEFAULT_MODEL = "deepseek-chat"

SYSTEM = f"""你是知识图谱的「关系归一器」。把给定的{'{'}表面谓词{'}'}逐个映射到下面的受控关系表。

# 受控关系表
{chr(10).join(f"- {r}: {d}" for r, d in RELATIONS.items())}

# 规则
1. 每个表面谓词映射到**恰好一个**关系 id；同义/近义一律映射到同一条。
2. **不要硬套**。出现下列情况时必须如实标注，不要为了映射而映射：
   · `unmapped`：表里确实没有合适的关系；
   · `split_needed`：这个"谓词"其实是**句子被压进来了**（含宾语、从句、修饰），
     应该拆成「关系 + 宾语/字段」，不是一个关系词。
3. 轻动词（is/has/can/be 各形式）请映射到最贴近的关系（is / has_part / can …），
   不要标 unmapped。
4. 否定、时态、情态都不算差异：`can't inspect` → `visible_to`（否定走 polarity）；
   `previously protected against` → `protects_against`。
5. 只输出 JSON，不要解释文字、不要代码围栏。格式：
   {{"mapping": {{"<表面谓词>": "<关系id|unmapped|split_needed>", ...}},
     "notes": {{"<表面谓词>": "<一句话，仅对 unmapped/split_needed 或易混淆者>", ...}},
     "table_gaps": ["<建议新增的关系，含理由>", ...]}}

# 注意
· 表里只有**语义关系**。出处/归属边（asserted_by / grounded_in / has_statement / evidence_for）
  不在本表内，也不参与判同指——遇到这类表面谓词请按语义判断（例：`X is determined by Y` 是语义关系，不是出处边）。
"""


def load_predicates() -> Counter:
    entities, edges = [], []
    for t in ["step1g", "step1h", "step1i", "step1m"]:
        b = json.loads((TMP / f"b6_A5_{t}.json").read_text(encoding="utf-8"))
        entities += b["entities"]
        edges += b["edges"]
    cu = json.loads((TMP / "b6_A5_step1m.json").read_text(encoding="utf-8")).get("content_unit") or {}
    chunks = [c for c in KbStore(KB_DB).list_chunk_offsets("A5") if c.get("file_idx", 0) == 0]
    prov = provenance_for(sorted({e["name"].strip() for e in entities}), cu.get("text", ""), chunks)
    l1 = merge_entities(entities, edges, provenance=prov)
    renamed, _ = normalize_brackets(l1["entities"])
    l1 = merge_entities(renamed, l1["edges"], provenance=prov)
    return Counter((e.get("predicate") or "").strip().lower() for e in l1["edges"])


async def main() -> None:
    parser = argparse.ArgumentParser(description="表面谓词 → 受控关系表 映射")
    parser.add_argument("--batch", type=int, default=70)
    parser.add_argument("--out", default=str(TMP / "predicate_mapping.json"))
    args = parser.parse_args()

    counts = load_predicates()
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    total_edges = sum(counts.values())
    print(f"表面谓词 {len(items)} 种 / 边 {total_edges} 条")

    settings = SqliteSettingStore(APP_DB).get_llm_settings()
    if not settings.api_key:
        sys.exit("未配置大模型 API Key")
    client = DeepSeekLLMClient(api_key=settings.api_key, model=settings.model or DEFAULT_MODEL)

    mapping: dict[str, str] = {}
    notes: dict[str, str] = {}
    gaps: list[str] = []
    for i in range(0, len(items), args.batch):
        chunk = items[i : i + args.batch]
        listing = "\n".join(f"{p or '(空)'} ×{c}" for p, c in chunk)
        part, _ = await _chat_json(
            client,
            [LLMMessage(role="system", content=SYSTEM), LLMMessage(role="user", content=listing)],
            label=f"谓词映射 {i // args.batch + 1}",
        )
        mapping.update({k.strip().lower(): v for k, v in (part.get("mapping") or {}).items()})
        notes.update(part.get("notes") or {})
        gaps += part.get("table_gaps") or []

    by_rel: dict[str, list[tuple[str, int]]] = defaultdict(list)
    unmapped, split, missing = [], [], []
    for p, c in items:
        r = mapping.get(p)
        if r is None:
            missing.append((p, c))
        elif r == "unmapped":
            unmapped.append((p, c))
        elif r == "split_needed":
            split.append((p, c))
        else:
            by_rel[r].append((p, c))

    def edges(pairs):
        return sum(c for _, c in pairs)

    print("\n=== 覆盖（按边数加权）===")
    mapped_e = total_edges - edges(unmapped) - edges(split) - edges(missing)
    print(f"  落进表：{mapped_e} / {total_edges} 条边（{mapped_e / total_edges:.0%}）")
    print(f"  unmapped：{len(unmapped)} 种 / {edges(unmapped)} 条边 | split_needed：{len(split)} 种 / {edges(split)} 条边"
          f" | 模型漏答：{len(missing)} 种")

    print("\n=== 每条关系收了多少 ===")
    for r in RELATIONS:
        got = by_rel.get(r, [])
        surfaces = sorted(got, key=lambda kv: -kv[1])
        top = ", ".join(f"{p}×{c}" for p, c in surfaces[:6])
        print(f"  {r:<18} {len(got):>3} 种 / {edges(got):>4} 条   例：{top}")
    extra = [r for r in by_rel if r not in RELATIONS]
    if extra:
        print(f"  ⚠ 模型自造的关系 id：{extra}")

    print("\n=== unmapped（表里没合适关系）===")
    for p, c in sorted(unmapped, key=lambda kv: -kv[1]):
        print(f"  ×{c:<3} {p}    {notes.get(p, '')}")
    print("\n=== split_needed（谓词里压了句子/宾语，该拆）===")
    for p, c in sorted(split, key=lambda kv: -kv[1]):
        print(f"  ×{c:<3} {p}    {notes.get(p, '')}")
    if missing:
        print("\n=== 模型漏答 ===")
        for p, c in missing:
            print(f"  ×{c:<3} {p}")
    print("\n=== 模型建议的表缺口 ===")
    for g in gaps:
        print(f"  - {g}")

    Path(args.out).write_text(
        json.dumps({"mapping": mapping, "notes": notes, "table_gaps": gaps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n→ 映射结果落盘 {args.out}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
