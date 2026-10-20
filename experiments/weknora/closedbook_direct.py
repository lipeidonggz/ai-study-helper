#!/usr/bin/env python
"""干净版闭卷基线：绕过 WeKnora，直接调模型 API。

与 closedbook_eval.py（走 WeKnora、agent 无工具）的区别：
- 不带任何系统提示（WeKnora 那版会注入系统级提示与工具约定，pro 会反复吐 search_knowledge 标记）；
- 只发一条 user 消息：题目 ＋ 与主臂完全相同的答案格式要求；
- 直接读 stream=false 的 JSON，答案就是模型输出。

密钥获取：WeKnora 把模型凭据 AES-256-GCM 加密存在 Postgres 的 models.parameters.api_key，
本脚本运行时从容器 DB 取密文、用 WeKnora/.env 的 SYSTEM_AES_KEY 解密，
**密钥只在内存里，不落盘、不打印**。

用法：
    python experiments/weknora/closedbook_direct.py run --models flash,pro --rounds 3
    python experiments/weknora/closedbook_direct.py score
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import weknora_qa_eval as ev  # noqa: E402

WEKNORA_ENV = Path(r"D:\lida-data\vscode\WeKnora\.env")
PG_CONTAINER = "WeKnora-postgres"
MODELS = {
    "flash": ("deepseek-flash", "4b0ab03b-be54-4658-a3f0-5338db734402"),
    "pro": ("deepseek-v4-pro", "ee081caf-43b9-4870-ad26-847cacbbd904"),
}
ANSWER_STYLE = ev.ANSWER_STYLE


def system_aes_key() -> bytes:
    for line in WEKNORA_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("SYSTEM_AES_KEY="):
            k = line.split("=", 1)[1].strip().strip('"').strip("'")
            if len(k) != 32:
                raise SystemExit(f"SYSTEM_AES_KEY 长度 {len(k)}，不是 32 字节")
            return k.encode()
    raise SystemExit("WeKnora/.env 里没有 SYSTEM_AES_KEY")


def decrypt(enc: str, key: bytes) -> str:
    """对齐 WeKnora internal/utils/crypto.go：enc:v1: + base64url(nonce||ciphertext)。"""
    if not enc.startswith("enc:v1:"):
        return enc
    raw = base64.urlsafe_b64decode(enc[len("enc:v1:"):] + "=" * (-len(enc[len("enc:v1:"):]) % 4))
    return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode()


def model_credential(model_id: str) -> tuple[str, str]:
    """返回 (base_url, api_key)。"""
    sql = (f"select coalesce(parameters->>'base_url',''), coalesce(parameters->>'api_key','') "
           f"from models where id='{model_id}'")
    out = subprocess.run(["docker", "exec", PG_CONTAINER, "psql", "-U", "postgres", "-d", "WeKnora",
                          "-t", "-A", "-F", "|", "-c", sql],
                         capture_output=True, text=True, check=True).stdout.strip()
    base_url, enc = out.split("|", 1)
    return base_url, decrypt(enc.strip(), system_aes_key())


def ask(base_url: str, api_key: str, model: str, question: str, timeout: int = 300,
        thinking: bool = True, max_tokens: int = 16384) -> dict:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": question + ANSWER_STYLE}],
        "temperature": 0.7,
        # 该模型先产出 reasoning_content（内部推理），给少了正文会被饿死：
        # 实测 4096 时 #21 这类题会把预算全烧在推理上、正文为空；给到 16384 才能正常收尾
        "max_tokens": max_tokens,
        "stream": False,
    }
    if not thinking:                 # 实测可把推理 token 降到 0（代价：难题直接瞎猜）
        payload["thinking"] = {"type": "disabled"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Authorization": f"Bearer {api_key}",
                                          "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        ch = (d.get("choices") or [{}])[0]
        msg = ch.get("message") or {}
        text = msg.get("content") or ""
        usage = d.get("usage") or {}
        warn = None if text.strip() else f"empty content (finish={ch.get('finish_reason')})"
        return {"text": text, "elapsed": round(time.time() - t0, 2), "usage": usage,
                "reasoning_len": len(msg.get("reasoning_content") or ""), "error": warn}
    except Exception as exc:
        return {"text": "", "elapsed": round(time.time() - t0, 2), "usage": {},
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def cmd_run() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="flash,pro")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--no-thinking", action="store_true", help="关掉内部推理（默认开，且给足 16k 预算）")
    ap.add_argument("--max-tokens", type=int, default=16384)
    args = ap.parse_args(sys.argv[2:])

    questions = ev.load_questions(None)
    only = {int(x) for x in args.only.split(",") if x.strip()}
    todo = [(i, q) for i, q in enumerate(questions, 1) if not only or i in only]
    tag = "" if only else "-r"

    for mkey in [m.strip() for m in args.models.split(",") if m.strip()]:
        model_name, model_id = MODELS[mkey]
        base_url, api_key = model_credential(model_id)
        print(f"\n=== direct closed-book[{mkey}] model={model_name} base={base_url} ===", flush=True)
        for rnd in range(1, args.rounds + 1):
            rows = []
            for i, q in todo:
                r = ask(base_url, api_key, model_name, q["question"],
                        thinking=not args.no_thinking, max_tokens=args.max_tokens)
                row = {
                    "arm": f"closedbook-direct-{mkey}", "round": rnd, "n": i, "id": q["id"], "hops": q["hops"],
                    "question": q["question"], "gold": q["answer"], "aliases": q.get("answer_aliases") or [],
                    "session_id": None, "pred": r["text"], "pred_from": "api",
                    "refs": [], "tool_calls": [], "tool_events": [],
                    "elapsed_sec": r["elapsed"], "events": 0, "error": r["error"], "completed": True,
                    "usage": r["usage"],
                }
                rows.append(row)
                f1, em = ev.best_match(row["pred"], row["gold"], row["aliases"])
                print(f"[{mkey} r{rnd} {i:02d}/{len(questions)}] {row['elapsed_sec']}s f1={f1:.2f} "
                      f"pred={row['pred'][:56]!r} gold={row['gold'][:26]!r}", flush=True)
            name = f"qa-closedbook-direct-{mkey}{'' if rnd == 1 else f'-r{rnd}'}.jsonl"
            p = ev.OUT_DIR / name
            p.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
            print(f"done -> {p}", flush=True)


def cmd_score() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="qa-closedbook-direct-*.jsonl")
    args = ap.parse_args(sys.argv[2:])
    out = {}
    for path in sorted(ev.OUT_DIR.glob(args.pattern)):
        rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        by_hop: dict[int, list[tuple[float, float]]] = {}
        alls = []
        for r in rows:
            f1, em = ev.best_match(r["pred"], r["gold"], r.get("aliases") or [])
            alls.append((f1, em))
            by_hop.setdefault(int(r["hops"]), []).append((f1, em))
        def agg(xs):
            n = len(xs) or 1
            return {"n": len(xs), "f1": round(sum(x[0] for x in xs) / n, 4), "em": round(sum(x[1] for x in xs) / n, 4)}
        out[path.name] = {"overall": agg(alls), "by_hops": {str(h): agg(v) for h, v in sorted(by_hop.items())}}
    print(json.dumps(out, ensure_ascii=False, indent=2))


COMMANDS = {"run": cmd_run, "score": cmd_score}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("用法：closedbook_direct.py [run|score]")
        raise SystemExit(2)
    COMMANDS[sys.argv[1]]()
