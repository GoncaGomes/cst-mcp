"""cst_set_mesh_density: whole-model wording, cell estimate and size guard (MCP 23, fakes only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.config import CSTConfig, mesh_cell_limits
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import mesh


class FakeModel:
    """model3d fake: query VBA writes canned output; history entries are recorded."""

    def __init__(self, output: str = "") -> None:
        self.output = output
        self.history: list[str] = []

    def is_solver_running(self, *, timeout=None):
        return False

    def add_to_history(self, title, code, *args, **kwargs):
        self.history.append(code)

    def _execute_vba_code(self, code: str, timeout=None) -> None:
        opened = re.search(r'Open "([^"]+)" For Output', code)
        if opened:
            Path(opened[1]).write_text(self.output, encoding="ascii")


def _client(tmp_path, model: FakeModel) -> CSTClient:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client.config.work_dir = tmp_path
    client._de = object()
    client._project = SimpleNamespace(model3d=model)
    return client


async def _call(client, **arguments) -> dict:
    content = await mesh.handle("cst_set_mesh_density", arguments, client)
    return json.loads(content[0].text)


def _box(x1, x2, y1, y2, z1, z2) -> str:
    return "\t".join(str(v) for v in (x1, x2, y1, y2, z1, z2))


# The failed PIFA run (cst-agentic pifa_20261127-155506): 60 x 100 x 9 mm model,
# 1.5-3.5 GHz, all faces expanded open (15 mm of added space per side assumed here).
PIFA_STRUCTURE = _box(-30, 30, -50, 50, -0.5, 8.5)
PIFA_CALCULATION = _box(-45, 45, -65, 65, -15.5, 23.5)
PIFA_OUTPUT = (
    "geometry_unit\t0,001\n"
    "frequency_unit\t1000000000\n"
    "fmax\t3,5\n"
    f"calculation\t{PIFA_CALCULATION}\n"
    f"structure\t{PIFA_STRUCTURE}\n"
)


@pytest.fixture(autouse=True)
def _default_limits(monkeypatch):
    monkeypatch.delenv("CST_MESH_MAX_CELLS", raising=False)
    monkeypatch.delenv("CST_MESH_WARN_CELLS", raising=False)


def test_min_cells_schema_is_whole_model_and_points_to_refinement() -> None:
    tool = next(t for t in mesh.TOOLS if t.name == "cst_set_mesh_density")
    schema = getattr(tool, "input_schema", None) or tool.inputSchema  # MCP SDK 2.x / 1.x
    text = schema["properties"]["min_cells"]["description"]
    assert "bounding box" in text
    assert "StepsPerBox" in text
    assert "not a local setting" in text
    assert "cst_add_mesh_refinement" in text


async def test_pifa_min_cells_423_is_rejected_without_history(tmp_path) -> None:
    model = FakeModel(PIFA_OUTPUT)
    result = await _call(_client(tmp_path, model), min_cells=423, mesh_type="Hex")
    assert result["status"] == "error"
    assert model.history == []
    estimate = result["mesh_estimate"]
    assert estimate["step_from"] == "box"
    assert estimate["box"] == "calculation"
    assert estimate["lower_bound"] is False
    assert estimate["step"] == pytest.approx(100 / 423)
    assert estimate["cells"] > 20_000_000
    assert f"{estimate['cells']:,}" in result["message"]
    assert "Nothing was changed in CST" in result["message"]
    assert "cst_add_mesh_refinement" in result["message"]


async def test_defaults_on_pifa_are_accepted_with_estimate(tmp_path) -> None:
    model = FakeModel(PIFA_OUTPUT)
    result = await _call(_client(tmp_path, model))
    assert result["status"] != "error"
    assert len(model.history) == 1
    assert '.Set "StepsPerBoxNear", "5"' in model.history[0]
    estimate = result["mesh_estimate"]
    # 3.5 GHz -> 85.65 mm wavelength / 15 = 5.71 mm, finer than 100 mm / 5 = 20 mm.
    assert estimate["step_from"] == "wavelength"
    assert estimate["step"] == pytest.approx(299_792_458 / 3.5e9 / 1e-3 / 15)
    assert estimate["cells_per_axis"] == [16, 23, 7]
    assert estimate["cells"] == 16 * 23 * 7
    assert estimate["unit"] == "mm"
    assert "warnings" not in result


async def test_large_cells_per_wavelength_is_rejected(tmp_path) -> None:
    model = FakeModel(PIFA_OUTPUT)
    result = await _call(_client(tmp_path, model), cells_per_wavelength=400)
    assert result["status"] == "error"
    assert result["mesh_estimate"]["step_from"] == "wavelength"
    assert model.history == []


async def test_warn_threshold_adds_warning_but_executes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CST_MESH_WARN_CELLS", "1000")
    model = FakeModel(PIFA_OUTPUT)
    result = await _call(_client(tmp_path, model))
    assert len(model.history) == 1
    assert result["mesh_estimate"]["limits"] == {"warn": 1000, "max": 20_000_000}
    assert any("Large mesh" in w for w in result["warnings"])


async def test_missing_calculation_box_falls_back_to_lower_bound(tmp_path) -> None:
    output = PIFA_OUTPUT.replace(
        f"calculation\t{PIFA_CALCULATION}", "calculation\t" + _box(*[0] * 6)
    )
    model = FakeModel(output)
    result = await _call(_client(tmp_path, model))
    estimate = result["mesh_estimate"]
    assert estimate["box"] == "structure"
    assert estimate["lower_bound"] is True
    assert any("lower bound" in w and "open boundaries" in w for w in result["warnings"])
    assert len(model.history) == 1


async def test_lower_bound_above_limit_still_rejects(tmp_path) -> None:
    output = PIFA_OUTPUT.replace(f"calculation\t{PIFA_CALCULATION}\n", "")
    model = FakeModel(output)
    result = await _call(_client(tmp_path, model), min_cells=1000)
    assert result["mesh_estimate"]["lower_bound"] is True
    assert result["status"] == "error"
    assert model.history == []


async def test_missing_fmax_uses_box_rule_as_lower_bound(tmp_path) -> None:
    output = PIFA_OUTPUT.replace("fmax\t3,5\n", "")
    model = FakeModel(output)
    result = await _call(_client(tmp_path, model))
    estimate = result["mesh_estimate"]
    assert estimate["step_from"] == "box"
    assert estimate["step"] == pytest.approx(20.0)
    assert estimate["lower_bound"] is True
    assert any("Maximum frequency" in r for r in estimate["lower_bound_reasons"])


async def test_no_geometry_gives_no_estimate_and_executes(tmp_path) -> None:
    model = FakeModel("geometry_unit\t0.001\nfmax\t3.5\nfrequency_unit\t1e9\n")
    result = await _call(_client(tmp_path, model))
    assert result["mesh_estimate"]["cells"] is None
    assert "bounding box" in result["mesh_estimate"]["reason"]
    assert len(model.history) == 1


async def test_tet_map_is_not_estimated(tmp_path) -> None:
    model = FakeModel(PIFA_OUTPUT)
    result = await _call(_client(tmp_path, model), min_cells=423, mesh_type="Tet")
    assert result["mesh_estimate"]["cells"] is None
    assert "Tet" in result["mesh_estimate"]["reason"]
    assert len(model.history) == 1


async def test_offline_client_reports_no_estimate() -> None:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    result = await _call(client, min_cells=423)
    assert result["mesh_estimate"]["cells"] is None
    assert result["mesh_estimate"]["reason"] == "Not connected to CST."


def test_estimate_query_is_read_only_vba() -> None:
    vba = mesh._mesh_estimate_vba()
    assert "Boundary.GetCalculationBox" in vba
    assert "Boundary.GetStructureBox" in vba
    assert "Solver.GetFmax" in vba
    assert "MeshSettings" not in vba


@pytest.mark.parametrize(
    ("warn", "maximum", "expected"),
    [
        (None, None, (5_000_000, 20_000_000)),
        ("100", "50", (50, 50)),
        ("abc", "-3", (5_000_000, 20_000_000)),
        ("1_000", "30_000_000", (1000, 30_000_000)),
    ],
)
def test_mesh_cell_limits_from_env(monkeypatch, warn, maximum, expected) -> None:
    for name, value in (("CST_MESH_WARN_CELLS", warn), ("CST_MESH_MAX_CELLS", maximum)):
        if value is not None:
            monkeypatch.setenv(name, value)
    assert mesh_cell_limits() == expected
