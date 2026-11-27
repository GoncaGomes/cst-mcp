"""cst_add_waveguide_port reads the port back and warns when it left its plane (MCP 22)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import ports

# The patch feed port of patch_20261127-183313: ymin, X [-7.5, 7.5], Z [-0.035, 9.1].
ARGS = {
    "port_number": 1,
    "orientation": "ymin",
    "x_min": -7.5,
    "x_max": 7.5,
    "y_min": 0,
    "y_max": 0,
    "z_min": -0.035,
    "z_max": 9.1,
}


def _port_output(y: float) -> str:
    return (
        "unit\t0.001\n"
        "count\t1\n"
        "port\t1\n"
        "1\ttype\tWaveguide\n"
        "1\tlabel\t\n"
        "1\tcenter\t0\t0\t4.5325\n"
        f"1\twindow\t2\t-7.5\t7.5\t{y}\t{y}\t-0.035\t9.1\n"
        "1\tmodes\t1\n"
    )


class FakeModel:
    """model3d fake: history records the port; queries return a canned port and mesh step."""

    def __init__(self, port_output: str, mesh_step: str | None = None) -> None:
        self.port_output = port_output
        self.mesh_step = mesh_step
        self.history: list[str] = []
        self.queries: list[str] = []

    def is_solver_running(self, *, timeout=None):
        return False

    def add_to_history(self, title, code, *args, **kwargs):
        self.history.append(code)

    def _execute_vba_code(self, code: str, timeout=None) -> None:
        opened = re.search(r'Open "([^"]+)" For Output', code)
        if not opened:
            return
        self.queries.append(code)
        if "GetMaximumEdgeLength" in code:
            text = (
                f"max_edge_length\t{self.mesh_step}\n"
                if self.mesh_step is not None
                else "max_edge_length.error\tNo mesh available\n"
            )
        else:
            text = self.port_output
        Path(opened[1]).write_text(text, encoding="ascii")


def _client(tmp_path, model: FakeModel) -> CSTClient:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client.config.work_dir = tmp_path
    client._de = object()
    client._project = SimpleNamespace(model3d=model)
    return client


async def _add(client, **overrides) -> dict:
    content = await ports.handle("cst_add_waveguide_port", {**ARGS, **overrides}, client)
    return json.loads(content[0].text)


async def test_port_on_requested_plane_has_readback_and_no_warning(tmp_path) -> None:
    model = FakeModel(_port_output(0))
    result = await _add(_client(tmp_path, model), port_on_bound=False)
    assert result["status"] == "executed"
    assert len(model.history) == 1 and "Port" in model.history[0]
    readback = result["readback"]
    assert readback["window"] == {"x": [-7.5, 7.5], "y": [0.0, 0.0], "z": [-0.035, 9.1]}
    assert readback["normal_axis"] == "y"
    assert readback["requested_normal"] == 0 and readback["readback_normal"] == 0
    assert readback["unit"] == "mm"
    assert "warning" not in result
    # No mesh query when the plane matches.
    assert not any("GetMaximumEdgeLength" in q for q in model.queries)


async def test_port_moved_to_expanded_boundary_warns_with_both_values(tmp_path) -> None:
    model = FakeModel(_port_output(-14.8412107920792))
    result = await _add(_client(tmp_path, model), port_on_bound=True)
    assert result["status"] == "executed"
    assert result["readback"]["readback_normal"] == -14.8412107920792
    assert result["readback"]["max_mesh_step"] is None
    warning = result["warning"]
    assert "y = 0 mm" in warning and "y = -14.8412 mm" in warning
    assert "port_on_bound=false" in warning
    assert "mesh step is not known" in warning


async def test_move_within_one_mesh_step_is_snapping_not_a_warning(tmp_path) -> None:
    model = FakeModel(_port_output(0.4), mesh_step="0.5")
    result = await _add(_client(tmp_path, model))
    assert result["readback"]["max_mesh_step"] == 0.5
    assert "warning" not in result


async def test_move_beyond_known_mesh_step_warns_without_unknown_step_text(tmp_path) -> None:
    model = FakeModel(_port_output(-14.84), mesh_step="0.5")
    result = await _add(_client(tmp_path, model))
    assert "port_on_bound=false" in result["warning"]
    assert "mesh step is not known" not in result["warning"]


async def test_internal_port_move_does_not_suggest_port_on_bound(tmp_path) -> None:
    model = FakeModel(_port_output(3.0), mesh_step="0.5")
    result = await _add(_client(tmp_path, model), port_on_bound=False)
    assert "y = 3 mm" in result["warning"]
    assert "port_on_bound=false" not in result["warning"]


async def test_max_orientation_compares_the_max_end(tmp_path) -> None:
    output = _port_output(0).replace("window\t2", "window\t3")
    model = FakeModel(output)
    result = await _add(_client(tmp_path, model), orientation="ymax", y_min=-1, y_max=0)
    assert result["readback"]["requested_normal"] == 0
    assert "warning" not in result


async def test_unreadable_window_keeps_the_port_and_adds_a_note(tmp_path) -> None:
    output = _port_output(0).replace("1\twindow\t2", "1\twindow_error\tNo mesh available\n#")
    model = FakeModel(output)
    result = await _add(_client(tmp_path, model))
    assert result["status"] == "executed"
    assert result["readback"]["window"] is None
    assert "No mesh available" in result["note"] and "cst_list_ports" in result["note"]
    assert "warning" not in result


async def test_failed_create_is_not_read_back(tmp_path) -> None:
    class FailingModel(FakeModel):
        def add_to_history(self, title, code, *args, **kwargs):
            raise RuntimeError("Port could not be created")

    model = FailingModel(_port_output(0))
    result = await _add(_client(tmp_path, model))
    assert result["status"] == "error"
    assert "readback" not in result
    assert not any("GetPortMeshCoordinates" in q for q in model.queries)


async def test_offline_has_no_readback() -> None:
    result = await _add(CSTClient(CSTConfig(connect_mode="disabled")))
    assert result["status"] == "offline"
    assert "readback" not in result and "warning" not in result


def test_description_mentions_readback_and_later_boundary_changes() -> None:
    tool = next(t for t in ports.TOOLS if t.name == "cst_add_waveguide_port")
    assert "readback.window" in tool.description
    assert "cst_list_ports before solving" in tool.description
