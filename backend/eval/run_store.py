"""跑批结果存储：SQLite 持久化 eval_runs / eval_run_cases 两张表。

设计角度（0017 需求）：
- 用例仍以 JSON 文件为真源；跑批结果会增长、需要查询，所以入 SQLite
- 人工标注（answer_correct / refusal / 备注）也存这里，与结果同表，闭环统计
- 单用户本地工具：同步 sqlite3 + 全局写锁足够；不引入 ORM
"""

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "eval.db"
_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def _connect(db_path: Path):
    """打开并自动关闭连接（sqlite3 连接对象的 with 只提交不关闭，会泄漏句柄）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH) -> None:
    """建表（幂等）。"""
    with _lock, _connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS eval_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER NOT NULL DEFAULT 0,
                total INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                verified TEXT NOT NULL DEFAULT '',
                verified_by TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS eval_run_cases (
                run_id INTEGER NOT NULL,
                case_id TEXT NOT NULL,
                category TEXT,
                title TEXT,
                mode TEXT,
                input TEXT,
                status TEXT,
                elapsed_ms REAL,
                rounds INTEGER,
                tool_calls TEXT,
                tokens TEXT,
                output TEXT,
                error TEXT,
                judgments TEXT,
                pending_human TEXT,
                metrics TEXT,
                judge_reasons TEXT NOT NULL DEFAULT '{}',
                diagnostics TEXT NOT NULL DEFAULT '{}',
                verdict TEXT NOT NULL DEFAULT '',
                repeat_count INTEGER NOT NULL DEFAULT 1,
                pass_count INTEGER NOT NULL DEFAULT 0,
                repeat_results TEXT NOT NULL DEFAULT '[]',
                trace TEXT NOT NULL DEFAULT '[]',
                answer_correct TEXT NOT NULL DEFAULT '',
                refusal TEXT NOT NULL DEFAULT '',
                annotate_note TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (run_id, case_id)
            );
            """
        )
        # 存量库迁移：老表没有新列时补列（幂等）
        run_cols = {r["name"] for r in conn.execute("PRAGMA table_info(eval_runs)")}
        if "verified" not in run_cols:
            conn.execute("ALTER TABLE eval_runs ADD COLUMN verified TEXT NOT NULL DEFAULT ''")
        if "verified_by" not in run_cols:
            conn.execute("ALTER TABLE eval_runs ADD COLUMN verified_by TEXT NOT NULL DEFAULT ''")
        case_cols = {r["name"] for r in conn.execute("PRAGMA table_info(eval_run_cases)")}
        if "verdict" not in case_cols:
            conn.execute(
                "ALTER TABLE eval_run_cases ADD COLUMN verdict TEXT NOT NULL DEFAULT ''"
            )
        if "judge_reasons" not in case_cols:
            conn.execute(
                "ALTER TABLE eval_run_cases ADD COLUMN judge_reasons TEXT NOT NULL DEFAULT '{}'"
            )
        if "diagnostics" not in case_cols:
            # 白盒归因输出（2026-09-04）：检索充分性三布尔 / 首个卡点 / 漏引记账 / 提炼归因，
            # 只服务根因定位，不改判分（verdict-first 编排）
            conn.execute(
                "ALTER TABLE eval_run_cases ADD COLUMN diagnostics TEXT NOT NULL DEFAULT '{}'"
            )
        if "repeat_count" not in case_cols:
            conn.execute("ALTER TABLE eval_run_cases ADD COLUMN repeat_count INTEGER NOT NULL DEFAULT 1")
        if "pass_count" not in case_cols:
            conn.execute("ALTER TABLE eval_run_cases ADD COLUMN pass_count INTEGER NOT NULL DEFAULT 0")
        if "repeat_results" not in case_cols:
            conn.execute("ALTER TABLE eval_run_cases ADD COLUMN repeat_results TEXT NOT NULL DEFAULT '[]'")
        if "trace" not in case_cols:
            conn.execute("ALTER TABLE eval_run_cases ADD COLUMN trace TEXT NOT NULL DEFAULT '[]'")
        if "pending_attempts" not in case_cols:
            # 需要人工标注的 attempt 数（详情页"待人工"统计用，避免前端解析大 JSON）
            conn.execute(
                "ALTER TABLE eval_run_cases ADD COLUMN pending_attempts INTEGER NOT NULL DEFAULT 0"
            )


def create_run(
    db_path: Path,
    name: str,
    config: dict,
    total: int,
) -> int:
    """创建一条 queued 状态跑批记录，返回 run_id。"""
    with _lock, _connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO eval_runs (name, config, status, total, created_at)"
            " VALUES (?, ?, 'queued', ?, ?)",
            (name, json.dumps(config, ensure_ascii=False), total, _now_iso()),
        )
        return int(cur.lastrowid)


def update_run(
    db_path: Path,
    run_id: int,
    *,
    status: str | None = None,
    progress: int | None = None,
    total: int | None = None,
    summary: dict | None = None,
    error: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """按需更新跑批记录的字段。"""
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    if total is not None:
        fields.append("total = ?")
        values.append(total)
    if summary is not None:
        fields.append("summary = ?")
        values.append(json.dumps(summary, ensure_ascii=False))
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if started:
        fields.append("started_at = ?")
        values.append(_now_iso())
    if finished:
        fields.append("finished_at = ?")
        values.append(_now_iso())
    if not fields:
        return
    values.append(run_id)
    with _lock, _connect(db_path) as conn:
        conn.execute(f"UPDATE eval_runs SET {', '.join(fields)} WHERE id = ?", values)


def rename_run(db_path: Path, run_id: int, name: str) -> bool:
    """修改跑批名称；返回是否命中。"""
    with _lock, _connect(db_path) as conn:
        cur = conn.execute("UPDATE eval_runs SET name = ? WHERE id = ?", (name, run_id))
        return cur.rowcount > 0


def insert_case_result(db_path: Path, run_id: int, entry: dict) -> None:
    """写入单条用例结果（覆盖式，重跑同 case 时更新）。"""
    with _lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO eval_run_cases
                (run_id, case_id, category, title, mode, input, status,
                 elapsed_ms, rounds, tool_calls, tokens, output, error,
                 judgments, pending_human, metrics, verdict, judge_reasons, diagnostics,
                 repeat_count, pass_count, repeat_results, trace, pending_attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, case_id) DO UPDATE SET
                category=excluded.category, title=excluded.title, mode=excluded.mode,
                input=excluded.input, status=excluded.status, elapsed_ms=excluded.elapsed_ms,
                rounds=excluded.rounds, tool_calls=excluded.tool_calls, tokens=excluded.tokens,
                output=excluded.output, error=excluded.error, judgments=excluded.judgments,
                pending_human=excluded.pending_human, metrics=excluded.metrics,
                verdict=excluded.verdict, judge_reasons=excluded.judge_reasons,
                diagnostics=excluded.diagnostics,
                repeat_count=excluded.repeat_count, pass_count=excluded.pass_count,
                repeat_results=excluded.repeat_results, trace=excluded.trace,
                pending_attempts=excluded.pending_attempts
            """,
            (
                run_id,
                entry["id"],
                entry.get("category"),
                entry.get("title"),
                entry.get("mode"),
                entry.get("input"),
                entry.get("status"),
                entry.get("elapsed_ms"),
                entry.get("rounds"),
                json.dumps(entry.get("tool_calls", []), ensure_ascii=False),
                json.dumps(entry.get("tokens", {}), ensure_ascii=False),
                entry.get("output"),
                entry.get("error"),
                json.dumps(entry.get("judgments", {}), ensure_ascii=False),
                json.dumps(entry.get("pending_human", []), ensure_ascii=False),
                json.dumps(entry.get("metrics", {}), ensure_ascii=False),
                entry.get("verdict", ""),
                json.dumps(entry.get("judge_reasons", {}), ensure_ascii=False),
                json.dumps(entry.get("diagnostics", {}), ensure_ascii=False),
                entry.get("repeat_count", 1),
                entry.get("pass_count", 0),
                json.dumps(entry.get("repeat_results", []), ensure_ascii=False),
                json.dumps(entry.get("trace", []), ensure_ascii=False),
                sum(
                    1
                    for a in entry.get("repeat_results", [])
                    if a.get("pending_human")
                ),
            ),
        )


def set_annotation(
    db_path: Path,
    run_id: int,
    case_id: str,
    *,
    answer_correct: str = "",
    refusal: str = "",
    note: str = "",
) -> None:
    """保存人工标注（对/错/存疑 + 合理/不合理/不适用 + 备注）。"""
    with _lock, _connect(db_path) as conn:
        conn.execute(
            "UPDATE eval_run_cases SET answer_correct = ?, refusal = ?, annotate_note = ?"
            " WHERE run_id = ? AND case_id = ?",
            (answer_correct, refusal, note, run_id, case_id),
        )


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("config", "summary"):
        if d.get(key):
            d[key] = json.loads(d[key])
    return d


def _case_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in (
        "tool_calls",
        "tokens",
        "judgments",
        "pending_human",
        "metrics",
        "judge_reasons",
        "repeat_results",
        "trace",
    ):
        if key in d and d[key]:
            d[key] = json.loads(d[key])
    return d


def get_run(db_path: Path, run_id: int) -> dict | None:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_runs(db_path: Path, limit: int = 50) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, name, status, progress, total, error, created_at, started_at,"
            " finished_at, summary, verified, verified_by, config"
            " FROM eval_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_run_cases(db_path: Path, run_id: int) -> list[dict]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM eval_run_cases WHERE run_id = ? ORDER BY case_id", (run_id,)
        ).fetchall()
        return [_case_row_to_dict(r) for r in rows]


def delete_run(db_path: Path, run_id: int) -> bool:
    """删除跑批记录及其全部用例结果；不存在返回 False。"""
    with _lock, _connect(db_path) as conn:
        conn.execute("DELETE FROM eval_run_cases WHERE run_id = ?", (run_id,))
        cur = conn.execute("DELETE FROM eval_runs WHERE id = ?", (run_id,))
        return cur.rowcount > 0


def mark_verified(db_path: Path, run_id: int, by: str = "local") -> bool:
    """标记跑批已人工核验，返回是否命中。"""
    with _lock, _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE eval_runs SET verified = ?, verified_by = ? WHERE id = ?",
            (_now_iso(), by, run_id),
        )
        return cur.rowcount > 0


def clear_verified(db_path: Path, run_id: int) -> bool:
    """清除核验标记（如重跑后核验失效）。"""
    with _lock, _connect(db_path) as conn:
        cur = conn.execute(
            "UPDATE eval_runs SET verified = '', verified_by = '' WHERE id = ?",
            (run_id,),
        )
        return cur.rowcount > 0


def clear_case_annotation(db_path: Path, run_id: int, case_id: str) -> None:
    """清空单条用例的人工标注（重跑产生新输出后旧标注不再适用）。"""
    with _lock, _connect(db_path) as conn:
        conn.execute(
            "UPDATE eval_run_cases SET answer_correct = '', refusal = '', annotate_note = ''"
            " WHERE run_id = ? AND case_id = ?",
            (run_id, case_id),
        )
