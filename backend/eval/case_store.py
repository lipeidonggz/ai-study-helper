"""用例文件存取层：JSON 文件是唯一真源，UI 与 CLI 都通过这里读写。

设计角度（0017 需求）：
- 用例继续以 backend/eval/cases/**/*.json 为唯一真源，git 可 diff、可评审、可回滚
- 本层只负责"文件 ↔ CaseFile 对象"的转换，不做业务判定；
  校验沿用 schema.py 的 Pydantic 模型，保存前必过一遍
- 单用户本地：不处理并发写冲突；删除前由调用方确认
"""

import json
from datetime import datetime
from pathlib import Path

from eval.schema import CaseFile, load_cases

CASES_DIR = Path(__file__).resolve().parent / "cases"


def _now_iso() -> str:
    """当前时间 ISO 字符串（保存时自动盖上时间戳）。"""
    return datetime.now().isoformat(timespec="seconds")


def _case_path(case_id: str, cases_dir: Path) -> Path | None:
    """按 id 在目录下查找用例文件（id 全局唯一，跨子目录搜索）。"""
    for f in cases_dir.rglob(f"{case_id}.json"):
        return f
    return None


def list_cases(cases_dir: Path = CASES_DIR) -> list[CaseFile]:
    """返回全部用例（按 id 排序），复用 schema 的加载与校验。"""
    return load_cases(cases_dir)


def get_case(case_id: str, cases_dir: Path = CASES_DIR) -> CaseFile | None:
    """按 id 取单个用例；不存在返回 None。"""
    path = _case_path(case_id, cases_dir)
    if path is None:
        return None
    return CaseFile.model_validate_json(path.read_text(encoding="utf-8"))


def save_case(
    case: CaseFile,
    cases_dir: Path = CASES_DIR,
    author: str = "local",
    stamp: bool = True,
) -> Path:
    """新建或覆盖一个用例文件，返回写入路径。

    - 若同一 id 的旧文件存在且目录类别变了，删除旧文件（避免双份）
    - stamp=True 时自动盖 updated_at / updated_by（编辑入口统一走这里）
    """
    if stamp:
        case = case.model_copy(
            update={"updated_at": _now_iso(), "updated_by": author}
        )
    old = _case_path(case.id, cases_dir)
    target = cases_dir / case.category / f"{case.id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(case.model_dump(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if old is not None and old.resolve() != target.resolve():
        old.unlink()  # 类别目录变化时清理旧文件
    return target


def delete_case(case_id: str, cases_dir: Path = CASES_DIR) -> bool:
    """删除用例文件；不存在返回 False。"""
    path = _case_path(case_id, cases_dir)
    if path is None:
        return False
    path.unlink()
    return True


def count_cases(cases_dir: Path = CASES_DIR) -> int:
    """用例总数（状态展示用）。"""
    return len(list_cases(cases_dir))
