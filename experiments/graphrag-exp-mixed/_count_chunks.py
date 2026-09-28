"""按 GraphRAG 的方式切块，数出总块数与中英文分布（决定 prompt-tune 的 --limit）。"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from pathlib import Path

from graphrag.config.load_config import load_config
from graphrag_chunking.chunker_factory import create_chunker
from graphrag_input import create_input_reader
from graphrag_storage import create_storage

from graphrag.index.workflows.create_base_text_units import chunk_document
from graphrag.tokenizer.get_tokenizer import get_tokenizer

ROOT = Path(__file__).resolve().parent
CJK = re.compile(r"[\u4e00-\u9fff]")


async def main() -> None:
    cfg = load_config(ROOT)
    tokenizer = get_tokenizer(model_config=cfg.get_completion_model_config(
        cfg.extract_graph.completion_model_id))
    chunker = create_chunker(cfg.chunking, tokenizer.encode, tokenizer.decode)
    reader = create_input_reader(cfg.input, create_storage(cfg.input_storage))
    docs = await reader.read_files()

    total = 0
    print("每篇的块数与语言：")
    for d in docs:
        d = dataclasses.asdict(d)
        chunks = chunk_document(d, chunker)
        zh = sum(1 for c in chunks if CJK.search(c))
        total += len(chunks)
        print(f"  {d.get('title', '?')[:44]:<46} 块 {len(chunks):>3}  含中文块 {zh:>3}")
    print(f"\n总块数 = {total}   ⇒ prompt-tune 要传 --limit {total}（默认 15 会越界崩溃）")


if __name__ == "__main__":
    asyncio.run(main())
