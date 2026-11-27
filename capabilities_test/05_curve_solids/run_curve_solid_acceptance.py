"""Acceptance of the task 18 tools through a real MCP stdio session.

Builds the task 18 geometry with ``cst_create_curve_wire`` and
``cst_create_trace_from_curve`` (no raw VBA for the geometry) and checks each result
against the task's acceptance criteria. Shares the probe's client: owned workspace,
``--reset``, no further calls after a timeout (exit code 2).
"""

from __future__ import annotations

import asyncio
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_curve_solid_probe as probe

# isort: split
# The probe import installs the search paths of batches 01 and 02.
from run_batch import UNITS_BLOCK
from run_parameter_brick import SHAPES_QUERY, UNITS_QUERY, StopTest, parse_shapes

OWNER = "cst-mcp-curve-solid-acceptance-v1"
TOOLSETS = "connection,project,geometry,materials,diagnostics,vba"

WIRE_COMPONENT, BOARD_COMPONENT, NEW_COMPONENT = "AccWire", "AccBoard", "AccNew"
# Project names: validate_name allows no parentheses. The library entries keep theirs.
COPPER, FR4 = "Copper_annealed", "FR4_lossy"
COPPER_LIBRARY, FR4_LIBRARY = "Copper (annealed)", "FR-4 (lossy)"
BOARD_TOP = 1.6
# Each case gets its own curve, far from the others.
PEC_X, COPPER_X, BENT_X, NEW_X, SPIRAL_X = 0.0, 100.0, 200.0, 300.0, -100.0
BENT_POINTS = [[BENT_X, 0, 0], [BENT_X, 0, 10], [BENT_X + 10, 0, 10]]

SETUP = f'Component.New "{WIRE_COMPONENT}"\nComponent.New "{BOARD_COMPONENT}"'
CURVES_GROUP = 'Curve.NewCurve "Curves"'
FIXED_VBA = frozenset({UNITS_BLOCK, UNITS_QUERY, SHAPES_QUERY, SETUP, CURVES_GROUP})
PLANNED_TOOLS = (
    *probe.PLANNED_TOOLS,
    "cst_create_analytical_curve",
    "cst_create_polygon3d",
    "cst_create_brick",
    "cst_load_material",
    "cst_create_curve_wire",
    "cst_create_trace_from_curve",
)


def helix_curve(name: str, cx: float) -> dict:
    x, y, z = probe.helix_laws(cx)
    return {
        "name": name,
        "x_expr": x,
        "y_expr": y,
        "z_expr": z,
        "t_min": 0,
        "t_max": probe.HELIX_T1,
    }


def spiral_curve(name: str) -> dict:
    x, y, _ = probe.spiral_laws(SPIRAL_X)
    return {
        "name": name,
        "x_expr": x,
        "y_expr": y,
        "z_expr": probe.fmt(BOARD_TOP),
        "t_min": 0,
        "t_max": probe.SPIRAL_T1,
    }


def wire(curve: str, name: str, **extra) -> dict:
    return {
        "curve": f"Curves:{curve}",
        "component": WIRE_COMPONENT,
        "name": name,
        "radius": probe.WIRE_RADIUS,
        **extra,
    }


def trace(curve: str, name: str) -> dict:
    return {
        "curve": f"Curves:{curve}",
        "component": BOARD_COMPONENT,
        "name": name,
        "width": probe.TRACE_WIDTH,
        "thickness": probe.TRACE_THICKNESS,
    }


def expected_acceptance() -> dict:
    base = probe.expected_geometry()

    def helix_bounds(cx):
        return probe.tube_bounds(
            lambda t: probe.helix_point(t, cx),
            probe.helix_tangent,
            probe.HELIX_T1,
            probe.WIRE_RADIUS,
        )

    spiral = probe.strip_bounds(
        lambda t: probe.spiral_point(t, SPIRAL_X),
        probe.spiral_tangent,
        probe.SPIRAL_T1,
        probe.TRACE_WIDTH,
        probe.TRACE_THICKNESS,
    )
    spiral["z"] = [BOARD_TOP, BOARD_TOP + probe.TRACE_THICKNESS]
    bent_length = 20.0
    return {
        "unit": "mm",
        "helix": base["helix"],
        "spiral": base["spiral"],
        "bent": {
            "length": bent_length,
            "volume": math.pi * probe.WIRE_RADIUS**2 * bent_length,
            "source": "pi * r^2 * polyline length; the corner shape makes this approximate",
        },
        "bounds": {
            "pec": helix_bounds(PEC_X),
            "copper": helix_bounds(COPPER_X),
            "new_component": helix_bounds(NEW_X),
            "spiral": spiral,
        },
    }


# Case table: key -> (kind, solid, expected geometry key, expected bounds key, history marker,
# expected history entries). kind "build" must make the solid; "refuse" must change nothing;
# "record" is reported without a pass/fail criterion.
CASES = {
    "H1_helix_pec": ("build", f"{WIRE_COMPONENT}:helix_pec", "helix", "pec", '"helix_pec"', 2),
    "H2_helix_copper": ("build", f"{WIRE_COMPONENT}:helix_cu", "helix", "copper", '"helix_cu"', 2),
    "T1_spiral_on_board": (
        "build",
        f"{BOARD_COMPONENT}:spiral",
        "spiral",
        "spiral",
        '"spiral"',
        1,
    ),
    "P1_bent_polygon_wire": ("record", f"{WIRE_COMPONENT}:bent", "bent", None, '"bent"', 2),
    "E1_wire_missing_curve": ("refuse", None, None, None, '"missing_wire"', 0),
    "E2_trace_missing_curve": ("refuse", None, None, None, '"missing_trace"', 0),
    "E3_wire_reused_curve": ("refuse", None, None, None, '"helix_again"', 0),
    "E4_wire_zero_radius": ("refuse", None, None, None, '"zero_radius"', 0),
    "N1_new_component": (
        "record",
        f"{NEW_COMPONENT}:helix_new",
        "helix",
        "new_component",
        '"helix_new"',
        None,
    ),
}
STEPS = tuple(CASES)
LIMITATIONS = [
    "One run on one CST version, with PEC and annealed copper only.",
    (
        "A disconnected curve cannot be passed: the tools take one 'group:item', and a single "
        "item is one connected path. The probe's F3 covered a disconnected group."
    ),
    (
        "The rollback after a failed conversion runs only if CST refuses one (N1 may show it); "
        "otherwise it is covered by unit tests only."
    ),
    "Bounds are loose (Solid.GetLooseBoundingBoxOfShape); volume is the main check.",
    "History counts are read from the saved Model/3D/ModelHistory.json.",
]


def history_codes(saved: dict) -> list[str]:
    data = saved.get("data") if isinstance(saved, dict) else None
    entries = data.get("history", []) if isinstance(data, dict) else []
    codes = []
    for entry in entries:
        code = entry.get("code", []) if isinstance(entry, dict) else []
        codes.append("\n".join(code) if isinstance(code, list) else str(code))
    return codes


def count_entries(codes: list[str], marker: str) -> int:
    return sum(marker in code for code in codes)


def judge_case(kind: str, payload: dict, expected=None, bounds=None) -> dict:
    """Pass/fail of one tool response against the task 18 criteria."""
    status = payload.get("status")
    if kind == "refuse":
        refused = status == "error" and not payload.get("history_labels")
        return {"passed": refused, "status": status, "code": payload.get("code")}
    measured = payload.get("measured") or {}
    entry = next(iter(measured.get("solids") or []), None)
    result = {
        "status": status,
        "curve_consumed": payload.get("curve_consumed"),
        "history_labels": payload.get("history_labels"),
        "measured_status": measured.get("status"),
    }
    if expected is not None and bounds is not None:
        result["judgement"] = probe.judge_solid(entry, expected["volume"], bounds)
    elif expected is not None and entry and isinstance(entry.get("volume"), float):
        result["volume"] = entry["volume"]
        result["volume_relative_error"] = (
            abs(entry["volume"] - expected["volume"]) / expected["volume"]
        )
    built = status == "executed" and payload.get("curve_consumed") is True
    if "judgement" in result:
        built = built and result["judgement"]["verdict"] == "matches"
    result["passed"] = built if kind == "build" else None
    return result


class AcceptanceTest(probe.ProbeTest):
    OWNER = OWNER
    TOOLSETS = TOOLSETS
    FIXED_VBA = FIXED_VBA
    PLANNED_TOOLS = PLANNED_TOOLS
    STEPS = STEPS

    @classmethod
    def default_work(cls, preflight: bool) -> Path:
        return HERE / "artifacts" / ("acceptance_preflight" if preflight else "acceptance")

    def __init__(self, options, work: Path | None = None):
        super().__init__(options, work)
        self.expected = expected_acceptance()
        self.cases: dict = {}
        self.findings: dict = {}
        self.last_shapes: dict | None = None
        self.metadata.update(
            method_order=STEPS,
            method_blocks=None,
            curve_blocks=None,
            cases={k: list(v) for k, v in CASES.items()},
            expected=self.expected,
            limitations=LIMITATIONS,
        )

    # -- plan -------------------------------------------------------------------

    def plan(self) -> list[tuple[str, str, dict]]:
        """(step, tool, arguments) in run order; N1 last, after a save."""
        return [
            ("H1_helix_pec", "cst_create_analytical_curve", helix_curve("helix_pec_path", PEC_X)),
            ("H1_helix_pec", "cst_create_curve_wire", wire("helix_pec_path", "helix_pec")),
            (
                "H2_helix_copper",
                "cst_load_material",
                {"name": COPPER, "library_name": COPPER_LIBRARY},
            ),
            (
                "H2_helix_copper",
                "cst_create_analytical_curve",
                helix_curve("helix_cu_path", COPPER_X),
            ),
            (
                "H2_helix_copper",
                "cst_create_curve_wire",
                wire("helix_cu_path", "helix_cu", material=COPPER),
            ),
            ("T1_spiral_on_board", "cst_load_material", {"name": FR4, "library_name": FR4_LIBRARY}),
            (
                "T1_spiral_on_board",
                "cst_create_brick",
                {
                    "component": BOARD_COMPONENT,
                    "name": "substrate",
                    "material": FR4,
                    "x_min": SPIRAL_X - 10,
                    "x_max": SPIRAL_X + 10,
                    "y_min": -10,
                    "y_max": 10,
                    "z_min": 0,
                    "z_max": BOARD_TOP,
                },
            ),
            ("T1_spiral_on_board", "cst_create_analytical_curve", spiral_curve("spiral_path")),
            ("T1_spiral_on_board", "cst_create_trace_from_curve", trace("spiral_path", "spiral")),
            (
                "P1_bent_polygon_wire",
                "cst_create_polygon3d",
                {"name": "bent_path", "points": BENT_POINTS},
            ),
            ("P1_bent_polygon_wire", "cst_create_curve_wire", wire("bent_path", "bent")),
            ("E1_wire_missing_curve", "cst_create_curve_wire", wire("missing", "missing_wire")),
            (
                "E2_trace_missing_curve",
                "cst_create_trace_from_curve",
                trace("missing", "missing_trace"),
            ),
            (
                "E3_wire_reused_curve",
                "cst_create_curve_wire",
                wire("helix_pec_path", "helix_again"),
            ),
            (
                "E4_wire_zero_radius",
                "cst_create_curve_wire",
                {**wire("helix_cu_path", "zero_radius"), "radius": 0},
            ),
            (
                "N1_new_component",
                "cst_create_analytical_curve",
                helix_curve("helix_new_path", NEW_X),
            ),
            (
                "N1_new_component",
                "cst_create_curve_wire",
                {**wire("helix_new_path", "helix_new"), "component": NEW_COMPONENT},
            ),
        ]

    # -- phases -----------------------------------------------------------------

    async def preflight(self, session) -> None:
        self.phase = "offline_preflight"
        for _, name, args in self.plan():
            self.validate(name, args)
        for name, args in (
            ("cst_connect", {"mode": "new"}),
            ("cst_create_project", {"path": str(self.project), "project_type": "MWS"}),
            ("cst_get_solid_bounds", {"solids": [c[1] for c in CASES.values() if c[1]]}),
            ("cst_save_project", {}),
            ("cst_close_project", {}),
            ("cst_disconnect", {}),
        ):
            self.validate(name, args)
        for code in sorted(FIXED_VBA):
            payload = await self.request(session, "cst_execute_vba", {"code": code})
            self.check(
                "fixed block passes the raw VBA screen unchanged",
                payload.get("status") == "offline" and payload.get("vba") == code,
                first_line=code.splitlines()[0],
            )
        for tool, args, blocks in (
            ("cst_create_curve_wire", wire("helix_pec_path", "helix_pec"), 2),
            ("cst_create_trace_from_curve", trace("spiral_path", "spiral"), 1),
        ):
            payload = await self.request(session, tool, args)
            self.check(
                f"{tool} generates its history blocks offline",
                payload.get("status") == "offline"
                and len(payload.get("history_blocks") or []) == blocks,
                payload=payload,
            )
        refused = await self.request(
            session, "cst_create_curve_wire", {**wire("x", "zero_radius"), "radius": 0}
        )
        self.check(
            "zero radius is refused offline", refused.get("status") == "error", payload=refused
        )
        self.exit_code = 0
        self.reason = "Catalog, schemas and offline generation passed; live acceptance pending"

    async def live(self, session) -> None:
        self.phase = "connect"
        self.live_attempted = True
        connected = await self.required(session, "cst_connect", {"mode": "new"}, status="connected")
        self.check(
            "new Design Environment without open projects",
            connected.get("newly_started") is True and connected.get("open_projects") == 0,
            payload=connected,
        )
        self.connected = True
        self.phase = "project"
        await self.required(
            session,
            "cst_create_project",
            {"path": str(self.project), "project_type": "MWS"},
            status="created",
        )
        await self.required(session, "cst_execute_vba", {"code": UNITS_BLOCK}, status="executed")
        units = probe.parse_units(await self.query(session, UNITS_QUERY))
        self.check("units are mm/GHz/ns", units == probe.EXPECTED_UNITS, actual=units)
        await self.required(session, "cst_execute_vba", {"code": SETUP}, status="executed")

        self.phase = "cases"
        for step, tool, args in self.plan():
            if step == "N1_new_component" and "before_new_component" not in self.histories:
                await self.checkpoint(session, "before_new_component")
            self.step = step
            if tool == "cst_create_analytical_curve" and "curves_group" not in self.findings:
                await self.first_curve(session, args)
                continue
            payload = await self.request(session, tool, args)
            self.cases.setdefault(step, {"calls": []})["calls"].append(
                {"tool": tool, "arguments": args, "payload": payload}
            )
            if tool in {"cst_create_curve_wire", "cst_create_trace_from_curve"}:
                await self.finish_case(session, step, payload)
            elif payload.get("status") not in {"executed", "ok", "loaded", "success"}:
                self.cases[step]["setup_failed"] = payload.get("status")
        self.step = None
        await self.checkpoint(session, "final")
        self.history_check()
        self.phase = "disconnect"
        await self.required(session, "cst_close_project", status="closed")
        await self.required(session, "cst_disconnect", status="disconnected")
        self.connected = False
        failed = [k for k, c in self.cases.items() if c.get("result", {}).get("passed") is False]
        failed += [k for k in STEPS if k not in self.cases]
        self.exit_code = 1 if failed else 0
        self.reason = (
            f"Acceptance failed: {', '.join(failed)}" if failed else "All acceptance cases passed"
        )

    async def first_curve(self, session, args: dict) -> None:
        """The agent has no tool to create the 'Curves' group; find out whether it needs one."""
        payload = await self.request(session, "cst_create_analytical_curve", args)
        self.findings["curves_group"] = {
            "needed": payload.get("status") != "executed",
            "first_attempt": payload,
        }
        if payload.get("status") != "executed":
            await self.required(
                session, "cst_execute_vba", {"code": CURVES_GROUP}, status="executed"
            )
            payload = await self.required(
                session, "cst_create_analytical_curve", args, status="executed"
            )
        self.cases.setdefault(self.step, {"calls": []})["calls"].append(
            {"tool": "cst_create_analytical_curve", "arguments": args, "payload": payload}
        )

    async def finish_case(self, session, step: str, payload: dict) -> None:
        kind, solid, geometry, bounds_key, _, _ = CASES[step]
        expected = self.expected.get(geometry) if geometry else None
        bounds = self.expected["bounds"].get(bounds_key) if bounds_key else None
        result = judge_case(kind, payload, expected, bounds)
        shapes = parse_shapes(await self.query(session, SHAPES_QUERY))
        result["shapes"] = shapes
        if solid and payload.get("status") == "executed":
            readback = await self.request(
                session, "cst_get_solid_bounds", {"solids": [solid], "include_volume": True}
            )
            entry = next(iter(readback.get("solids") or []), {})
            own = next(iter((payload.get("measured") or {}).get("solids") or []), {})
            result["readback_agrees"] = entry.get("bounds") == own.get("bounds") and entry.get(
                "volume"
            ) == own.get("volume")
            result["material"] = entry.get("material")
            if step == "H2_helix_copper":
                result["material_ok"] = entry.get("material") == COPPER
                result["passed"] = bool(result["passed"] and result["material_ok"])
            if kind == "build":
                result["passed"] = bool(result["passed"] and result["readback_agrees"])
        if kind == "refuse":
            previous = self.last_shapes
            result["shapes_unchanged"] = previous is None or previous == shapes
            result["passed"] = bool(result["passed"] and result["shapes_unchanged"])
        self.last_shapes = shapes
        self.cases[step]["result"] = result
        self.event({"event": "case", "step": step, "result": result})

    def history_check(self) -> None:
        codes = history_codes(self.histories.get("final", {}))
        for step, (_, _, _, _, marker, expected) in CASES.items():
            case = self.cases.get(step)
            if case is None:
                continue
            found = count_entries(codes, marker)
            result = case.setdefault("result", {})
            result["history_entries"] = found
            if expected is not None:
                result["history_ok"] = found == expected
                if result.get("passed") is not None:
                    result["passed"] = bool(result["passed"] and result["history_ok"])

    async def checkpoint(self, session, label: str) -> None:
        self.step = f"save_{label}"
        await self.required(session, "cst_save_project", status="saved")
        saved = probe.read_saved_history(self.project)
        self.histories[label] = saved
        self.step = None

    # -- reports ----------------------------------------------------------------

    def rows(self) -> list[dict]:
        rows = []
        for step in STEPS:
            case = self.cases.get(step, {})
            result = case.get("result", {})
            judgement = result.get("judgement", {})
            rows.append(
                {
                    "step": step,
                    "kind": CASES[step][0],
                    "passed": result.get("passed"),
                    "status": result.get("status", self.outcomes.get(step, {}).get("outcome")),
                    "code": result.get("code"),
                    "volume": judgement.get("volume", result.get("volume")),
                    "volume_relative_error": judgement.get(
                        "volume_relative_error", result.get("volume_relative_error")
                    ),
                    "bounds_ok": judgement.get("bounds_ok"),
                    "material": result.get("material"),
                    "history_entries": result.get("history_entries"),
                    "calls": len(case.get("calls", [])),
                }
            )
        return rows

    def finalize(self) -> None:
        rows = self.rows()
        histories = {
            k: {kk: vv for kk, vv in v.items() if kk != "data"} for k, v in self.histories.items()
        }
        summary = {
            "invocation": self.invocation,
            "phase": self.phase,
            "step": self.step,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "indeterminate": self.unknown,
            "preflight": self.options.preflight,
            "live_attempted": self.live_attempted,
            "project": str(self.project),
            "expected": self.expected,
            "cases": rows,
            "details": self.cases,
            "findings": self.findings,
            "outcomes": self.outcomes,
            "histories": histories,
            "checks": self.checks,
            "limitations": LIMITATIONS,
        }
        probe.write_json(self.work / "summary.json", summary)
        self.metadata.update(
            finished=probe.timestamp(), exit_code=self.exit_code, reason=self.reason
        )
        probe.write_json(self.work / "metadata.json", self.metadata)
        (self.work / "summary.md").write_text(self.summary_markdown(rows), encoding="utf-8")
        for stream in (self.calls, self.stderr):
            stream.flush()
            stream.close()

    def summary_markdown(self, rows: list[dict]) -> str:
        def cell(value):
            if value is None:
                return "—"
            if isinstance(value, float):
                return f"{value:.4g}"
            return str(value).replace("|", "/").replace("\n", " ")

        lines = [
            "# Curve-to-solid acceptance (task 18)",
            "",
            f"Invocation `{self.invocation}`; phase `{self.phase}`; exit {self.exit_code}.",
            "",
            self.reason,
            "",
            (
                "| Case | Kind | Passed | Status | Code | Volume | Volume error | Bounds ok "
                "| Material | History entries | Calls |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            error = row["volume_relative_error"]
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["step"],
                        row["kind"],
                        cell(row["passed"]),
                        cell(row["status"]),
                        cell(row["code"]),
                        cell(row["volume"]),
                        "—" if error is None else f"{100 * error:.2f} %",
                        cell(row["bounds_ok"]),
                        cell(row["material"]),
                        cell(row["history_entries"]),
                        cell(row["calls"]),
                    ]
                )
                + " |"
            )
        group = self.findings.get("curves_group")
        if group is not None:
            lines += [
                "",
                (
                    'The first analytical curve needed `Curve.NewCurve "Curves"` first: '
                    f"{cell(group['needed'])}."
                ),
            ]
        lines += ["", "## Limitations", "", *[f"- {item}" for item in LIMITATIONS], ""]
        if self.unknown:
            lines += [
                (
                    "The run stopped on an indeterminate outcome. No further calls were made. "
                    "Inspect the CST window before closing it or running again."
                ),
                "",
            ]
        return "\n".join(lines)


def main(argv=None) -> int:
    parsed = probe.options(argv)
    work = AcceptanceTest.default_work(parsed.preflight)
    try:
        with probe.WorkspaceLock(work):
            return asyncio.run(AcceptanceTest(parsed, work).run())
    except StopTest as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
