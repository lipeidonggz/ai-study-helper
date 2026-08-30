"""评测台（0017）测试：用例文件存取、跑批结果库、eval API 全流程。"""

import json
import time

from fastapi.testclient import TestClient

from eval import case_store, run_store
from eval.schema import CaseFile, CaseInput, Expected, InputMessage


def _minimal_case(case_id: str = "console-001") -> CaseFile:
    return CaseFile(
        id=case_id,
        category="tool_call",
        title="评测台测试用例",
        mode="tool_enhanced",
        input=CaseInput(messages=[InputMessage(role="user", content="3+5等于几？")]),
        expected=Expected(behavior="调用计算器", criteria=["tool_used"]),
    )


def test_case_store_roundtrip(tmp_path):
    """用例文件存取：新建 → 读取 → 覆盖（类别变更清理旧文件）→ 删除。"""
    case = _minimal_case()
    path = case_store.save_case(case, cases_dir=tmp_path, author="tester")
    assert path.exists()
    assert path.name == "console-001.json"
    assert path.parent.name == "tool_call"

    loaded = case_store.get_case("console-001", cases_dir=tmp_path)
    assert loaded is not None
    assert loaded.title == "评测台测试用例"
    assert loaded.enabled is True  # 默认值
    assert loaded.updated_by == "tester"

    # 改类别：旧文件应被清理，避免双份
    moved = _minimal_case().model_copy(
        update={"id": "console-001", "category": "combined"}
    )
    case_store.save_case(moved, cases_dir=tmp_path, author="tester")
    assert (tmp_path / "tool_call" / "console-001.json").exists() is False
    assert (tmp_path / "combined" / "console-001.json").exists()

    assert case_store.delete_case("console-001", cases_dir=tmp_path) is True
    assert case_store.delete_case("console-001", cases_dir=tmp_path) is False


def test_case_schema_defaults_for_old_files(tmp_path):
    """存量用例 JSON 不带新字段时，Pydantic 默认值应补齐。"""
    case = _minimal_case()
    (tmp_path / "tool_call").mkdir(parents=True)
    (tmp_path / "tool_call" / "console-002.json").write_text(
        json.dumps(
            case.model_dump(exclude={"enabled", "admin_note", "updated_at", "updated_by"}),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    loaded = case_store.get_case("console-002", cases_dir=tmp_path)
    assert loaded is not None
    assert loaded.enabled is True
    assert loaded.admin_note == ""


def test_run_store_create_and_annotate(tmp_path):
    """跑批结果库：创建 run → 写入结果 → 人工标注 → 查询。"""
    db = tmp_path / "eval.db"
    run_store.init_db(db)
    run_id = run_store.create_run(db, "fake 跑批", {"llm": "fake"}, total=1)
    assert run_store.get_run(db, run_id)["status"] == "queued"

    run_store.update_run(db, run_id, status="running", started=True)
    run_store.insert_case_result(
        db,
        run_id,
        {
            "id": "console-001",
            "category": "tool_call",
            "title": "t",
            "mode": "general",
            "input": "user: hi",
            "status": "ok",
            "elapsed_ms": 12.0,
            "rounds": 1,
            "tool_calls": ["calculator"],
            "tokens": {"total": 10},
            "output": "结果是 8",
            "error": "",
            "judgments": {"tool_used": "pass"},
            "pending_human": ["answer_correct"],
            "metrics": {},
        },
    )
    run_store.set_annotation(
        db, run_id, "console-001", answer_correct="对", refusal="不适用", note="ok"
    )
    run_store.update_run(db, run_id, status="done", progress=1, finished=True)

    run = run_store.get_run(db, run_id)
    assert run["status"] == "done"
    assert run["progress"] == 1
    rows = run_store.get_run_cases(db, run_id)
    assert len(rows) == 1
    assert rows[0]["answer_correct"] == "对"
    assert rows[0]["refusal"] == "不适用"


def test_eval_api_full_flow(tmp_path):
    """eval API 全流程：建用例 → 启动 fake 跑批 → 轮询完成 → 标注 → 导出。"""
    from app.main import create_app

    app = create_app(
        eval_db_path=str(tmp_path / "eval.db"),
        eval_cases_dir=str(tmp_path / "cases"),
    )
    with TestClient(app) as client:
        # 新建用例
        body = _minimal_case("console-003").model_dump()
        resp = client.post("/api/eval/cases", json=body)
        assert resp.status_code == 201, resp.text
        assert client.get("/api/eval/cases/console-003").status_code == 200
        assert client.post("/api/eval/cases", json=body).status_code == 409

        # 启动 fake 跑批（只跑这一条）
        resp = client.post(
            "/api/eval/runs",
            json={
                "name": "console fake run",
                "llm": "fake",
                "concurrency": 1,
                "repeat": 1,
                "case_filter": {"ids": ["console-003"]},
            },
        )
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["run_id"]

        # 轮询直到 done
        status = ""
        for _ in range(100):
            detail = client.get(f"/api/eval/runs/{run_id}").json()
            status = detail["run"]["status"]
            if status in ("done", "error", "canceled"):
                break
            time.sleep(0.05)
        assert status == "done", detail
        assert detail["run"]["progress"] == 1
        assert detail["cases"][0]["case_id"] == "console-003"

        # 轻量模式：列表/轮询不带重复明细与轨迹；单条接口可按需取回全量
        light = client.get(f"/api/eval/runs/{run_id}?light=1").json()
        assert light["cases"][0]["repeat_results"] == []
        assert light["cases"][0]["trace"] == []
        full = client.get(f"/api/eval/runs/{run_id}/cases/console-003").json()
        assert full["case_id"] == "console-003"
        assert len(full["repeat_results"]) == 1

        # 重命名跑批
        resp = client.patch(f"/api/eval/runs/{run_id}", json={"name": "重命名后的跑批"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["name"] == "重命名后的跑批"
        renamed = client.get(f"/api/eval/runs/{run_id}").json()["run"]
        assert renamed["name"] == "重命名后的跑批"

        # 人工标注 + 校验落库
        resp = client.patch(
            f"/api/eval/runs/{run_id}/cases/console-003",
            json={"answer_correct": "对", "refusal": "不适用", "note": "ok"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["answer_correct"] == "对"

        # 导出
        resp = client.get(f"/api/eval/runs/{run_id}/export")
        assert resp.status_code == 200
        assert '"answer_correct": "对"' in resp.text
