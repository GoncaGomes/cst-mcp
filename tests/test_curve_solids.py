"""cst_create_curve_wire and cst_create_trace_from_curve (task 18), with a fake client."""

from __future__ import annotations

import json

import pytest

from cst_mcp.tools import geometry
from cst_mcp.tools.annotations import is_destructive, is_read_only
from cst_mcp.validators import validate_vba_input

BOUNDS_OUTPUT = (
    "unit\t0,001\n"
    "0\tbox\t-20,68\t20,68\t-20,69\t20,69\t3,49\t172,49\n"
    "0\tmaterial\tPEC\n"
    "0\tvolume\t606,7\n"
)


class FakeClient:
    """Records every call; history results are scripted in order (default: executed)."""

    def __init__(self, *, connected=True, curve_output="points\t2\n", history=()):
        self.connected = connected
        self.has_project = connected
        self.curve_output = curve_output
        self.history = list(history)
        self.executed: list[str] = []
        self.queries: list[str] = []

    def capture_vba_output(self, code: str) -> dict:
        self.queries.append(code)
        if "GetNumberOfPoints" in code:
            return {"status": "ok", "output": self.curve_output}
        if "GetLooseBoundingBoxOfShape" in code:
            return {"status": "ok", "output": BOUNDS_OUTPUT}
        raise AssertionError(f"unexpected query: {code}")

    def execute_vba(self, code: str, history_label=None) -> dict:
        self.executed.append(code)
        result = self.history.pop(0) if self.history else {"status": "executed"}
        return {"label": f"cst_mcp_{len(self.executed)}", **result}


WIRE = {"curve": "Curves:helix", "component": "Antenna", "name": "helix", "radius": 0.5}
TRACE = {
    "curve": "Curves:spiral",
    "component": "Antenna",
    "name": "spiral",
    "width": 0.5,
    "thickness": 0.035,
}


async def _call(name: str, arguments: dict, client) -> dict:
    content = await geometry.handle(name, arguments, client)
    return json.loads(content[0].text)


@pytest.mark.asyncio
async def test_curve_wire_offline_vba() -> None:
    data = await _call("cst_create_curve_wire", WIRE, FakeClient(connected=False))
    assert data["status"] == "offline" and data["solid"] == "Antenna:helix"
    wire, convert = data["history_blocks"]
    for line in (
        '.Name "helix"',
        '.Type "Curvewire"',
        '.Curve "Curves:helix"',
        '.Radius "0.5"',
        '.SolidWireModel "True"',
        '.Material "PEC"',
        '.Termination "natural"',
        ".Add",
    ):
        assert line in wire
    for line in (
        '.SolidName "Antenna:helix"',
        '.Name "helix"',
        '.KeepWire "False"',
        ".ConvertToSolidShape",
    ):
        assert line in convert
    for block in data["history_blocks"]:
        validate_vba_input(block)


@pytest.mark.asyncio
async def test_curve_wire_builds_converts_and_measures() -> None:
    client = FakeClient()
    data = await _call("cst_create_curve_wire", {**WIRE, "material": "Copper (annealed)"}, client)
    assert data["status"] == "executed"
    assert data["solid"] == "Antenna:helix" and data["curve_consumed"] is True
    assert data["history_labels"] == ["cst_mcp_1", "cst_mcp_2"]
    assert len(client.executed) == 2
    assert ".Add" in client.executed[0] and ".ConvertToSolidShape" in client.executed[1]
    assert '.Material "Copper (annealed)"' in client.executed[0]
    (solid,) = data["measured"]["solids"]
    assert solid["volume"] == pytest.approx(606.7)
    assert solid["bounds"]["z"] == [3.49, 172.49]
    assert data["measured"]["unit"] == "mm"
    # The curve check runs first, the bounds query last; neither writes history.
    assert 'Curve.GetNumberOfPoints("Curves:helix")' in client.queries[0]
    assert 'GetLooseBoundingBoxOfShape("Antenna:helix"' in client.queries[1]


@pytest.mark.asyncio
async def test_missing_curve_is_refused_before_any_change() -> None:
    client = FakeClient(curve_output="error\tCurve does not exist: Curves:helix\n")
    data = await _call("cst_create_curve_wire", WIRE, client)
    assert data["status"] == "error" and data["code"] == "curve_not_found"
    assert data["project_changed"] is False
    assert "Curve does not exist" in data["message"]
    assert client.executed == []


@pytest.mark.asyncio
async def test_failed_curve_check_query_changes_nothing() -> None:
    client = FakeClient()
    client.capture_vba_output = lambda code: {"status": "busy", "message": "solver running"}
    data = await _call("cst_create_trace_from_curve", TRACE, client)
    assert data["status"] == "busy" and data["stage"] == "curve_check"
    assert client.executed == []


@pytest.mark.asyncio
async def test_rejected_wire_stops_before_conversion() -> None:
    client = FakeClient(
        history=[{"status": "error", "message": "The specified curve could not be accessed"}]
    )
    data = await _call("cst_create_curve_wire", WIRE, client)
    assert data["status"] == "error" and data["stage"] == "create"
    assert data["project_changed"] is False
    assert len(client.executed) == 1


@pytest.mark.asyncio
async def test_failed_conversion_deletes_the_wire() -> None:
    client = FakeClient(
        history=[{"status": "executed"}, {"status": "error", "message": "conversion failed"}]
    )
    data = await _call("cst_create_curve_wire", WIRE, client)
    assert data["status"] == "error" and data["stage"] == "convert"
    assert data["wire_removed"] is True and data["curve_consumed"] is True
    assert client.executed[2] == 'Wire.Delete "helix"'
    assert data["history_labels"] == ["cst_mcp_1"]
    assert "create the curve again" in data["note"]


@pytest.mark.asyncio
async def test_failed_rollback_is_reported() -> None:
    client = FakeClient(
        history=[
            {"status": "executed"},
            {"status": "error", "message": "conversion failed"},
            {"status": "error", "message": "The specified wire does not exist."},
        ]
    )
    data = await _call("cst_create_curve_wire", WIRE, client)
    assert data["wire_removed"] is False
    assert data["rollback"]["status"] == "error"
    assert "still in the project" in data["note"]


@pytest.mark.asyncio
async def test_conversion_timeout_makes_no_further_calls() -> None:
    client = FakeClient(
        history=[{"status": "executed"}, {"status": "timeout", "execution_state": "unknown"}]
    )
    data = await _call("cst_create_curve_wire", WIRE, client)
    assert data["status"] == "timeout" and data["stage"] == "convert"
    assert len(client.executed) == 2
    assert len(client.queries) == 1  # Only the curve check; no bounds query.


@pytest.mark.asyncio
async def test_trace_vba_and_measurement() -> None:
    client = FakeClient()
    data = await _call(
        "cst_create_trace_from_curve",
        {**TRACE, "round_start": True, "gap_type": "rounded", "material": "Copper (annealed)"},
        client,
    )
    assert data["status"] == "executed" and data["solid"] == "Antenna:spiral"
    (trace,) = client.executed
    for line in (
        "With TraceFromCurve",
        '.Name "spiral"',
        '.Component "Antenna"',
        '.Material "Copper (annealed)"',
        '.Curve "Curves:spiral"',
        '.Thickness "0.035"',
        '.Width "0.5"',
        '.RoundStart "True"',
        '.RoundEnd "False"',
        '.GapType "0"',
        ".Create",
    ):
        assert line in trace
    validate_vba_input(trace)
    assert data["history_labels"] == ["cst_mcp_1"]
    assert data["measured"]["status"] == "ok"


@pytest.mark.asyncio
async def test_trace_default_gap_type_is_natural() -> None:
    data = await _call("cst_create_trace_from_curve", TRACE, FakeClient(connected=False))
    assert '.GapType "2"' in data["vba"]


@pytest.mark.asyncio
async def test_parameter_expressions_are_kept() -> None:
    data = await _call(
        "cst_create_curve_wire", {**WIRE, "radius": "wire_r"}, FakeClient(connected=False)
    )
    assert '.Radius "wire_r"' in data["vba"]


@pytest.mark.parametrize(
    ("tool", "arguments", "fragment"),
    [
        ("cst_create_curve_wire", {**WIRE, "radius": 0}, "radius"),
        ("cst_create_curve_wire", {**WIRE, "radius": -1}, "radius"),
        ("cst_create_curve_wire", {**WIRE, "curve": "helix"}, "curvegroup:curveitem"),
        ("cst_create_curve_wire", {**WIRE, "curve": "a:b:c"}, "curvegroup:curveitem"),
        ("cst_create_curve_wire", {**WIRE, "curve": 'Curves:x" & Shell'}, "curve item"),
        ("cst_create_curve_wire", {**WIRE, "termination": "flat"}, "termination"),
        ("cst_create_trace_from_curve", {**TRACE, "width": 0}, "width"),
        ("cst_create_trace_from_curve", {**TRACE, "thickness": -0.1}, "thickness"),
        ("cst_create_trace_from_curve", {**TRACE, "gap_type": "sharp"}, "gap_type"),
        ("cst_create_trace_from_curve", {**TRACE, "name": "bad\nname"}, ""),
    ],
)
@pytest.mark.asyncio
async def test_invalid_arguments_are_refused_before_cst(tool, arguments, fragment) -> None:
    client = FakeClient()
    data = await _call(tool, arguments, client)
    assert data["status"] == "error"
    assert fragment in data["message"]
    assert client.executed == [] and client.queries == []


def test_annotations_and_schemas() -> None:
    tools = {t.name: t for t in geometry.TOOLS}
    for name in ("cst_create_curve_wire", "cst_create_trace_from_curve"):
        assert not is_read_only(name) and is_destructive(name)
        tool = tools[name]
        # MCP SDK 1.x names it inputSchema, 2.x input_schema.
        schema = getattr(tool, "inputSchema", None) or tool.input_schema
        assert schema["additionalProperties"] is False
        assert {"curve", "component", "name"} <= set(schema["required"])
        assert "consum" in tools[name].description or "uses up" in tools[name].description
