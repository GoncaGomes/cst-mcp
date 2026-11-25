"""cst_get_solid_bounds: measured bounds readback through output capture (fakes only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import geometry
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
    content = await geometry.handle("cst_get_solid_bounds", arguments, client)
    return json.loads(content[0].text)


SUBSTRATE_OUTPUT = (
    "unit\t0,001\n"
    "0\tbox\t-20\t20\t-23,5\t40\t0\t0,787\n"
    "0\tmaterial\tFR-4 (lossy)\n"
    "0\tvolume\t1999,5\n"
)


@pytest.mark.asyncio
async def test_bounds_are_parsed_without_history(tmp_path) -> None:
    model = FakeModel(SUBSTRATE_OUTPUT)
    data = await _call({"solids": ["substrate:board"]}, _client(tmp_path, model))

    assert data["status"] == "ok"
    assert data["unit"] == "mm" and data["unit_to_si"] == 0.001
    assert data["bounds_kind"] == "loose" and "guaranteed" in data["note"]
    (solid,) = data["solids"]
    assert solid["found"] is True
    assert solid["bounds"] == {"x": [-20.0, 20.0], "y": [-23.5, 40.0], "z": [0.0, 0.787]}
    assert solid["size"] == pytest.approx([40.0, 63.5, 0.787])
    assert solid["material"] == "FR-4 (lossy)"
    assert solid["volume"] == 1999.5 and data["volume_unit"] == "mm^3"

    (code,) = model.codes
    assert 'Solid.GetLooseBoundingBoxOfShape("substrate:board"' in code
    # Output capture only redirects Debug.Print at line start; none may be left over.
    assert "Debug.Print" not in code


@pytest.mark.asyncio
async def test_unknown_and_failing_solids_are_reported_per_solid(tmp_path) -> None:
    output = (
        "unit\t0.001\n"
        "0\tbox\t0\t1\t0\t2\t0\t3\n"
        "0\tmaterial\tPEC\n"
        "0\tvolume\t6\n"
        "1\tmissing\n"
        "2\terror\tShape has no geometry\n"
    )
    names = ["a:one", "a:nope", "a/b:empty", "a:silent"]
    data = await _call({"solids": names}, _client(tmp_path, FakeModel(output)))

    assert data["status"] == "error"
    assert data["message"] == "No bounds for: a:nope, a/b:empty, a:silent"
    found, missing, failed, silent = data["solids"]
    assert found["found"] is True and found["bounds"]["z"] == [0.0, 3.0]
    assert missing["found"] is False and "not found" in missing["error"]
    assert failed["found"] is False and failed["error"] == "Shape has no geometry"
    assert silent["found"] is False and "no result" in silent["error"]


@pytest.mark.asyncio
async def test_volume_is_optional_and_volume_errors_are_kept(tmp_path) -> None:
    output = "unit\t0.001\n0\tbox\t0\t1\t0\t1\t0\t1\n0\tvolume_error\tnot a solid\n"
    model = FakeModel(output)
    data = await _call({"solids": ["s:sheet"]}, _client(tmp_path, model))
    assert data["status"] == "ok"
    assert data["solids"][0]["volume_error"] == "not a solid"

    model = FakeModel("unit\t0.001\n0\tbox\t0\t1\t0\t1\t0\t1\n")
    data = await _call({"solids": ["s:x"], "include_volume": False}, _client(tmp_path, model))
    assert "GetVolume" not in model.codes[0]
    assert "volume" not in data["solids"][0] and "volume_unit" not in data


@pytest.mark.asyncio
async def test_duplicate_names_are_queried_once(tmp_path) -> None:
    model = FakeModel("0\tbox\t0\t1\t0\t1\t0\t1\n")
    data = await _call({"solids": ["c:s", "c:s"]}, _client(tmp_path, model))
    assert [s["name"] for s in data["solids"]] == ["c:s"]
    assert model.codes[0].count("GetLooseBoundingBoxOfShape") == 1
    assert data["unit"] is None  # unit line absent: not guessed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "solids",
    [
        [],
        ["no_colon"],
        ["a:b:c"],
        ['a:b" : Shell "calc'],
        ["a:b\nShell 1"],
        [":solid"],
        "a:b",
    ],
)
async def test_invalid_names_are_rejected_before_any_vba(tmp_path, solids) -> None:
    model = FakeModel()
    data = await _call({"solids": solids}, _client(tmp_path, model))
    assert data["status"] == "error"
    assert model.codes == []


@pytest.mark.asyncio
async def test_busy_solver_refuses_without_running_vba(tmp_path) -> None:
    model = FakeModel(running=True)
    data = await _call({"solids": ["c:s"]}, _client(tmp_path, model))
    assert data["status"] == "busy"
    assert model.codes == []


@pytest.mark.asyncio
async def test_offline_returns_query_vba_only() -> None:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    data = await _call({"solids": ["c:s"]}, client)
    assert data["status"] == "offline"
    assert "GetLooseBoundingBoxOfShape" in data["vba"]


def test_annotated_read_only_and_listed_in_geometry() -> None:
    ann = annotations_for("cst_get_solid_bounds")
    # MCP 1.x uses camelCase attributes; 2.x uses snake_case.
    assert getattr(ann, "readOnlyHint", getattr(ann, "read_only_hint", None)) is True
    assert "cst_get_solid_bounds" in {t.name for t in geometry.TOOLS}
