"""验证 chunk 重叠是否真实存在、以及重建文本有没有把重叠去干净。

用 DB 里 A5 的 70 个文本块：
 1) 逐对检查「上一块的尾部」与「下一块的开头」的最长公共重叠；
 2) 把重叠全部保留地拼一遍（模拟"没去重"），与 knowledge 里存的原文长度对比。
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
        [DOCKER, "exec", "WeKnora-postgres", "psql", "-U", "postgres", "-d", "WeKnora", "-t", "-A", "-F", "\t", "-c", sql],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return out.stdout


def main() -> None:
    # 用 JSON 取，避开内容里的换行把行切碎
    raw_json = psql(
        "select coalesce(json_agg(json_build_object('i', chunk_index, 'c', content) order by chunk_index), '[]') "
        f"from chunks where knowledge_id='{KNOWLEDGE_ID}' and chunk_type='text';"
    )
    chunks = [(int(r["i"]), r["c"] or "") for r in json.loads(raw_json.strip())]

    print("chunks:", len(chunks), "total_chars(含重复):", sum(len(c) for _, c in chunks))

    # 1) 逐对最长后缀/前缀重叠
    overlaps = []
    for (i, a), (j, b) in zip(chunks, chunks[1:]):
        limit = min(len(a), len(b), 400)
        best = 0
        for k in range(limit, 11, -1):
            if a[-k:] == b[:k]:
                best = k
                break
        overlaps.append((i, j, best))
    with_ov = [o for o in overlaps if o[2] > 0]
    print("相邻对总数:", len(overlaps), "检测到重叠的对:", len(with_ov))
    print("重叠长度分布:", sorted(set(o[2] for o in with_ov)))
    print("重叠总字符:", sum(o[2] for o in with_ov))
    print("样例:", json.dumps(with_ov[:6], ensure_ascii=True))

    # 2) "不去重"的朴素拼接长度（就是各块长度之和）
    naive = sum(len(c) for _, c in chunks)
    raw = 28953  # 日志里 reconstruct 之后、截断之前的长度
    print("\n朴素拼接（含全部重复）:", naive)
    print("实际重建（日志 content_len raw）:", raw)
    print("差异（≈被去掉的重叠量）:", naive - raw)


if __name__ == "__main__":
    main()
