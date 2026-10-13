"""Regressions for bugs found in a live CST 2026 run (fakes only; no CST contacted)."""

from __future__ import annotations

import json
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.session import CSTSession
from cst_mcp.tools import import_export, mesh, official, project, results, simulation, solvers
from cst_mcp.validators import ValidationError


def _decode(content) -> dict:
    return json.loads(content[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["executed", "offline", "error"])
async def test_touchstone_export_never_uses_model_history(tmp_path, status) -> None:
    calls = []

    class Capture:
        def execute_vba(self, vba):
            raise AssertionError("Touchstone export reached model history")

        def execute_vba_silent(self, vba, *, history_fallback):
            calls.append((vba, history_fallback))
            return {"status": status, "message": "native response"}

    path = str(tmp_path / "dipole.s1p")
    arguments = {"file_path": path, "format": "s1p"}
    result = _decode(await import_export.handle("cst_export_touchstone", arguments, Capture()))
    assert calls == [(import_export._build_export_touchstone(arguments), False)]
    assert result["status"] == status
    assert result["message"] == "native response"
    assert result["exported_file"] == path
    assert result["format"] == "s1p"


@pytest.mark.parametrize(
    "filename,native_filename",
    [
        ("dipole.s1p", "dipole"),
        ("network.s2p", "network"),
        ("array.s12p", "array"),
        ("dipole.S1P", "dipole"),
        ("dipole", "dipole"),
        ("dipole.v2.s1p", "dipole.v2"),
        ("dipole.data", "dipole.data"),
    ],
)
def test_touchstone_filename_leaves_extension_to_cst(tmp_path, filename, native_filename) -> None:
    folder = tmp_path / "results.s1p"
    code = import_export._build_export_touchstone({"file_path": str(folder / filename)})
    assert f'.FileName "{folder / native_filename}"' in code


@pytest.mark.asyncio
@pytest.mark.parametrize("z0", [None, 75])
async def test_input_impedance_uses_spp_and_scalar_reference_with_explicit_provenance(z0):
    calls = []
    raw = {
        "status": "ok", "source": "cst.results", "project_path": "project.cst", "run_id": 0,
        "n": 5, "x": [.8, .9, 1, 1.1, 1.2], "real": [0, .5, 0, -.5, 1],
        "imag": [0, 0, .5, 0, 0], "xlabel": "Frequency / GHz", "title": "S2,2",
    }

    class Capture:
        connected = True

        def get_result(self, tree):
            calls.append(tree)
            return {**raw, "tree_path": tree}

    args = {"port": 2}
    if z0 is not None:
        args.update(mode="input", z0=z0)
    schema = next(t.model_dump(by_alias=True)["inputSchema"]
                  for t in results.TOOLS if t.name == "cst_get_impedance")
    jsonschema.validate(args, schema)
    response = _decode(await results.handle("cst_get_impedance", args, Capture()))
    reference = 50 if z0 is None else z0
    assert calls == [r"1D Results\S-Parameters\S2,2"]
    assert response["real"][:-1] == pytest.approx([reference, 3*reference, .6*reference, reference/3])
    assert response["imag"][:-1] == pytest.approx([0, 0, .8*reference, 0])
    assert response["real"][-1] is None and response["imag"][-1] is None
    assert response["y"][-1] is None
    assert response["x"] == raw["x"] and response["n"] == raw["n"]
    assert response["source"] == "derived_from_s_parameters"
    assert response["source_api"] == "cst.results"
    assert response["source_tree_path"] == calls[0]
    assert response["impedance_mode"] == "input" and response["z0"] == reference
    assert response["unit"] == "ohm"
    assert "reference impedances" in response["other_ports"]
    assert "match" in response["assumption"]
    json.dumps(response, allow_nan=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["ok", "error", "busy"])
async def test_native_z_parameter_reads_native_curve_without_s_parameter_fallback(status):
    calls = []
    native = {"status": status, "source": "cst.results", "x": [.9, 1],
              "real": [37, 41], "imag": [9, -3], "message": "native response"}

    class Capture:
        connected = True

        def get_result(self, tree):
            calls.append(tree)
            return dict(native)

    response = _decode(await results.handle(
        "cst_get_impedance", {"port": 3, "mode": "z_parameter"}, Capture()
    ))
    assert calls == [r"1D Results\Z-Parameters\Z3,3"]
    assert response["status"] == status and response["real"] == native["real"]
    assert response["imag"] == native["imag"]
    assert response["impedance_mode"] == "z_parameter"
    assert "z0" not in response and "formula" not in response
    if status == "ok":
        assert "Open-circuited" in response["other_ports"]


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {"mode": "unknown"}, {"z0": 0}, {"z0": -1}, {"z0": float("nan")}, {"z0": float("inf")}
])
async def test_impedance_invalid_mode_or_reference_is_rejected_before_reading(arguments):
    class NoRead:
        connected = True

        def get_result(self, tree):
            raise AssertionError("invalid impedance request reached CST")

    response = _decode(await results.handle("cst_get_impedance", arguments, NoRead()))
    assert response["status"] == "error"


@pytest.mark.asyncio
async def test_input_impedance_preserves_missing_source_error_and_offline_has_no_fabricated_data():
    class Capture:
        connected = True

        def get_result(self, tree):
            return {"status": "error", "message": "Spp not found", "tree_path": tree}

    client = Capture()
    response = _decode(await results.handle("cst_get_impedance", {}, client))
    assert response["status"] == "error" and response["message"] == "Spp not found"
    assert "real" not in response and "y" not in response
    client.connected = False
    for mode in ("input", "z_parameter"):
        response = _decode(await results.handle("cst_get_impedance", {"mode": mode}, client))
        assert response["status"] == "offline" and response["impedance_mode"] == mode
        assert "real" not in response and "y" not in response


class FakeModel:
    """model3d fake: non-history VBA writes canned query output; history is forbidden."""

    def __init__(self, output: str = "", run_info: dict | None = None) -> None:
        self.output = output
        self.run_info = run_info
        self.codes: list[str] = []

    def is_solver_running(self, *, timeout=None) -> bool:
        return False

    def add_to_history(self, *args, **kwargs):
        raise AssertionError("read-only tool wrote to the model history")

    def _execute_vba_code(self, code: str, timeout=None) -> None:
        self.codes.append(code)
        opened = re.search(r'Open "([^"]+)" For Output', code)
        if opened:
            Path(opened[1]).write_text(self.output, encoding="ascii")
        exported = re.search(r'Plot\.ExportImage "([^"]+)"', code)
        if exported:
            Path(exported[1]).write_bytes(b"png")

    def get_active_solver_name(self, *, timeout=None) -> str:
        return "HF Time Domain"

    def get_solver_run_info(self, *, timeout=None) -> dict | None:
        return self.run_info


def _client(tmp_path, model: FakeModel) -> CSTClient:
    client = CSTClient(CSTConfig())
    client.config.work_dir = tmp_path
    client._de = object()
    client._project = SimpleNamespace(model3d=model)
    return client


# 1. MeshSettings needs a setting map before .Set --------------------------------


@pytest.mark.parametrize(
    "mesh_type,native_type",
    [
        ("Hexahedral", "PBA"),
        ("Hexahedral TLM", "HexahedralTLM"),
        ("Tetrahedral", "Tetrahedral"),
        ("Surface", "Surface"),
    ],
)
def test_mesh_type_uses_native_cst_values(mesh_type, native_type) -> None:
    class Capture:
        def execute_vba(self, vba):
            return {"status": "offline", "vba": vba}

    result = _decode(mesh._set_mesh_type({"mesh_type": mesh_type}, Capture()))
    assert result["vba"] == f'With Mesh\n  .MeshType "{native_type}"\nEnd With'
    assert result["mesh_type"] == mesh_type


@pytest.mark.parametrize("mesh_type", ["Hex", "Tet"])
def test_mesh_density_selects_setting_map_first(mesh_type) -> None:
    captured = {}

    class Capture:
        def execute_vba(self, vba):
            captured["vba"] = vba
            return {"status": "offline", "vba": vba}

    result = _decode(
        mesh._set_mesh_density({"cells_per_wavelength": 12, "mesh_type": mesh_type}, Capture())
    )
    vba = captured["vba"]
    assert f'.SetMeshType "{mesh_type}"' in vba
    assert vba.index(".SetMeshType") < vba.index('.Set "StepsPerWaveNear"')
    assert ("RatioLimit" in vba) is (mesh_type == "Hex")
    assert result["mesh_type"] == mesh_type


@pytest.mark.parametrize(
    "arguments,expected_factor",
    [
        ({"component": "Dipole", "solid": "ArmNegative"}, 2.0),
        ({"component": "Other", "solid": "Feed", "refinement_factor": 3.5}, 3.5),
    ],
)
def test_local_mesh_refinement_uses_solid_volume_api(arguments, expected_factor) -> None:
    class Capture:
        def execute_vba(self, vba):
            return {"status": "offline", "vba": vba}

    result = _decode(mesh._add_mesh_refinement(arguments, Capture()))
    solid_path = f'{arguments["component"]}:{arguments["solid"]}'
    assert result["vba"] == (
        f'With Solid\n  .SetMeshRefinement "{solid_path}", "False", "1", '
        f'"True", "{expected_factor}"\nEnd With'
    )
    assert result["component"] == arguments["component"]
    assert result["solid"] == arguments["solid"]
    assert result["refinement_factor"] == expected_factor


@pytest.mark.parametrize("factor", [0, -1, float("nan"), float("inf")])
def test_local_mesh_refinement_rejects_invalid_factors_before_execution(factor) -> None:
    class NoExecution:
        def execute_vba(self, vba):
            raise AssertionError("invalid refinement reached CST")

    with pytest.raises((ValidationError, ValueError)):
        mesh._add_mesh_refinement(
            {"component": "Dipole", "solid": "ArmNegative", "refinement_factor": factor},
            NoExecution(),
        )


@pytest.mark.parametrize(
    "arguments,enabled,max_passes,threshold",
    [
        ({}, True, 3, 0.02),
        ({"enabled": False, "max_passes": 1, "threshold": 0.05}, False, 1, 0.05),
        ({"enabled": True, "max_passes": 5, "threshold": 0}, True, 5, 0),
    ],
)
def test_adaptive_mesh_uses_time_domain_api(arguments, enabled, max_passes, threshold) -> None:
    class Capture:
        def execute_vba(self, vba):
            return {"status": "offline", "vba": vba}

    result = _decode(mesh._set_adaptive_mesh(arguments, Capture()))
    expected = f'With Solver\n  .MeshAdaption "{enabled}"\nEnd With'
    if enabled:
        expected = (
            'With MeshAdaption3D\n  .SetType "Time"\n  .MinPasses "2"\n'
            f'  .MaxPasses "{max_passes}"\n  .MaxDeltaS "{threshold}"\nEnd With\n'
            + expected
        )
    else:
        assert result["notes"] == [
            "max_passes and threshold are not applied when adaptive meshing is disabled."
        ]
    assert result["vba"] == expected
    assert result["enabled"] is enabled
    assert result["max_passes"] == max_passes
    assert result["threshold"] == threshold


@pytest.mark.parametrize("threshold", [-0.01, 1.01, float("nan"), float("inf")])
def test_adaptive_mesh_rejects_invalid_linear_threshold_before_execution(threshold) -> None:
    class NoExecution:
        def execute_vba(self, vba):
            raise AssertionError("invalid threshold reached CST")

    with pytest.raises((ValidationError, ValueError)):
        mesh._set_adaptive_mesh({"threshold": threshold}, NoExecution())


def test_adaptive_mesh_rejects_single_pass_when_enabled_before_execution() -> None:
    class NoExecution:
        def execute_vba(self, vba):
            raise AssertionError("invalid pass count reached CST")

    with pytest.raises(ValidationError, match="max_passes"):
        mesh._set_adaptive_mesh({"enabled": True, "max_passes": 1}, NoExecution())


# 2. Sub Main stripping and project tree enumeration -----------------------------


def test_strip_sub_main_tolerates_leading_comments_and_option_lines() -> None:
    code = "\n' header comment\nRem another\nOption Explicit\n\nSub Main()\n  x = 1\nEnd Sub\n"
    bare = CSTSession._strip_sub_main(code)
    assert "Sub Main" not in bare and "End Sub" not in bare and "Option" not in bare
    assert bare.splitlines() == ["' header comment", "Rem another", "x = 1"]
    # Code without a Sub Main wrapper is left untouched.
    assert CSTSession._strip_sub_main("x = 1\n") == "x = 1\n"


def test_project_tree_walks_children_without_history(tmp_path) -> None:
    output = (
        "0\tComponents\n1\tComponents\\component1\n2\tComponents\\component1\\patch\n"
        "0\tPorts\n1\tPorts\\port1\n"
    )
    model = FakeModel(output)
    client = _client(tmp_path, model)
    data = _decode(_run(project.handle("cst_project_tree", {}, client)))
    assert data["status"] == "ok"
    assert [i["path"] for i in data["items"]] == [
        "Components",
        "Components\\component1",
        "Components\\component1\\patch",
        "Ports",
        "Ports\\port1",
    ]
    code = model.codes[0]
    # Everything is inside exactly one Sub Main (the old wrapper put code outside it).
    assert code.count("Sub Main") == 1 and code.rstrip().endswith("End Sub")
    assert "Resulttree.GetFirstChildName(cur)" in code and "GetNextItemName(child)" in code
    assert "GetNumberOfSelectedTreeItems" not in code


def test_project_tree_subfolder_strips_root(tmp_path) -> None:
    model = FakeModel("0\tComponents\n1\tComponents\\component1\nTRUNCATED\t4\n")
    client = _client(tmp_path, model)
    data = _decode(
        _run(
            project.handle("cst_project_tree", {"tree_path": "Components", "max_depth": 1}, client)
        )
    )
    assert data["items"] == [{"path": "Components\\component1", "name": "component1", "depth": 0}]
    assert data["truncated"] is True
    assert "Const maxDepth = 1" in model.codes[0]


# 3. Read-only info tools use output capture, never history ----------------------


def test_solver_info_reports_values_without_history(tmp_path) -> None:
    model = FakeModel(
        "solver_type\tHF Time Domain\nfmin\t1.5\nfmax\t3\nn_frequency_samples.error\tno ports\n"
    )
    data = _decode(solvers._get_solver_info({}, _client(tmp_path, model)))
    assert data["status"] == "ok"
    assert data["solver_type"] == "HF Time Domain" and data["fmin"] == 1.5 and data["fmax"] == 3
    assert data["unavailable"] == {"n_frequency_samples": "no ports"}
    assert "ReportInformation" not in model.codes[0]


def test_mesh_info_reports_counts_without_update_or_history(tmp_path) -> None:
    model = FakeModel("mesh_type\tPBA\ntotal_cells\t123456\nmin_edge_length\t0.05\n")
    data = _decode(mesh._get_mesh_info({}, _client(tmp_path, model)))
    assert data["status"] == "ok" and data["mesh_type"] == "PBA" and data["total_cells"] == 123456
    assert data["min_edge_length"] == 0.05
    assert ".Update" not in model.codes[0]


def test_capture_never_falls_back_to_history(tmp_path) -> None:
    client = _client(tmp_path, FakeModel())
    client._project = SimpleNamespace(
        model3d=SimpleNamespace(
            is_solver_running=lambda **kw: False,
            add_to_history=lambda *a, **kw: pytest.fail("history fallback used"),
        )
    )
    result = client.capture_vba_output('Debug.Print "x"')
    assert result["status"] == "error"


# 4. Structure views reselect the 3D model first ---------------------------------


def test_structure_views_select_components_before_export(tmp_path) -> None:
    model = FakeModel()
    client = _client(tmp_path, model)
    result = client.export_plot_images(tmp_path / "views", views=["front", "custom"])
    assert result["status"] == "ok"
    exports = [c for c in model.codes if "Plot.ExportImage" in c]
    assert len(exports) == 2
    for code in exports:
        assert code.index('SelectTreeItem "Components"') < code.index("Plot.ExportImage")


# 5. Saved results for the session's own open project ----------------------------


def _fake_cst_results(monkeypatch, project_file):
    results = types.ModuleType("cst.results")
    results.ProjectFile = project_file
    package = types.ModuleType("cst")
    package.results = results
    monkeypatch.setitem(sys.modules, "cst", package)
    monkeypatch.setitem(sys.modules, "cst.results", results)


def _own_project_client(tmp_path, monkeypatch):
    path = tmp_path / "model.cst"
    path.touch()
    client = CSTClient(CSTConfig())
    client._project_path = str(path)
    monkeypatch.setattr(client, "is_solver_running", lambda **kw: False)
    return client, path


@pytest.mark.asyncio
async def test_list_saved_results_allows_own_idle_project(tmp_path, monkeypatch) -> None:
    seen = {}

    class ProjectFile:
        def __init__(self, path, allow_interactive=False):
            seen["allow_interactive"] = allow_interactive

        def get_3d(self):
            return SimpleNamespace(
                get_tree_items=lambda: ["1D Results\\S-Parameters\\S1,1"],
                get_run_ids=lambda tree: [0],
            )

    _fake_cst_results(monkeypatch, ProjectFile)
    client, path = _own_project_client(tmp_path, monkeypatch)
    data = _decode(
        await official.handle("cst_list_saved_results", {"project_path": str(path)}, client)
    )
    assert data["status"] == "ok" and seen["allow_interactive"] is True
    assert data["entries"][0]["run_ids"] == [0]


@pytest.mark.asyncio
async def test_list_saved_results_failure_is_clean_error(tmp_path, monkeypatch) -> None:
    class ProjectFile:
        def __init__(self, path, allow_interactive=False):
            raise UserWarning("Project is opened in CST Studio Suite.")

    _fake_cst_results(monkeypatch, ProjectFile)
    path = tmp_path / "other.cst"
    path.touch()
    data = _decode(
        await official.handle(
            "cst_list_saved_results", {"project_path": str(path)}, CSTClient(CSTConfig())
        )
    )
    assert data["status"] == "error" and "opened in CST" in data["message"]


@pytest.mark.asyncio
async def test_read_saved_result_passes_allow_interactive_for_own_project(
    tmp_path, monkeypatch
) -> None:
    seen = {}

    def fake_read(path, tree, run_id, allow_interactive=False):
        seen["allow_interactive"] = allow_interactive
        return {"status": "ok", "n": 1, "x": [1.0], "real": [0.5], "imag": [0.0]}

    monkeypatch.setattr(official, "read_curve", fake_read)
    client, path = _own_project_client(tmp_path, monkeypatch)
    data = _decode(
        await official.handle(
            "cst_read_saved_result",
            {"project_path": str(path), "tree_path": "1D Results\\x"},
            client,
        )
    )
    assert data["status"] == "ok" and seen["allow_interactive"] is True


# 6. Stale run-info state is labelled -------------------------------------------


@pytest.mark.asyncio
async def test_simulation_status_labels_stale_run_state(tmp_path) -> None:
    model = FakeModel(run_info={"message": "History update failed", "state": "ERROR"})
    client = _client(tmp_path, model)
    data = _decode(await simulation.handle("cst_get_simulation_status", {}, client))
    assert data["running"] is False
    assert "state" not in data["run_info"]
    assert data["run_info"]["last_reported_state"] == "ERROR"
    assert "stale" in data["run_info"]["state_note"]
    jsonschema.validate(data, simulation.SOLVER_STATUS_OUTPUT_SCHEMA)


def test_run_info_with_timestamp_keeps_state() -> None:
    info = CSTSession._label_run_info({"state": "SUCCESS", "end_time": "2026-09-23T20:00"}, False)
    assert info["state"] == "SUCCESS" and info["reported_at"] == "2026-09-23T20:00"


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_mesh_query_marks_stale_zero_cell_count():
    from types import SimpleNamespace

    from cst_mcp.tools import mesh

    client = SimpleNamespace(
        query_values=lambda fields: {
            "status": "ok",
            "values": {"mesh_type": "PBA", "total_cells": "0", "mesh_points": "27540"},
            "errors": {},
            "source": "test",
        }
    )
    out = mesh._query_mesh(client)
    assert "total_cells" not in out
    assert "total_cells" in out["unavailable"]
    assert out["mesh_points"] == 27540


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,fmt,requested,expected",
    [
        ("E-Field", "txt", None, {"x": 32, "y": 32, "z": 32}),
        ("H-Field", "csv", {"x": 8, "y": 12, "z": 40}, {"x": 8, "y": 12, "z": 40}),
        ("E-Field", "csv", {"z": 64}, {"x": 32, "y": 32, "z": 64}),
    ],
)
async def test_field_export_uses_and_reports_configurable_sampling(field, fmt, requested, expected):
    codes = []

    class Capture:
        connected = True

        def execute_vba_silent(self, code, *, history_fallback):
            assert history_fallback is False
            codes.append(code)
            return {"status": "executed"}

    arguments = {
        "result_path": f"2D/3D Results\\{field}\\field (f=1) [1]",
        "output_file": f"field.{fmt}", "format": fmt,
    }
    if requested is not None:
        arguments["field_samples"] = requested
    schema = next(t.model_dump(by_alias=True)["inputSchema"]
                  for t in results.TOOLS if t.name == "cst_export_result")
    jsonschema.validate(arguments, schema)
    result = _decode(await results.handle("cst_export_result", arguments, Capture()))
    assert result["status"] == "executed"
    assert result["field_sampling"] == {
        "mode": "FixedNumber", "samples_per_axis": expected, "applied": True,
    }
    assert len(codes) == 1
    code = codes[0]
    assert '.Mode "FixedNumber"' in code
    for axis, count in expected.items():
        assert f'.Step{axis.upper()} "{count}"' in code
    assert code.index(".Reset") < code.index(".Mode") < code.index(".StepX") < code.index(".Execute")


@pytest.mark.asyncio
@pytest.mark.parametrize("samples", [{"x": 0}, {"y": 1.5}, {"z": True}, {"x": "32\nShell x"}])
async def test_field_export_rejects_invalid_sampling_before_native_execution(samples):
    class NoExecution:
        connected = True

        def execute_vba_silent(self, *args, **kwargs):
            raise AssertionError("invalid field samples reached CST")

    result = _decode(await results.handle("cst_export_result", {
        "result_path": r"2D/3D Results\E-Field\e-field (f=1) [1]",
        "output_file": "field.txt", "field_samples": samples,
    }, NoExecution()))
    assert result["status"] == "error"
    assert "field_samples" in result["message"]


@pytest.mark.asyncio
async def test_failed_and_offline_field_export_do_not_claim_sampling_was_applied():
    class Client:
        connected = True

        def execute_vba_silent(self, *args, **kwargs):
            return {"status": "error", "message": "native failure"}

    client = Client()
    args = {"result_path": r"2D/3D Results\H-Field\h-field (f=1) [1]",
            "output_file": "field.txt", "format": "txt"}
    failed = _decode(await results.handle("cst_export_result", args, client))
    assert failed["field_sampling"]["applied"] is False
    client.connected = False
    offline = _decode(await results.handle("cst_export_result", args, client))
    assert offline["field_sampling"]["applied"] is False
    assert '.Mode "FixedNumber"' in offline["vba_script"]


def test_field_sampling_is_not_silently_applied_to_other_exports():
    path = r"1D Results\S-Parameters\S1,1"
    code = results._build_export_result_vba(path, "s11.txt", "txt")
    assert ".Mode" not in code and ".StepX" not in code
    with pytest.raises(ValueError, match="only applies"):
        results._build_export_result_vba(path, "s11.txt", "txt", {"x": 32})


def test_export_result_never_writes_history():
    import asyncio
    import json

    from cst_mcp.tools import results

    calls = []

    class Client:
        connected = True

        def execute_vba(self, *a, **kw):
            calls.append(("history", kw))
            return {"status": "executed"}

        def execute_vba_silent(self, code, **kw):
            calls.append(("silent", kw))
            return {"status": "executed"}

    res = asyncio.run(
        results.handle(
            "cst_export_result",
            {
                "result_path": r"1D Results\S-Parameters\S1,1",
                "output_file": "s11.csv",
                "format": "csv",
            },
            Client(),
        )
    )
    assert json.loads(res[0].text)["status"] == "executed"
    assert calls == [("silent", {"history_fallback": False})]
