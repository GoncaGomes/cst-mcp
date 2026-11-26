"""cst_get_project_materials: project material readback through output capture (fakes only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import materials
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
    content = await materials.handle("cst_get_project_materials", arguments, client)
    return json.loads(content[0].text)


# Background vacuum, PEC, annealed copper, lossy FR-4 and an anisotropic substrate, printed
# with a decimal-comma locale.
PROJECT = (
    "count\t5\n"
    "material\t0\tVacuum\n"
    "0\ttype\tNormal\n"
    "0\tbackground\tTrue\n"
    "0\tepsilon\t1\t1\t1\n"
    "0\tmu\t1\t1\t1\n"
    "0\tsigma\t0\t0\t0\n"
    "0\tsigma_m\t0\t0\t0\n"
    "material\t1\tPEC\n"
    "1\ttype\tPEC\n"
    "1\tbackground\tFalse\n"
    "1\tepsilon\t1\t1\t1\n"
    "1\tmu\t1\t1\t1\n"
    "1\tsigma\t0\t0\t0\n"
    "1\tsigma_m\t0\t0\t0\n"
    "material\t2\tCopper (annealed)\n"
    "2\ttype\tLossy Metal\n"
    "2\tbackground\tFalse\n"
    "2\tepsilon\t1\t1\t1\n"
    "2\tmu\t1\t1\t1\n"
    "2\tsigma\t58000000\t58000000\t58000000\n"
    "2\tsigma_m\t0\t0\t0\n"
    "material\t3\tFR-4 (lossy)\n"
    "3\ttype\tNormal\n"
    "3\tbackground\tFalse\n"
    "3\tepsilon\t4,3\t4,3\t4,3\n"
    "3\tmu\t1\t1\t1\n"
    "3\tsigma\t0\t0\t0\n"
    "3\tsigma_m\t0\t0\t0\n"
    "material\t4\tSapphire\n"
    "4\ttype\tAnisotropic\n"
    "4\tbackground\tFalse\n"
    "4\tepsilon\t9,4\t9,4\t11,6\n"
    "4\tmu\t1\t1\t1\n"
    "4\tsigma\t0\t0\t0\n"
    "4\tsigma_m\t0\t0\t0\n"
)


@pytest.mark.asyncio
async def test_project_materials_are_read_back_without_history(tmp_path) -> None:
    model = FakeModel(PROJECT)
    data = await _call({}, _client(tmp_path, model))

    assert data["status"] == "ok"
    assert data["material_count"] == 5
    assert data["material_names"] == [
        "Vacuum",
        "PEC",
        "Copper (annealed)",
        "FR-4 (lossy)",
        "Sapphire",
    ]
    vacuum, pec, copper, fr4, sapphire = data["materials"]

    assert vacuum["background"] is True and vacuum["type"] == "normal"

    assert copper["found"] is True
    assert copper["type"] == "lossy_metal" and copper["raw_type"] == "Lossy Metal"
    assert copper["conductivity"] == 5.8e7
    assert copper["conductivity_axes"] == {"x": 5.8e7, "y": 5.8e7, "z": 5.8e7}
    assert copper["tan_d"] is None and "Not applicable" in copper["unavailable"]["tan_d"]

    assert fr4["type"] == "normal" and fr4["epsilon_r"] == 4.3 and fr4["mu_r"] == 1.0
    assert fr4["conductivity"] == 0.0
    # No CST getter: never inferred from the creation arguments.
    assert fr4["tan_d"] is None and fr4["tan_d_freq"] is None and fr4["dispersion_model"] is None
    assert set(fr4["unavailable"]) == {"tan_d", "tan_d_freq", "dispersion_model"}
    assert "no getter" in fr4["unavailable"]["tan_d"]

    assert pec["type"] == "pec"
    assert pec["epsilon_r"] is None and pec["conductivity"] is None
    assert "PEC" in pec["unavailable"]["conductivity"]

    assert sapphire["type"] == "anisotropic"
    assert sapphire["epsilon_r"] is None
    assert sapphire["epsilon_r_axes"] == {"x": 9.4, "y": 9.4, "z": 11.6}
    assert "epsilon_r_axes" in sapphire["unavailable"]["epsilon_r"]

    (code,) = model.codes
    assert "Material.GetNumberOfMaterials()" in code
    assert "Material.GetEpsilon mcpName, mcpA, mcpB, mcpC" in code
    assert "If True Then" in code
    # Output capture only redirects Debug.Print at line start; every one must be.
    assert all(
        line.lstrip().startswith("Debug.Print")
        for line in code.splitlines()
        if "Debug.Print" in line
    )


@pytest.mark.asyncio
async def test_names_filter_found_and_missing(tmp_path) -> None:
    model = FakeModel(PROJECT)
    data = await _call(
        {"names": ["FR-4 (lossy)", "copper", "Rogers RO4003C", "FR-4 (lossy)"]},
        _client(tmp_path, model),
    )

    assert data["status"] == "ok"
    fr4, copper, rogers = data["materials"]
    assert fr4["found"] is True and fr4["epsilon_r"] == 4.3
    # Exact names only; a near match is offered, not substituted.
    assert copper == {"name": "copper", "found": False, "similar": ["Copper (annealed)"]}
    assert rogers == {"name": "Rogers RO4003C", "found": False}
    assert data["missing"] == ["copper", "Rogers RO4003C"]
    assert data["message"] == "Not in the project: copper, Rogers RO4003C"
    (code,) = model.codes
    assert (
        'If (mcpName = "FR-4 (lossy)" Or mcpName = "copper" Or mcpName = "Rogers RO4003C") Then'
        in code
    )


@pytest.mark.asyncio
async def test_quotes_in_names_are_escaped(tmp_path) -> None:
    model = FakeModel("count\t0\n")
    await _call({"names": ['Odd "name"']}, _client(tmp_path, model))

    (code,) = model.codes
    assert 'mcpName = "Odd ""name"""' in code


@pytest.mark.asyncio
async def test_project_with_no_materials(tmp_path) -> None:
    data = await _call({}, _client(tmp_path, FakeModel("count\t0\n")))

    assert data["status"] == "ok"
    assert data["material_count"] == 0 and data["materials"] == []
    assert data["message"] == "The project has no materials."


@pytest.mark.asyncio
async def test_getter_failures_carry_cst_reason(tmp_path) -> None:
    output = (
        "count\t1\n"
        "material\t0\tOdd\n"
        "0\ttype\tTensor formula\n"
        "0\tbackground_error\tUnknown method\n"
        "0\tepsilon_error\tNot available for this material type\n"
        "0\tmu\t1\t1\n"
        "0\tsigma\t0\t0\t0\n"
        "0\tsigma_m\t0\t0\t0\n"
    )
    data = await _call({}, _client(tmp_path, FakeModel(output)))

    (odd,) = data["materials"]
    assert odd["type"] == "other" and odd["raw_type"] == "Tensor formula"
    assert odd["background"] is None
    assert odd["unavailable"]["background"] == "CST reported: Unknown method"
    assert odd["epsilon_r"] is None and odd["epsilon_r_axes"] is None
    assert odd["unavailable"]["epsilon_r"] == "CST reported: Not available for this material type"
    assert odd["mu_r"] is None and "Unreadable" in odd["unavailable"]["mu_r"]
    assert odd["conductivity"] == 0.0


@pytest.mark.asyncio
async def test_count_failure_is_an_error(tmp_path) -> None:
    data = await _call({}, _client(tmp_path, FakeModel("count_error\tObject not available\n")))

    assert data["status"] == "error"
    assert "Object not available" in data["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"names": []},
        {"names": [""]},
        {"names": ["   "]},
        {"names": ["Copper\nMsgBox 1"]},
        {"names": ["a\tb"]},
        {"names": ["x" * 201]},
        {"names": [3]},
        {"names": "Copper"},
    ],
)
async def test_invalid_arguments_rejected_before_vba(tmp_path, arguments) -> None:
    model = FakeModel(PROJECT)
    data = await _call(arguments, _client(tmp_path, model))

    assert data["status"] == "error"
    assert model.codes == []


@pytest.mark.asyncio
async def test_busy_solver_refuses(tmp_path) -> None:
    model = FakeModel(PROJECT, running=True)
    data = await _call({}, _client(tmp_path, model))

    assert data["status"] == "busy"
    assert model.codes == []


@pytest.mark.asyncio
async def test_offline_returns_query_vba_only() -> None:
    data = await _call({}, CSTClient(CSTConfig(connect_mode="disabled")))

    assert data["status"] == "offline"
    assert "Material.GetNumberOfMaterials()" in data["vba"]


def test_project_materials_is_read_only() -> None:
    tool = next(t for t in materials.TOOLS if t.name == "cst_get_project_materials")
    ann = annotations_for(tool.name)
    # MCP 1.x uses camelCase attributes; 2.x uses snake_case.
    assert getattr(ann, "readOnlyHint", getattr(ann, "read_only_hint", None)) is True
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    assert schema["additionalProperties"] is False
