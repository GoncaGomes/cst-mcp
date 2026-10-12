"""Compact extrusion regressions and independent-client preservation checks, offline."""

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.tools import geometry
from cst_mcp.validators import validate_vba_input

BASE = {
    "component": "c",
    "name": "e",
    "height": "H",
    "axis": "y",
    "y_offset": "Offset",
    "points": [[0, 0], ["W", 0], ["W", "V"], [0, "V"]],
    "holes": [[["W/4", 1], ["W/4", "V-1"], ["3*W/4", "V-1"], ["3*W/4", 1]]],
}


@pytest.mark.parametrize("shape", ["extrude", "polygon_extrude"])
def test_mixed_history_dependencies_holes_and_y_negation(shape):
    code = geometry._HANDLERS[f"cst_create_{shape}"](BASE)
    validate_vba_input(code)
    assert 'cstProfile0U(0) = Evaluate("0")' in code
    assert 'cstProfile0V(0) = Evaluate("0")' in code
    assert 'cstProfile1V(0) = Evaluate("1")' in code
    assert 'cstProfile0U(1) = Evaluate("W")' in code
    assert 'Evaluate("Offset")' in code and 'Evaluate("W")' in code
    assert code.index("cstProfile1Area = 0") < code.index(".Create")
    assert 'Evaluate("V-1")' in code and 'Evaluate("3*W/4")' in code
    assert code.index('.Name "e_hole1"') < code.index('Solid.Subtract "c:e", "c:e_hole1"')
    if shape == "extrude":
        assert '.Height "H"' in code and '.Origin "0", Evaluate("Offset"), "0"' in code
        down = geometry._build_extrude({**BASE, "extrude_direction": "down"})
        assert '.Vvector "0", "0", "1"' in down and 'Evaluate("-(V)")' in down
        assert '.Height "H"' in down and '.Origin "0", Evaluate("Offset"), "0"' in down
    else:
        assert '.Thickness Evaluate("H")' in code and 'Evaluate("-(V)")' in code
        assert "If cstProfile0Area < 0 Then" in code and "If cstProfile1Area < 0 Then" in code
        down = geometry._build_polygon_extrude({**BASE, "extrude_direction": "down"})
        assert "If cstProfile0Area > 0 Then" in down and "If cstProfile1Area > 0 Then" in down
        assert down.count("Err.Raise 5") == 2
        # Both possible orderings retain the same world coordinates and base.
        assert down.count('.Point Evaluate("W"), Evaluate("Offset"), Evaluate("-(V)")') == 2


@pytest.mark.parametrize("shape", ["extrude", "polygon_extrude"])
@pytest.mark.parametrize(
    "changes",
    [
        {"height": True},
        {"y_offset": None},
        {"x_offset": "0"},
        {"z_offset": False},
        {"holes": None},
        {"extrude_direction": ""},
        {"points": [[0, 0], [1, 0], [1, "V\x01"]]},
    ],
)
def test_representative_invalid_values_never_execute(shape, changes):
    calls = []

    class NoExecution:
        def execute_vba(self, code):
            calls.append(code)
            raise AssertionError("invalid input must be rejected before execution")

    result = asyncio.run(geometry.handle(f"cst_create_{shape}", {**BASE, **changes}, NoExecution()))
    assert json.loads(result[0].text)["status"] == "error"
    assert calls == []


@pytest.mark.parametrize(
    "native",
    [
        {"status": "error", "message": "native error", "cst_messages_tail": ["detail"], "code": 7},
        {"status": "timeout", "execution_state": "unknown", "cst_messages_tail": ["pending"]},
        {"status": "executed", "execution_state": "unknown", "cst_messages_tail": ["pending"]},
    ],
)
def test_native_failures_remain_complete_without_creation_metadata(native):
    class Client:
        def execute_vba(self, code):
            return native

    result = asyncio.run(geometry.handle("cst_create_polygon_extrude", BASE, Client()))
    assert json.loads(result[0].text) == native
    assert "extrusion" not in native


def test_symbolic_metadata_is_prepared_before_mutation(monkeypatch):
    executed = []

    class Client:
        def execute_vba(self, code):
            assert prepared
            executed.append(code)
            return {"status": "executed"}

    prepared = []
    original = geometry._polygon_extrusion_note

    def prepare(args):
        note = original(args)
        prepared.append(note)
        return note

    monkeypatch.setattr(geometry, "_polygon_extrusion_note", prepare)
    result = asyncio.run(geometry.handle("cst_create_polygon_extrude", BASE, Client()))
    note = json.loads(result[0].text)["extrusion"]
    assert note["numeric_range_evaluated"] is False and "expected_range" not in note
    assert note["symbolic_endpoints"] == {"base": "Offset", "end": "(Offset) + (H)"}
    assert (
        original({**BASE, "extrude_direction": "down"})["symbolic_endpoints"]["end"]
        == "(Offset) - (H)"
    )
    assert original({**BASE, "height": -2, "y_offset": 3})["expected_range"] == [1, 3]

    def broken_metadata(args):
        return {"not_serializable": object()}

    monkeypatch.setattr(geometry, "_polygon_extrusion_note", broken_metadata)
    rejected = asyncio.run(geometry.handle("cst_create_polygon_extrude", BASE, Client()))
    assert json.loads(rejected[0].text)["status"] == "error" and len(executed) == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "capabilities_test/02_parameters"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "extrusions_client", scripts / "run_parameter_extrusions.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.WORK.name == "03_extrusions"
    assert not any(
        isinstance(value, type) and value.__name__ in {"ParameterTest", "PrimitiveTest"}
        for value in vars(module).values()
    )
    work = tmp_path / "03_extrusions"
    work.mkdir()
    monkeypatch.setattr(module, "WORK", work)
    options = SimpleNamespace(
        cst_path=str(tmp_path),
        reset=False,
        preflight=False,
        call_timeout=0.01,
        connection_timeout=0.01,
    )
    instance = module.ExtrusionTest(options)
    yield module, instance
    instance.finalize()


def test_client_checkpoint_guard_and_scoped_reset(client, tmp_path):
    module, instance = client
    sibling = tmp_path / "02_primitives"
    sibling.mkdir()
    (sibling / "project.cst").write_bytes(b"earlier project")
    instance.prepare_project()
    instance.project.write_bytes(b"owned project")
    instance.project.with_suffix("").mkdir()
    instance.manifest.update(
        generation_state="ready",
        fixture="ready",
        creation_requested=True,
        saved_sha256=module.sha256(instance.project),
        saved_files=instance.project_snapshot(),
    )
    instance.store_manifest()
    instance.prepare_project()  # Verified checkpoint reuse.
    instance.project.write_bytes(b"changed externally")
    with pytest.raises(module.StopTest, match="changed or incomplete"):
        instance.prepare_project()
    instance.options.reset = True
    instance.prepare_project()
    assert not instance.project.exists()
    assert (sibling / "project.cst").read_bytes() == b"earlier project"
    assert (module.WORK / "mcp_calls.jsonl").is_file()


@pytest.mark.parametrize("failure", ["native_unknown", "transport_timeout"])
def test_client_stops_all_calls_after_unknown(client, failure):
    module, instance = client
    instance.catalog["cst_set_parameter"] = {"inputSchema": {"type": "object"}}
    calls = []

    class Session:
        async def call_tool(self, name, args):
            calls.append(name)
            if failure == "transport_timeout":
                await asyncio.sleep(1)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "status": "timeout",
                                "execution_state": "unknown",
                                "cst_messages_tail": ["pending"],
                            }
                        ),
                    }
                ]
            }

    async def exercise():
        with pytest.raises(module.UnknownState):
            await instance.request(Session(), "cst_set_parameter", {"name": "PEx_W", "value": 8})
        with pytest.raises(module.UnknownState):
            await instance.request(Session(), "cst_disconnect")

    asyncio.run(exercise())
    assert calls == ["cst_set_parameter"] and instance.unknown
