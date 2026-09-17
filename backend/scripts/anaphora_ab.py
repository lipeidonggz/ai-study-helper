"""S4.5 字段顺序 A/B 实验（单变量：只改输出字段顺序，其余一字不动）。

用法：cd backend && .venv\\Scripts\\python.exe -m scripts.anaphora_ab --source A5
产物：backend/data/tmp/anaphora_ab_<源>_<variant>.json（含逐条结果，供人工看）

看三个指标：
  ① evidence 逐字出现在窗口里的比例（**程序可查**，是"有没有真在原文里找依据"的硬指标）
  ② 三态分布（尤其 unresolved / not_anaphora 的比例——B 若真的"更敢说判不出"会体现在这里）
  ③ 未回答（模型漏条）与批失败数
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.agent.llm import DeepSeekLLMClient
from app.compile.anaphora import apply_evidence_guard, evidence_verbatim, find_targets, resolve_targets
from app.kb.ingest import _file_units
from app.kb.manifest import parse_manifest
from app.storage.sqlite.settings_store import SqliteSettingStore
from scripts.compile_slice_b6 import APP_DB, MANIFEST_PATH, _build_clean_t

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "tmp"


async def main() -> None:
    ap = argparse.ArgumentParser(description="S4.5 字段顺序 A/B")
    ap.add_argument("--source", default="A5")
    ap.add_argument("--variants", default="A,B")
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--model", default="deepseek-chat")
    ap.add_argument("--out-tag", default="", help="产物文件名后缀（区分同名变体的多次跑，如窗口改版）")
    ap.add_argument("--claims-file", default="", help="固定输入：用指定 claims.json（金标准打分必须对固定输入）")
    ap.add_argument("--temperature", type=float, default=None, help="采样温度；不传=服务端默认 1.0")
    ap.add_argument("--rounds", type=int, default=1, help="同配置重复几遍（看稳定性）")
    args = ap.parse_args()

    settings = SqliteSettingStore(APP_DB).get_llm_settings()
    if not settings.api_key:
        sys.exit("[S4.5] 未配置大模型 API Key（设置面板配置）")
    model = args.model or settings.model or "deepseek-chat"

    src = next(s for s in parse_manifest(MANIFEST_PATH) if s.source_id == args.source)
    _title, sections = _file_units(src)[0]
    clean_t = _build_clean_t(sections)
    cf = Path(args.claims_file) if args.claims_file else (OUT_DIR.parent / "compile" / args.source / "claims.json")
    claims = json.loads(cf.read_text(encoding="utf-8"))
    print(f"[S4.5] 输入 claims：{cf}（{len(claims)} 条）")

    targets = find_targets(claims, clean_t)
    kinds: dict[str, int] = {}
    for t in targets:
        kinds[f"{t['position']}/{t['kind']}"] = kinds.get(f"{t['position']}/{t['kind']}", 0) + 1
    print(f"[S4.5] {args.source}：入库 claim {len(claims)} 条 → 待消解目标 {len(targets)} 个 {kinds}")

    client = DeepSeekLLMClient(api_key=settings.api_key, model=model, temperature=args.temperature)
    win = {(t["claim_idx"], t["position"]): t["window"] for t in targets}
    all_stats: dict[str, dict] = {}
    rounds: dict[str, list[dict]] = {}          # 每遍的逐条结果（看稳定性用）
    for variant in [v.strip() for v in args.variants.split(",") if v.strip()]:
        for rd in range(1, args.rounds + 1):
            label = f"{variant}#{rd}" if args.rounds > 1 else variant
            out = await resolve_targets(client, args.source, targets, variant=variant, batch=args.batch)
            # 「依据逐字」按修正口径重算：归一化匹配 + **空依据算不通过**
            for r in out["results"]:
                r["evidence_verbatim"] = evidence_verbatim(r.get("evidence") or "", win[(r["claim_idx"], r["position"])])
            raw = dict(out["stats"])
            raw["evidence_verbatim"] = sum(1 for r in out["results"] if r["evidence_verbatim"])
            raw["empty_evidence"] = sum(1 for r in out["results"] if not (r.get("evidence") or "").strip())
            guard = apply_evidence_guard(out["results"], targets)      # 程序护栏：依据非逐字 → 降 unresolved
            all_stats[label] = {"llm": raw, "guarded": {**raw, **guard}}
            rounds[label] = out["results"]
            tag = f"{args.out_tag}_{variant}{'' if args.rounds == 1 else '_r' + str(rd)}" if args.out_tag else label
            p = OUT_DIR / f"anaphora_ab_{args.source}_{tag}.json"
            p.write_text(
                json.dumps({"stats": guard, "llm_stats": raw, "targets": targets, "results": out["results"]},
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            print(f"[S4.5] {label} → {p.name}")
            print(f"        LLM 原始：三态 {raw['resolved']}/{raw['unresolved']}/{raw['not_anaphora']}"
                  f"、依据逐字 {raw['evidence_verbatim']}/{raw['targets']}（空证据 {raw['empty_evidence']}）"
                  f" | 过护栏：resolved {guard['resolved']}、unresolved {guard['unresolved']}（降级 {guard['demoted']}）")

    if args.rounds > 1 and len(rounds) > 1:
        print("\n=== 稳定性：同输入多遍的逐条一致率 ===")
        for label, rows in rounds.items():
            base = next(iter(rounds.values()))
            bm = {(r["claim_idx"], r["position"]): r for r in base}
            same = sum(
                1 for r in rows
                if bm.get((r["claim_idx"], r["position"]), {}).get("status") == r["status"]
            )
            print(f"  {label}: 与首遍三态一致 {same}/{len(rows)} = {same / len(rows):.3f}")

    print("\n=== A/B 指标对比 ===")
    keys = ["targets", "answered", "resolved", "unresolved", "not_anaphora", "evidence_verbatim", "empty_evidence", "batches", "failed_batches"]
    print("指标".ljust(18) + "".join(v.rjust(10) for v in all_stats))
    for k in keys:
        print(k.ljust(18) + "".join(str(all_stats[v]["llm"].get(k, "-")).rjust(10) for v in all_stats))

    if set(all_stats) == {"A", "B"}:          # 只有跑 A/B 两版且没加 tag 时才做逐条对照
        a = json.loads((OUT_DIR / f"anaphora_ab_{args.source}_A.json").read_text(encoding="utf-8"))
        b = json.loads((OUT_DIR / f"anaphora_ab_{args.source}_B.json").read_text(encoding="utf-8"))
        ra = {(r["claim_idx"], r["position"]): r for r in a["results"]}
        rb = {(r["claim_idx"], r["position"]): r for r in b["results"]}
        diff = [k for k in ra if k in rb and (ra[k]["status"], ra[k]["resolution"]) != (rb[k]["status"], rb[k]["resolution"])]
        print(f"\n两版不一致 {len(diff)} 条：")
        for k in diff:
            print(f"  claim[{k[0]}].{k[1]}  代词={ra[k]['anaphor']}")
            print(f"     A: {ra[k]['status']} → {ra[k]['resolution']}")
            print(f"     B: {rb[k]['status']} → {rb[k]['resolution']}")


if __name__ == "__main__":
    asyncio.run(main())
