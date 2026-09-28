"""查概念表有没有"类别 / 上位节点"这个维度（第 3 条用例的根因核对）。

用法：backend\\.venv\\Scripts\\python.exe data\\tmp\\_concept_hierarchy_probe.py
只读，不动库。
"""

import sqlite3

conn = sqlite3.connect("backend/data/compile.db")

print("== 1. concept.type 取值分布 ==")
for r in conn.execute(
    "select type, count(*) from compile_concept group by type order by 2 desc"
):
    print("   ", r)

print("\n== 2. is_a 边原始记录（含端点缺失）==")
for r in conn.execute(
    """
    select st.id, st.subject_id, st.object_id,
           (select canonical from compile_concept where id = st.subject_id),
           (select canonical from compile_concept where id = st.object_id),
           st.polarity
    from compile_statement st
    where st.predicate = 'is_a'
    """
):
    print("   ", r)

print("\n== 3. 用 is 冒充类归属的例子（对象侧是类别词）==")
for r in conn.execute(
    """
    select s.canonical, o.canonical
    from compile_statement st
    join compile_concept s on s.id = st.subject_id
    join compile_concept o on o.id = st.object_id
    where st.predicate = 'is'
      and (o.canonical like '%surface%' or o.canonical like '%category%'
           or o.canonical like '%class%' or o.canonical like '%kind%'
           or o.canonical like '%type%' or o.canonical like '%layer%')
    """
):
    print("   ", r)

print("\n== 4. 防护类概念（看它们是否碎片化、有无上位）==")
rows = list(
    conn.execute(
        """
        select canonical from compile_concept
        where canonical like '%control%' or canonical like '%defen%'
           or canonical like '%mechanism%' or canonical like '%isolat%'
           or canonical like '%sandbox%' or canonical like '%protect%'
           or canonical like '%boundar%' or canonical like '%allowlist%'
           or canonical like '%permission%' or canonical like '%approval%'
        order by canonical
        """
    )
)
print(f"   碎片数 = {len(rows)}")
for r in rows:
    print("   ", r[0])
