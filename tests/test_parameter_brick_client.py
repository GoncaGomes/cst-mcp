"""Batch 02 local workspace and MCP-response substitutes; never contact CST."""

import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.tools import connection, diagnostics, geometry, parameters, project, vba

SCRIPT = (
    Path(__file__).resolve().parents[1] / "capabilities_test/02_parameters/run_parameter_brick.py"
)
spec = importlib.util.spec_from_file_location("parameter_brick_client", SCRIPT)
batch = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = batch
spec.loader.exec_module(batch)


@pytest.fixture
def client(tmp_path, monkeypatch):
    work = tmp_path / "artifacts"
    work.mkdir()
    source = tmp_path / "source" / "project.cst"
    source.parent.mkdir()
    source.write_bytes(b"saved source project")
    companion = source.with_suffix("")
    (companion / "Model").mkdir(parents=True)
    (companion / "Model" / "model.txt").write_text("source companion", encoding="utf-8")
    (source.parent / "summary.md").write_text("original report", encoding="utf-8")
    monkeypatch.setattr(batch, "WORK", work)
    options = SimpleNamespace(
        source_project=str(source),
        cst_path=str(tmp_path / "cst"),
        reset=False,
        preflight=False,
        call_timeout=1,
        connection_timeout=1,
        pause_for_inspection=False,
    )
    instance = batch.ParameterTest(options)
    yield instance
    if not instance.calls.closed:
        instance.finalize()


def load_catalog(client):
    client.catalog = {
        t.name: t.model_dump(mode="json", by_alias=True)
        for module in (connection, diagnostics, geometry, parameters, project, vba)
        for t in module.TOOLS
    }


def result(payload, *, is_error=False):
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": is_error}


class Session:
    """Only an MCP-shaped test substitute, with no CST or server handler calls."""

    def __init__(self, response=None):
        self.calls = []
        self.response = response

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def test_copy_preserves_original_and_records_actual_fingerprints(client):
    client.prepare_project()
    assert client.project.read_bytes() == client.source.read_bytes()
    assert (batch.WORK / "project/Model/model.txt").read_text() == "source companion"
    assert not (batch.WORK / "summary.md").exists()
    assert (client.source.parent / "summary.md").read_text() == "original report"
    provenance = client.manifest["source_copy"]
    assert provenance["files"][0]["sha256"] == batch.sha256(client.source)
    assert provenance["invocation"] == client.invocation
    assert client.manifest["fixture"] == "absent"


def test_rerun_keeps_owned_copy_when_source_changes(client):
    client.prepare_project()
    original = client.project.read_bytes()
    client.source.write_bytes(b"new source save")
    client.prepare_project()
    assert client.project.read_bytes() == original


def test_reset_preserves_logs_notes_reports_and_source(client):
    client.prepare_project()
    notes = batch.WORK / "manual_inspection.md"
    notes.write_text("user observations", encoding="utf-8")
    calls_before = (batch.WORK / "mcp_calls.jsonl").read_bytes()
    client.source.write_bytes(b"new saved source")
    client.options.reset = True
    client.prepare_project()
    assert client.project.read_bytes() == b"new saved source"
    assert notes.read_text() == "user observations"
    assert (batch.WORK / "mcp_calls.jsonl").read_bytes().startswith(calls_before)
    assert (client.source.parent / "summary.md").read_text() == "original report"
    events = [json.loads(line) for line in (batch.WORK / "metadata.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events].count("source_copied") == 2
    assert all({"invocation", "timestamp", "sequence", "phase"} <= e.keys() for e in events)


def test_source_lock_even_zero_bytes_blocks_copy_and_is_preserved(client):
    lock = client.source.with_suffix("") / "Model.lok"
    lock.touch()
    with pytest.raises(batch.StopTest, match="Lock files found"):
        client.prepare_project()
    assert lock.exists()
    assert not client.project.exists()


def test_working_lock_blocks_reset_without_deleting_anything(client):
    client.prepare_project()
    lock = client.project.with_suffix("") / "Model.lok"
    lock.touch()
    client.options.reset = True
    with pytest.raises(batch.StopTest, match="Lock files found"):
        client.prepare_project()
    assert client.project.exists() and lock.exists()


@pytest.mark.parametrize("path", ["../outside.cst", "project/../outside.cst", "summary.md"])
def test_reset_rejects_unverified_paths_before_deletion(client, path):
    client.prepare_project()
    client.manifest["generated_paths"].append(path)
    client.store_manifest()
    client.options.reset = True
    with pytest.raises(batch.StopTest, match="Unsafe generated"):
        client.prepare_project()
    assert client.project.exists()


def test_changed_owned_project_requires_explicit_reset(client):
    client.prepare_project()
    client.project.write_bytes(b"user-edited owned file")
    with pytest.raises(batch.StopTest, match="changed since"):
        client.prepare_project()
    assert client.project.read_bytes() == b"user-edited owned file"


def test_concurrent_workspace_lock_refuses_second_holder(client):
    with (
        batch.WorkspaceLock(),
        pytest.raises(batch.StopTest, match="already in use"),
        batch.WorkspaceLock(),
    ):
        raise AssertionError("second lock acquired")


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "timeout"},
        {"status": "error", "message": "Operation timed out."},
        {"status": "error", "execution_state": "unknown"},
        {"status": "busy", "running": None},
    ],
)
def test_unknown_response_forbids_all_later_mcp_calls(client, payload):
    load_catalog(client)
    session = Session(result(payload))
    with pytest.raises(batch.UnknownState):
        asyncio.run(client.request(session, "cst_set_parameter", {"name": "PBrick_L", "value": 12}))
    for tool in ("cst_read_project_log", "cst_save_project", "cst_close_project", "cst_disconnect"):
        with pytest.raises(batch.UnknownState):
            asyncio.run(client.request(session, tool))
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "error",
    [TimeoutError("native wait"), ConnectionError("lost transport"), asyncio.CancelledError()],
)
def test_transport_loss_or_interrupt_forbids_replay(client, error):
    load_catalog(client)
    session = Session(error)
    with pytest.raises((batch.UnknownState, asyncio.CancelledError)):
        asyncio.run(client.request(session, "cst_set_parameter", {"name": "PBrick_L", "value": 12}))
    assert client.unknown
    with pytest.raises(batch.UnknownState):
        asyncio.run(client.request(session, "cst_save_project"))
    assert len(session.calls) == 1


def test_complete_responses_messages_and_iserror_are_retained(client):
    load_catalog(client)
    response = result({"status": "ok", "messages": ["inherited", "inherited"]})
    session = Session(response)
    asyncio.run(client.messages_at(session))
    records = [
        json.loads(line) for line in (batch.WORK / "mcp_calls.jsonl").read_text().splitlines()
    ]
    assert records[0]["event"] == "request_start"
    assert records[1]["response"] == response
    assert records[1]["arguments"] == {} and records[1]["isError"] is False
    messages = [
        json.loads(line) for line in (batch.WORK / "cst_messages.jsonl").read_text().splitlines()
    ]
    assert messages[0]["payload"]["messages"] == ["inherited", "inherited"]
    assert all(
        {"invocation", "timestamp", "sequence", "phase"} <= r.keys() for r in records + messages
    )


def test_multiblock_error_is_not_hidden(client):
    load_catalog(client)
    response = result({"status": "ok"})
    response["content"].append({"type": "text", "text": '{"status":"error","message":"failed"}'})
    with pytest.raises(batch.StopTest):
        asyncio.run(client.request(Session(response), "cst_save_project"))


def test_arbitrary_raw_vba_rejected_before_transport(client):
    load_catalog(client)
    session = Session()
    with pytest.raises(batch.StopTest, match="fixed read-only"):
        asyncio.run(client.request(session, "cst_execute_vba", {"code": 'Solid.Delete "x:y"'}))
    assert session.calls == []


def test_native_measurement_parser_is_strict_and_locale_aware():
    assert batch.parse_measurements("VOLUME\t120,0\nAREA\t184.0\nDONE\n") == {
        "VOLUME": 120,
        "AREA": 184,
    }
    for bad in (
        "",
        "VOLUME\t120\nDONE",
        "VOLUME\tNaN\nAREA\t184\nDONE",
        "VOLUME\t120\nAREA\t184\nAREA\t184\nDONE",
    ):
        with pytest.raises((batch.StopTest, ValueError)):
            batch.parse_measurements(bad)


class ScenarioSession(Session):
    """Analytic substitute used only to check orchestration, not real geometry."""

    def __init__(self, client, *, baseline_error=False, unexpected=False):
        super().__init__()
        self.client = client
        self.params = {}
        self.solid = unexpected
        self.open = False
        self.baseline_error = baseline_error

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        status = "ok"
        payload = {}
        if name == "cst_connect":
            payload = {
                "status": "connected",
                "mode": "new",
                "newly_started": True,
                "open_projects": 0,
                "open_project_paths": [],
                "project_path": None,
            }
        elif name == "cst_open_project":
            self.open = True
            status = "opened"
        elif name in {"cst_project_info", "cst_connection_status"}:
            payload = {
                "mode": "connected",
                "project_path": str(self.client.project) if self.open else None,
                "project_open": self.open,
                "solver_running": False,
            }
        elif name == "cst_set_parameter":
            self.params[args["name"]] = args["value"]
            if args.get("rebuild", True) and self.baseline_error:
                return result(
                    {"status": "error", "stage": "rebuild", "message": "inherited history failed"},
                    is_error=True,
                )
            payload = {
                "status": "ok",
                "history_written": False,
                "rebuilt": args.get("rebuild", True),
            }
        elif name == "cst_create_brick":
            assert not self.solid, "duplicate creation"
            self.solid = True
            status = "executed"
        elif name == "cst_list_parameters":
            payload = {
                "status": "ok",
                "output": "".join(f"{n} = {v}\n" for n, v in self.params.items()),
            }
        elif name == "cst_get_parameter":
            payload = {
                "status": "ok",
                "output": f"Parameter {args['name']} = {self.params[args['name']]}\n",
            }
        elif name == "cst_execute_vba":
            code = args["code"]
            if code == batch.UNITS_QUERY:
                output = "Length\tmm\nFrequency\tGHz\nTime\tns\n"
            elif code == batch.SHAPES_QUERY:
                output = (
                    f"COUNT\t1\nSOLID\t0\t{batch.SOLID}\tPEC\nDONE\n"
                    if self.solid
                    else "COUNT\t0\nDONE\n"
                )
            elif code == batch.MEASURE_QUERY:
                length, width, height = self.params["PBrick_L"], 6, self.params["PBrick_H"] / 2
                output = f"VOLUME\t{length * width * height}\nAREA\t{2 * (length * width + length * height + width * height)}\nDONE\n"
            else:
                raise AssertionError("unknown query")
            payload = {"status": "ok", "output": output}
        elif name == "cst_save_project":
            self.client.project.write_text(json.dumps(self.params), encoding="utf-8")
            payload = {"status": "saved", "path": str(self.client.project)}
        elif name == "cst_close_project":
            self.open = False
            status = "closed"
        elif name == "cst_disconnect":
            status = "disconnected"
        elif name != "cst_read_project_log":
            raise AssertionError(f"unexpected tool {name}")
        return result(payload or {"status": status})


def test_full_scenario_and_rerun_do_not_duplicate_fixture(client):
    client.prepare_project()
    load_catalog(client)
    session = ScenarioSession(client)
    asyncio.run(client.live(session))
    assert client.exit_code == 0 and not session.open
    assert session.params == {"PBrick_L": 12, "PBrick_H": 6}
    assert client.manifest["fixture"] == "ready"
    assert sum(n == "cst_create_brick" for n, _ in session.calls) == 1
    before = len(session.calls)
    asyncio.run(client.live(session))
    assert not any(n == "cst_create_brick" for n, _ in session.calls[before:])
    assert client.exit_code == 0


def test_inherited_rebuild_failure_stops_before_creation(client):
    client.prepare_project()
    load_catalog(client)
    session = ScenarioSession(client, baseline_error=True)
    with pytest.raises(batch.StopTest):
        asyncio.run(client.live(session))
    assert client.phase == "baseline_rebuild"
    assert not any(
        n in {"cst_create_brick", "cst_save_project", "cst_close_project"} for n, _ in session.calls
    )


def test_unexpected_object_is_never_deleted_or_reused(client):
    client.prepare_project()
    load_catalog(client)
    session = ScenarioSession(client, unexpected=True)
    with pytest.raises(batch.StopTest):
        asyncio.run(client.live(session))
    assert not any(
        n in {"cst_set_parameter", "cst_create_brick", "cst_save_project"} for n, _ in session.calls
    )


def test_unknown_run_finalizes_reports_without_cleanup_calls(client, monkeypatch):
    load_catalog(client)
    session = ScenarioSession(client)
    original = session.call_tool

    async def unknown_change(name, args):
        if name == "cst_set_parameter":
            session.calls.append((name, args))
            return result({"status": "timeout", "execution_state": "unknown"})
        return await original(name, args)

    session.call_tool = unknown_change

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield (None, None)

    @asynccontextmanager
    async def session_context(*args):
        yield session

    async def skip_catalog(_):
        return None

    monkeypatch.setattr(batch, "stdio_client", transport)
    monkeypatch.setattr(batch, "ClientSession", session_context)
    monkeypatch.setattr(batch, "preserve_cst_processes", nullcontext)
    monkeypatch.setattr(client, "catalog_and_preflight", skip_catalog)
    assert asyncio.run(client.run()) == 2
    assert session.calls[-1][0] == "cst_set_parameter"
    summary = json.loads((batch.WORK / "summary.json").read_text())
    assert summary["indeterminate"] is True and summary["scenario_completed"] is False
    assert (batch.WORK / "metadata.jsonl").read_text().count('"event": "invocation_end"') == 1
    assert client.project.exists()


def test_preflight_uses_disabled_mode_and_never_copies_project(client, monkeypatch):
    # Re-create options through the constructor to check the actual environment.
    client.options.preflight = True
    client.finalize()
    offline = batch.ParameterTest(client.options)
    load_catalog(offline)

    async def preflight_only(_):
        offline.exit_code = 0
        offline.reason = "substitute preflight"

    @asynccontextmanager
    async def transport(params, **kwargs):
        assert params.command == sys.executable and params.args == ["-m", "cst_mcp.server"]
        assert params.env["CST_CONNECT_MODE"] == "disabled"
        yield (None, None)

    @asynccontextmanager
    async def session_context(*args):
        yield Session()

    monkeypatch.setattr(batch, "stdio_client", transport)
    monkeypatch.setattr(batch, "ClientSession", session_context)
    monkeypatch.setattr(batch, "preserve_cst_processes", nullcontext)
    monkeypatch.setattr(offline, "catalog_and_preflight", preflight_only)
    assert asyncio.run(offline.run()) == 0
    assert not offline.project.exists() and not (batch.WORK / "workspace.json").exists()
    summary = json.loads((batch.WORK / "summary.json").read_text())
    assert summary["independently_verified_properties"] == []


def test_reset_can_recover_owned_incomplete_copy(client):
    client.prepare_project()
    client.manifest["copy_state"] = "copying"
    client.store_manifest()
    client.options.reset = True
    client.prepare_project()
    assert client.manifest["copy_state"] == "ready"
