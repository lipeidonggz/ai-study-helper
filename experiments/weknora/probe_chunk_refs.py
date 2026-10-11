"""把 wiki_pages.chunk_refs 与 chunks 表做集合对账（悬空引用 / 覆盖率 / 每页分布）。

口径：
  · chunk_refs 是"页 → 块"的持久化引用（jsonb 数组，元素为 chunk UUID）
  · 对账 = (引用的 id 集合) − (该知识库 chunks 表里存在的 id 集合)
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKER = r"C:\Users\lida\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
KB_ID = "8990fb05-2432-40a0-b731-04e7fad995da"  # A5 standard 库


def psql_json(sql: str):
    out = subprocess.run(
        [DOCKER, "exec", "WeKnora-postgres", "psql", "-U", "postgres", "-d", "WeKnora", "-t", "-A", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
    )
    txt = out.stdout.strip()
    return json.loads(txt) if txt else None


def main() -> None:
    pages = psql_json(
        "select coalesce(json_agg(json_build_object('slug', slug, 'type', page_type, 'refs', coalesce(chunk_refs,'[]'::jsonb))), '[]') "
        f"from wiki_pages where knowledge_base_id='{KB_ID}' and deleted_at is null;"
    )
    kb_chunks = psql_json(
        "select coalesce(json_agg(id), '[]') from chunks where knowledge_base_id=%s;" % ("'" + KB_ID + "'")
    )
    chunk_ids = set(kb_chunks or [])

    all_refs: set[str] = set()
    per_page = []
    for p in pages or []:
        refs = p.get("refs") or []
        if isinstance(refs, str):
            refs = json.loads(refs)
        refs = [r for r in refs if r]
        per_page.append((p["slug"], p.get("type"), len(refs)))
        all_refs.update(refs)

    dangling = sorted(all_refs - chunk_ids)
    hist = Counter(n for _, _, n in per_page)

    print("页数 =", len(pages))
    print("有引用的页 =", sum(1 for _, _, n in per_page if n > 0))
    print("引用总数（含重复） =", sum(n for _, _, n in per_page))
    print("去重后的被引块 =", len(all_refs), " / 该库 chunks 总数 =", len(chunk_ids))
    print("覆盖率（被至少一页引用的块 / 全部块） = %.1f%%" % (len(all_refs & chunk_ids) / max(1, len(chunk_ids)) * 100))
    print("悬空引用（引用里指向不存在 chunk 的） =", len(dangling), dangling[:5])
    print("每页引用数分布（引用数:页数） =", dict(sorted(hist.items())))
    top = sorted(per_page, key=lambda x: -x[2])[:5]
    print("引用最多的 5 页 =", top)


if __name__ == "__main__":
    main()
