"""own-002 非 pass repeat 的完整 trace：工具调用与各步骤顺序。"""

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


def dump_step(step: dict, idx: int) -> None:
    stype = step.get("type")
    data = step.get("data") or step
    print(f"\n--- step[{idx}] type={stype} ---")
    if stype in ("tool_call", "tool_result", "tool"):
        print(json.dumps(data, ensure_ascii=False)[:1000])
    elif stype == "retrieval":
        d = data.get("data") or data
        print("reason:", d.get("reason"))
        for h in (d.get("hits") or [])[:3]:
            print("  hit:", h.get("score"), (h.get("section_path") or "")[-50:])
    else:
        print(json.dumps(data, ensure_ascii=False)[:400])


def main() -> None:
    row = get("/api/eval/runs/107/cases/rag-own-002")
    rr = row.get("repeat_results") or []
    for idx in (14, 16):
        r = rr[idx]
        print(
            f"\n########## repeat[{idx}] verdict={r.get('verdict')} "
            f"rounds={r.get('rounds')} tool_calls={r.get('tool_calls')} "
            f"elapsed={r.get('elapsed_ms')}ms ##########"
        )
        trace = r.get("trace") or []
        for i, step in enumerate(trace):
            if isinstance(step, dict):
                dump_step(step, i)


if __name__ == "__main__":
    main()
