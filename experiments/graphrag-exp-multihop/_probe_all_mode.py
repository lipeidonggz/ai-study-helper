"""Confirm what each of the four selection modes actually returns."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from graphrag.config.load_config import load_config
from graphrag.prompt_tune.loader.input import load_docs_in_chunks
from graphrag.prompt_tune.types import DocSelectionType

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    cfg = load_config(root_dir=ROOT)
    log = logging.getLogger("probe")
    for method in (DocSelectionType.ALL, DocSelectionType.TOP,
                   DocSelectionType.RANDOM, DocSelectionType.AUTO):
        chunks = await load_docs_in_chunks(
            config=cfg, select_method=method, limit=15, logger=log,
            n_subset_max=32, k=4,
        )
        print(f"  {str(method):7s} -> {len(chunks):4d} chunks")


if __name__ == "__main__":
    print("corpus has 141 chunks; limit=15, n_subset_max=32, k=4")
    asyncio.run(main())
