"""体检（质量门）命令行：逻辑在 app/compile/health.py，这里只做输入输出。

用法：
  cd backend && .venv\\Scripts\\python.exe -m scripts.health_check --source A5 \
      --claims data/tmp/b6_claims_A5_p1v7a.json [--doc-subject Anthropic] [--gold] [--out 路径]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.compile.health import run_health_check  # noqa: E402
from app.kb.manifest import parse_manifest  # noqa: E402
from scripts.compile_slice_b6 import MANIFEST_PATH, _build_clean_t, _file_units  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="编译层体检（质量门）")
    ap.add_argument("--source", default="A5")
    ap.add_argument("--claims", required=True)
    ap.add_argument("--doc-subject", default="", help="元主语改写成的文档主体（默认从 manifest 推）")
    ap.add_argument("--no-gold", action="store_true", help="跳过召回检查")
    ap.add_argument("--out", default="", help="过滤后 claim 的落盘路径")
    args = ap.parse_args()

    claims_path = Path(args.claims)
    claims = json.loads(claims_path.read_text(encoding="utf-8"))
    src = next(s for s in parse_manifest(MANIFEST_PATH) if s.source_id == args.source)
    _ft, sections = _file_units(src)[0]
    clean_t = _build_clean_t(sections)

    kept, report = run_health_check(
        claims, clean_t, args.source, doc_subject=args.doc_subject, gold=not args.no_gold
    )

    print(f"===== 体检报告：{args.source} · {claims_path.name} =====")
    print(f"claim {report['claims_in']} 条 → 保留 {report['claims_out']} 条"
          f"（丢弃 {len(report['dropped'])}、改写 {len(report['rewritten'])}、标记 {len(report['marked'])}）")
    for k, v in report["checks"].items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)}")
    rc = report["recall"]
    if rc.get("executed"):
        print(f"召回（{rc['gold']}）：命中 {rc['hit']}/{rc['items']}；must {rc['must_hit']}/{rc['must_total']}"
              + (f"，漏：{rc['missing_must']}" if rc["missing_must"] else ""))
    else:
        print(f"召回：{rc['note']}")
    print("红线判定：")
    for k, v in report["verdict"].items():
        print(f"  {'✅' if v['pass'] else '❌'} {k}: {v['value']:.1%}（阈值 {v['threshold']:.0%}）")
    print(f"→ 总判定：{'通过 ✅' if report['red_line_pass'] else '不过 ❌（该篇编译失败，需人工介入）'}")
    for d in report["dropped"][:8]:
        print(f"  丢弃 [{d['idx']}] {d['subject']} / {d['predicate']}  ← {'；'.join(d['reasons'])}")

    out = Path(args.out) if args.out else claims_path.with_name(claims_path.stem + "_checked.json")
    out.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_name(out.stem + "_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"→ 过滤后 claim：{out}")


if __name__ == "__main__":
    main()
