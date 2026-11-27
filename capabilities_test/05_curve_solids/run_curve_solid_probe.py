"""Probe which native CST method turns a curve into a solid, through a real MCP stdio session.

Task 18 (``docs/tasks/mcp/18-solid-from-curve.md``) needs one of these methods for
``cst_create_curve_wire`` and ``cst_create_trace_from_curve``. Each method runs as a
fixed raw VBA block, so the probe needs no new MCP tool. Outcomes, including native
errors, are recorded rather than treated as failures of the probe. A timeout stops all
further calls (exit code 2) and is itself the answer for the method that caused it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

from jsonschema.validators import validator_for
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[2]
BATCH = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "capabilities_test" / "02_parameters"))
# Stateless helpers only; batch 02's runner classes are never constructed.
from run_parameter_brick import (
    DEFAULT_CST_PATH,
    EXPECTED_UNITS,
    LOCK_SUFFIXES,
    SHAPES_QUERY,
    UNITS_QUERY,
    StopTest,
    UnknownState,
    ensure_closed,
    exception_leaves,
    indeterminate,
    interpret,
    parse_shapes,
    parse_units,
    positive_timeout,
    preserve_cst_processes,
    reject_links,
    serialize,
    sha256,
    timestamp,
    write_json,
)

# isort: split
# The preceding import installs batch 01's import-search path.
from run_batch import UNITS_BLOCK

logger = logging.getLogger(__name__)

OWNER = "cst-mcp-curve-solid-probe-v1"
TOOLSETS = "connection,project,geometry,diagnostics,vba"
HELP_ROOT = "Online Help/mergedProjects/VBA_3D"
REFERENCES = {
    "wire": (
        f"{HELP_ROOT}/common_vbabasicsolids/common_vbawire_object.htm",
        ("Curvewire", "ConvertToSolidShape", "KeepWire", "SolidWireModel", "SolidName"),
    ),
    "trace": (
        f"{HELP_ROOT}/common_vbacurves/common_vbacurves_tracefromcurve_object.htm",
        ("TraceFromCurve", "Thickness", "GapType", "RoundStart"),
    ),
    "sweep": (
        f"{HELP_ROOT}/common_vbacurves/common_vbacurves_sweepcurve_object.htm",
        ("SweepCurve", "ProjectProfileToPathAdvanced"),
    ),
    "curve": (
        f"{HELP_ROOT}/common_vbacurves/common_vbacurves_curve_object.htm",
        ("StartCurveNameIteration", "GetNextCurveName", "IsClosed"),
    ),
    "analytical_curve": (
        f"{HELP_ROOT}/common_vbacurves/common_vbacurves_analyticalcurve_object.htm",
        ("LawX", "ParameterRange"),
    ),
}

# ---------------------------------------------------------------------------
# Fixed geometry: the task 18 benchmark helix and a printed Archimedean spiral
# ---------------------------------------------------------------------------

HELIX_RADIUS = 20.0
WIRE_RADIUS = 0.5
HELIX_Z0, HELIX_Z1, HELIX_TURNS = 4.0, 172.0, 6
HELIX_T1 = 2 * math.pi * HELIX_TURNS
HELIX_RISE = (HELIX_Z1 - HELIX_Z0) / HELIX_T1  # z per radian
SPIRAL_A, SPIRAL_PITCH, SPIRAL_TURNS = 2.0, 1.5, 3
SPIRAL_B = SPIRAL_PITCH / (2 * math.pi)  # radius growth per radian
SPIRAL_T1 = 2 * math.pi * SPIRAL_TURNS
TRACE_WIDTH, TRACE_THICKNESS = 0.5, 0.1
# Each method gets its own copy of the curve, far from the others, in its own group.
SWEEP_X, MODEL_X, CONVERT_X, SPIRAL_X = 0.0, 100.0, 200.0, -100.0
VOLUME_TOLERANCE = 0.03  # task 18 acceptance: 606.7 mm^3 +/- 3 %
BOUNDS_TOLERANCE = 0.05  # mm the box may fall short of the exact extent
BOUNDS_SLACK = 1.0  # mm a loose box may exceed the exact extent


def fmt(value: float) -> str:
    text = format(value, ".15g")
    return "0" if text == "-0" else text


def helix_point(t: float, cx: float) -> tuple[float, float, float]:
    return (
        cx + HELIX_RADIUS * math.cos(t),
        HELIX_RADIUS * math.sin(t),
        HELIX_Z0 + HELIX_RISE * t,
    )


def helix_tangent(t: float) -> tuple[float, float, float]:
    return (-HELIX_RADIUS * math.sin(t), HELIX_RADIUS * math.cos(t), HELIX_RISE)


def spiral_point(t: float, cx: float) -> tuple[float, float, float]:
    r = SPIRAL_A + SPIRAL_B * t
    return (cx + r * math.cos(t), r * math.sin(t), 0.0)


def spiral_tangent(t: float) -> tuple[float, float, float]:
    r = SPIRAL_A + SPIRAL_B * t
    return (
        SPIRAL_B * math.cos(t) - r * math.sin(t),
        SPIRAL_B * math.sin(t) + r * math.cos(t),
        0.0,
    )


def curve_length(tangent, t1: float, steps: int = 20000) -> float:
    """Simpson's rule over |dP/dt| from 0 to t1 (steps must be even)."""
    h = t1 / steps
    total = 0.0
    for i in range(steps + 1):
        weight = 1 if i in (0, steps) else (4 if i % 2 else 2)
        total += weight * math.hypot(*tangent(i * h))
    return total * h / 3


def _normalize(v):
    length = math.hypot(*v)
    return tuple(c / length for c in v)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def tube_bounds(point, tangent, t1, radius, steps=3000, around=48):
    """Exact extent of a round tube with flat ends, sampled on the rims of its cross-sections."""
    lo, hi = [math.inf] * 3, [-math.inf] * 3
    for i in range(steps + 1):
        t = t1 * i / steps
        p, d = point(t), _normalize(tangent(t))
        helper = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
        u = _normalize(_cross(d, helper))
        v = _cross(d, u)
        for k in range(around):
            a = 2 * math.pi * k / around
            for axis in range(3):
                c = p[axis] + radius * (math.cos(a) * u[axis] + math.sin(a) * v[axis])
                lo[axis], hi[axis] = min(lo[axis], c), max(hi[axis], c)
    return {"x": [lo[0], hi[0]], "y": [lo[1], hi[1]], "z": [lo[2], hi[2]]}


def strip_bounds(point, tangent, t1, width, thickness, steps=6000):
    """Extent of a flat trace in the XY plane: its two edge curves, extruded along +Z."""
    lo, hi = [math.inf] * 2, [-math.inf] * 2
    for i in range(steps + 1):
        t = t1 * i / steps
        p, d = point(t), _normalize(tangent(t))
        normal = (-d[1], d[0])
        for side in (-0.5, 0.5):
            for axis in range(2):
                c = p[axis] + side * width * normal[axis]
                lo[axis], hi[axis] = min(lo[axis], c), max(hi[axis], c)
    return {"x": [lo[0], hi[0]], "y": [lo[1], hi[1]], "z": sorted([0.0, thickness])}


def expected_geometry() -> dict:
    helix_length = curve_length(helix_tangent, HELIX_T1)
    spiral_length = curve_length(spiral_tangent, SPIRAL_T1)
    helix_volume = math.pi * WIRE_RADIUS**2 * helix_length
    helix = {
        "length": helix_length,
        "volume": helix_volume,
        "source": "pi * r^2 * curve length (flat ends; curvature radius > wire radius)",
    }
    return {
        "unit": "mm",
        "helix": helix,
        "spiral": {
            "length": spiral_length,
            "volume": spiral_length * TRACE_WIDTH * TRACE_THICKNESS,
            "source": "curve length * width * thickness (flat ends, gap type 2)",
        },
        "bounds": {
            "sweep": tube_bounds(
                lambda t: helix_point(t, SWEEP_X), helix_tangent, HELIX_T1, WIRE_RADIUS
            ),
            "convert": tube_bounds(
                lambda t: helix_point(t, CONVERT_X), helix_tangent, HELIX_T1, WIRE_RADIUS
            ),
            "model": tube_bounds(
                lambda t: helix_point(t, MODEL_X), helix_tangent, HELIX_T1, WIRE_RADIUS
            ),
            "trace": strip_bounds(
                lambda t: spiral_point(t, SPIRAL_X),
                spiral_tangent,
                SPIRAL_T1,
                TRACE_WIDTH,
                TRACE_THICKNESS,
            ),
        },
    }


def profile_circle_laws(cx: float) -> tuple[str, str, str]:
    """Circle of the wire radius at the helix start, in the plane normal to the path.

    At t = 0 the tangent is (0, R, rise), so X is one in-plane axis and
    (0, -rise, R) / |T| the other.
    """
    norm = math.hypot(HELIX_RADIUS, HELIX_RISE)
    y_coef = -WIRE_RADIUS * HELIX_RISE / norm
    z_coef = WIRE_RADIUS * HELIX_RADIUS / norm
    return (
        f"{fmt(cx + HELIX_RADIUS)}+{fmt(WIRE_RADIUS)}*cos(t)",
        f"{fmt(y_coef)}*sin(t)",
        f"{fmt(HELIX_Z0)}+{fmt(z_coef)}*sin(t)",
    )


def _shift(cx: float, expr: str) -> str:
    return expr if cx == 0 else f"{fmt(cx)}+{expr}"


def helix_laws(cx: float) -> tuple[str, str, str]:
    return (
        _shift(cx, f"{fmt(HELIX_RADIUS)}*cos(t)"),
        f"{fmt(HELIX_RADIUS)}*sin(t)",
        f"{fmt(HELIX_Z0)}+{fmt(HELIX_RISE)}*t",
    )


def spiral_laws(cx: float) -> tuple[str, str, str]:
    r = f"({fmt(SPIRAL_A)}+{fmt(SPIRAL_B)}*t)"
    return (_shift(cx, f"{r}*cos(t)"), f"{r}*sin(t)", "0")


def analytical_curve(group: str, item: str, laws: tuple[str, str, str], t1: float) -> str:
    return "\n".join(
        [
            "With AnalyticalCurve",
            "  .Reset",
            f'  .Name "{item}"',
            f'  .Curve "{group}"',
            f'  .LawX "{laws[0]}"',
            f'  .LawY "{laws[1]}"',
            f'  .LawZ "{laws[2]}"',
            f'  .ParameterRange "0", "{fmt(t1)}"',
            "  .Create",
            "End With",
        ]
    )


def polygon_line(group: str, item: str, start, end) -> str:
    points = [f'  .Point "{fmt(a)}", "{fmt(b)}", "{fmt(c)}"' for a, b, c in (start, end)]
    return "\n".join(
        ["With Polygon3D", "  .Reset", f'  .Name "{item}"', f'  .Curve "{group}"', *points]
        + ["  .Create", "End With"]
    )


def wire_add(name: str, curve: str, solid_model: bool) -> str:
    return "\n".join(
        [
            "With Wire",
            "  .Reset",
            f'  .Name "{name}"',
            '  .Type "Curvewire"',
            f'  .Curve "{curve}"',
            f'  .Radius "{fmt(WIRE_RADIUS)}"',
            f'  .SolidWireModel "{solid_model}"',
            '  .Material "PEC"',
            '  .Termination "natural"',
            '  .AdvancedChainSelection "True"',
            "  .Add",
            "End With",
        ]
    )


def trace_from_curve(name: str, curve: str) -> str:
    return "\n".join(
        [
            "With TraceFromCurve",
            "  .Reset",
            f'  .Name "{name}"',
            '  .Component "ProbeTrace"',
            '  .Material "PEC"',
            f'  .Curve "{curve}"',
            f'  .Thickness "{fmt(TRACE_THICKNESS)}"',
            f'  .Width "{fmt(TRACE_WIDTH)}"',
            '  .RoundStart "False"',
            '  .RoundEnd "False"',
            '  .GapType "2"',
            "  .Create",
            "End With",
        ]
    )


SETUP = "\n".join(
    [f'Component.New "{c}"' for c in ("ProbeSweep", "ProbeTrace", "ProbeWire")]
    + [
        f'Curve.NewCurve "{g}"'
        for g in (
            "ProbeSweepPath",
            "ProbeSweepProfile",
            "ProbeTracePath",
            "ProbeWireModelPath",
            "ProbeWireSolidPath",
            "ProbeSplit",
        )
    ]
)
CURVE_BLOCKS = {
    "sweep_path": analytical_curve("ProbeSweepPath", "helix", helix_laws(SWEEP_X), HELIX_T1),
    "sweep_profile": analytical_curve(
        "ProbeSweepProfile", "xsec", profile_circle_laws(SWEEP_X), 2 * math.pi
    ),
    "trace_path": analytical_curve("ProbeTracePath", "spiral", spiral_laws(SPIRAL_X), SPIRAL_T1),
    "wire_model_path": analytical_curve(
        "ProbeWireModelPath", "helix", helix_laws(MODEL_X), HELIX_T1
    ),
    "wire_solid_path": analytical_curve(
        "ProbeWireSolidPath", "helix", helix_laws(CONVERT_X), HELIX_T1
    ),
    "split_a": polygon_line("ProbeSplit", "line_a", (300, 0, 0), (310, 0, 0)),
    "split_b": polygon_line("ProbeSplit", "line_b", (300, 20, 0), (310, 20, 0)),
}
SWEEP_BLOCK = """With SweepCurve
  .Reset
  .Name "helix_sweep"
  .Component "ProbeSweep"
  .Material "PEC"
  .Twistangle "0.0"
  .Taperangle "0.0"
  .ProjectProfileToPathAdvanced "True"
  .Path "ProbeSweepPath:helix"
  .Curve "ProbeSweepProfile:xsec"
  .Create
End With"""


def convert_block(solid: str, wire: str) -> str:
    return "\n".join(
        [
            "With Wire",
            "  .Reset",
            f'  .SolidName "{solid}"',
            f'  .Name "{wire}"',
            '  .Material "PEC"',
            '  .KeepWire "False"',
            "  .ConvertToSolidShape",
            "End With",
        ]
    )


METHOD_BLOCKS = {
    "D_trace": trace_from_curve("spiral_trace", "ProbeTracePath:spiral"),
    "C_sweep": SWEEP_BLOCK,
    "F1_wire_missing_curve": wire_add("missing_wire", "ProbeMissing:none", False),
    "F2_trace_missing_curve": trace_from_curve("missing_trace", "ProbeMissing:none"),
    "F3_wire_disconnected_group": wire_add("split_wire", "ProbeSplit", False),
    "B_wire_model": wire_add("helix_model", "ProbeWireModelPath:helix", True),
    "A1_wire_add": wire_add("helix_wire", "ProbeWireSolidPath:helix", False),
    # A thin wire cannot be converted; the first probe got "Wire for conversion to solid
    # is of thin wire model." Kept to record that refusal.
    "A2_convert": convert_block("ProbeWire:helix_solid", "helix_wire"),
    # The conversion that stalled an earlier CST 2026 test: a solid-model wire.
    "A3_convert_solid_model": convert_block("ProbeWire:helix_model_solid", "helix_model"),
}
# Run order. The conversions go last, after a save: a stall then costs only their results.
METHOD_ORDER = tuple(METHOD_BLOCKS)
CONVERSIONS = {"A2_convert": "A1_wire_add", "A3_convert_solid_model": "B_wire_model"}
FAILURE_CASES = ("F1_wire_missing_curve", "F2_trace_missing_curve", "F3_wire_disconnected_group")
# Steps that are meant to fail: the failure cases, and converting a thin wire, which CST 2025
# refuses ("Wire for conversion to solid is of thin wire model").
EXPECTED_ERRORS = (*FAILURE_CASES, "A2_convert")
SOLIDS = {
    "D_trace": ("ProbeTrace:spiral_trace", "spiral", "trace"),
    "C_sweep": ("ProbeSweep:helix_sweep", "helix", "sweep"),
    "A2_convert": ("ProbeWire:helix_solid", "helix", "convert"),
    "A3_convert_solid_model": ("ProbeWire:helix_model_solid", "helix", "model"),
}
WIRES = ("helix_model", "helix_wire", "missing_wire", "split_wire")
CURVE_ITEMS = (
    "ProbeSweepPath:helix",
    "ProbeSweepProfile:xsec",
    "ProbeTracePath:spiral",
    "ProbeWireModelPath:helix",
    "ProbeWireSolidPath:helix",
    "ProbeSplit:line_a",
    "ProbeSplit:line_b",
)
# The curve items each method reads, to see whether the method consumed them.
METHOD_CURVES = {
    "D_trace": ("ProbeTracePath:spiral",),
    "C_sweep": ("ProbeSweepPath:helix", "ProbeSweepProfile:xsec"),
    "F3_wire_disconnected_group": ("ProbeSplit:line_a", "ProbeSplit:line_b"),
    "B_wire_model": ("ProbeWireModelPath:helix",),
    "A1_wire_add": ("ProbeWireSolidPath:helix",),
    "A3_convert_solid_model": ("ProbeWireModelPath:helix",),
}

CURVES_QUERY = """Dim mcpN As Long, mcpI As Long
mcpN = Curve.StartCurveNameIteration("all")
Debug.Print "COUNT" & vbTab & CStr(mcpN)
For mcpI = 1 To mcpN
  Debug.Print "CURVE" & vbTab & Curve.GetNextCurveName()
Next mcpI
Debug.Print "DONE\""""
PROFILE_QUERY = """Dim mcpClosed As Boolean
On Error Resume Next
Err.Clear
mcpClosed = Curve.IsClosed("ProbeSweepProfile:xsec")
If Err.Number <> 0 Then
  Debug.Print "ERROR" & vbTab & Err.Description
Else
  Debug.Print "CLOSED" & vbTab & CStr(mcpClosed)
End If
Debug.Print "DONE\""""
WIRES_QUERY = "\n".join(
    ["Dim mcpLength As Double", "On Error Resume Next"]
    + [
        line
        for name in WIRES
        for line in (
            "Err.Clear",
            f'mcpLength = Wire.GetLength("{name}")',
            "If Err.Number <> 0 Then",
            f'  Debug.Print "ERROR" & vbTab & "{name}" & vbTab & Err.Description',
            "Else",
            f'  Debug.Print "LENGTH" & vbTab & "{name}" & vbTab & CStr(mcpLength)',
            "End If",
        )
    ]
    + ['Debug.Print "DONE"']
)
# CURVES_QUERY lists curve groups only; this asks for each curve item by name.
ITEMS_QUERY = "\n".join(
    ["Dim mcpPoints As Long", "On Error Resume Next"]
    + [
        line
        for name in CURVE_ITEMS
        for line in (
            "Err.Clear",
            f'mcpPoints = Curve.GetNumberOfPoints("{name}")',
            "If Err.Number <> 0 Then",
            f'  Debug.Print "ERROR" & vbTab & "{name}" & vbTab & Err.Description',
            "Else",
            f'  Debug.Print "POINTS" & vbTab & "{name}" & vbTab & CStr(mcpPoints)',
            "End If",
        )
    ]
    + ['Debug.Print "DONE"']
)
QUERIES = frozenset(
    {UNITS_QUERY, SHAPES_QUERY, CURVES_QUERY, ITEMS_QUERY, PROFILE_QUERY, WIRES_QUERY}
)
MUTATIONS = frozenset({UNITS_BLOCK, SETUP, *CURVE_BLOCKS.values(), *METHOD_BLOCKS.values()})
FIXED_VBA = QUERIES | MUTATIONS
PLANNED_TOOLS = (
    "cst_connect",
    "cst_create_project",
    "cst_project_info",
    "cst_connection_status",
    "cst_execute_vba",
    "cst_get_solid_bounds",
    "cst_read_project_log",
    "cst_save_project",
    "cst_close_project",
    "cst_disconnect",
)
LIMITATIONS = [
    (
        "One probe per method on one CST version. A method that works here may still need "
        "confirmation on other versions."
    ),
    "The trace curve, path curves and profile are fixed; other curve shapes are not covered.",
    (
        "Bounds come from Solid.GetLooseBoundingBoxOfShape, which may exceed the solid. They "
        f"are compared against the exact extent with a slack of {BOUNDS_SLACK} mm; volume is "
        "the main check."
    ),
    (
        "Whether a failed call leaves a history entry is read from the saved "
        "Model/3D/ModelHistory.json, so it reflects what CST writes on save."
    ),
    "CST messages may be inherited or repeated; they do not prove which call caused them.",
]


# ---------------------------------------------------------------------------
# Readback interpretation (pure; covered by the offline tests)
# ---------------------------------------------------------------------------


def parse_tagged(output: str) -> list[list[str]]:
    """Tab-separated query records up to the DONE marker, which must be present."""
    rows, done = [], False
    for line in output.splitlines():
        line = line.rstrip("\r")
        if not line.strip():
            continue
        if line == "DONE":
            done = True
            continue
        if done:
            raise StopTest(f"Output after the end marker: {line!r}")
        rows.append(line.split("\t"))
    if not done:
        raise StopTest("Native query output has no end marker")
    return rows


def parse_curves(output: str) -> dict:
    rows = parse_tagged(output)
    counts = [r[1] for r in rows if r[0] == "COUNT" and len(r) == 2]
    names = ["\t".join(r[1:]) for r in rows if r[0] == "CURVE"]
    if len(counts) != 1:
        raise StopTest("Curve query returned no single count")
    return {"count": int(counts[0]), "names": names}


def parse_wires(output: str) -> dict:
    result = {}
    for row in parse_tagged(output):
        if row[0] == "LENGTH" and len(row) == 3:
            result[row[1]] = {"length": _native_float(row[2])}
        elif row[0] == "ERROR" and len(row) >= 2:
            result[row[1]] = {"error": "\t".join(row[2:]).strip() or "error"}
        else:
            raise StopTest(f"Unexpected wire query record: {row!r}")
    return result


def parse_items(output: str) -> dict:
    """Per curve item: present with its point count, or the native error."""
    result = {}
    for row in parse_tagged(output):
        if row[0] == "POINTS" and len(row) == 3:
            points = _native_float(row[2])
            result[row[1]] = {"present": True, "points": None if points is None else int(points)}
        elif row[0] == "ERROR" and len(row) >= 2:
            result[row[1]] = {"present": False, "error": "\t".join(row[2:]).strip() or "error"}
        else:
            raise StopTest(f"Unexpected curve item record: {row!r}")
    return result


def parse_profile(output: str) -> dict:
    rows = parse_tagged(output)
    if len(rows) != 1 or rows[0][0] not in {"CLOSED", "ERROR"}:
        raise StopTest(f"Unexpected profile query output: {rows!r}")
    if rows[0][0] == "ERROR":
        return {"closed": None, "error": "\t".join(rows[0][1:])}
    return {"closed": rows[0][1].strip().lower() in {"true", "-1"}, "raw": rows[0][1]}


def _native_float(text: str) -> float | None:
    try:
        value = float(text.strip().replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def classify_outcome(key: str, status: str | None) -> str:
    """Label a method's result, so planned failures don't read as faults."""
    if key in EXPECTED_ERRORS:
        if status == "error":
            return "expected error"
        if status == "executed":
            return "unexpected success"
    return "executed" if status == "executed" else f"native {status}"


def judge_solid(entry: dict | None, expected_volume: float, expected_bounds: dict) -> dict:
    """Compare one cst_get_solid_bounds entry with the exact geometry."""
    if not entry or not entry.get("found") or "bounds" not in entry:
        return {"verdict": "no solid", "entry": entry}
    volume = entry.get("volume")
    volume_error = (
        abs(volume - expected_volume) / expected_volume if isinstance(volume, float) else None
    )
    shortfall, excess = {}, {}
    for axis, (lo, hi) in expected_bounds.items():
        got_lo, got_hi = entry["bounds"][axis]
        shortfall[axis] = max(got_lo - lo, hi - got_hi)
        excess[axis] = max(lo - got_lo, got_hi - hi)
    bounds_ok = all(v <= BOUNDS_TOLERANCE for v in shortfall.values()) and all(
        v <= BOUNDS_SLACK for v in excess.values()
    )
    volume_ok = volume_error is not None and volume_error <= VOLUME_TOLERANCE
    return {
        "verdict": "matches" if bounds_ok and volume_ok else "geometry differs",
        "volume": volume,
        "expected_volume": expected_volume,
        "volume_relative_error": volume_error,
        "volume_ok": volume_ok,
        "bounds": entry["bounds"],
        "expected_bounds": expected_bounds,
        "bounds_shortfall": shortfall,
        "bounds_excess": excess,
        "bounds_ok": bounds_ok,
        "material": entry.get("material"),
    }


def _code_key(code) -> tuple[str, ...]:
    lines = code.splitlines() if isinstance(code, str) else list(code)
    return tuple(s.strip() for s in lines if s.strip())


def history_presence(history: dict, blocks: dict[str, str]) -> dict:
    """Which fixed blocks appear in a saved ModelHistory.json, by exact code."""
    entries = history.get("history", []) if isinstance(history, dict) else []
    keys = [_code_key(e.get("code", [])) for e in entries if isinstance(e, dict)]
    return {name: _code_key(code) in keys for name, code in blocks.items()}


def read_saved_history(project: Path) -> dict:
    path = project.with_suffix("") / "Model" / "3D" / "ModelHistory.json"
    if not path.is_file():
        return {"status": "missing", "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        return {"status": "unreadable", "path": str(path), "message": str(exc)}
    return {"status": "ok", "path": str(path), "sha256": sha256(path), "data": data}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class WorkspaceLock:
    def __init__(self, work: Path):
        self.work = work

    def __enter__(self):
        reject_links(self.work)
        self.work.mkdir(parents=True, exist_ok=True)
        target = self.work / "workspace.lock"
        self.stream = target.open("a+b")
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            raise StopTest("The probe workspace is in use by another invocation") from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


def default_work(preflight: bool) -> Path:
    return BATCH / "artifacts" / ("preflight" if preflight else "probe")


class ProbeTest:
    def __init__(self, options, work: Path | None = None):
        self.options = options
        self.work = work or default_work(options.preflight)
        self.project = self.work / "project.cst"
        self.invocation = uuid.uuid4().hex
        self.sequence = 0
        self.phase = "preparation"
        self.step = None
        self.unknown = False
        self.connected = False
        self.live_attempted = False
        self.exit_code = 1
        self.reason = "Not completed"
        self.catalog: dict = {}
        self.checks: list = []
        self.outcomes: dict = {}
        self.readbacks: dict = {}
        self.histories: dict = {}
        self.expected = expected_geometry()
        reject_links(self.work)
        self.work.mkdir(parents=True, exist_ok=True)
        self.calls = (self.work / "mcp_calls.jsonl").open("a", encoding="utf-8")
        self.stderr = (self.work / "server_stderr.log").open("a", encoding="utf-8")
        libs = Path(options.cst_path) / "AMD64" / "python_cst_libraries"
        self.env = dict(os.environ)
        self.env.update(
            CST_CONNECT_MODE="disabled" if options.preflight else "manual",
            CST_VERSION="2025",
            CST_PATH=options.cst_path,
            CST_WORK_DIR=str(self.work),
            CST_TOOLSETS=TOOLSETS,
            CST_ALLOW_RAW_VBA="1",
            PYTHONPATH=os.pathsep.join(
                [str(ROOT / "src"), str(libs), os.environ.get("PYTHONPATH", "")]
            ),
        )
        try:
            revision = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            revision = "unavailable"
        self.metadata = {
            "invocation": self.invocation,
            "started": timestamp(),
            "revision": revision,
            "script_sha256": sha256(Path(__file__)),
            "python": sys.version,
            "packages": {p: importlib.metadata.version(p) for p in ("mcp", "jsonschema")},
            "options": vars(options),
            "workspace": str(self.work),
            "environment": {
                k: self.env[k]
                for k in (
                    "CST_CONNECT_MODE",
                    "CST_VERSION",
                    "CST_PATH",
                    "CST_WORK_DIR",
                    "CST_TOOLSETS",
                    "CST_ALLOW_RAW_VBA",
                )
            },
            "method_order": METHOD_ORDER,
            "method_blocks": METHOD_BLOCKS,
            "curve_blocks": CURVE_BLOCKS,
            "expected": self.expected,
            "references": {},
            "limitations": LIMITATIONS,
        }

    # -- records ------------------------------------------------------------

    def tag(self, record: dict) -> dict:
        return dict(
            record,
            invocation=self.invocation,
            timestamp=timestamp(),
            sequence=self.sequence,
            phase=self.phase,
            step=self.step,
        )

    def event(self, record: dict) -> None:
        self.calls.write(json.dumps(serialize(self.tag(record)), ensure_ascii=False) + "\n")
        self.calls.flush()

    def check(self, scope: str, passed: bool, **evidence) -> None:
        record = self.tag(dict(scope=scope, passed=bool(passed), **evidence))
        self.checks.append(record)
        self.event(dict(event="check", **record))
        if not passed:
            raise StopTest(f"{self.phase}: {scope} failed; see mcp_calls.jsonl")

    # -- workspace ------------------------------------------------------------

    def owned_paths(self) -> list[Path]:
        paths = [self.project, self.project.with_suffix("")]
        paths += [
            p
            for p in self.work.glob("project.*")
            if p != self.project and p.suffix.lower() not in LOCK_SUFFIXES
        ]
        return paths

    def prepare_workspace(self) -> None:
        """A live probe always starts from a new project; --reset removes the previous one."""
        manifest_path = self.work / "workspace.json"
        existing = [p for p in self.owned_paths() if p.exists()]
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict) or manifest.get("owner") != OWNER:
                raise StopTest("workspace.json is not owned by this probe; no deletion")
            if existing and not self.options.reset:
                raise StopTest(
                    "A previous probe project exists. Inspect it, then rerun with --reset "
                    f"to delete it: {self.project}"
                )
            ensure_closed(self.project)
            for path in existing:
                reject_links(path)
                if path.resolve().parent != self.work.resolve():
                    raise StopTest(f"Owned path escapes the workspace: {path}")
            for path in existing:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            self.event({"event": "reset", "deleted": [str(p) for p in existing]})
        elif existing:
            raise StopTest("Project paths exist without probe ownership; refusing to touch them")
        elif self.options.reset:
            raise StopTest("Nothing to reset: no probe project in the workspace")
        write_json(
            manifest_path,
            {
                "owner": OWNER,
                "version": 1,
                "project": str(self.project),
                "generated_paths": ["project.cst", "project", "project.*"],
                "invocation": self.invocation,
                "created_at": timestamp(),
            },
        )

    # -- MCP requests -----------------------------------------------------------

    def validate(self, name: str, arguments: dict) -> None:
        if name not in self.catalog:
            raise StopTest(f"Required tool missing from the MCP catalog: {name}")
        schema = self.catalog[name]["inputSchema"]
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(arguments)

    async def request(self, session, name: str, arguments: dict | None = None, *, protocol=False):
        """One MCP call. Returns the payload dict; native errors are returned, not raised."""
        if self.unknown:
            raise UnknownState("No further MCP requests after an indeterminate outcome")
        arguments = arguments or {}
        if name == "cst_execute_vba" and arguments.get("code") not in FIXED_VBA:
            raise StopTest("Raw VBA is restricted to the probe's fixed blocks")
        if not protocol:
            self.validate(name, arguments)
        self.sequence += 1
        timeout = (
            self.options.connection_timeout if name == "cst_connect" else self.options.call_timeout
        )
        base = {"tool": name, "arguments": arguments, "timeout_seconds": timeout}
        self.event(dict(event="request_start", **base))
        start = time.monotonic()
        try:
            call = (
                getattr(session, name)(**arguments)
                if protocol
                else session.call_tool(name, arguments)
            )
            response = await asyncio.wait_for(call, timeout)
        except BaseException as exc:
            self.unknown = True
            self.record_outcome("client timeout or transport loss", time.monotonic() - start)
            self.event(
                dict(
                    event="request_exception",
                    **base,
                    duration_seconds=time.monotonic() - start,
                    exception={"type": type(exc).__name__, "message": str(exc)},
                    execution_state="unknown",
                )
            )
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            raise UnknownState(f"{self.step or name}: timeout or transport loss") from exc
        duration = time.monotonic() - start
        raw = serialize(response)
        if protocol:
            self.event(dict(event="request_complete", **base, response=raw))
            return raw
        decoded = interpret(raw)
        unknown = indeterminate(raw, decoded)
        payload = decoded["payload"]
        self.event(
            dict(
                event="request_complete",
                **base,
                response=raw,
                duration_seconds=duration,
                execution_state="unknown" if unknown else "response_received",
            )
        )
        print(f"{self.sequence:03d} {self.phase}: {name}", flush=True)
        if unknown:
            self.unknown = True
            self.record_outcome("server timeout (execution state unknown)", duration, payload)
            raise UnknownState(f"{self.step or name}: server reported a timeout")
        if not isinstance(payload, dict):
            self.unknown = True
            raise UnknownState(f"{name}: response without a JSON payload; outcome unknown")
        if not self.options.preflight and payload.get("status") == "offline":
            raise StopTest(f"{name}: offline response during a live run")
        payload = dict(payload, _duration_seconds=duration)
        return payload

    def record_outcome(self, outcome: str, duration: float, payload=None) -> None:
        if self.step in METHOD_BLOCKS and self.step not in self.outcomes:
            self.outcomes[self.step] = {
                "outcome": outcome,
                "duration_seconds": duration,
                "payload": payload,
            }

    async def required(self, session, name: str, arguments: dict | None = None, *, status: str):
        payload = await self.request(session, name, arguments)
        self.check(f"{name} returns {status}", payload.get("status") == status, payload=payload)
        return payload

    async def query(self, session, code: str) -> str:
        payload = await self.required(session, "cst_execute_vba", {"code": code}, status="ok")
        return str(payload.get("output", ""))

    # -- run ------------------------------------------------------------------

    async def run(self) -> int:
        try:
            if sys.version_info[:2] != (3, 12):
                raise StopTest("Python 3.12 is required (CST 2025 library ABI)")
            if not self.options.preflight:
                self.prepare_workspace()
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "cst_mcp.server"], cwd=str(ROOT), env=self.env
            )
            with preserve_cst_processes():
                async with (
                    stdio_client(params, errlog=self.stderr) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await self.catalog_and_references(session)
                    if self.options.preflight:
                        await self.preflight(session)
                    else:
                        await self.live(session)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.exit_code = 130
            self.reason = "Interrupted; no cleanup calls; project and logs kept"
        except BaseException as exc:
            logger.exception("Curve-solid probe stopped; writing reports")
            leaves = list(exception_leaves(exc))
            cause = next((e for e in leaves if isinstance(e, StopTest)), leaves[0])
            self.exit_code = 2 if self.unknown else 1
            self.reason = f"{self.phase}/{self.step}: {type(cause).__name__}: {cause}"
            if self.unknown:
                self.reason += (
                    ". No further calls were made: inspect CST by hand before running again"
                )
            self.event(
                {
                    "event": "failure",
                    "exception": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                }
            )
        finally:
            self.finalize()
        print(f"{self.reason}\nReports: {self.work}\nExit: {self.exit_code}", flush=True)
        return self.exit_code

    async def catalog_and_references(self, session) -> None:
        self.phase = "catalog"
        await self.request(session, "initialize", protocol=True)
        cursor, seen = None, set()
        while True:
            page = await self.request(
                session, "list_tools", {"cursor": cursor} if cursor else {}, protocol=True
            )
            for tool in page["tools"]:
                if tool["name"] in self.catalog:
                    raise StopTest(f"Duplicate catalog tool: {tool['name']}")
                self.catalog[tool["name"]] = tool
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in seen:
                raise StopTest("Repeated MCP catalog cursor")
            seen.add(cursor)
        write_json(self.work / "tool_catalog.json", list(self.catalog.values()))
        missing = [name for name in PLANNED_TOOLS if name not in self.catalog]
        self.check("planned tools are in the catalog", not missing, missing=missing)
        for name, (rel, tokens) in REFERENCES.items():
            path = Path(self.options.cst_path) / rel
            if not path.is_file():
                raise StopTest(f"Installed help topic not found: {path}")
            text = path.read_text(encoding="utf-8", errors="replace")
            absent = [t for t in tokens if t not in text]
            self.metadata["references"][name] = {
                "path": str(path),
                "sha256": sha256(path),
                "tokens": tokens,
            }
            self.check(f"help documents {name}", not absent, path=str(path), absent=absent)
        state = await self.request(session, "cst_connection_status")
        self.check(
            "server starts disconnected",
            state.get("mode") == "offline" and state.get("project_open") is False,
            payload=state,
        )

    async def preflight(self, session) -> None:
        self.phase = "offline_preflight"
        for name, args in (
            ("cst_connect", {"mode": "new"}),
            ("cst_create_project", {"path": str(self.project), "project_type": "MWS"}),
            ("cst_get_solid_bounds", {"solids": [s[0] for s in SOLIDS.values()]}),
            ("cst_save_project", {}),
            ("cst_close_project", {}),
            ("cst_disconnect", {}),
            ("cst_read_project_log", {}),
            ("cst_project_info", {}),
        ):
            self.validate(name, args)
        for code in sorted(FIXED_VBA):
            payload = await self.request(session, "cst_execute_vba", {"code": code})
            self.check(
                "fixed block passes the raw VBA screen unchanged",
                payload.get("status") == "offline" and payload.get("vba") == code,
                first_line=code.splitlines()[0],
            )
        bounds = await self.request(
            session, "cst_get_solid_bounds", {"solids": [s[0] for s in SOLIDS.values()]}
        )
        self.check("bounds query is offline", bounds.get("status") == "offline", payload=bounds)
        self.exit_code = 0
        self.reason = "Catalog, help references and offline VBA checks passed; live probe pending"

    async def live(self, session) -> None:
        self.phase = "connect"
        self.live_attempted = True
        connected = await self.required(session, "cst_connect", {"mode": "new"}, status="connected")
        self.check(
            "new Design Environment without open projects",
            connected.get("newly_started") is True
            and connected.get("open_projects") == 0
            and not connected.get("project_path"),
            payload=connected,
        )
        self.connected = True
        self.phase = "project"
        created = await self.required(
            session,
            "cst_create_project",
            {"path": str(self.project), "project_type": "MWS"},
            status="created",
        )
        self.check(
            "project created at the owned path",
            Path(created.get("path") or "").resolve() == self.project.resolve(),
            payload=created,
        )
        await self.required(session, "cst_execute_vba", {"code": UNITS_BLOCK}, status="executed")
        units = parse_units(await self.query(session, UNITS_QUERY))
        self.check("units are mm/GHz/ns", units == EXPECTED_UNITS, actual=units)
        await self.required(session, "cst_execute_vba", {"code": SETUP}, status="executed")
        self.phase = "curves"
        for key, code in CURVE_BLOCKS.items():
            self.step = key
            await self.required(session, "cst_execute_vba", {"code": code}, status="executed")
        self.step = None
        self.readbacks["curves_before"] = parse_curves(await self.query(session, CURVES_QUERY))
        # Recorded, not required: if GetNumberOfPoints fails even on curves that exist, the
        # consumption columns are void but the methods still run.
        self.readbacks["items_before"] = parse_items(await self.query(session, ITEMS_QUERY))
        self.readbacks["profile"] = parse_profile(await self.query(session, PROFILE_QUERY))
        await self.project_log(session, "after_curves")

        self.phase = "methods"
        for key in METHOD_ORDER:
            if key in CONVERSIONS:
                if "before_convert" not in self.histories:
                    await self.checkpoint(session, "before_convert")
                source = CONVERSIONS[key]
                if self.outcomes.get(source, {}).get("outcome") != "executed":
                    self.outcomes[key] = {"outcome": f"not run: {source} did not execute"}
                    continue
            await self.run_method(session, key)
        await self.checkpoint(session, "final")
        self.phase = "disconnect"
        await self.required(session, "cst_close_project", status="closed")
        await self.required(session, "cst_disconnect", status="disconnected")
        self.connected = False
        self.exit_code = 0
        self.reason = "Probe completed; see the method table in summary.md"

    async def run_method(self, session, key: str) -> None:
        self.step = key
        payload = await self.request(session, "cst_execute_vba", {"code": METHOD_BLOCKS[key]})
        status = payload.get("status")
        self.outcomes[key] = {
            "outcome": classify_outcome(key, status),
            "duration_seconds": payload.get("_duration_seconds"),
            "message": payload.get("message"),
            "cst_messages_tail": payload.get("cst_messages_tail"),
        }
        await self.readback(session, key)
        self.step = None

    async def readback(self, session, key: str) -> None:
        record: dict = {}
        record["shapes"] = parse_shapes(await self.query(session, SHAPES_QUERY))
        record["curves"] = parse_curves(await self.query(session, CURVES_QUERY))
        record["items"] = parse_items(await self.query(session, ITEMS_QUERY))
        if key.startswith(("A", "B", "F1", "F3")):
            record["wires"] = parse_wires(await self.query(session, WIRES_QUERY))
        if key in SOLIDS:
            solid, curve, bounds_key = SOLIDS[key]
            payload = await self.request(
                session, "cst_get_solid_bounds", {"solids": [solid], "include_volume": True}
            )
            entry = next(iter(payload.get("solids") or []), None)
            record["bounds_payload"] = payload
            record["judgement"] = judge_solid(
                entry, self.expected[curve]["volume"], self.expected["bounds"][bounds_key]
            )
        self.readbacks[key] = record
        self.event({"event": "readback", "key": key, "record": record})

    async def project_log(self, session, label: str) -> None:
        payload = await self.request(session, "cst_read_project_log")
        self.readbacks[f"log_{label}"] = payload

    async def checkpoint(self, session, label: str) -> None:
        self.step = f"save_{label}"
        await self.required(session, "cst_save_project", status="saved")
        saved = read_saved_history(self.project)
        if saved.get("status") == "ok":
            blocks = {**CURVE_BLOCKS, **METHOD_BLOCKS}
            saved["presence"] = history_presence(saved.pop("data"), blocks)
        self.histories[label] = saved
        await self.project_log(session, label)
        self.step = None

    # -- reports ----------------------------------------------------------------

    def method_rows(self) -> list[dict]:
        rows = []
        presence = next(
            (h["presence"] for h in reversed(self.histories.values()) if "presence" in h), {}
        )
        for key in METHOD_ORDER:
            outcome = self.outcomes.get(key, {"outcome": "not run"})
            readback = self.readbacks.get(key, {})
            judgement = readback.get("judgement", {})
            wires = readback.get("wires", {})
            items = readback.get("items", {})
            rows.append(
                {
                    "step": key,
                    "outcome": outcome.get("outcome"),
                    "duration_seconds": outcome.get("duration_seconds"),
                    "verdict": judgement.get("verdict"),
                    "volume": judgement.get("volume"),
                    "volume_relative_error": judgement.get("volume_relative_error"),
                    "bounds_ok": judgement.get("bounds_ok"),
                    "wire_lengths": {k: v.get("length") for k, v in wires.items()},
                    "curves_left": {
                        name: items[name]["present"]
                        for name in METHOD_CURVES.get(key, ())
                        if name in items
                    },
                    "in_saved_history": presence.get(key),
                    "message": outcome.get("message"),
                }
            )
        return rows

    def finalize(self) -> None:
        rows = self.method_rows()
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
            "methods": rows,
            "outcomes": self.outcomes,
            "readbacks": self.readbacks,
            "histories": self.histories,
            "checks": self.checks,
            "references": self.metadata["references"],
            "limitations": LIMITATIONS,
        }
        write_json(self.work / "summary.json", summary)
        self.metadata.update(finished=timestamp(), exit_code=self.exit_code, reason=self.reason)
        write_json(self.work / "metadata.json", self.metadata)
        (self.work / "summary.md").write_text(self.summary_markdown(rows), encoding="utf-8")
        for stream in (self.calls, self.stderr):
            stream.flush()
            stream.close()

    def summary_markdown(self, rows: list[dict]) -> str:
        helix = self.expected["helix"]
        spiral = self.expected["spiral"]

        def cell(value):
            if value is None:
                return "—"
            if isinstance(value, float):
                return f"{value:.4g}"
            return str(value).replace("|", "/").replace("\n", " ")

        lines = [
            "# Curve-to-solid probe",
            "",
            f"Invocation `{self.invocation}`; phase `{self.phase}`; exit {self.exit_code}.",
            "",
            self.reason,
            "",
            (
                f"Expected helix: length {helix['length']:.3f} mm, "
                f"volume {helix['volume']:.2f} mm³. "
                f"Expected spiral trace: length {spiral['length']:.3f} mm, "
                f"volume {spiral['volume']:.4f} mm³."
            ),
            "",
            (
                "| Step | Outcome | Time (s) | Verdict | Volume | Volume error | Bounds ok "
                "| Wire lengths | Curve items left | In saved history |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            error = row["volume_relative_error"]
            lengths = ", ".join(
                f"{k}={cell(v)}" for k, v in row["wire_lengths"].items() if v is not None
            )
            curves_left = ", ".join(f"{k}={v}" for k, v in row["curves_left"].items())
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["step"],
                        cell(row["outcome"]),
                        cell(row["duration_seconds"]),
                        cell(row["verdict"]),
                        cell(row["volume"]),
                        "—" if error is None else f"{100 * error:.2f} %",
                        cell(row["bounds_ok"]),
                        lengths or "—",
                        curves_left or "—",
                        cell(row["in_saved_history"]),
                    ]
                )
                + " |"
            )
        before = self.readbacks.get("items_before")
        if before:
            present = sum(v["present"] for v in before.values())
            lines += [
                "",
                (
                    f"Curve items found before the methods: {present} of {len(before)}. "
                    "If fewer, the curve items left column cannot be trusted."
                ),
            ]
        profile = self.readbacks.get("profile")
        if profile:
            lines += ["", f"Sweep profile closed: {cell(profile.get('closed'))}."]
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


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Real MCP catalog, help references and offline VBA screen; CST disabled",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the previous probe project (owned paths only) before a live run",
    )
    parser.add_argument("--cst-path", default=DEFAULT_CST_PATH)
    parser.add_argument("--connection-timeout", type=positive_timeout, default=120)
    parser.add_argument("--call-timeout", type=positive_timeout, default=120)
    parsed = parser.parse_args(argv)
    if parsed.preflight and parsed.reset:
        parser.error("--reset cannot be combined with --preflight")
    return parsed


def main(argv=None) -> int:
    parsed = options(argv)
    work = default_work(parsed.preflight)
    try:
        with WorkspaceLock(work):
            return asyncio.run(ProbeTest(parsed, work).run())
    except StopTest as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
