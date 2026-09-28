"""只读探针：看我们自己的编译产物里 A5 / O2 的规模。

用途：评估"拿 A5/O2 去跑 GraphRAG 能看到什么"时，需要一个对照基线——
我们的编译器在同样两篇文章上抽出了多少实体 / 断言 / chunk。
只读打开（mode=ro），不写任何东西。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DBS = ["backend/data/compile.db", "backend/data/kb.db"]
SOURCES = ("A5", "O2")


def tables(con: sqlite3.Connection) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "select name from sqlite_master where type='table' order by name"
        )
    ]


def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f'pragma table_info("{table}")')]


def main() -> None:
    for db in DBS:
        if not Path(db).exists():
            print(f"skip (missing): {db}")
            continue
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        print(f"===== {db}")
        for t in tables(con):
            cols = columns(con, t)
            n = con.execute(f'select count(*) from "{t}"').fetchone()[0]
            print(f"  {t:<24} rows={n:>8,}   cols={cols}")
            # 对形如 source_id 的列按 A5/O2 分组统计
            key = next(
                (c for c in ("source_id", "source", "doc_id", "document_id") if c in cols),
                None,
            )
            if key is None or n == 0:
                continue
            rows = con.execute(
                f'select "{key}", count(*) from "{t}" group by 1 order by 2 desc'
            ).fetchall()
            shown = [r for r in rows if r[0] in SOURCES]
            top = rows[:6]
            print(f"    by {key}: total_groups={len(rows)}  top={top}")
            if shown:
                print(f"    A5/O2 -> {shown}")
        con.close()
        print()


if __name__ == "__main__":
    main()
