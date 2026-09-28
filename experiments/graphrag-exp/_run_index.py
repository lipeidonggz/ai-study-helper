"""跑 GraphRAG 索引，同时**保证密钥不落进任何输出**。

安全设计（2026-09-25，吸取上一次泄露的教训）：
1. 密钥只从 `backend/data/app.db` 读进子进程环境变量，绝不写文件、绝不作为命令行参数；
2. 子进程的 stdout/stderr **逐行过一遍脱敏**再打印——因为 graphrag 有可能把解析后的
   配置打出来（那样就会带上密钥）；
3. 跑完扫一遍本实验目录，报告是否出现 `sk-` 形态的串（只报"有/无"，不打印内容）。

用法：
    python _run_index.py            # 真跑
    python _run_index.py --dry-run  # 只校验配置，不执行步骤
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent.parent.parent
APP_DB = REPO / "backend" / "data" / "app.db"
SECRET_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")


def read_llm_settings() -> dict[str, str]:
    """从应用库里读出 provider / model / api_key。**不打印任何值。**"""
    con = sqlite3.connect(f"file:{APP_DB}?mode=ro", uri=True)
    row = con.execute("select value from settings limit 1").fetchone()
    con.close()
    if not row:
        raise SystemExit("app.db 的 settings 表是空的 —— 请先在应用里配置模型")
    data = json.loads(row[0])
    need = ("provider", "model", "api_key")
    missing = [k for k in need if not data.get(k)]
    if missing:
        raise SystemExit(f"settings 里缺少字段: {missing}")
    return data


def redact(text: str, secret: str) -> str:
    out = text.replace(secret, "<redacted-key>")
    return SECRET_RE.sub("<redacted-key>", out)


SKIP_DIRS = {".venv", "__pycache__", ".git"}


def scan_for_secrets(secret: str) -> None:
    """两段式扫描，区分"真泄露"和"形似噪声"。

    2026-09-25 修正：第一版只按 `sk-...` 正则扫，结果把 `.venv` 里几十个文件
    和 `input/a5.txt` 全报成命中——因为 `disk-`、`risk-`、`task-` 这类普通词
    也含 `sk-`。**正则形似 ≠ 泄露**，所以改成：
      · 真泄露 = 文件里出现了**那把真实密钥**（精确子串比对）；
      · 形似   = 不含真实密钥但匹配 `sk-…` 模式（正常，可以忽略）。
    """
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
    print("\n=== 密钥残留扫描（不含 .venv/__pycache__）")
    print("  真实密钥命中 :", real or "无")
    print(f"  形似 sk- 的文件数: {lookalike}（多为 disk-/risk-/task- 这类普通词，可忽略）")


def main() -> int:
    llm = read_llm_settings()
    print(f"模型配置：provider={llm['provider']} model={llm['model']} api_key=<len={len(llm['api_key'])}>")
    print(f"experiment root: {ROOT}")

    env = dict(os.environ)
    env["DEEPSEEK_API_KEY"] = llm["api_key"]
    # 注入 DeepSeek 兼容 shim：Python 启动时会自动 import 本目录下的 sitecustomize.py
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["GRAPHRAG_SHIM_VERBOSE"] = "1"
    print("已注入 shim：sitecustomize.py（剥掉 API 层的 response_format=pydantic）")

    cmd = [sys.executable, "-m", "graphrag", "index", "--root", str(ROOT), *sys.argv[1:]]
    print("命令:", " ".join(cmd[:6]), "...", " ".join(sys.argv[1:]) or "(无额外参数)")
    print("-" * 70)

    run_log = ROOT / f"run-{time.strftime('%m%d-%H%M%S')}.out.log"
    print(f"子进程输出写到: {run_log.name}（实时可 tail；内容已脱敏）")

    # ⚠️ 2026-09-25 重要修正：**不要把子进程输出转发到本进程的 stdout**。
    # 第一次重跑时两次都"卡在 132/317 不再前进"——真因是管道死锁：
    # 本进程 stdout 未被实时消费 ⇒ 本进程阻塞在 write ⇒ 不再读子进程 stdout
    # ⇒ 子进程写进度时管道写满 ⇒ 双方僵死。改为直接落盘（仍逐行脱敏）。
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
    print("退出码:", proc.returncode)

    out_dir = ROOT / "output"
    if out_dir.exists():
        files = sorted(p.name for p in out_dir.glob("*.parquet"))
        print("产出表:", files or "(无 parquet)")
    print(f"子进程日志: {run_log.name}")
    scan_for_secrets(llm["api_key"])
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
