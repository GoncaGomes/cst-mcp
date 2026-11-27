"""cst_get_boundaries: boundary/domain readback through output capture (fakes only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import boundaries
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


async def _call(client) -> dict:
    content = await boundaries.handle("cst_get_boundaries", {}, client)
    return json.loads(content[0].text)


MIXED_OUTPUT = (
    "unit\t0,001\n"
    "face\tx_min\texpanded open\n"
    "face\tx_max\texpanded open\n"
    "face\ty_min\texpanded open\n"
    "face\ty_max\texpanded open\n"
    "face\tz_min\telectric\n"
    "face\tz_max\texpanded open\n"
    "symmetry\tx_plane\tmagnetic\n"
    "symmetry\ty_plane\tnone\n"
    "symmetry\tz_plane\tnone\n"
    "box\tcalculation_box\t-40\t40\t-45,5\t60\t0\t30,787\n"
    "box\tstructure_box\t-20\t20\t-23,5\t40\t0\t0,787\n"
)


@pytest.mark.asyncio
async def test_readback_is_parsed_without_history(tmp_path) -> None:
    model = FakeModel(MIXED_OUTPUT)
    data = await _call(_client(tmp_path, model))

    assert data["status"] == "ok"
    assert data["unit"] == "mm" and data["unit_to_si"] == 0.001
    assert data["boundaries"] == {
        "x_min": "expanded open",
        "x_max": "expanded open",
        "y_min": "expanded open",
        "y_max": "expanded open",
        "z_min": "electric",
        "z_max": "expanded open",
    }
    assert data["symmetry"] == {"x_plane": "magnetic", "y_plane": "none", "z_plane": "none"}
    assert data["calculation_box"] == {"x": [-40.0, 40.0], "y": [-45.5, 60.0], "z": [0.0, 30.787]}
    assert data["structure_box"] == {"x": [-20.0, 20.0], "y": [-23.5, 40.0], "z": [0.0, 0.787]}
    assert data["added_space"] == pytest.approx(
        {"x_min": 20, "x_max": 20, "y_min": 22, "y_max": 20, "z_min": 0, "z_max": 30}
    )
    # Values CST has no getter for are reported, never inferred from the last set call.
    assert data["background"] is None and data["open_boundary_distance"] is None
    assert set(data["unavailable"]) == {"background", "open_boundary_distance"}
    assert "no model history" in data["source"]

    (code,) = model.codes
    for getter in (
        "GetXmin",
        "GetZmax",
        "GetXSymmetry",
        "GetZSymmetry",
        "GetCalculationBox",
        "GetStructureBox",
    ):
        assert f"Boundary.{getter}" in code
    # Output capture only redirects Debug.Print at line start; none may be left over.
    assert "Debug.Print" not in code


@pytest.mark.asyncio
async def test_failed_getters_are_unavailable_with_reason(tmp_path) -> None:
    output = (
        "unit\t0,001\n"
        "face\tx_min\topen\n"
        "error\tface.x_max\tMethod not supported\n"
        "symmetry\tx_plane\tnone\n"
        "box\tcalculation_box\t0\t0\t0\t0\t0\t0\n"
        "error\tstructure_box\tNo structure\n"
    )
    data = await _call(_client(tmp_path, FakeModel(output)))

    assert data["status"] == "ok"
    assert data["boundaries"]["x_min"] == "open"
    assert data["boundaries"]["x_max"] is None
    assert data["unavailable"]["boundaries.x_max"] == "CST error: Method not supported"
    assert data["unavailable"]["boundaries.y_min"] == "CST returned no value"
    assert data["unavailable"]["symmetry.y_plane"] == "CST returned no value"
    assert data["calculation_box"] is None
    assert "zero-size" in data["unavailable"]["calculation_box"]
    assert data["unavailable"]["structure_box"] == "CST error: No structure"
    assert data["added_space"] is None and "added_space" in data["unavailable"]


@pytest.mark.asyncio
async def test_empty_output_is_an_error(tmp_path) -> None:
    data = await _call(_client(tmp_path, FakeModel("")))
    assert data["status"] == "error"
    assert data["boundaries"] == dict.fromkeys(boundaries._FACE_GETTERS)


@pytest.mark.asyncio
async def test_busy_solver_refuses_without_running_vba(tmp_path) -> None:
    model = FakeModel(MIXED_OUTPUT, running=True)
    data = await _call(_client(tmp_path, model))
    assert data["status"] != "ok"
    assert model.codes == []


@pytest.mark.asyncio
async def test_offline_returns_query_vba() -> None:
    data = await _call(CSTClient(CSTConfig(connect_mode="disabled")))
    assert data["status"] == "offline"
    assert "Boundary.GetCalculationBox" in data["vba"]


def test_schema_and_read_only_annotation() -> None:
    tool = next(t for t in boundaries.TOOLS if t.name == "cst_get_boundaries")
    schema = getattr(tool, "input_schema", None) or tool.inputSchema  # MCP SDK 2.x / 1.x
    assert schema["properties"] == {}
    ann = annotations_for(tool.name)
    # MCP 1.x uses camelCase attributes; 2.x uses snake_case.
    assert getattr(ann, "readOnlyHint", getattr(ann, "read_only_hint", None)) is True
