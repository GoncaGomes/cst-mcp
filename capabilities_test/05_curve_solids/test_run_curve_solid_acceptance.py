"""Offline checks for the task 18 acceptance client. No CST and no MCP server are started."""

import asyncio
import json
import sys
from pathlib import Path

import jsonschema
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_curve_solid_acceptance as acc
import run_curve_solid_probe as probe

from cst_mcp.tools import geometry
from cst_mcp.validators import validate_vba_input

EXPECTED = acc.expected_acceptance()
GEOMETRY_SCHEMAS = {
    t.name: getattr(t, "inputSchema", None) or t.input_schema for t in geometry.TOOLS
}


def make_runner(tmp_path, *argv):
    runner = acc.AcceptanceTest(probe.options(list(argv)), work=tmp_path)
    runner.catalog = {
        name: {"inputSchema": GEOMETRY_SCHEMAS.get(name, {"type": "object"})}
        for name in acc.PLANNED_TOOLS
    }
    return runner


def close(runner):
    runner.calls.close()
    runner.stderr.close()


class FakeCST:
    """A scripted MCP session that keeps just enough model state for the acceptance flow."""

    def __init__(self, project: Path, *, needs_group=False, volume_scale=None):
        self.project = project
        self.needs_group = needs_group
        self.volume_scale = volume_scale or {}
        self.curves: set[str] = set()
        self.shapes: dict[str, str] = {}
        self.history: list[list[str]] = []
        self.measured: dict[str, dict] = {}
        self.calls: list[str] = []

    @staticmethod
    def reply(payload: dict) -> dict:
        return {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}

    def entry(self, solid: str) -> dict:
        _, _, geometry_key, bounds_key, *_ = next(c for c in acc.CASES.values() if c[1] == solid)
        volume = EXPECTED[geometry_key]["volume"] * self.volume_scale.get(solid, 1.0)
        bounds = EXPECTED["bounds"].get(bounds_key) or {"x": [0, 1], "y": [0, 1], "z": [0, 1]}
        return {
            "name": solid,
            "found": True,
            "bounds": bounds,
            "material": self.shapes[solid],
            "volume": volume,
        }

    def build(self, args: dict, entries: int) -> dict:
        radius_like = [args.get(k) for k in ("radius", "width", "thickness") if k in args]
        if any(v <= 0 for v in radius_like):
            return {"status": "error", "message": "radius must be positive"}
        item = args["curve"].split(":")[1]
        if item not in self.curves:
            return {"status": "error", "code": "curve_not_found", "project_changed": False}
        self.curves.discard(item)
        solid = f"{args['component']}:{args['name']}"
        self.shapes[solid] = args.get("material", "PEC")
        self.history += [[f'.Name "{args["name"]}"']] * entries
        return {
            "status": "executed",
            "solid": solid,
            "curve_consumed": True,
            "history_labels": [f"h{i}" for i in range(entries)],
            "measured": {"status": "ok", "solids": [self.entry(solid)]},
        }

    async def call_tool(self, name, args):
        self.calls.append(name)
        if name == "cst_connect":
            return self.reply({"status": "connected", "newly_started": True, "open_projects": 0})
        if name == "cst_create_project":
            return self.reply({"status": "created", "path": args["path"]})
        if name == "cst_execute_vba":
            code = args["code"]
            if code == acc.UNITS_QUERY:
                return self.reply(
                    {"status": "ok", "output": "Length\tmm\nFrequency\tGHz\nTime\tns\n"}
                )
            if code == acc.SHAPES_QUERY:
                lines = [f"COUNT\t{len(self.shapes)}"]
                lines += [f"SOLID\t{i}\t{n}\t{m}" for i, (n, m) in enumerate(self.shapes.items())]
                return self.reply({"status": "ok", "output": "\n".join([*lines, "DONE"])})
            if code == acc.CURVES_GROUP:
                self.needs_group = False
            self.history.append(code.splitlines())
            return self.reply({"status": "executed"})
        if name in {"cst_create_analytical_curve", "cst_create_polygon3d"}:
            if self.needs_group:
                return self.reply({"status": "error", "message": "curve group not found"})
            self.curves.add(args["name"])
            self.history.append([f'.Name "{args["name"]}"'])
            return self.reply({"status": "executed"})
        if name == "cst_load_material":
            self.history.append([f'.Name "{args["name"]}"'])
            return self.reply({"status": "executed"})
        if name == "cst_create_brick":
            self.shapes[f"{args['component']}:{args['name']}"] = args["material"]
            self.history.append([f'.Name "{args["name"]}"'])
            return self.reply({"status": "executed"})
        if name == "cst_create_curve_wire":
            return self.reply(self.build(args, 2))
        if name == "cst_create_trace_from_curve":
            return self.reply(self.build(args, 1))
        if name == "cst_get_solid_bounds":
            return self.reply({"status": "ok", "solids": [self.entry(s) for s in args["solids"]]})
        if name == "cst_save_project":
            path = self.project.with_suffix("") / "Model" / "3D" / "ModelHistory.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            history = [{"caption": f"h{i}", "code": c} for i, c in enumerate(self.history)]
            path.write_text(json.dumps({"history": history}), encoding="utf-8")
            return self.reply({"status": "saved"})
        if name == "cst_close_project":
            return self.reply({"status": "closed"})
        if name == "cst_disconnect":
            return self.reply({"status": "disconnected"})
        raise AssertionError(f"unexpected tool {name}")


def run_live(tmp_path, **fake):
    runner = make_runner(tmp_path)
    session = FakeCST(runner.project, **fake)
    try:
        asyncio.run(runner.live(session))
        rows = {row["step"]: row for row in runner.rows()}
        markdown = runner.summary_markdown(runner.rows())
    finally:
        close(runner)
    return runner, session, rows, markdown


def test_expected_geometry():
    assert EXPECTED["helix"]["volume"] == pytest.approx(606.7, abs=0.05)
    assert EXPECTED["bounds"]["pec"]["x"] == pytest.approx([-20.5, 20.5], abs=1e-6)
    assert EXPECTED["bounds"]["copper"]["x"] == pytest.approx([79.5, 120.5], abs=1e-6)
    assert EXPECTED["bounds"]["spiral"]["z"] == pytest.approx([1.6, 1.7])
    assert EXPECTED["bent"]["volume"] == pytest.approx(15.708, abs=1e-3)


def make_plan(tmp_path):
    runner = make_runner(tmp_path)
    try:
        return runner.plan()
    finally:
        close(runner)


def test_plan_covers_every_case_and_new_component_runs_last(tmp_path):
    plan = make_plan(tmp_path)
    assert {step for step, _, _ in plan} == set(acc.STEPS)
    assert plan[-1][0] == "N1_new_component"
    # The geometry goes through the MCP tools only.
    assert "cst_execute_vba" not in {tool for _, tool, _ in plan}
    for _, tool, args in plan:
        if tool in GEOMETRY_SCHEMAS:
            jsonschema.validate(args, GEOMETRY_SCHEMAS[tool])


def test_history_markers_do_not_collide(tmp_path):
    plan = make_plan(tmp_path)
    names = [args.get("name") for _, _, args in plan if args.get("name")]
    for step, case in acc.CASES.items():
        marker = case[4]
        hits = [n for n in names if f'"{n}"' == marker]
        assert len(hits) <= 1, step


def test_fixed_blocks_pass_the_raw_vba_screen():
    for code in acc.FIXED_VBA:
        validate_vba_input(code)


def test_judge_case():
    ok = {
        "status": "executed",
        "curve_consumed": True,
        "measured": {
            "status": "ok",
            "solids": [
                {"found": True, "bounds": EXPECTED["bounds"]["pec"], "volume": 606.0},
            ],
        },
    }
    result = acc.judge_case("build", ok, EXPECTED["helix"], EXPECTED["bounds"]["pec"])
    assert result["passed"] is True
    off = {**ok, "measured": {"solids": [{**ok["measured"]["solids"][0], "volume": 500.0}]}}
    assert (
        acc.judge_case("build", off, EXPECTED["helix"], EXPECTED["bounds"]["pec"])["passed"]
        is False
    )
    assert (
        acc.judge_case(
            "build", {**ok, "curve_consumed": None}, EXPECTED["helix"], EXPECTED["bounds"]["pec"]
        )["passed"]
        is False
    )
    refused = {"status": "error", "code": "curve_not_found"}
    assert acc.judge_case("refuse", refused)["passed"] is True
    assert acc.judge_case("refuse", ok)["passed"] is False
    assert acc.judge_case("record", ok, EXPECTED["bent"])["passed"] is None


def test_history_counting():
    saved = {
        "data": {"history": [{"code": ['.Name "helix_pec"']}, {"code": ['.Name "helix_pec_path"']}]}
    }
    codes = acc.history_codes(saved)
    assert acc.count_entries(codes, '"helix_pec"') == 1
    assert acc.history_codes({"status": "missing"}) == []


def test_live_flow_passes_on_a_correct_cst(tmp_path):
    runner, _, rows, markdown = run_live(tmp_path)
    assert runner.exit_code == 0, runner.reason
    for step, (kind, *_rest) in acc.CASES.items():
        expected = None if kind == "record" else True
        assert rows[step]["passed"] is expected, step
    assert rows["H1_helix_pec"]["calls"] == 2  # curve and wire: task 18's "about three calls"
    assert rows["H1_helix_pec"]["history_entries"] == 2
    assert rows["T1_spiral_on_board"]["history_entries"] == 1
    assert rows["E3_wire_reused_curve"]["code"] == "curve_not_found"
    assert rows["H2_helix_copper"]["material"] == acc.COPPER
    assert runner.findings["curves_group"]["needed"] is False
    assert "All acceptance cases passed" in markdown


def test_live_flow_records_a_missing_curves_group(tmp_path):
    runner, _, _, markdown = run_live(tmp_path, needs_group=True)
    assert runner.findings["curves_group"]["needed"] is True
    assert runner.exit_code == 0, runner.reason
    assert "needed `Curve.NewCurve" in markdown and "True" in markdown


def test_live_flow_fails_on_wrong_volume(tmp_path):
    runner, _, rows, _ = run_live(tmp_path, volume_scale={"AccWire:helix_cu": 0.9})
    assert runner.exit_code == 1
    assert rows["H2_helix_copper"]["passed"] is False
    assert rows["H1_helix_pec"]["passed"] is True
    assert "H2_helix_copper" in runner.reason


def test_raw_vba_outside_the_fixed_blocks_is_refused(tmp_path):
    runner = make_runner(tmp_path, "--preflight")

    class NoCalls:
        async def call_tool(self, *args):
            raise AssertionError("no call expected")

    try:
        with pytest.raises(probe.StopTest, match="fixed blocks"):
            asyncio.run(runner.request(NoCalls(), "cst_execute_vba", {"code": probe.SETUP}))
    finally:
        close(runner)


def test_workspace_is_separate_from_the_probe():
    assert acc.AcceptanceTest.default_work(False).name == "acceptance"
    assert acc.AcceptanceTest.default_work(True).name == "acceptance_preflight"
    assert probe.ProbeTest.default_work(False).name == "probe"
    assert acc.OWNER != probe.OWNER
