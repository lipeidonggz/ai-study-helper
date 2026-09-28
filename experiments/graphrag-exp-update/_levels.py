"""三份产物的社区层级分布（用于「层级粒度不可比」的实测数字）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ts = sorted(p.name for p in (ROOT / "update_output").iterdir() if p.is_dir())[0]
    for tag, p in [
        ("previous", ROOT / "update_output" / ts / "previous"),
        ("delta", ROOT / "update_output" / ts / "delta"),
        ("合并后", ROOT / "output"),
    ]:
        c = pd.read_parquet(p / "communities.parquet")
        dist = c["level"].value_counts().sort_index().to_dict()
        roots = int((c["parent"] < 0).sum())
        print(f"{tag:<8} 社区 {len(c):>3}   level 分布 {dist}   根节点 {roots}")

    r = pd.read_parquet(ROOT / "output" / "community_reports.parquet")
    print("合并后报告 level 分布:", r["level"].value_counts().sort_index().to_dict())
    print("合并后报告编号:", sorted(int(x) for x in r["community"]))
    print("合并后报告标题（全部）:")
    for _, x in r.sort_values(["level", "community"]).iterrows():
        tag = "旧" if int(x["community"]) < 14 else "新"
        print(f"  [{tag}] #{int(x['community']):<3} L{int(x['level'])}  {str(x['title'])[:48]}")


if __name__ == "__main__":
    main()
