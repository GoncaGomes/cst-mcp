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


def test_face_client_native_boolean_readbacks():
    import run_face_from_curves as face

    # Replay the actual closure response that stopped the first live invocation.
    closed = face.parse_records("CLOSED\t-1\nDONE\n", {"CLOSED"})
    assert face.parse_native_bool(closed["CLOSED"]) is True
    sheet = face.parse_records("EXISTS\t-1\nIS_SOLID\t0\nDONE\n", {"EXISTS", "IS_SOLID"})
    assert face.parse_native_bool(sheet["EXISTS"]) is True
    assert face.parse_native_bool(sheet["IS_SOLID"]) is False
    assert face.parse_native_bool(" True ") is True
    assert face.parse_native_bool("FALSE") is False
    for unexpected in ("", "unknown", "2"):
        with pytest.raises(face.StopTest, match="Unexpected native Boolean"):
            face.parse_native_bool(unexpected)
    assert closed == {"CLOSED": "-1"}  # Retain the native text for evidence.


@pytest.mark.asyncio
async def test_face_client_raw_area_and_persistence():
    from unittest.mock import AsyncMock, Mock

    import run_face_from_curves as face

    # Actual native response from the user's successful sheet-area query.
    payload = {
        "status": "ok",
        "output": "AREA_ERROR_NUMBER\t0\nAREA_ERROR_DESCRIPTION\t[]\nAREA\t48\nDONE\n",
    }
    before = face.interpret_area_readback(payload)
    assert before["expected_planar_area"] == 24
    assert before["native_shape_area"] == 48
    assert before["measured_planar_area"] is None
    assert before["measurement_status"] == "pending manual inspection"
    assert before["normalization_applied"] is False
    assert before["native_records"]["AREA"] == "48"
    assert "unconfirmed hypothesis" in before["area_interpretation"]
    after = face.interpret_area_readback(payload)
    same = face.compare_native_area_persistence(before, after)
    assert same["passed"] is True
    assert same["one_sided_area_verified"] is False
    different = face.interpret_area_readback(
        dict(payload, output=payload["output"].replace("AREA\t48", "AREA\t40"))
    )
    assert face.compare_native_area_persistence(before, different)["passed"] is False
    unavailable = face.interpret_area_readback({"status": "unavailable"})
    assert face.compare_native_area_persistence(before, unavailable)["passed"] is None

    # Exercise the real measurement method without constructing a workspace or CST session.
    instance = face.FaceTest.__new__(face.FaceTest)
    instance.phase = "sheet_readback"
    instance.measurements = []
    instance.checks = []
    instance.actual_units = {"Length": "mm", "Frequency": "GHz", "Time": "ns"}
    instance.metadata = {"references": {"solid": {"path": "installed Solid reference"}}}
    instance.tag = lambda record: record
    instance.event = Mock()
    instance.owned_info = AsyncMock()
    instance.units = AsyncMock()
    instance.shapes = AsyncMock(return_value={face.SHAPE: "Vacuum"})
    instance.accepted = AsyncMock(
        return_value={"output": "EXISTS\t-1\nIS_SOLID\t0\nFACE_ID\t1\nDONE\n"}
    )
    instance.request = AsyncMock(return_value=payload)
    instance.messages_at = AsyncMock()
    await instance.measure(None)
    instance.phase = "reopened_persistence"
    await instance.measure(None)
    assert [m["native_shape_area"] for m in instance.measurements] == [48, 48]
    assert all(m["measured_planar_area"] is None for m in instance.measurements)
    assert instance.measurements[-1]["native_shape_area_persistence"]["passed"] is True
    instance.messages_at.assert_awaited()


def test_face_client_native_area_timeout_still_stops():
    import run_face_from_curves as face

    payload = {
        "status": "ok",
        "output": "AREA_ERROR_NUMBER\t5\nAREA_ERROR_DESCRIPTION\t[timed out]\nAREA\t0\nDONE\n",
    }
    with pytest.raises(face.UnknownState, match="timeout"):
        face.interpret_area_readback(payload)


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
