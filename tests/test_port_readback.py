"""cst_list_ports: port readback through output capture (fakes only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import ports
from cst_mcp.tools.annotations import annotations_for


class FakeModel:
    """model3d fake: non-history VBA writes canned query output; history is forbidden."""

    def __init__(self, output: str = "", running: bool | None = False) -> None:
        self.output = output
        self.running = running
        self.codes: list[str] = []

    def is_solver_running(self, *, timeout=None):
        return self.running

    def add_to_history(self, *args, **kwargs):
        raise AssertionError("read-only tool wrote to the model history")

    def _execute_vba_code(self, code: str, timeout=None) -> None:
        self.codes.append(code)
        opened = re.search(r'Open "([^"]+)" For Output', code)
        if opened:
            Path(opened[1]).write_text(self.output, encoding="ascii")


def _client(tmp_path, model: FakeModel) -> CSTClient:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client.config.work_dir = tmp_path
    client._de = object()
    client._project = SimpleNamespace(model3d=model)
    return client


async def _call(arguments: dict, client) -> dict:
    content = await ports.handle("cst_list_ports", arguments, client)
    return json.loads(content[0].text)


# The Vivaldi feed port (ymin, window X [-4, 4], Z [-0.035, 3.935] at Y = -23.5) and a
# 50-ohm discrete edge port, printed with a decimal-comma locale.
TWO_PORTS = (
    "unit\t0,001\n"
    "count\t2\n"
    "port\t1\n"
    "port\t2\n"
    "1\ttype\tWaveguide\n"
    "1\tlabel\t\n"
    "1\tcenter\t0\t-23,5\t1,95\n"
    "1\twindow\t2\t-4\t4\t-23,5\t-23,5\t-0,035\t3,935\n"
    "1\tmodes\t1\n"
    "1\tmode_type\t1\tQTEM\n"
    "1\tmode_z\t1\t49,8\n"
    "2\ttype\tDiscrete\n"
    "2\tlabel\tfeed\n"
    "2\tcenter\t0\t0\t0,5\n"
    "2\tedge_coords\t0\t0\t0\t0\t0\t1\n"
    "2\tedge_props\tSParameter\t50\n"
    "2\tface_props_none\n"
    "2\tface_shape\t0\t0\t0\n"
)


@pytest.mark.asyncio
async def test_waveguide_and_discrete_ports_are_read_back_without_history(tmp_path) -> None:
    model = FakeModel(TWO_PORTS)
    data = await _call({}, _client(tmp_path, model))

    assert data["status"] == "ok"
    assert data["port_count"] == 2 and data["port_numbers"] == [1, 2]
    assert data["unit"] == "mm" and data["unit_to_si"] == 0.001

    waveguide, discrete = data["ports"]
    assert waveguide["number"] == 1 and waveguide["type"] == "waveguide"
    assert waveguide["center"] == [0.0, -23.5, 1.95]
    assert waveguide["orientation"] == "ymin"
    assert waveguide["window"] == {"x": [-4.0, 4.0], "y": [-23.5, -23.5], "z": [-0.035, 3.935]}
    assert waveguide["window_kind"] == "mesh_snapped"
    assert waveguide["num_modes"] == 1
    assert waveguide["modes"] == [{"mode": 1, "type": "QTEM", "line_impedance": 49.8}]
    # Never inferred from the creation arguments.
    assert waveguide["on_boundary"] is None and "on_boundary" in waveguide["unavailable"]
    assert waveguide["reference_impedance"] is None
    assert set(waveguide["unavailable"]) == {"on_boundary", "reference_impedance"}

    assert discrete["type"] == "discrete" and discrete["discrete_kind"] == "edge"
    assert discrete["label"] == "feed"
    assert discrete["endpoints"] == {"start": [0.0, 0.0, 0.0], "end": [0.0, 0.0, 1.0]}
    assert discrete["excitation_type"] == "SParameter" and discrete["impedance"] == 50.0
    assert discrete["unavailable"] == {}
    assert "face_shape" not in discrete

    (code,) = model.codes
    assert "Port.GetType(mcpPn)" in code
    assert "Port.GetPortMeshCoordinates mcpPn, mcpOrient" in code
    assert "DiscretePort.GetCoordinates(mcpPn" in code
    # Output capture only redirects Debug.Print at line start; none may be left over.
    assert "Debug.Print" not in code


@pytest.mark.asyncio
async def test_project_with_no_ports(tmp_path) -> None:
    data = await _call({}, _client(tmp_path, FakeModel("unit\t0.001\ncount\t0\n")))

    assert data["status"] == "ok"
    assert data["port_count"] == 0 and data["port_numbers"] == [] and data["ports"] == []
    assert data["message"] == "The project has no ports."


@pytest.mark.asyncio
async def test_unavailable_values_carry_cst_reason(tmp_path) -> None:
    output = (
        "unit\t0.001\n"
        "count\t1\n"
        "port\t1\n"
        "1\ttype\tWaveguide\n"
        "1\tlabel\t\n"
        "1\tcenter_error\tNo mesh available\n"
        "1\twindow_error\tNo mesh available\n"
        "1\tmodes\t2\n"
        "1\tmode_type_error\t1\tPort modes not calculated\n"
        "1\tmode_z\t1\t0\n"
        "1\tmode_type\t2\tTE\n"
        "1\tmode_z\t2\t0\n"
    )
    data = await _call({}, _client(tmp_path, FakeModel(output)))

    assert data["status"] == "ok"
    (port,) = data["ports"]
    assert port["center"] is None and port["window"] is None and port["orientation"] is None
    assert port["unavailable"]["center"] == "CST reported: No mesh available"
    assert port["unavailable"]["window"].startswith("CST reported: No mesh available")
    assert "window" in port["unavailable"]["orientation"]
    first, second = port["modes"]
    assert first["type"] is None
    assert first["unavailable"]["type"] == "CST reported: Port modes not calculated"
    assert first["line_impedance"] is None and "TEM" in first["unavailable"]["line_impedance"]
    assert second["type"] == "TE" and second["line_impedance"] is None


@pytest.mark.asyncio
async def test_discrete_face_port(tmp_path) -> None:
    output = (
        "count\t1\n"
        "port\t4\n"
        "4\ttype\tDiscrete\n"
        "4\tlabel\t\n"
        "4\tcenter\t1,5\t0\t0\n"
        "4\tedge_coords_none\n"
        "4\tedge_props_none\n"
        "4\tface_props\tSParameter\t75,5\n"
        "4\tface_shape\t1\t2\t0,5\n"
    )
    data = await _call({}, _client(tmp_path, FakeModel(output)))

    (port,) = data["ports"]
    assert port["discrete_kind"] == "face"
    assert port["impedance"] == 75.5 and port["excitation_type"] == "SParameter"
    assert port["face_shape"] == {"kind": "rectangular", "width": 2.0, "length": 0.5}
    assert port["endpoints"] is None and "face ports" in port["unavailable"]["endpoints"]
    assert data["unit"] is None  # No unit line: reported as unknown, not assumed.


@pytest.mark.asyncio
async def test_port_numbers_filter_and_missing_ports(tmp_path) -> None:
    model = FakeModel(TWO_PORTS)
    data = await _call({"port_numbers": [2, 7, 2]}, _client(tmp_path, model))

    assert data["status"] == "error"
    assert data["port_numbers"] == [1, 2]
    assert [p["number"] for p in data["ports"]] == [2]
    assert data["missing"] == [7] and data["message"] == "No such port: 7"
    (code,) = model.codes
    assert "If (mcpPn = 2 Or mcpPn = 7) Then" in code


@pytest.mark.asyncio
async def test_count_failure_is_an_error(tmp_path) -> None:
    data = await _call({}, _client(tmp_path, FakeModel("count_error\tObject not available\n")))

    assert data["status"] == "error"
    assert "Object not available" in data["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"port_numbers": []},
        {"port_numbers": [0]},
        {"port_numbers": [1000]},
        {"port_numbers": ["1\nMsgBox 1"]},
        {"port_numbers": [True]},
        {"port_numbers": 1},
    ],
)
async def test_invalid_arguments_rejected_before_vba(tmp_path, arguments) -> None:
    model = FakeModel(TWO_PORTS)
    content = await ports.handle("cst_list_ports", arguments, _client(tmp_path, model))
    data = json.loads(content[0].text)

    assert data["status"] == "error"
    assert model.codes == []


@pytest.mark.asyncio
async def test_busy_solver_refuses(tmp_path) -> None:
    model = FakeModel(TWO_PORTS, running=True)
    data = await _call({}, _client(tmp_path, model))

    assert data["status"] == "busy"
    assert model.codes == []


@pytest.mark.asyncio
async def test_offline_returns_query_vba_only() -> None:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    data = await _call({}, client)

    assert data["status"] == "offline"
    assert "Port.StartPortNumberIteration()" in data["vba"]
    assert "If True Then" in data["vba"]


def test_list_ports_is_read_only() -> None:
    tool = next(t for t in ports.TOOLS if t.name == "cst_list_ports")
    ann = annotations_for(tool.name)
    # MCP 1.x uses camelCase attributes; 2.x uses snake_case.
    assert getattr(ann, "readOnlyHint", getattr(ann, "read_only_hint", None)) is True
    # Unknown arguments are rejected by the registry's schema validation.
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    assert schema["additionalProperties"] is False
