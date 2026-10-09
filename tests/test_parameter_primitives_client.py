"""Small local ownership/isolation checks. No real artifacts or CST access."""

import asyncio
import importlib.util
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "capabilities_test/02_parameters"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "parameter_primitives_client", SCRIPTS / "run_parameter_primitives.py"
)
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


@pytest.fixture
def client(tmp_path, monkeypatch):
    work = tmp_path / "artifacts" / "02_primitives"
    work.mkdir(parents=True)
    sibling = tmp_path / "artifacts" / "01_brick"
    sibling.mkdir()
    (sibling / "project.cst").write_bytes(b"brick checkpoint")
    monkeypatch.setattr(batch, "WORK", work)
    options = SimpleNamespace(
        cst_path=str(tmp_path / "cst"),
        reset=False,
        preflight=False,
        call_timeout=1,
        connection_timeout=1,
    )
    instance = batch.PrimitiveTest(options)
    yield instance
    instance.finalize()
    assert (sibling / "project.cst").read_bytes() == b"brick checkpoint"
    assert set(sibling.iterdir()) == {sibling / "project.cst"}


def own_saved_project(client):
    client.prepare_project()
    client.project.write_bytes(b"primitive checkpoint")
    client.project.with_suffix("").mkdir()
    (client.project.with_suffix("") / "model.txt").write_text("companion")
    client.manifest.update(
        generation_state="ready",
        fixture="ready",
        creation_requested=True,
        created_path=str(client.project),
        saved_sha256=batch.sha256(client.project),
        saved_files=client.project_snapshot(),
    )
    client.store_manifest()


def test_reservation_creates_no_project_or_source_copy(client):
    client.prepare_project()
    assert not client.project.exists()
    assert client.manifest["fixture"] == "absent"
    assert "source_copy" not in client.manifest
    assert batch.WORK.name == "02_primitives"


def test_reset_preserves_logs_notes_and_sibling(client):
    own_saved_project(client)
    notes = batch.WORK / "notes.md"
    notes.write_text("user notes")
    calls = (batch.WORK / "mcp_calls.jsonl").read_bytes()
    client.options.reset = True
    client.prepare_project()
    assert not client.project.exists() and not client.project.with_suffix("").exists()
    assert notes.read_text() == "user notes"
    assert (batch.WORK / "mcp_calls.jsonl").read_bytes().startswith(calls)
    assert client.manifest["generation_state"] == "creating"


def test_reset_rejects_files_appearing_before_client_creation(client):
    client.prepare_project()
    client.project.write_bytes(b"unverified project")
    client.options.reset = True
    with pytest.raises(batch.StopTest, match="ownership unverified"):
        client.prepare_project()
    assert client.project.read_bytes() == b"unverified project"


@pytest.mark.parametrize(
    "condition", ["file_change", "companion_change", "incomplete", "lock", "unsafe"]
)
def test_reuse_or_reset_refuses_unverified_state(client, condition):
    own_saved_project(client)
    if condition == "file_change":
        client.project.write_bytes(b"external edit")
    elif condition == "companion_change":
        (client.project.with_suffix("") / "model.txt").write_text("external edit")
    elif condition == "incomplete":
        client.manifest["generation_state"] = "running"
        client.store_manifest()
    elif condition == "lock":
        (client.project.with_suffix("") / "Model.lok").touch()
        client.options.reset = True
    else:
        client.manifest["generated_paths"].append("../01_brick/project.cst")
        client.store_manifest()
        client.options.reset = True
    before = client.project.read_bytes()
    with pytest.raises(batch.StopTest):
        client.prepare_project()
    assert client.project.read_bytes() == before


def test_retained_os_lock_prevents_concurrent_invocation(client):
    with (
        batch.WorkspaceLock(),
        pytest.raises(batch.StopTest, match="already in use"),
        batch.WorkspaceLock(),
    ):
        raise AssertionError("second lock acquired")
    assert (batch.WORK / "workspace.lock").exists()


def test_unknown_response_freezes_primitive_client_transport(client):
    client.catalog["cst_set_parameter"] = {"inputSchema": {"type": "object"}}
    calls = []

    class Session:
        async def call_tool(self, name, args):
            calls.append((name, args))
            return {"content": [{"type": "text", "text": json.dumps({"status": "timeout"})}]}

    session = Session()
    with pytest.raises(batch.UnknownState):
        asyncio.run(client.request(session, "cst_set_parameter", {"name": "PGeom_R", "value": 3}))
    for name in ("cst_read_project_log", "cst_save_project", "cst_close_project", "cst_disconnect"):
        with pytest.raises(batch.UnknownState):
            asyncio.run(client.request(session, name))
    assert len(calls) == 1


def test_torus_reference_uses_outer_inner_extents_and_ellipse_area_is_unsupported():
    expected = batch.analytic_expected(2, 6)  # Outer=6, inner=4; major=5, tube=1.
    assert expected["Torus.VOLUME"] == pytest.approx(10 * math.pi**2)
    assert expected["Torus.AREA"] == pytest.approx(20 * math.pi**2)
    assert "ECylinder.AREA" not in expected
