"""Fixes from the 2026-11-24 live-run assessment (docs/tasks/mcp 01-03), fakes only."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from jsonschema import ValidationError, validate

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import simulation, solvers
from cst_mcp.tools.registry import _tool_schema

# --- 01: a failed solver selection must surface its own error -----------------------------


class SelectionClient:
    def __init__(self, selection: dict) -> None:
        self.selection = selection
        self.started = False

    def solver_status(self, timeout_s: float = 30.0) -> dict:
        return {"status": "ok", "running": False}

    def execute_vba(self, code: str, history_label: str | None = None) -> dict:
        return dict(self.selection)

    def start_solver(self, timeout_s: float = 30.0) -> dict:
        self.started = True
        return {"status": "started"}

    def run_solver(self, timeout_s: float = 3600.0) -> dict:
        self.started = True
        return {"status": "finished"}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["cst_run_simulation", "cst_run_simulation_async"])
async def test_failed_solver_selection_returns_its_own_error(name) -> None:
    client = SelectionClient({"status": "error", "message": "Solver type not licensed"})
    content = await simulation.handle(name, {"solver_type": "Frequency Domain"}, client)
    data = json.loads(content[0].text)  # valid JSON, no circular reference

    assert data["status"] == "error"
    assert data["message"] == "Solver type not licensed"
    assert data["solver_selection"] == {
        "status": "error",
        "message": "Solver type not licensed",
    }
    assert client.started is False


@pytest.mark.asyncio
async def test_successful_solver_selection_is_still_attached() -> None:
    client = SelectionClient({"status": "executed"})
    content = await simulation.handle(
        "cst_run_simulation_async", {"solver_type": "Time Domain"}, client
    )
    data = json.loads(content[0].text)
    assert data["status"] == "started"
    assert data["solver_selection"] == {"status": "executed"}
    assert client.started is True


# --- 02: relative accuracy ranges are published in the schemas ----------------------------


def _schema(name: str) -> dict:
    tool = next(t for t in solvers.TOOLS if t.name == name)
    return _tool_schema(tool, "inputSchema", "input_schema")


def test_frequency_domain_accuracy_range_is_published() -> None:
    field = _schema("cst_configure_frequency_domain_solver")["properties"]["accuracy"]
    assert field["minimum"] == 1e-12 and field["maximum"] == 1e-3
    assert "not db" in field["description"].lower()

    base = {"f_min": 1, "f_max": 2}
    validate({**base, "accuracy": 1e-6}, _schema("cst_configure_frequency_domain_solver"))
    for bad in (-40, 1e-2, 1e-13):
        with pytest.raises(ValidationError):
            validate({**base, "accuracy": bad}, _schema("cst_configure_frequency_domain_solver"))


@pytest.mark.parametrize(
    "name",
    [
        "cst_configure_eigenmode_solver",
        "cst_configure_eigenmode_advanced",
        "cst_configure_integral_equation_solver",
        "cst_configure_ie_solver_advanced",
    ],
)
def test_other_relative_accuracies_reject_db_values(name) -> None:
    field = _schema(name)["properties"]["accuracy"]
    assert field["exclusiveMinimum"] == 0
    assert "not db" in field["description"].lower()
    with pytest.raises(ValidationError):
        validate({"accuracy": -40}, _schema(name))


def test_time_domain_accuracy_stays_in_db() -> None:
    field = _schema("cst_configure_time_domain_solver")["properties"]["accuracy"]
    assert field["minimum"] == -80 and field["maximum"] == 0


# --- 03: saving to the project's own path saves in place ----------------------------------


class FakeProject:
    def __init__(self, filename: str, existing: set[str] | None = None) -> None:
        self._filename = filename
        self.existing = existing or set()
        self.calls: list[str | None] = []

    def filename(self) -> str:
        return self._filename

    def save(self, path: str = "") -> None:
        # Mirrors CST: an explicit existing path is refused (allow_overwrite=False).
        self.calls.append(path or None)
        if path and path.lower() in self.existing:
            raise RuntimeError(f"Failed to save project. The given path already exists: {path}")


def _client(project: FakeProject) -> CSTClient:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client._de = object()
    client._project = project
    client._project_path = project.filename()
    return client


CURRENT = r"C:\work\Vivaldi\vivaldi.cst"


@pytest.mark.parametrize(
    "path",
    [
        CURRENT,
        r"c:\WORK\vivaldi\VIVALDI.CST",
        "C:/work/Vivaldi/vivaldi.cst",
        r"C:\work\Vivaldi\..\Vivaldi\vivaldi.cst",
        None,
    ],
)
def test_save_with_current_path_saves_in_place(path) -> None:
    project = FakeProject(CURRENT, existing={CURRENT.lower()})
    client = _client(project)
    result = client.save_project(path)
    assert result == {"status": "saved", "path": CURRENT}
    assert project.calls == [None]


def test_save_to_a_different_existing_path_still_fails() -> None:
    other = r"C:\work\Other\other.cst"
    project = FakeProject(CURRENT, existing={CURRENT.lower(), other.lower()})
    client = _client(project)
    result = client.save_project(other)
    assert result["status"] == "error"
    assert "already exists" in result["message"]
    assert project.calls == [other]
    assert client.project_path == CURRENT


def test_save_as_new_path_updates_the_project_path() -> None:
    new = r"C:\work\Copy\copy.cst"
    project = FakeProject(CURRENT)
    client = _client(project)
    assert client.save_project(new) == {"status": "saved", "path": new}
    assert project.calls == [new]


def test_live_filename_wins_over_a_stale_cached_path() -> None:
    project = FakeProject(CURRENT, existing={CURRENT.lower()})
    client = _client(project)
    client._project_path = r"C:\stale\requested.cst"
    assert client.save_project(CURRENT)["path"] == CURRENT
    assert project.calls == [None]


def test_save_without_filename_support_uses_cached_path() -> None:
    project = SimpleNamespace(calls=[])
    project.save = lambda path="": project.calls.append(path or None)
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client._de = object()
    client._project = project
    client._project_path = CURRENT
    assert client.save_project(CURRENT.lower()) == {"status": "saved", "path": CURRENT}
    assert project.calls == [None]
