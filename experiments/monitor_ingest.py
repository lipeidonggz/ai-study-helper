"""全量入库后台监控：每 120s 查一次 /api/kb/documents，进度写日志；全部完成或有失败时退出。"""

import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000/api/kb/documents"
LOG = Path("data/tmp/ingest-progress.log")
POLL_SEC = 120
EXPECTED_TOTAL = 30  # manifest 里可入库源总数（含未采集项，未采集会失败/跳过时以实际为准）


def log(line: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{ts}] {line}\n")
    print(f"[{ts}] {line}", flush=True)


def main() -> None:
    log("monitor start")
    last_state = ""
    stable_rounds = 0
    while True:
        try:
            with urllib.request.urlopen(BASE, timeout=30) as resp:
                docs = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # 后端重启/短暂不可达：记录并重试
            log(f"WARN fetch failed: {exc}")
            time.sleep(POLL_SEC)
            continue
        ready = [x for x in docs if x["status"] == "ready"]
        busy = [x["source_id"] for x in docs if x["status"] == "indexing"]
        failed = [f"{x['source_id']}:{(x.get('error') or '')[:120]}" for x in docs if x["status"] == "failed"]
        chunks = sum(x.get("chunk_count") or 0 for x in ready)
        state = f"ready={len(ready)}/{len(docs)} busy={busy} chunks={chunks}"
        if failed:
            state += " FAILED=" + " || ".join(failed)
        log(state)
        if len(ready) >= len(docs) and not busy:
            log("DONE all sources ready")
            break
        if failed and not busy and len(ready) + len(failed) >= len(docs):
            log("DONE with failures")
            break
        # 连续 3 轮（约 6 分钟）无变化且无 busy → 视为停滞退出，避免无限挂
        if state == last_state:
            stable_rounds += 1
        else:
            stable_rounds = 0
        last_state = state
        if stable_rounds >= 3 and not busy:
            log("STALLED no progress; monitor exit")
            break
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
