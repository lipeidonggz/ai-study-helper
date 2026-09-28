"""抽查 rag-cmp-002 pass 样本的判官核对表与输出关键概念。"""

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
    row = get("/api/eval/runs/112/cases/rag-cmp-002")
    rr = row.get("repeat_results") or []
    for idx in (1, 8):
        r = rr[idx]
        print(f"===== [{idx}] verdict={r.get('verdict')} =====")
        jr = r.get("judge_reasons") or {}
        print("JUDGE:", json.dumps(jr.get("answer_correct"), ensure_ascii=False)[:1700])
        out = r.get("output") or ""
        marks = [
            k
            for k in (
                "AGENTS",
                "ARCHITECTURE",
                "Plain Text",
                "外部内容",
                "external",
                "三层",
                "三类风险",
                "Cowork",
                "local VM",
                "MCP",
            )
            if k.lower() in out.lower()
        ]
        print("OUTPUT 关键概念命中:", marks)


if __name__ == "__main__":
    main()
