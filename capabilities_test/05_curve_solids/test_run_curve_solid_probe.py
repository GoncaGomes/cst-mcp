"""Offline checks for the curve-to-solid probe. No CST and no MCP server are started."""

import asyncio
import json
import math
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_curve_solid_probe as probe

from cst_mcp.validators import validate_vba_input

EXPECTED = probe.expected_geometry()


def evaluate(law: str, t: float) -> float:
    # The laws are the probe's own fixed strings, built from numbers only.
    return eval(law, {"__builtins__": {}}, {"cos": math.cos, "sin": math.sin, "t": t})


def make_probe(tmp_path, *argv):
    runner = probe.ProbeTest(probe.options(list(argv)), work=tmp_path)
    runner.catalog = {
        "cst_execute_vba": {
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            }
        }
    }
    return runner


def close(runner):
    runner.calls.close()
    runner.stderr.close()


def test_benchmark_helix_matches_task_18():
    assert EXPECTED["helix"]["volume"] == pytest.approx(606.7, abs=0.05)
    bounds = EXPECTED["bounds"]["sweep"]
    assert bounds["x"] == pytest.approx([-20.5, 20.5], abs=1e-6)
    assert bounds["y"] == pytest.approx([-20.5, 20.5], abs=1e-6)
    # The flat end caps are tilted by the helix pitch angle, so Z is not exactly 3.5..172.5.
    assert bounds["z"] == pytest.approx([3.512, 172.488], abs=1e-3)
    assert EXPECTED["bounds"]["convert"]["x"] == pytest.approx([179.5, 220.5], abs=1e-6)
    assert EXPECTED["bounds"]["model"]["x"] == pytest.approx([79.5, 120.5], abs=1e-6)


def test_spiral_trace_expectation():
    spiral = EXPECTED["spiral"]
    assert spiral["volume"] == pytest.approx(
        spiral["length"] * probe.TRACE_WIDTH * probe.TRACE_THICKNESS
    )
    bounds = EXPECTED["bounds"]["trace"]
    # The outer end is at radius 6.5 on +X; the flat end adds half the width there.
    assert bounds["x"][1] == pytest.approx(probe.SPIRAL_X + 6.5 + 0.25, abs=1e-3)
    assert bounds["z"] == [0.0, probe.TRACE_THICKNESS]


@pytest.mark.parametrize("t", [0.0, 1.0, 7.5, probe.HELIX_T1])
def test_helix_laws_follow_the_reference_curve(t):
    for cx in (probe.SWEEP_X, probe.MODEL_X, probe.CONVERT_X):
        laws = probe.helix_laws(cx)
        assert [evaluate(law, t) for law in laws] == pytest.approx(probe.helix_point(t, cx))


@pytest.mark.parametrize("t", [0.0, 2.0, probe.SPIRAL_T1])
def test_spiral_laws_follow_the_reference_curve(t):
    laws = probe.spiral_laws(probe.SPIRAL_X)
    assert [evaluate(law, t) for law in laws] == pytest.approx(
        probe.spiral_point(t, probe.SPIRAL_X), abs=1e-12
    )


@pytest.mark.parametrize("s", [0.0, 0.7, 2.0, 4.0, 2 * math.pi])
def test_sweep_profile_is_a_wire_circle_normal_to_the_path_start(s):
    start = probe.helix_point(0.0, probe.SWEEP_X)
    tangent = probe.helix_tangent(0.0)
    point = [evaluate(law, s) for law in probe.profile_circle_laws(probe.SWEEP_X)]
    offset = [p - q for p, q in zip(point, start)]
    assert math.hypot(*offset) == pytest.approx(probe.WIRE_RADIUS)
    assert sum(o * d for o, d in zip(offset, tangent)) == pytest.approx(0.0, abs=1e-12)


def test_every_fixed_block_passes_the_raw_vba_screen():
    for code in probe.FIXED_VBA:
        validate_vba_input(code)


def test_queries_use_output_capture_and_mutations_use_history():
    # CSTClient.execute_vba routes code with Debug.Print to output capture (no history).
    assert all("Debug.Print" in code for code in probe.QUERIES)
    assert not any("Debug.Print" in code for code in probe.MUTATIONS)


def test_conversion_runs_last_after_its_wire():
    order = probe.METHOD_ORDER
    conversions = [k for k in order if k in probe.CONVERSIONS]
    assert order[-len(conversions) :] == tuple(conversions)
    assert order[-1] == "A3_convert_solid_model"
    for conversion, source in probe.CONVERSIONS.items():
        assert order.index(source) < order.index(conversion)
    assert all(order.index(f) < order.index("B_wire_model") for f in probe.FAILURE_CASES)
    assert set(probe.SOLIDS) <= set(order)


def test_planned_failures_are_labelled():
    assert set(probe.EXPECTED_ERRORS) <= set(probe.METHOD_ORDER)
    assert probe.classify_outcome("F1_wire_missing_curve", "error") == "expected error"
    assert probe.classify_outcome("A2_convert", "error") == "expected error"
    assert probe.classify_outcome("A2_convert", "executed") == "unexpected success"
    assert probe.classify_outcome("A3_convert_solid_model", "executed") == "executed"
    assert probe.classify_outcome("A3_convert_solid_model", "error") == "native error"
    assert probe.classify_outcome("F2_trace_missing_curve", "timeout") == "native timeout"


def test_conversions_name_the_wires_their_sources_create():
    for conversion, source in probe.CONVERSIONS.items():
        wire = probe.METHOD_BLOCKS[source].split('.Name "')[1].split('"')[0]
        assert f'.Name "{wire}"' in probe.METHOD_BLOCKS[conversion]
        assert '.SolidWireModel "True"' in probe.METHOD_BLOCKS[source] or conversion == "A2_convert"
    assert set(probe.METHOD_CURVES) <= set(probe.METHOD_ORDER)
    assert {c for names in probe.METHOD_CURVES.values() for c in names} <= set(probe.CURVE_ITEMS)


def test_each_method_uses_its_own_curve_group():
    groups = {}
    for key in ("D_trace", "C_sweep", "B_wire_model", "A1_wire_add"):
        code = probe.METHOD_BLOCKS[key]
        refs = [line.split('"')[1] for line in code.splitlines() if ".Curve " in line]
        refs += [line.split('"')[1] for line in code.splitlines() if ".Path " in line]
        for ref in refs:
            groups.setdefault(ref.split(":")[0], set()).add(key)
    assert all(len(users) == 1 for users in groups.values())


def solid_entry(volume, bounds):
    return {"name": "X:Y", "found": True, "bounds": bounds, "volume": volume}


def test_judge_solid_verdicts():
    volume = EXPECTED["helix"]["volume"]
    exact = EXPECTED["bounds"]["sweep"]
    assert probe.judge_solid(solid_entry(volume, exact), volume, exact)["verdict"] == "matches"

    loose = {axis: [lo - 0.5, hi + 0.5] for axis, (lo, hi) in exact.items()}
    assert probe.judge_solid(solid_entry(volume, loose), volume, exact)["bounds_ok"] is True

    too_loose = {axis: [lo - 2, hi + 2] for axis, (lo, hi) in exact.items()}
    result = probe.judge_solid(solid_entry(volume, too_loose), volume, exact)
    assert result["verdict"] == "geometry differs" and result["bounds_ok"] is False

    short = dict(exact, z=[exact["z"][0], exact["z"][1] - 10])
    assert probe.judge_solid(solid_entry(volume, short), volume, exact)["bounds_ok"] is False

    faceted = probe.judge_solid(solid_entry(volume * 0.9, exact), volume, exact)
    assert faceted["verdict"] == "geometry differs"
    assert faceted["volume_relative_error"] == pytest.approx(0.1)

    assert probe.judge_solid(None, volume, exact)["verdict"] == "no solid"
    missing = {"name": "X:Y", "found": False, "error": "Solid not found"}
    assert probe.judge_solid(missing, volume, exact)["verdict"] == "no solid"
    no_volume = solid_entry(None, exact)
    assert probe.judge_solid(no_volume, volume, exact)["volume_ok"] is False


def test_history_presence_matches_code_ignoring_indentation():
    blocks = {
        "C_sweep": probe.METHOD_BLOCKS["C_sweep"],
        "F1": probe.METHOD_BLOCKS["F1_wire_missing_curve"],
    }
    saved = {
        "history": [
            {
                "caption": "cst_mcp_1",
                "code": [line.strip() for line in blocks["C_sweep"].splitlines()],
            },
            {"caption": "cst_mcp_2", "code": ['Component.New "Other"']},
        ]
    }
    assert probe.history_presence(saved, blocks) == {"C_sweep": True, "F1": False}
    assert probe.history_presence({}, blocks) == {"C_sweep": False, "F1": False}


def test_read_saved_history(tmp_path):
    project = tmp_path / "project.cst"
    assert probe.read_saved_history(project)["status"] == "missing"
    history = tmp_path / "project" / "Model" / "3D" / "ModelHistory.json"
    history.parent.mkdir(parents=True)
    history.write_text("﻿" + json.dumps({"history": []}), encoding="utf-8")
    saved = probe.read_saved_history(project)
    assert saved["status"] == "ok" and saved["data"] == {"history": []}
    history.write_text("{", encoding="utf-8")
    assert probe.read_saved_history(project)["status"] == "unreadable"


def test_curve_item_parser():
    items = probe.parse_items(
        "POINTS\tProbeTracePath:spiral\t512\nERROR\tProbeSweepPath:helix\tCurve not found\nDONE"
    )
    assert items == {
        "ProbeTracePath:spiral": {"present": True, "points": 512},
        "ProbeSweepPath:helix": {"present": False, "error": "Curve not found"},
    }
    assert all(f'"{name}"' in probe.ITEMS_QUERY for name in probe.CURVE_ITEMS)
    with pytest.raises(probe.StopTest):
        probe.parse_items("POINTS\tonly-two\nDONE")


def test_query_parsers():
    curves = probe.parse_curves("COUNT\t2\nCURVE\tProbeA:helix\nCURVE\tProbeB:xsec\nDONE\n")
    assert curves == {"count": 2, "names": ["ProbeA:helix", "ProbeB:xsec"]}
    wires = probe.parse_wires(
        "LENGTH\thelix_model\t772,47\nERROR\tmissing_wire\tWire not found\nDONE"
    )
    assert wires == {
        "helix_model": {"length": 772.47},
        "missing_wire": {"error": "Wire not found"},
    }
    assert probe.parse_profile("CLOSED\tTrue\nDONE")["closed"] is True
    assert probe.parse_profile("CLOSED\tFalse\nDONE")["closed"] is False
    assert probe.parse_profile("ERROR\tno such curve\nDONE")["closed"] is None
    with pytest.raises(probe.StopTest):
        probe.parse_curves("COUNT\t1\nCURVE\tA:b\n")
    with pytest.raises(probe.StopTest):
        probe.parse_wires("SOMETHING\telse\nDONE")
    with pytest.raises(probe.StopTest):
        probe.parse_tagged("DONE\nlate")


async def test_unlisted_raw_vba_is_refused_before_any_call(tmp_path):
    runner = make_probe(tmp_path, "--preflight")

    class NoCalls:
        async def call_tool(self, *args):
            raise AssertionError("no call expected")

    try:
        with pytest.raises(probe.StopTest, match="fixed blocks"):
            await runner.request(NoCalls(), "cst_execute_vba", {"code": 'Solid.Delete "X:Y"'})
        assert runner.sequence == 0
    finally:
        close(runner)


async def test_timeout_records_the_step_and_stops_further_calls(tmp_path):
    runner = make_probe(tmp_path, "--call-timeout", "0.05")

    class Stalls:
        calls = 0

        async def call_tool(self, *args):
            Stalls.calls += 1
            await asyncio.sleep(10)

    runner.step = "A2_convert"
    session = Stalls()
    try:
        with pytest.raises(probe.UnknownState):
            await runner.request(
                session, "cst_execute_vba", {"code": probe.METHOD_BLOCKS["A2_convert"]}
            )
        assert runner.unknown is True
        assert runner.outcomes["A2_convert"]["outcome"] == "client timeout or transport loss"
        with pytest.raises(probe.UnknownState):
            await runner.request(session, "cst_execute_vba", {"code": probe.SHAPES_QUERY})
        assert Stalls.calls == 1
        rows = {row["step"]: row for row in runner.method_rows()}
        assert rows["A2_convert"]["outcome"] == "client timeout or transport loss"
        assert rows["C_sweep"]["outcome"] == "not run"
    finally:
        close(runner)


def test_server_timeout_payload_is_indeterminate(tmp_path):
    runner = make_probe(tmp_path)

    class Reports:
        async def call_tool(self, *args):
            text = json.dumps({"status": "timeout", "execution_state": "unknown"})
            return {"content": [{"type": "text", "text": text}], "isError": False}

    runner.step = "C_sweep"
    try:
        with pytest.raises(probe.UnknownState):
            asyncio.run(
                runner.request(
                    Reports(), "cst_execute_vba", {"code": probe.METHOD_BLOCKS["C_sweep"]}
                )
            )
        assert runner.outcomes["C_sweep"]["outcome"].startswith("server timeout")
    finally:
        close(runner)


def test_workspace_refuses_unowned_and_unreset_projects(tmp_path):
    (tmp_path / "project.cst").write_text("x", encoding="utf-8")
    runner = make_probe(tmp_path)
    try:
        with pytest.raises(probe.StopTest, match="without probe ownership"):
            runner.prepare_workspace()
        (tmp_path / "workspace.json").write_text(
            json.dumps({"owner": probe.OWNER}), encoding="utf-8"
        )
        with pytest.raises(probe.StopTest, match="--reset"):
            runner.prepare_workspace()
        (tmp_path / "workspace.json").write_text(json.dumps({"owner": "other"}), encoding="utf-8")
        with pytest.raises(probe.StopTest, match="not owned"):
            runner.prepare_workspace()
    finally:
        close(runner)


def test_reset_deletes_only_the_owned_project(tmp_path):
    (tmp_path / "project.cst").write_text("x", encoding="utf-8")
    (tmp_path / "project" / "Model").mkdir(parents=True)
    (tmp_path / "project.log").write_text("x", encoding="utf-8")
    (tmp_path / "summary.json").write_text("{}", encoding="utf-8")
    (tmp_path / "other.cst").write_text("x", encoding="utf-8")
    (tmp_path / "workspace.json").write_text(json.dumps({"owner": probe.OWNER}), encoding="utf-8")
    runner = make_probe(tmp_path, "--reset")
    try:
        runner.prepare_workspace()
    finally:
        close(runner)
    assert not (tmp_path / "project.cst").exists()
    assert not (tmp_path / "project").exists()
    assert not (tmp_path / "project.log").exists()
    assert (tmp_path / "summary.json").exists() and (tmp_path / "other.cst").exists()
    manifest = json.loads((tmp_path / "workspace.json").read_text(encoding="utf-8"))
    assert manifest["owner"] == probe.OWNER and manifest["invocation"] == runner.invocation


def test_reset_with_nothing_to_reset_is_refused(tmp_path):
    runner = make_probe(tmp_path, "--reset")
    try:
        with pytest.raises(probe.StopTest, match="Nothing to reset"):
            runner.prepare_workspace()
    finally:
        close(runner)


def test_reset_is_refused_with_preflight():
    with pytest.raises(SystemExit):
        probe.options(["--preflight", "--reset"])
