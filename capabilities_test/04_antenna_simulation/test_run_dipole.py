"""Offline checks use temporary folders and never import a CST application client."""

import asyncio
import json
import math
import sys
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_dipole as dipole
from common import (
    Incomplete,
    Stop,
    bandwidth,
    compare,
    complex_curve,
    derive,
    dipole_physics,
    farfield_grid,
    ffs_source,
    prepare_outputs,
    sphere_mean,
    touchstone,
)


def make_runner(tmp_path, preflight=False):
    runner = dipole.Dipole(dipole.options(["--preflight"] if preflight else []), parent=tmp_path)
    prepare_outputs(runner.base, tmp_path, preflight)
    runner.prepared = True
    runner.persist()
    return runner


def catalog(runner, tool, properties=None):
    runner.catalog[tool] = {
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "additionalProperties": False,
        }
    }


def response(payload, is_error=False):
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload)},
            {"type": "text", "text": "original second block"},
        ],
        "structuredContent": payload,
        "isError": is_error,
        "customExtension": {"retained": True},
    }


def test_independent_complex_conversion_and_touchstone(tmp_path):
    # Expected values are analytical and independently specified.
    values = [complex(0, 0), complex(0.5, 0), complex(0, 0.5), complex(-0.5, 0), complex(1, 0)]
    expected_db = [None, -6.020599913279624, -6.020599913279624, -6.020599913279624, 0]
    derived = derive(values)
    assert compare(derived["db"], expected_db, 1e-12)
    assert derived["vswr"] == [1, 3, 3, 3, None]
    assert compare(derived["z"], [50 + 0j, 150 + 0j, 30 + 40j, 50 / 3 + 0j, None], 1e-12)
    assert derived["phase"] == [None, 0, 90, 180, 0]
    assert compare([179, -179], [179, 181], wrapped=True)
    file = tmp_path / "model.s1p"
    file.write_text(
        "! independently specified one port\n# MHZ S RI R 50\n800 0 0\n900 .5 0\n1000 0 .5\n1100 -.5 0\n1200 1 0\n"
    )
    parsed = touchstone(file)
    assert parsed["x"] == pytest.approx([0.8, 0.9, 1, 1.1, 1.2])
    assert parsed["values"] == values
    assert parsed["reference_ohm"] == 50
    assert parsed["representation"] == "RI"


@pytest.mark.parametrize(
    "representation, pair", [("MA", "0.5 90"), ("DB", "-6.020599913279624 90")]
)
def test_touchstone_declared_representation(tmp_path, representation, pair):
    file = tmp_path / "model.s1p"
    file.write_text(f"# GHz S {representation} R 75\n1 {pair}\n")
    parsed = touchstone(file)
    assert abs(parsed["values"][0] - 0.5j) < 1e-12
    assert parsed["reference_ohm"] == 75


@pytest.mark.parametrize(
    "frequency_shift,complex_shift,drop_sample,passes",
    [(0, 0, False, True), (2e-7, 0, False, False), (0, 2e-5, False, False), (0, 0, True, False)],
)
def test_touchstone_validation_allows_frequency_rounding_only(
    tmp_path, frequency_shift, complex_shift, drop_sample, passes
):
    runner = make_runner(tmp_path)
    runner.baseline = ([0.800000011920929, 1.2000000476837158], [0.3 + 0.1j, 0.2 - 0.1j])
    path = tmp_path / "dipole.s1p"
    rows = [f"{0.8 + frequency_shift:.10f} {0.3 + complex_shift:.10f} 0.1"]
    if not drop_sample:
        rows.append("1.2 0.2 -0.1")
    path.write_text("# GHz S RI R 50\n" + "\n".join(rows) + "\n")
    artifact = {"archived": str(path), "fresh": True}
    response_data = {"artifacts": [artifact]}
    if passes:
        runner.validate_touchstone(response_data)
    else:
        with pytest.raises(Stop, match="Touchstone consistency"):
            runner.validate_touchstone(response_data)
    check = next(c for c in runner.checks if c["name"] == "Touchstone consistency")
    assert check["verdict"] == ("passed" if passes else "failed")
    assert check["tolerance"] == {"frequency_ghz": 1e-7, "complex_s11": 1e-5}
    if passes:
        assert check["obtained"]["max_frequency_difference_ghz"] == pytest.approx(4.76837158e-8)
        assert check["obtained"]["max_complex_s11_difference"] == 0


def test_bandwidth_multiple_intervals_and_truncated_edges():
    # -20 dB means magnitude 0.1, crossings at 1.5, 2.5, 3.5, 4.5.
    bands = bandwidth([1, 2, 3, 4, 5], [0.05, 0.15, 0.05, 0.15, 0.05], -20)
    assert [(b["f_lower_ghz"], b["f_upper_ghz"]) for b in bands] == pytest.approx(
        [(1, 1.5), (2.5, 3.5), (4.5, 5)]
    )
    assert [(b["lower_truncated"], b["upper_truncated"]) for b in bands] == [
        (True, False),
        (False, False),
        (False, True),
    ]
    assert bandwidth([1, 2], [0.5, 0.5]) == []
    with pytest.raises(Incomplete):
        bandwidth([1, 1], [0.5, 0.2])


def test_raw_curve_units_and_singular_values_are_not_fabricated():
    with pytest.raises(Incomplete, match="GHz"):
        complex_curve({"x": [1], "real": [0.2], "imag": [0], "xlabel": "Frequency"})
    with pytest.raises(Stop, match="nonfinite"):
        complex_curve({"x": [1], "real": [math.nan], "imag": [0], "xlabel": "Frequency / GHz"})
    with pytest.raises(Stop, match="preview"):
        complex_curve({"x": [1], "real": [0.2], "imag": [0], "xlabel": "GHz", "sampled": True})
    assert derive([1 + 0j])["z"] == [None]
    assert derive([1.01 + 0j])["vswr"] == [None]


def test_partial_failure_keeps_exact_request_response_and_reports(tmp_path):
    runner = make_runner(tmp_path)
    catalog(runner, "cst_get_impedance", {"port": {"type": "integer"}})
    payload = {"status": "error", "message": "Native Z absent", "native_detail": "original"}
    raw = response(payload, True)
    session = AsyncMock()
    session.call_tool.return_value = raw
    with pytest.raises(Stop):
        asyncio.run(runner.request(session, "cst_get_impedance", {"port": 1}))
    runner.exit_code, runner.reason = 1, "Test partial failure"
    runner.finalize()
    calls = [json.loads(line) for line in (runner.reports / "calls.jsonl").read_text().splitlines()]
    assert calls == [{"id": 1, "tool": "cst_get_impedance", "arguments": {"port": 1}}]
    evidence = json.loads(next((runner.results / "mcp").glob("*.json")).read_text())
    assert evidence["response"] == raw
    assert evidence["structuredContent"] == payload
    assert evidence["parsed_text_json"][0]["value"] == payload
    assert evidence["protocol_success"] is True
    assert evidence["tool_execution"] == "rejected"
    assert (runner.reports / "validation.md").is_file()
    assert len(list(runner.reports.iterdir())) == 2
    assert json.loads((runner.results / "validation.json").read_text())["exit_code"] == 1


@pytest.mark.parametrize("transport", [True, False])
def test_unknown_state_blocks_all_later_native_requests(tmp_path, transport):
    runner = make_runner(tmp_path)
    catalog(runner, "cst_run_simulation", {"timeout_s": {"type": "number"}})
    catalog(runner, "cst_connection_status")
    session = AsyncMock()
    if transport:
        session.call_tool.side_effect = TimeoutError("lost response")
        error = TimeoutError
    else:
        session.call_tool.return_value = response(
            {"status": "timeout", "execution_state": "unknown"}, True
        )
        error = Incomplete
    with pytest.raises(error):
        asyncio.run(runner.request(session, "cst_run_simulation", {"timeout_s": 1800}))
    assert runner.unknown
    with pytest.raises(Incomplete):
        asyncio.run(runner.request(session, "cst_connection_status", {}))
    assert session.call_tool.await_count == 1
    timeout = session.call_tool.await_args.kwargs["read_timeout_seconds"]
    assert (timeout.total_seconds() if hasattr(timeout, "total_seconds") else timeout) >= 1920
    runner.finalize()
    assert runner.exit_code == 2


def test_preflight_protocol_uses_only_disabled_status_and_paginates(tmp_path):
    runner = make_runner(tmp_path, True)
    session = AsyncMock()
    session.initialize.return_value = {"serverInfo": {"name": "offline fixture"}}
    # Schemas are mocked only for client behavior, never live-support evidence.
    tools = []
    for name in sorted({item["tool"] for item in dipole.MANIFEST}):
        props = {
            key: {} for item in dipole.MANIFEST if item["tool"] == name for key in item["arguments"]
        }
        tools.append({"name": name, "inputSchema": {"type": "object", "properties": props}})
    session.list_tools.side_effect = [
        {"tools": tools[:10], "nextCursor": "next"},
        {"tools": tools[10:], "nextCursor": None},
    ]
    session.call_tool.return_value = response(
        {"mode": "offline", "project_open": False, "cst_available": False}
    )
    asyncio.run(runner.protocol(session))
    assert session.list_tools.await_count == 2
    assert session.list_tools.await_args.kwargs["cursor"] == "next"
    assert session.call_tool.await_count == 1
    assert session.call_tool.await_args.args == ("cst_connection_status", {})
    planned = json.loads((runner.results / "protocol/planned_manifest.json").read_text())
    assert all(item["execution"] == "not_executed" for item in planned)
    assert all(item["schema_validation"] == "passed" for item in planned)
    for tool in ("cst_connect", "cst_run_simulation", "cst_execute_vba", "cst_create_project"):
        with pytest.raises(Stop, match="Preflight forbids"):
            asyncio.run(runner.request(session, tool, {}))


@pytest.mark.parametrize("preflight", [False, True])
def test_missing_figure_dependencies_named_not_misreported_as_schema_failure(tmp_path, preflight):
    runner = make_runner(tmp_path, preflight)
    runner.phase = "installation"
    runner.check("import numpy", False)
    runner.check("import matplotlib.pyplot", False)
    session = AsyncMock()
    session.initialize.return_value = {"serverInfo": {"name": "offline fixture"}}
    session.list_tools.return_value = {
        "tools": [
            {
                "name": name,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        key: {}
                        for item in dipole.MANIFEST
                        if item["tool"] == name
                        for key in item["arguments"]
                    },
                },
            }
            for name in sorted({item["tool"] for item in dipole.MANIFEST})
        ]
    }
    session.call_tool.return_value = response({"mode": "offline", "project_open": False})
    with pytest.raises(Stop) as failure:
        asyncio.run(runner.protocol(session))
    message = str(failure.value)
    assert "import numpy" in message
    assert "import matplotlib.pyplot" in message
    assert "uv run --extra figures" in message
    assert "complete planned manifest schemas" not in message
    assert runner.metadata["schema_discrepancies"] == []
    assert session.call_tool.await_count == (1 if preflight else 0)


def test_replacement_is_fixed_scoped_and_preflight_preserves_live(tmp_path):
    live = tmp_path / "dipole"
    prepare_outputs(live, tmp_path, False)
    for name in ("project", "results", "reports"):
        (live / name / "old.txt").write_text("old")
    (live / "user_notes.txt").write_text("preserve")
    before = {str(p.relative_to(live)): p.read_bytes() for p in live.rglob("*") if p.is_file()}
    preflight = tmp_path / "preflight/dipole"
    prepare_outputs(preflight, tmp_path, True)
    (preflight / "results/stale.txt").write_text("stale")
    prepare_outputs(preflight, tmp_path, True)
    after = {str(p.relative_to(live)): p.read_bytes() for p in live.rglob("*") if p.is_file()}
    assert before == after
    assert not (preflight / "results/stale.txt").exists()
    prepare_outputs(live, tmp_path, False)
    assert not any(p.name == "old.txt" for p in live.rglob("*"))
    assert (live / "user_notes.txt").read_text() == "preserve"
    assert (live / "reports/calls.jsonl").read_text() == ""
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dipole", "preflight"]


def test_locked_project_stops_before_any_output_deletion(tmp_path):
    live = tmp_path / "dipole"
    prepare_outputs(live, tmp_path, False)
    (live / "project/other.cst").write_bytes(b"native fixture, not a real CST project")
    (live / "project/other.lck").write_bytes(b"")
    sentinel = live / "results/preserve.txt"
    sentinel.write_text("previous evidence")
    with pytest.raises(Exception, match="[Ll]ock|closed"):
        prepare_outputs(live, tmp_path, False)
    assert sentinel.read_text() == "previous evidence"
    assert (live / "project/other.lck").exists()


def test_cleanup_rejects_escaped_and_linked_paths(tmp_path, monkeypatch):
    other = tmp_path / "unrelated"
    other.mkdir()
    sentinel = other / "keep.txt"
    sentinel.write_text("untouched")
    with pytest.raises(Stop):
        prepare_outputs(other, tmp_path, False)
    live = tmp_path / "dipole"
    live.mkdir()
    try:
        (live / "results").symlink_to(other, target_is_directory=True)
    except OSError:
        # Windows can deny creating symlinks. Exercise the same link rejection
        # branch with a simulated link, still leaving all real files untouched.
        real_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path, "is_symlink", lambda p: p == live / "results" or real_is_symlink(p)
        )
    with pytest.raises(Stop):
        prepare_outputs(live, tmp_path, False)
    assert sentinel.read_text() == "untouched"


def test_saved_results_pagination_has_same_filter_and_monotonic_offset(tmp_path):
    runner = make_runner(tmp_path)
    catalog(
        runner,
        "cst_list_saved_results",
        {"project_path": {}, "contains": {}, "offset": {}, "limit": {}},
    )
    session = AsyncMock()
    session.call_tool.return_value = response(
        {"status": "ok", "entries": [{"tree_path": "last"}], "next_offset": None}
    )
    args = {
        "project_path": str(runner.project),
        "contains": "S-Parameters",
        "offset": 0,
        "limit": 50,
    }
    items = asyncio.run(
        runner.saved_pages(session, args, {"entries": [{"tree_path": "first"}], "next_offset": 50})
    )
    assert [p["tree_path"] for p in items] == ["first", "last"]
    assert session.call_tool.await_args.args[1] == {**args, "offset": 50}
    with pytest.raises(Stop):
        asyncio.run(runner.saved_pages(session, args, {"entries": [], "next_offset": 0}))


def test_quantity_provenance_and_ideal_pattern_with_seam(tmp_path):
    file = tmp_path / "pattern.txt"
    lines = ["Theta [deg.] Phi [deg.] Abs(Directivity) [dBi]"]
    for p in range(0, 361, 5):
        for t in range(0, 181, 5):
            amplitude = (
                0
                if t in (0, 180)
                else abs(
                    math.cos(math.pi / 2 * math.cos(math.radians(t))) / math.sin(math.radians(t))
                )
            )
            db = 2.15 + 20 * math.log10(max(amplitude, 1e-12))
            lines.append(f"{t} {p} {db:.12g}")
    file.write_text("\n".join(lines))
    grid = farfield_grid(file, "directivity")
    assert grid["seam_duplicates"] == 37
    assert grid["sample_count"] == 37 * 72
    physics = dipole_physics(grid)
    assert physics["peak_dbi"] == pytest.approx(2.15)
    assert physics["hpbw_deg"] == pytest.approx(78, abs=1)
    assert physics["axial_suppression_db"] > 200
    assert physics["azimuth_ripple_db"] == 0
    assert physics["shape_max_error_db_above_minus20"] < 1e-8
    assert sphere_mean(grid) == pytest.approx(1, abs=0.004)
    with pytest.raises(Incomplete, match="original header"):
        farfield_grid(file, "realized_gain")


@pytest.mark.parametrize(
    "label,quantity", [("Abs(Grlz)", "realized_gain"), ("Abs(Dir.)", "directivity")]
)
def test_native_farfield_quantity_labels_are_recognized_without_substitution(
    tmp_path, label, quantity
):
    path = tmp_path / "native.txt"
    lines = [f"Theta [deg.] Phi [deg.] {label}[dBi   ]"]
    lines.extend(
        f"{theta} {phi} {2 if theta == 90 else -40}" for phi in (0, 180) for theta in (0, 90, 180)
    )
    path.write_text("\n".join(lines))
    grid = farfield_grid(path, quantity)
    assert grid["quantity"] == quantity
    assert grid["sample_count"] == 6
    with pytest.raises(Incomplete, match="original header"):
        farfield_grid(path, "directivity" if quantity == "realized_gain" else "realized_gain")
    path.write_text("\n".join(lines).replace(label, "Abs(Gain)"))
    with pytest.raises(Incomplete, match="original header"):
        farfield_grid(path, quantity)


def test_archive_sources_before_same_execution_overwrite(tmp_path):
    runner = make_runner(tmp_path)
    source = runner.results / "work/automatic.txt"
    source.write_text("theta phi realized gain\n0 0 1\n")
    first = runner.archive({"path": str(source)}, {}, 1)[0]
    source.write_text("overwritten by next call")
    second = runner.archive({"path": str(source)}, {}, 2)[0]
    assert Path(first["archived"]).read_text().startswith("theta")
    assert Path(second["archived"]).read_text() == "overwritten by next call"
    assert first["sha256"] != second["sha256"]


def test_entry_point_paths_are_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert dipole.ROOT == HERE.parents[1]
    assert dipole.BATCH == HERE
    runner = dipole.Dipole(dipole.options(["--preflight"]))
    assert runner.base == HERE / "preflight/dipole"


def test_runner_finally_writes_reports_after_failure_without_native_cleanup(tmp_path, monkeypatch):
    runner = dipole.Dipole(dipole.options([]), parent=tmp_path)
    session = AsyncMock()
    session.call_tool.return_value = response(
        {"status": "error", "message": "missing native prerequisite"}, True
    )
    catalog(runner, "cst_get_impedance", {"port": {"type": "integer"}})

    @asynccontextmanager
    async def transport(*args, **kwargs):
        yield None, None

    @asynccontextmanager
    async def session_context(*args, **kwargs):
        yield session

    def environment():
        runner.env = {"CST_CONNECT_MODE": "disabled"}

    async def protocol(selected_session):
        pass

    async def live(selected_session):
        runner.phase = "E"
        await runner.request(selected_session, "cst_get_impedance", {"port": 1})

    monkeypatch.setattr(dipole, "stdio_client", transport)
    monkeypatch.setattr(dipole, "ClientSession", session_context)
    monkeypatch.setattr(dipole, "preserve_cst_processes", nullcontext)
    monkeypatch.setattr(runner, "configure_environment", environment)
    monkeypatch.setattr(runner, "protocol", protocol)
    monkeypatch.setattr(runner, "live", live)
    assert asyncio.run(runner.run()) == 1
    assert (runner.reports / "validation.md").exists()
    assert len((runner.reports / "calls.jsonl").read_text().splitlines()) == 1
    assert session.call_tool.await_count == 1
    assert json.loads((runner.results / "validation.json").read_text())["exit_code"] == 1


def test_probes_reject_transport_or_unrelated_errors_as_contract_success():
    assert dipole.Dipole.probe_rejection(
        "cst_get_radiation_pattern_3d", {}, "Cartesian resampling is not implemented"
    )
    assert not dipole.Dipole.probe_rejection(
        "cst_get_radiation_pattern_3d", {}, "license server refused the operation"
    )


@pytest.mark.parametrize("power_block", ["1 1.1 1.2 1e9", "1\n1.1\n1.2\n1e9", "1 1.1\n\n1.2 1e9"])
def test_ffs_complex_fields_and_counts_are_distinct_from_gain(tmp_path, power_block):
    path = tmp_path / "field.ffs"
    path.write_text(
        "// Data Type:\nFarfield\n// Radiated/Accepted/Stimulated Power, Frequency:\n"
        f"{power_block}\n"
        "// Total #phi samples, total #theta samples:\n2 2\n"
        "// Phi, Theta, Re(E_Theta), Im(E_Theta), Re(E_Phi), Im(E_Phi):\n"
        "0 0 1 2 3 4\n0 180 2 3 4 5\n180 0 3 4 5 6\n180 180 4 5 6 7\n"
    )
    parsed = ffs_source(path)
    assert parsed["sample_count"] == 4
    assert parsed["counts_match"]
    assert parsed["frequency_ghz"] == [1]
    assert parsed["finite_field_components"]
    assert "not gain" in parsed["quantity"]
    assert parsed["source_reuse_tested"] is False


@pytest.mark.parametrize("block", ["1 1.1 1.2", "1 1.1 1.2 nan", "1 1.1 1.2 -1e9"])
def test_ffs_invalid_frequency_block_does_not_consume_next_section(tmp_path, block):
    path = tmp_path / "field.ffs"
    path.write_text(
        "// Radiated/Accepted/Stimulated Power, Frequency:\n"
        + block
        + "\n// Total #phi samples, total #theta samples:\n2 2\n"
        + "// Phi, Theta, Re(E_Theta), Im(E_Theta), Re(E_Phi), Im(E_Phi):\n"
        + "0 0 1 2 3 4\n"
    )
    with pytest.raises(Incomplete, match="FFS"):
        ffs_source(path)


def test_material_failure_blocks_geometry_and_solver_in_mock_workflow(tmp_path):
    runner = make_runner(tmp_path)
    runner.connected = True
    session = AsyncMock()
    tools = {
        "cst_load_material": {"name": {}, "library_name": {}},
        "cst_read_project_log": {},
        "cst_get_simulation_status": {"timeout_s": {}},
    }
    for tool, props in tools.items():
        catalog(runner, tool, props)
    session.call_tool.side_effect = [
        response({"status": "error", "message": "native library load failed"}, True),
        response({"status": "ok", "tail": "native message"}),
        response({"status": "ok", "running": False}),
    ]
    # Enter the real manifest loop at phase B. Earlier phases are mocked solely
    # to test dependency blocking and never count as native support.
    original = dipole.PHASES
    try:
        dipole.PHASES = {"B": original["B"], "C": original["C"], "D": original["D"]}
        with pytest.raises(Stop):
            asyncio.run(runner.live(session))
    finally:
        dipole.PHASES = original
    calls = [c.args[0] for c in session.call_tool.await_args_list]
    assert calls == ["cst_load_material", "cst_read_project_log", "cst_get_simulation_status"]
    assert not any("cylinder" in call or "simulation" == call for call in calls)
