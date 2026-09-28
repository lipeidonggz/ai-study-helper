"""后台重入库 T1（同步 POST，完成后写日志）。"""

import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
LOG = Path("data/tmp/t1-reindex.log")


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def main() -> None:
    log("T1 reindex start")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/kb/documents/T1/index",
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3600) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        log("T1 reindex done: " + json.dumps(data, ensure_ascii=False))
    except Exception as exc:
        log(f"T1 reindex FAILED: {exc}")


if __name__ == "__main__":
    main()
