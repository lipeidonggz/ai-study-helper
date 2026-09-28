"""Does a mismatched few-shot example leak its content into an unrelated extraction?

Three prompt variants, same entity types, same test chunks — only the -Examples- block differs:
  A  mismatched : text=chunk_a -> output=<another chunk's real extraction>   (x2, same output)
  B  matched    : text=chunk_a -> output=<chunk_a's own real extraction>
  C  no examples: -Examples- section removed

Then run each variant on several HELD-OUT chunks and measure:
  - n_entities
  - grounded rate  : entity name appears in the chunk's own text
  - leak rate      : entity name appears in the *demonstration* output but NOT in the input
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

from graphrag.config.load_config import load_config
from graphrag_chunking.chunker_factory import create_chunker
from graphrag_input import create_input_reader
from graphrag_llm.completion import create_completion
from graphrag_llm.embedding import create_embedding
from graphrag_storage import create_storage
from graphrag.index.workflows.create_base_text_units import chunk_document
from graphrag.prompt_tune.template.extract_graph import GRAPH_EXTRACTION_PROMPT

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
APP_DB = REPO / "backend" / "data" / "app.db"
ENTITY_TYPES = ("technology product, technology company, brand, technology platform, "
                "hardware component, software feature, professional sports league, "
                "sports team, athlete, live sports event")
EXAMPLE_CHUNKS = (0, 1)          # 示例里的两段 text 取自这两块
MISMATCH_SOURCE = 15             # 错配臂的 output 统一取自这一块（Inter Miami 那篇）
TEST_CHUNKS = (60, 95, 130)      # 留出块（与上面都不相干）
REPEATS = 3
ENT_RE = re.compile(r'\("entity"<\|>\s*([^<|]+?)\s*<\|>')


def read_key() -> str:
    con = sqlite3.connect(f"file:{APP_DB}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    return json.loads(row[0])["api_key"]


async def get_chunks() -> list[str]:
    cfg = load_config(root_dir=ROOT)
    model = create_embedding(cfg.get_embedding_model_config(cfg.embed_text.embedding_model_id))
    tok = model.tokenizer
    chunker = create_chunker(cfg.chunking, tok.encode, tok.decode)
    reader = create_input_reader(cfg.input, create_storage(cfg.input_storage))
    out: list[str] = []
    for doc in await reader.read_files():
        out.extend(chunk_document(dataclasses.asdict(doc), chunker))
    return out


def render(example_text: str, chunk: str, model) -> str:
    prompt = GRAPH_EXTRACTION_PROMPT.format(
        entity_types=ENTITY_TYPES, examples=example_text, language="English")
    assert "{input_text}" in prompt, "placeholder not found — check template escaping"
    return prompt.replace("{input_text}", chunk)


def example_block(n: int, text: str, output: str) -> str:
    return (f"Example {n}:\n\nentity_types: [{ENTITY_TYPES}]\ntext:\n{text}\n"
            "------------------------\n"
            f"output:\n{output}\n#############################\n\n")


async def extract(model, prompt: str) -> str:
    resp = await model.completion_async(messages=prompt)
    return resp.content


def parse_entities(text: str) -> list[str]:
    return [e.strip() for e in ENT_RE.findall(text)]


async def main() -> int:
    key = read_key()
    os.environ["DEEPSEEK_API_KEY"] = key
    cfg = load_config(root_dir=ROOT)
    model = create_completion(cfg.get_completion_model_config("default_completion_model"))
    chunks = await get_chunks()
    print(f"corpus: {len(chunks)} chunks")

    if os.environ.get("PROBE_DRY") == "1":
        print("type of one chunk:", type(chunks[0]).__name__)
        demo = example_block(1, chunks[0][:200], '("entity"<|>DEMO<|>brand<|>demo entity)')
        for name, ex in (("A", demo), ("C", "")):
            p = render(ex, chunks[60], model)
            seg = p[p.find("-Examples-"):p.find("-Real Data-")]
            print(f"\n--- variant {name}: prompt {len(p)} chars; Example count={seg.count('Example ')}")
            print("   example section head:", seg[:220].replace("\n", " | "))
            tail = p[p.find("-Real Data-"):][:200].replace("\n", " | ")
            print("   real-data section   :", tail)
        return 0

    # 1) 先用【无示例】分别抽 3 个块，拿到"真实"输出（用于正配臂与错配臂）
    plain = GRAPH_EXTRACTION_PROMPT.format(
        entity_types=ENTITY_TYPES, examples="", language="English")
    assert "{input_text}" in plain
    real: dict[int, str] = {}
    for idx in (EXAMPLE_CHUNKS[0], EXAMPLE_CHUNKS[1], MISMATCH_SOURCE):
        p = plain.replace("{input_text}", chunks[idx])
        real[idx] = await extract(model, p)
        print(f"  baseline extraction of chunk {idx}: {len(parse_entities(real[idx]))} entities")

    mism_out = real[MISMATCH_SOURCE]
    variants = {
        "A 错配": (example_block(1, chunks[EXAMPLE_CHUNKS[0]], mism_out)
                   + example_block(2, chunks[EXAMPLE_CHUNKS[1]], mism_out)),
        "B 正配": (example_block(1, chunks[EXAMPLE_CHUNKS[0]], real[EXAMPLE_CHUNKS[0]])
                   + example_block(2, chunks[EXAMPLE_CHUNKS[1]], real[EXAMPLE_CHUNKS[1]])),
        "C 无示例": "",
    }
    leak_markers = [e.lower() for e in parse_entities(mism_out)]
    print(f"\nmismatched-output markers ({len(leak_markers)}): {leak_markers[:8]} ...\n")

    rows = []
    for vname, ex in variants.items():
        for t in TEST_CHUNKS:
            prompt = render(ex, chunks[t], model)
            low = chunks[t].lower()
            tasks = [extract(model, prompt) for _ in range(REPEATS)]
            outs = await asyncio.gather(*tasks)
            for o in outs:
                ents = parse_entities(o)
                low_ents = [e.lower() for e in ents]
                grounded = sum(1 for e in low_ents if e in low)
                leaked = sum(1 for e in low_ents if e not in low and e in set(leak_markers))
                rows.append((vname, t, len(ents), grounded, leaked))

    print("=== results (per run) ===")
    print(f"{'arm':10s} {'chunk':>6s} {'entities':>9s} {'grounded':>9s} {'leaked':>7s}")
    for r in rows:
        print(f"{r[0]:10s} {r[1]:6d} {r[2]:9d} {r[3]:9d} {r[4]:7d}")

    print("\n=== aggregate per arm ===")
    for vname in variants:
        sub = [r for r in rows if r[0] == vname]
        n = sum(r[2] for r in sub)
        g = sum(r[3] for r in sub)
        l = sum(r[4] for r in sub)
        print(f"  {vname:10s}: entities={n:4d}  grounded={g:4d} ({g/n if n else 0:5.1%})"
              f"  leaked={l:3d} ({l/n if n else 0:5.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
