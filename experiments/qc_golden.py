"""批量导出 enabled 用例的金标准（分批阅读用）。"""

import glob
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    start, end = int(sys.argv[1]), int(sys.argv[2])
    paths = sorted(glob.glob(r"backend/eval/cases/rag/*.json"))
    enabled = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            case = json.load(f)
        if case.get("enabled", True):
            enabled.append(case)
    for case in enabled[start:end]:
        print(f"\n########## {case['id']} ##########")
        print(case["annotation"].get("golden_answer", "(无 golden)"))


if __name__ == "__main__":
    main()
