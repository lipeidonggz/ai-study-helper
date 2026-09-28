"""跑 GraphRAG 增量（update），密钥不落盘、输出脱敏。

与 index 版（graphrag-exp-juejin/_run_index.py）的差别只有两点：
  · 命令是 `graphrag update`（而不是 `index`）；
  · 默认带上 `--skip-validation`（占位 embedding key 过不了预检）。

用法：
    python _run_update.py
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


def scan_for_secrets(secret: str) -> None:
    real: list[str] = []
    lookalike = 0
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.stat().st_size > 5_000_000:
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in {".parquet", ".sqlite", ".db"}:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        if secret and secret in text:
            real.append(str(p.relative_to(ROOT)))
        elif SECRET_RE.search(text):
            lookalike += 1
    print("\n=== 密钥残留扫描 ===")
    print("  真实密钥命中 :", real or "无")
    print(f"  形似 sk- 的文件数: {lookalike}（多为 disk-/risk- 等普通词）")


def main() -> int:
    llm = read_llm_settings()
    print(f"模型：provider={llm.get('provider')} model={llm.get('model')} key=<len={len(llm.get('api_key',''))}>")
    print(f"root: {ROOT}")

    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = llm["api_key"]
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["GRAPHRAG_SHIM_VERBOSE"] = "1"

    extra = sys.argv[1:] or ["--skip-validation"]
    cmd = [sys.executable, "-m", "graphrag", "update", "--root", str(ROOT), *extra]
    print("命令: graphrag update --root <root>", " ".join(extra))
    print("-" * 70)

    run_log = ROOT / f"update-{time.strftime('%m%d-%H%M%S')}.out.log"
    print(f"子进程输出写到: {run_log.name}")
    started = time.time()
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert proc.stdout is not None
    with run_log.open("w", encoding="utf-8") as fh:
        try:
            for line in proc.stdout:
                fh.write(redact(line.rstrip("\n"), llm["api_key"]) + "\n")
                fh.flush()
        finally:
            proc.wait()

    print("-" * 70)
    print(f"退出码: {proc.returncode}  耗时 {time.time() - started:.0f}s")
    for d in ("output", "update_output"):
        p = ROOT / d
        if p.exists():
            print(f"{d}/:", sorted(x.name for x in p.iterdir()))
    print(f"日志: {run_log.name}")
    scan_for_secrets(llm["api_key"])
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
