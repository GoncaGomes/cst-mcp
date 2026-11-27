"""A pending async solve whose CST stops answering is reported and guarded (MCP 07)."""

from __future__ import annotations

import json
from pathlib import Path

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import project as project_tools
from cst_mcp.tools import simulation


class FakeModel:
    """model3d fake whose solver queries answer, or time out like a busy CST."""

    def __init__(self) -> None:
        self.answer: bool | None = False  # None: time out
        self.queries = 0
        self.history: list[str] = []
        self.abort_times_out = False

    def is_solver_running(self, *, timeout=None):
        self.queries += 1
        if self.answer is None:
            raise RuntimeError("Operation timed out.")
        return self.answer

    def start_solver(self, *, timeout=None):
        return None

    def abort_solver(self, *, timeout=None):
        if self.abort_times_out:
            raise TimeoutError("Operation timed out.")

    def add_to_history(self, title, code, *args, **kwargs):
        self.history.append(code)


class FakeProject:
    def __init__(self, path: str) -> None:
        self.model3d = FakeModel()
        self.path = path
        self.saves: list[str | None] = []

    def filename(self) -> str:
        return self.path

    def save(self, path: str = "") -> None:
        self.saves.append(path or None)


def _client(tmp_path: Path) -> tuple[CSTClient, FakeProject]:
    path = str(tmp_path / "vivaldi.cst")
    project = FakeProject(path)
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client._de = object()
    client._project = project
    client._project_path = path
    return client, project


def _unresponsive_solve(tmp_path: Path) -> tuple[CSTClient, FakeProject]:
    """An async solve that started, was seen running, then CST stopped answering."""
    client, project = _client(tmp_path)
    assert client.start_solver()["status"] == "started"
    project.model3d.answer = True
    assert client.solver_status(running_only=True)["running"] is True
    project.model3d.answer = None
    return client, project


def _data(content) -> dict:
    return json.loads(content[0].text)


def test_timed_out_query_during_pending_solve_is_unresponsive(tmp_path) -> None:
    client, _ = _unresponsive_solve(tmp_path)
    state = client.solver_status(running_only=True)
    assert state["status"] == "unresponsive"
    assert state["likely_running"] is True and state["running"] is None
    assert "cst_wait_for_simulation" in state["hint"]
    assert "Operation timed out" in state["message"]
    assert "solver_log" not in state  # no log file yet


def test_timeout_without_pending_solve_is_still_an_error(tmp_path) -> None:
    client, project = _client(tmp_path)
    project.model3d.answer = None
    state = client.solver_status()
    assert state["status"] == "error" and state["running"] is None
    assert client.solver_unresponsive() is None


def test_non_timeout_failure_is_not_unresponsive(tmp_path) -> None:
    client, project = _unresponsive_solve(tmp_path)

    def broken(**kw):
        raise RuntimeError("COM object disconnected")

    project.model3d.is_solver_running = broken
    client._pending_solve.pop("unresponsive_since", None)
    assert client.solver_status()["status"] == "error"


def test_responsive_running_solver_is_unchanged(tmp_path) -> None:
    client, project = _client(tmp_path)
    client.start_solver()
    project.model3d.answer = True
    state = client.solver_status(running_only=True)
    assert state == {"status": "ok", "running": True}
    assert client.solver_unresponsive() is None


def test_save_fails_fast_without_contacting_cst(tmp_path) -> None:
    client, project = _unresponsive_solve(tmp_path)
    client.solver_status(running_only=True)
    queries = project.model3d.queries
    result = client.save_project()
    assert result["status"] == "busy" and result["code"] == "solver_unresponsive"
    assert result["likely_running"] is True
    assert project.saves == []
    assert project.model3d.queries == queries


def test_model_changes_fail_fast_without_contacting_cst(tmp_path) -> None:
    client, project = _unresponsive_solve(tmp_path)
    client.solver_status(running_only=True)
    queries = project.model3d.queries
    result = client.run_history('Component.New "c"')
    assert result["code"] == "solver_unresponsive"
    assert project.model3d.history == [] and project.model3d.queries == queries


def test_save_is_refused_while_a_responsive_solver_runs(tmp_path) -> None:
    client, project = _client(tmp_path)
    project.model3d.answer = True
    result = client.save_project()
    assert result["status"] == "busy" and result["running"] is True
    assert project.saves == []


def test_idle_save_is_unchanged(tmp_path) -> None:
    client, project = _client(tmp_path)
    assert client.save_project() == {"status": "saved", "path": project.path}
    assert project.saves == [None]


def test_answer_from_cst_clears_the_state(tmp_path) -> None:
    client, project = _unresponsive_solve(tmp_path)
    client.solver_status(running_only=True)
    project.model3d.answer = True
    assert client.solver_status(running_only=True) == {"status": "ok", "running": True}
    assert client.solver_unresponsive() is None
    project.model3d.answer = False
    assert client.save_project()["status"] == "saved"


def test_stop_timeout_during_pending_solve_is_unresponsive(tmp_path) -> None:
    client, project = _unresponsive_solve(tmp_path)
    project.model3d.abort_times_out = True
    result = _data(simulation._handle_simple_solver_command("abort", {}, client))
    assert result["status"] == "unresponsive" and result["command"] == "stop"
    assert result["delivered"] == "unknown"
    assert "rather than resending" in result["note"]
    # The solve is still pending: a later wait can confirm the finish.
    assert client.pending_solve is not None


def test_stop_timeout_without_pending_solve_is_an_error(tmp_path) -> None:
    client, project = _client(tmp_path)
    project.model3d.abort_times_out = True
    assert client.abort_solver()["status"] == "error"


async def test_wait_returns_unresponsive_after_one_poll(tmp_path) -> None:
    client, _ = _unresponsive_solve(tmp_path)
    result = _data(await simulation.handle("cst_wait_for_simulation", {"max_wait_s": 30}, client))
    assert result["status"] == "unresponsive" and result["likely_running"] is True
    assert result["polls"] == 1 and "elapsed_s" in result
    assert client.pending_solve is not None


async def test_get_status_tool_reports_unresponsive(tmp_path) -> None:
    client, _ = _unresponsive_solve(tmp_path)
    result = _data(await simulation.handle("cst_get_simulation_status", {}, client))
    assert result["status"] == "unresponsive"


async def test_save_tool_reports_the_refusal(tmp_path) -> None:
    client, project = _unresponsive_solve(tmp_path)
    client.solver_status(running_only=True)
    result = _data(await project_tools.handle("cst_save_project", {}, client))
    assert result["code"] == "solver_unresponsive"
    assert project.saves == []


def test_unresponsive_status_includes_solver_log_tail(tmp_path) -> None:
    client, _ = _unresponsive_solve(tmp_path)
    log = tmp_path / "vivaldi" / "Result" / "output.txt"
    log.parent.mkdir(parents=True)
    log.write_text("x" * 5000 + "\nFrequency sample 7 of 21 done\n", encoding="utf-8")
    state = client.solver_status(running_only=True)
    solver_log = state["solver_log"]
    assert solver_log["path"] == str(log)
    assert solver_log["tail"].rstrip().endswith("Frequency sample 7 of 21 done")
    assert len(solver_log["tail"]) <= 1500
    assert solver_log["modified_s_ago"] >= 0
    assert "not verified" in solver_log["note"]


def test_closing_the_project_drops_the_state(tmp_path) -> None:
    client, project = _unresponsive_solve(tmp_path)
    client.solver_status(running_only=True)
    client.clear_pending_solve()
    assert client.solver_unresponsive() is None
    project.model3d.answer = False
    assert client.save_project()["status"] == "saved"


def test_descriptions_mention_unresponsive() -> None:
    tools = {t.name: t for t in simulation.TOOLS + project_tools.TOOLS}
    assert "'unresponsive'" in tools["cst_wait_for_simulation"].description
    assert "'unresponsive'" in tools["cst_get_simulation_status"].description
    assert "delivered='unknown'" in tools["cst_stop_simulation"].description
    assert "'busy'" in tools["cst_save_project"].description
