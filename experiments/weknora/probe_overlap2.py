"""细查相邻块：是"真没重叠"，还是"有重叠但边界空白/分隔符不同导致精确匹配失败"。

对每一对相邻块，打印：
  - 上一块的尾 60 字符 / 下一块的头 60 字符（可见空白用 ␊ 标出）
  - 去掉首尾空白后再做后缀/前缀匹配，看能不能命中
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKER = r"C:\Users\lida\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
KNOWLEDGE_ID = "11b50d63-ebf4-4e4c-9005-2e30fe1df2bc"


def psql(sql: str) -> str:
    out = subprocess.run(
        [DOCKER, "exec", "WeKnora-postgres", "psql", "-U", "postgres", "-d", "WeKnora", "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return out.stdout


def vis(s: str) -> str:
    return s.replace("\n", "␊").replace("\t", "␉")


def best_overlap(a: str, b: str, limit: int = 400, floor: int = 12) -> int:
    for k in range(min(len(a), len(b), limit), floor - 1, -1):
        if a[-k:] == b[:k]:
            return k
    return 0


def main() -> None:
    raw = psql(
        "select coalesce(json_agg(json_build_object('i', chunk_index, 'c', content) order by chunk_index), '[]') "
        f"from chunks where knowledge_id='{KNOWLEDGE_ID}' and chunk_type='text';"
    )
    chunks = [(int(r["i"]), r["c"] or "") for r in json.loads(raw.strip())]

    strict = trimmed = 0
    examples = []
    for (i, a), (j, b) in zip(chunks, chunks[1:]):
        k1 = best_overlap(a, b)
        k2 = best_overlap(a.strip(), b.strip())
        if k1:
            strict += 1
        elif k2:
            trimmed += 1
            if len(examples) < 4:
                examples.append((i, j, k1, k2, vis(a[-50:]), vis(b[:50])))

    print(f"严格（原样）匹配到重叠: {strict} 对")
    print(f'仅「去首尾空白后」匹配到重叠: {trimmed} 对')
    print("→ 说明后者是「有重叠但边界空白不同」；若为 0，则那些对是真的没给重叠\n")
    for i, j, k1, k2, ta, hb in examples:
        print(f"--- chunk {i} → {j}  (strict={k1}, trimmed={k2}) ---")
        print("  尾:", ta)
        print("  头:", hb)


if __name__ == "__main__":
    main()
