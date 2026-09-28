"""Call the real load_docs_in_chunks() and see which chunks it actually returns."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from graphrag.config.load_config import load_config
from graphrag.prompt_tune.loader.input import load_docs_in_chunks
from graphrag.prompt_tune.types import DocSelectionType

ROOT = Path(__file__).resolve().parent
DOCS = {f.name: f.read_text(encoding="utf-8", errors="ignore")
        for f in sorted((ROOT / "input").glob("*.txt"))}


def source_of(chunk: str) -> str:
    probe = chunk.replace("{{", "{").replace("}}", "}")[:70]
    for name, body in DOCS.items():
        if probe in body:
            return name[:11]
    return "??"


async def run(tag: str, n_subset_max: int, k: int, times: int) -> None:
    cfg = load_config(root_dir=ROOT)
    log = logging.getLogger("probe")
    print(f"=== {tag}  (n_subset_max={n_subset_max}, k={k}) ===")
    for i in range(1, times + 1):
        picked = await load_docs_in_chunks(
            config=cfg, select_method=DocSelectionType.AUTO, limit=15,
            logger=log, n_subset_max=n_subset_max, k=k,
        )
        srcs = [source_of(c) for c in picked]
        print(f"  run {i}: {len(picked)} chunks -> {srcs}")
    print()


async def main() -> None:
    # our experiment settings
    await run("OUR SETTINGS (auto + cramped subset)", 32, 4, 3)
    # neutralise the bug: n_subset_max >= corpus size (141)
    await run("CONTROL: n_subset_max >= N", 300, 4, 3)


if __name__ == "__main__":
    asyncio.run(main())
