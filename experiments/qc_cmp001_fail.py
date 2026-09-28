"""Run#107 cmp-001 fail 现场：输出全文 + 工具调用 + 注入块构成（对照 pass）。"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")


def get(path: str):
    return json.loads(
        urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=60)
        .read()
        .decode("utf-8")
    )


def main() -> None:
    row = get("/api/eval/runs/107/cases/rag-cmp-001")
    rr = row.get("repeat_results") or []
    for idx in (2, 14, 0):
        r = rr[idx]
        verdict = r.get("verdict")
        print(
            f"\n\n########## repeat[{idx}] verdict={verdict} rounds={r.get('rounds')} "
            f"tool_calls={r.get('tool_calls')} elapsed={r.get('elapsed_ms')}ms ##########"
        )
        if verdict != "pass":
            jr = r.get("judge_reasons") or {}
            print("judge answer_correct:", json.dumps(jr.get("answer_correct"), ensure_ascii=False)[:400])
        out = r.get("output") or ""
        print(f"\n--- OUTPUT ({len(out)} chars) ---\n{out[:2600]}")
        trace = r.get("trace") or []
        for step in trace:
            if not isinstance(step, dict):
                continue
            if step.get("type") == "tool_exec":
                print(
                    "\n[tool]",
                    step.get("name"),
                    json.dumps(step.get("arguments"), ensure_ascii=False),
                    "->",
                    str(step.get("result"))[:200],
                )
            elif step.get("type") == "retrieval":
                d = step.get("data") or {}
                print("\n[retrieval]", d.get("reason"))
                for h in d.get("hits") or []:
                    print(
                        f"   {h.get('score'):.4f} | {(h.get('section_path') or '')[-75:]} "
                        f"| {h.get('text','')[:60]}"
                    )


if __name__ == "__main__":
    main()
