"""跑 prompt-tune：同样的"密钥不落盘 + shim 生效 + 输出脱敏落盘"套路。

和 _run_index.py 的差别只有命令（prompt-tune 而非 index）与默认参数。

用法：
    python _run_prompt_tune.py --limit 7 --selection-method top --output prompts-tuned
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
APP_DB = REPO / "backend" / "data" / "app.db"
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
SKIP_DIRS = {".venv", "__pycache__", ".git"}


def read_llm_settings() -> dict[str, str]:
    con = sqlite3.connect(f"file:{APP_DB}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    if not row:
        raise SystemExit("app.db 的 settings 表是空的")
    return json.loads(row[0])


def redact(text: str, secret: str) -> str:
    return SECRET_RE.sub("<redacted-key>", text.replace(secret, "<redacted-key>"))


def main() -> int:
    llm = read_llm_settings()
    print(f"模型：provider={llm['provider']} model={llm['model']} api_key=<len={len(llm['api_key'])}>")
    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = llm["api_key"]
    env["OPENAI_API_KEY"] = "not-used-placeholder"
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["GRAPHRAG_SHIM_VERBOSE"] = "1"

    extra = sys.argv[1:] or ["--limit", "7", "--selection-method", "top",
                             "--output", "prompts-tuned"]
    cmd = [sys.executable, "-m", "graphrag", "prompt-tune", "--root", str(ROOT), *extra]
    print("命令:", " ".join(extra))

    run_log = ROOT / f"prompt-tune-{time.strftime('%m%d-%H%M%S')}.out.log"
    print(f"子进程输出写到: {run_log.name}")
    proc = subprocess.Popen(
        cmd, cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    assert proc.stdout is not None
    with run_log.open("w", encoding="utf-8") as fh:
        for line in proc.stdout:
            fh.write(redact(line.rstrip("\n"), llm["api_key"]) + "\n")
            fh.flush()
    proc.wait()
    print("退出码:", proc.returncode)

    out_dir = ROOT / "prompts-tuned"
    if out_dir.exists():
        print("产出:", sorted(p.name for p in out_dir.iterdir()))
    # 密钥残留扫描（只看非 .venv 文本文件）
    hits = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in {".parquet", ".sqlite", ".db"}:
            continue
        try:
            if llm["api_key"] in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(p.relative_to(ROOT)))
        except Exception:  # noqa: BLE001
            pass
    print("真实密钥命中:", hits or "无")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
