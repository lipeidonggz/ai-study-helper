"""第 3 条用例：防护类谓词的对象侧到底长什么样（"靠对象侧"这句话的核对）。

用法：backend\\.venv\\Scripts\\python.exe data\\tmp\\_defense_objects_probe.py
只读。
"""

import json
import sqlite3

conn = sqlite3.connect("backend/data/compile.db")
FAMILY = (
    "protects_against",
    "blocks",
    "detects",
    "isolates",
    "enforces",
    "monitors",
    "constrains",
    "threatens",
)

rows = list(
    conn.execute(
        f"""
        select st.id, s.canonical, st.predicate, o.canonical, st.evidence_texts_json
        from compile_statement st
        left join compile_concept s on s.id = st.subject_id
        left join compile_concept o on o.id = st.object_id
        where st.predicate in ({','.join('?' * len(FAMILY))})
        order by st.predicate, st.id
        """,
        FAMILY,
    )
)

for stmt_id, subj, pred, obj, ev in rows:
    try:
        ev_list = json.loads(ev) if ev else []
    except Exception:
        ev_list = []
    first_ev = (ev_list[0] if ev_list else "").replace("\n", " ")
    first_ev = first_ev[:110]
    print(f"{stmt_id}  [{pred}]")
    print(f"    subj : {subj}")
    print(f"    obj  : {obj}")
    print(f"    ev   : {first_ev}")
