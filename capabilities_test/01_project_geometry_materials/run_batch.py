# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mcp>=1.29,<3", "jsonschema>=4.20"]
# ///
"""Deterministic batch 01. All CST operations use an MCP client over stdio."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError
from jsonschema.validators import validator_for
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from preserving_stdio import preserve_cst_processes

ROOT = Path(__file__).resolve().parents[2]
BATCH_DIR = Path(__file__).resolve().parent
DEFAULT_CST_PATH = r"C:\Program Files (x86)\CST Studio Suite 2025"
TOOLSETS = "connection,project,geometry,materials,diagnostics,vba"
EXPECTED_UNITS = {"Length": "mm", "Frequency": "GHz", "Time": "ns"}
UNITS_BLOCK = '''With Units
  .SetUnit "Length", "mm"
  .SetUnit "Frequency", "GHz"
  .SetUnit "Time", "ns"
End With'''
UNITS_QUERY = '''Debug.Print "Length" & vbTab & Units.GetUnit("Length")
Debug.Print "Frequency" & vbTab & Units.GetUnit("Frequency")
Debug.Print "Time" & vbTab & Units.GetUnit("Time")'''
SHAPES_QUERY = '''Dim n As Long, i As Long
Dim fullName As String
n = Solid.GetNumberOfShapes()
Debug.Print "COUNT" & vbTab & CStr(n)
For i = 0 To n - 1
  fullName = Solid.GetNameOfShapeFromIndex(i)
  Debug.Print "SOLID" & vbTab & CStr(i) & vbTab & fullName & vbTab & Solid.GetMaterialNameForShape(fullName)
Next i
Debug.Print "DONE"'''
FIXED_VBA = frozenset({UNITS_BLOCK, UNITS_QUERY, SHAPES_QUERY})
TIMEOUT_RE = re.compile(r"\btimeout\b|timed\s+out", re.IGNORECASE)


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def serialize(value: Any) -> Any:
    """Retain every MCP content block and model field, including extension fields."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True, exclude_none=False)
    if isinstance(value, dict):
        return {str(k): serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, value: Any) -> None:
    # Replace only this run's local report; never touch a previous run.
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(serialize(value), indent=2, ensure_ascii=False,
                                    allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def interpret(response: dict) -> dict:
    parsed = []
    structured = response.get("structuredContent", response.get("structured_content"))
    if structured is not None:
        parsed.append({"source": "structuredContent", "value": structured})
    text_objects = []
    for index, block in enumerate(response.get("content", [])):
        if block.get("type") != "text":
            continue
        try:
            value = json.loads(block.get("text", ""))
        except (ValueError, TypeError):
            continue
        parsed.append({"source": f"content[{index}].text", "value": value})
        if isinstance(value, dict):
            text_objects.append(value)
    # Multiple unrelated JSON objects must not silently be merged.
    payload = structured if isinstance(structured, dict) else (
        text_objects[0] if len(text_objects) == 1 else None)
    return {"payload": payload, "parsed_payloads": parsed,
            "isError": response.get("isError", response.get("is_error", False))}


def walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def exception_leaves(exc: BaseException):
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            yield from exception_leaves(child)
    else:
        yield exc


def reported_timeout(response: dict, decoded: dict) -> bool:
    for item in walk_dicts(decoded["parsed_payloads"]):
        if item.get("status") == "timeout":
            return True
        # Some lifecycle handlers wrap native timeouts as ordinary errors.
        if any(TIMEOUT_RE.search(str(item.get(key, ""))) for key in
               ["message", "solver_status_error", "last_error"]):
            return True
    return bool(decoded["isError"] and any(
        TIMEOUT_RE.search(block.get("text", ""))
        for block in response.get("content", []) if block.get("type") == "text"))


def indeterminate(response: dict, decoded: dict) -> bool:
    if reported_timeout(response, decoded):
        return True
    for item in walk_dicts(decoded["parsed_payloads"]):
        if item.get("execution_state") == "unknown":
            return True
        if item.get("status") == "busy" and item.get("running", False) is None:
            return True
        if item.get("project_open") is True and "solver_running" in item and item["solver_running"] is None:
            return True
    return False


def parse_units(output: str) -> dict[str, str]:
    result = {}
    for line in output.splitlines():
        key, sep, value = line.rstrip("\r").partition("\t")
        if sep and key in EXPECTED_UNITS:
            if key in result:
                raise ValueError(f"Duplicate unit key: {key}")
            result[key] = value.strip()
    if set(result) != set(EXPECTED_UNITS):
        raise ValueError("Unit query did not return all three effective unit values")
    return result


def parse_shapes(output: str) -> dict[str, str]:
    count = None
    done = False
    shapes = {}
    indices = set()
    for line in output.splitlines():
        fields = line.rstrip("\r").split("\t")
        if fields[0] == "COUNT" and len(fields) == 2:
            if count is not None:
                raise ValueError("Duplicate shape count")
            count = int(fields[1])
        elif fields[0] == "SOLID" and len(fields) == 4:
            index, name, material = int(fields[1]), fields[2], fields[3]
            if index in indices or name in shapes or not name or not material:
                raise ValueError("Duplicate or incomplete shape record")
            indices.add(index)
            shapes[name] = material
        elif fields == ["DONE"]:
            done = True
    if count is None or count < 0 or not done or indices != set(range(count)):
        raise ValueError("Incomplete shape enumeration (count/index/end marker mismatch)")
    return shapes


@dataclass
class Case:
    case_id: str
    tool: str
    arguments: dict = field(default_factory=dict)
    kind: str = "mutation"
    expected: dict = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    contract: str = "implemented; real effect awaits separate evidence"
    notes: str = ""


def make_plan(work: Path) -> list[Case]:
    cases = []

    def add(case_id, tool, args=None, **kwargs):
        cases.append(Case(case_id, tool, args or {}, **kwargs))

    add("initial_status", "cst_connection_status", kind="initial_status")
    add("connect_new", "cst_connect", {"mode": "new"}, kind="connect")
    add("create_project", "cst_create_project", {"path": str(work / "project.cst"),
        "project_type": "MWS"}, kind="create_project")
    add("created_info", "cst_project_info", kind="info")
    add("created_log", "cst_read_project_log", kind="diagnostic")
    add("set_units", "cst_execute_vba", {"code": UNITS_BLOCK})
    add("effective_units", "cst_execute_vba", {"code": UNITS_QUERY}, kind="units")
    add("save_units", "cst_save_project", kind="save")
    add("units_log", "cst_read_project_log", kind="diagnostic")
    add("dielectric", "cst_create_material", {"name": "BatchDielectric", "epsilon": 4.3,
        "mu": 1.0, "tan_d_e": 0.02, "conductivity": 0.0},
        notes="Returned properties echo input; electromagnetic properties require manual inspection.")
    add("copper", "cst_create_lossy_metal", {"name": "BatchCopper",
        "conductivity": 58000000.0, "mu": 1.0},
        notes="Returned properties echo input; conductivity requires manual inspection.")
    database_contract = "implemented: bundled database query; no project custom material readback"
    add("database_metals", "cst_list_materials", {"category": "metals"}, kind="database", contract=database_contract)
    add("database_copper", "cst_get_material_info", {"name": "Copper"}, kind="database", contract=database_contract)
    add("materials_log", "cst_read_project_log", kind="diagnostic")

    def solid(case_id, tool, args, expectation, **kwargs):
        args = {"component": "Batch", "name": expectation.pop("name"), **args}
        if tool != "cst_create_wire":
            args["material"] = "PEC"
        add(case_id, tool, args, expected={"solid": f"Batch:{args['name']}",
            **expectation}, **kwargs)

    solid("brick", "cst_create_brick", {"x_min": 0, "x_max": 10, "y_min": 0, "y_max": 6,
          "z_min": 0, "z_max": 2}, {"name": "BatchBrick", "dimensions_mm": [10, 6, 2]})
    solid("cylinder", "cst_create_cylinder", {"axis": "z", "outer_radius": 2,
          "inner_radius": 0, "center_x": 20, "center_y": 3, "center_z": 0, "range_min": 0, "range_max": 6},
          {"name": "BatchCylinder", "radius_mm": 2, "z_range_mm": [0, 6]})
    solid("cone", "cst_create_cone", {"axis": "z", "bottom_radius": 3, "top_radius": 1,
          "center_x": 35, "center_y": 3, "center_z": 0, "range_min": 0, "range_max": 5},
          {"name": "BatchCone", "radii_mm": [3, 1], "z_range_mm": [0, 5]})
    solid("sphere", "cst_create_sphere", {"radius": 3, "center_x": 50, "center_y": 3,
          "center_z": 3}, {"name": "BatchSphere", "radius_mm": 3})
    solid("torus", "cst_create_torus", {"axis": "z", "outer_radius": 5, "inner_radius": 3,
          "center_x": 65, "center_y": 5, "center_z": 1},
          {"name": "BatchTorus", "major_radius_mm": 4, "tube_radius_mm": 1,
               "outer_surface_radius_mm": 5, "hole_radius_mm": 3, "z_range_mm": [0, 2]},
          contract="limited: schema radius descriptions conflict with installed CST 2025 diagram",
          notes="Builder passes OuterRadius/InnerRadius unchanged; installed torus.gif shows outer/hole radii.")
    solid("ecylinder", "cst_create_ecylinder", {"axis": "z", "x_radius": 4, "y_radius": 2,
          "center_x": 80, "center_y": 3, "center_z": 0, "range_min": 0, "range_max": 3},
          {"name": "BatchECylinder", "radii_mm": [4, 2], "z_range_mm": [0, 3]})
    add("primitives_log", "cst_read_project_log", kind="diagnostic")
    solid("wire", "cst_create_wire", {"start_x": 95, "start_y": 0, "start_z": 1,
          "end_x": 101, "end_y": 4, "end_z": 6, "radius": 0.3},
          {"name": "BatchWire", "endpoints_mm": [[95, 0, 1], [101, 4, 6]], "radius_mm": 0.3},
          contract="limited: one straight segment; cylinder plus rigid transforms",
          notes="Exposed schema has no material argument and no multisegment path.")
    add("outline", "cst_create_polygon3d", {"name": "BatchOutline", "points": [
        [95, 20, 0], [101, 20, 0], [101, 24, 0], [95, 24, 0], [95, 20, 0]]},
        expected={"curve": "Curves:BatchOutline", "closed": True},
        notes="Curve existence, closure and dimensions remain pending manual inspection.")
    add("analytical_circle", "cst_create_analytical_curve", {"name": "BatchCircle",
        "x_expr": "110+3*cos(t)", "y_expr": "25+3*sin(t)", "z_expr": "0",
        "t_min": 0, "t_max": 2 * math.pi}, expected={"curve": "Curves:BatchCircle"},
        notes="Curve existence and circle shape remain pending manual inspection.")
    solid("extrude_l", "cst_create_extrude", {"points": [
          [0, 20], [6, 20], [6, 22], [2, 22], [2, 26], [0, 26]],
          "height": 3, "axis": "z", "z_offset": 1}, {"name": "BatchLExtrude", "z_range_mm": [1, 4]})
    for direction, x, z_range in [("up", 15, [5, 7]), ("down", 25, [3, 5])]:
        solid(f"polygon_{direction}", "cst_create_polygon_extrude", {"points": [
              [x, 20], [x + 3, 20], [x + 3, 23], [x, 23]], "height": 2, "axis": "z",
              "z_offset": 5, "extrude_direction": direction},
              {"name": f"BatchPolygon{direction.title()}", "z_range_mm": z_range},
              notes="Returned extrusion.expected_range is a prediction, not a measured extent.")
    add("curves_extrusions_log", "cst_read_project_log", kind="diagnostic")
    add("before_assignment_shapes", "cst_execute_vba", {"code": SHAPES_QUERY}, kind="shapes")
    for suffix, material, dependency in [("copper", "BatchCopper", "copper"),
                                          ("dielectric", "BatchDielectric", "dielectric")]:
        add(f"assign_{suffix}", "cst_assign_material", {"solid": "Batch:BatchBrick",
            "material": material}, dependencies=["brick", dependency])
        add(f"verify_{suffix}", "cst_execute_vba", {"code": SHAPES_QUERY}, kind="shapes",
            expected={"association": {"Batch:BatchBrick": material}},
            dependencies=[f"assign_{suffix}"])
    add("assignments_log", "cst_read_project_log", kind="diagnostic")
    add("checkpoint_save", "cst_save_project", kind="save")
    add("checkpoint_log", "cst_read_project_log", kind="diagnostic")
    add("face_qualified_reference", "cst_create_face_from_curves", {"component": "Batch",
        "name": "BatchFaceProbe", "curve_names": ["Curves:BatchOutline"]}, kind="face",
        contract="incompatible: qualified reference rejected by validate_name; builder uses AddCurve",
        notes="CST 2025 documents CoverCurve.Curve. Expected rejection is no geometric execution.")
    add("face_log", "cst_read_project_log", kind="diagnostic")
    add("loft_contract_probe", "cst_create_loft", {"component": "Batch",
        "name": "BatchLoftProbe", "material": "PEC", "profiles": [
        [[120, 20], [126, 20], [126, 26], [120, 26]],
        [[121, 21], [125, 21], [125, 25], [121, 25]]]}, kind="loft",
        contract="limited/incompatible: no separate planes; Polygon plus undocumented Loft.AddCurve",
        notes="Last construction call. Executed status does not prove a transition between distinct planes.")
    add("loft_log", "cst_read_project_log", kind="diagnostic")
    for prefix in ["before_reopen", "after_reopen"]:
        if prefix == "before_reopen":
            add("final_save", "cst_save_project", kind="save")
        add(f"{prefix}_info", "cst_project_info", kind="info")
        add(f"{prefix}_tree", "cst_project_tree", {"tree_path": "Components", "max_depth": 3}, kind="tree",
            notes="Only returned items are evidence; tree is not a complete geometry enumeration or measurement.")
        add(f"{prefix}_units", "cst_execute_vba", {"code": UNITS_QUERY}, kind="units")
        add(f"{prefix}_shapes", "cst_execute_vba", {"code": SHAPES_QUERY}, kind="shapes")
        add(f"{prefix}_log", "cst_read_project_log", kind="diagnostic")
        if prefix == "before_reopen":
            add("close_owned_project", "cst_close_project", kind="close")
            add("reopen_owned_project", "cst_open_project", {"path": str(work / "project.cst")}, kind="open")
    add("disconnect", "cst_disconnect", kind="disconnect")
    return cases


def classify(case: Case, raw: dict, decoded: dict, preflight: bool, connected: bool) -> dict:
    payload = decoded["payload"] or {}
    result = {"classification": "failure", "command_accepted": False,
              "executed_in_real_cst": False, "effect_confirmed": False, "checks": []}
    if indeterminate(raw, decoded):
        result["classification"] = "indeterminate_state"
        return result
    rejected = decoded["isError"] or any(
        item.get("status") in {"error", "busy", "not_found"}
        for item in walk_dicts(decoded["parsed_payloads"]))
    if rejected:
        message = "\n".join(block.get("text", "") for block in raw.get("content", [])
                            if block.get("type") == "text")
        if case.kind == "face" and "curve_name" in message and "Invalid" in message:
            result["classification"] = "known_limitation"
            result["checks"].append({"scope": "qualified reference rejection only", "passed": True})
        elif case.kind == "negative":
            result["classification"] = "expected_rejection"
            result["checks"].append({"scope": "server rejection handling only", "passed": True})
        return result
    if case.kind == "negative":
        return result  # An accepted negative control is a failure.
    if any(item.get("status") == "offline" or item.get("mode") == "offline"
           for item in walk_dicts(decoded["parsed_payloads"])):
        result["classification"] = "offline_only"
        result["command_accepted"] = True
        return result
    status = payload.get("status")
    accepted = False
    if case.kind == "database":
        if case.tool == "cst_list_materials":
            accepted = isinstance(payload.get("materials"), list) and payload.get("count") == len(payload["materials"])
        else:
            accepted = isinstance(payload.get("material"), dict) and payload["material"].get("name", "").lower() == "copper"
    elif case.kind in {"initial_status", "state"}:
        accepted = payload.get("mode") == "connected"
    elif case.kind == "info":
        accepted = payload.get("mode") == "connected" and payload.get("project_open") is True
    elif case.kind in {"units", "shapes"}:
        accepted = status == "ok" and isinstance(payload.get("output"), str)
    elif case.kind == "tree":
        accepted = status == "ok" and isinstance(payload.get("items"), list)
    elif case.kind == "diagnostic":
        accepted = status in {"ok", "empty"}
    else:
        accepted = status == {"connect": "connected", "create_project": "created",
            "save": "saved", "close": "closed", "open": "opened",
            "disconnect": "disconnected"}.get(case.kind, "executed")
    if accepted:
        result.update(classification=("database_query" if case.kind == "database" else
            "no_messages_available" if case.kind == "diagnostic" and status == "empty" else
            "command_accepted"), command_accepted=True,
            executed_in_real_cst=(not preflight and (connected or case.kind == "connect") and
                                 case.kind not in {"database", "diagnostic", "disconnect"}))
    result["checks"].append({"scope": "tool-specific response contract only", "passed": accepted})
    return result


class StopBatch(RuntimeError):
    pass


class UnknownState(StopBatch):
    pass


class Batch:
    def __init__(self, options: argparse.Namespace):
        self.options = options
        self.run_id = f"run_{datetime.now(UTC):%Y%m%dT%H%M%S_%fZ}_{uuid.uuid4().hex[:10]}"
        self.work = BATCH_DIR / "runs" / self.run_id
        self.work.mkdir(parents=True, exist_ok=False)
        # Logs exist and are open before the compatibility probe and MCP server.
        self.calls = (self.work / "mcp_calls.jsonl").open("a", encoding="utf-8")
        self.messages = (self.work / "cst_messages.jsonl").open("a", encoding="utf-8")
        self.stderr = (self.work / "server_stderr.log").open("a", encoding="utf-8")
        self.plan = make_plan(self.work)
        self.by_id = {case.case_id: case for case in self.plan}
        self.results: dict[str, dict] = {}
        self.catalog: dict[str, dict] = {}
        self.sequence = 0
        self.connected = False
        self.unknown = False
        self.actual_path: Path | None = None
        self.snapshots: dict[str, Any] = {}
        self.exit_code = 1
        self.completed = False
        self.reason = "Execution did not complete"
        self.metadata = {
            "run_id": self.run_id, "started_at": timestamp(), "mode": "preflight" if options.preflight else "live",
            "repository_root": str(ROOT), "execution_directory": str(self.work),
            "python": {"executable": sys.executable, "version": sys.version},
            "client_dependencies": {name: importlib.metadata.version(name) for name in ["mcp", "jsonschema"]},
            "timeouts_seconds": {"handshake": options.call_timeout, "connection": options.connection_timeout,
                                  "call": options.call_timeout, "server_native": 30},
            "raw_vba": {"enabled_in_server_subprocess_only": True, "env": "CST_ALLOW_RAW_VBA=1",
                        "allowed_blocks": {"set_units": UNITS_BLOCK, "read_units": UNITS_QUERY, "read_shapes": SHAPES_QUERY},
                        "purpose": "Fixed units block and fixed read-only queries; no CLI VBA, modeling fallback or LLM."},
            "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "transport_process_policy": "Real SDK stdio_client with two scoped client-only lifecycle hooks: launch without a Windows kill-on-close Job Object and terminate only the Python server PID. Never terminate CST descendants. Missing hooks block startup.",
            "dynamic_calls": "Normal failures: project log and connection state check; blocked dependencies recorded locally.",
            "torus_reference": "Installed CST 2025 Online Help: common_vbatorus_object.htm, common_struct_torus.htm, image/torus.gif. Outer/inner surface radii 5/3 imply major/tube radii 4/1; schema descriptions disagree.",
        }
        for label, command in [("git_revision", ["git", "rev-parse", "HEAD"]),
                               ("git_branch", ["git", "branch", "--show-current"]),
                               ("git_status_initial", ["git", "status", "--short"])]:
            try:
                self.metadata[label] = subprocess.run(command, cwd=ROOT, capture_output=True,
                    text=True, timeout=10, check=False).stdout.strip()
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.metadata[label] = {"error": str(exc)}
        self.env = self.server_environment()
        self.metadata["server"] = {"command": sys.executable, "args": ["-m", "cst_mcp.server"],
            "cwd": str(ROOT), "environment_overrides": {key: self.env[key] for key in [
            "CST_CONNECT_MODE", "CST_VERSION", "CST_PATH", "CST_WORK_DIR", "CST_TOOLSETS",
            "CST_ALLOW_RAW_VBA", "PYTHONPATH"]}}
        write_json(self.work / "metadata.json", self.metadata)
        write_json(self.work / "case_plan.json", [asdict(case) for case in self.plan])
        write_json(self.work / "tool_catalog.json", {"pages": [], "tools": [], "retrieved": False})

    def server_environment(self) -> dict[str, str]:
        env = os.environ.copy()
        install = Path(self.options.cst_path).expanduser().resolve()
        libs = install / "AMD64" / "python_cst_libraries"
        useful_paths = [str(ROOT / "src"), str(libs), *env.get("PYTHONPATH", "").split(os.pathsep)]
        env.update(CST_CONNECT_MODE="disabled" if self.options.preflight else "manual",
                   CST_VERSION="2025", CST_PATH=str(install), CST_WORK_DIR=str(self.work),
                   CST_TOOLSETS=TOOLSETS, CST_ALLOW_RAW_VBA="1",
                   PYTHONPATH=os.pathsep.join(dict.fromkeys(p for p in useful_paths if p)))
        return env

    def event(self, value: dict, *, messages=False) -> None:
        stream = self.messages if messages else self.calls
        stream.write(json.dumps(serialize({"run_id": self.run_id, "timestamp": timestamp(), **value}),
                                ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()

    def check(self, case_id: str, scope: str, passed: bool, evidence: Any = None) -> None:
        check = {"scope": scope, "passed": passed, "evidence": evidence}
        record = self.results[case_id]
        record["checks"].append(check)
        if passed and scope.startswith("readback:"):
            record["effect_confirmed"] = True
        if not passed:
            record["verification_failed"] = True
        self.event({"event": "check", "case_id": case_id, "sequence": record["sequence"], "check": check})

    def blocked(self, case: Case, reason: str) -> None:
        record = {"case_id": case.case_id, "tool": case.tool, "arguments": case.arguments,
                  "classification": "blocked_dependency", "reason": reason, "checks": [],
                  "command_accepted": False, "executed_in_real_cst": False, "effect_confirmed": False}
        self.results[case.case_id] = record
        self.event({"event": "blocked", **record})

    async def request(self, session: ClientSession, case: Case, *, protocol=False) -> dict:
        if self.unknown:
            raise UnknownState("Further MCP calls forbidden after indeterminate state")
        if case.tool == "cst_execute_vba" and case.arguments.get("code") not in FIXED_VBA:
            raise StopBatch("Only the three fixed VBA blocks are permitted")
        checks = []
        if not protocol and case.kind != "negative":
            self.validate(case)
            checks.append({"scope": "catalog input schema", "passed": True})
        self.sequence += 1
        sequence = self.sequence
        timeout = self.options.connection_timeout if case.kind == "connect" else self.options.call_timeout
        base = {"case_id": case.case_id, "sequence": sequence, "tool": case.tool,
                "arguments": case.arguments, "timeout_seconds": timeout}
        started = time.monotonic()
        self.event({**base, "event": "start", "duration_seconds": 0, "checks": checks})
        raw = None
        response_received = False
        decoded = {"payload": None, "parsed_payloads": [], "isError": None}
        try:
            if protocol:
                method = session.initialize if case.tool == "initialize" else session.list_tools
                response = await asyncio.wait_for(method(**case.arguments), timeout)
            else:
                response = await asyncio.wait_for(session.call_tool(case.tool, case.arguments), timeout)
            response_received = True
            raw = serialize(response)
            if protocol:
                result = {"classification": "protocol_success", "command_accepted": True,
                          "executed_in_real_cst": False, "effect_confirmed": False, "checks": []}
            else:
                decoded = interpret(raw)
                result = classify(case, raw, decoded, self.options.preflight, self.connected)
            record = {**base, **result, "event": "completion", "timestamp": timestamp(),
                      "duration_seconds": round(time.monotonic() - started, 6), "response": raw,
                      **decoded, "exception": None, "timeout": reported_timeout(raw, decoded),
                      "checks": checks + result["checks"]}
            self.results[case.case_id] = record
            if result["classification"] == "indeterminate_state":
                self.unknown = True
            self.event(record)
            self.preserve_response_artifacts(record)
            print(f"{sequence:02d} {case.case_id}: {record['classification']}", flush=True)
            if result["classification"] == "indeterminate_state":
                self.unknown = True
                raise UnknownState(f"{case.case_id}: server timeout or unknown CST state")
            return record
        except UnknownState:
            raise
        except BaseException as exc:
            if response_received:
                # A local recording failure is not a second tool response.
                raise StopBatch(f"{case.case_id}: local response processing failed: {exc}") from exc
            # Cancellation/transport loss can leave a native request running, including readbacks.
            self.unknown = True
            record = {**base, "event": "error", "timestamp": timestamp(),
                "duration_seconds": round(time.monotonic() - started, 6), "response": raw, **decoded,
                "exception": {"type": type(exc).__name__, "message": str(exc)},
                "timeout": isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or bool(TIMEOUT_RE.search(str(exc))),
                "classification": "indeterminate_state", "command_accepted": False,
                "executed_in_real_cst": False, "effect_confirmed": False, "checks": checks}
            self.results[case.case_id] = record
            self.event(record)
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            raise UnknownState(f"{case.case_id}: transport exception; no further CST calls") from exc

    def preserve_response_artifacts(self, record: dict) -> None:
        for entry in record["parsed_payloads"]:
            for index, item in enumerate(walk_dicts(entry["value"])):
                for key in ["vba", "vba_script"]:
                    if isinstance(item.get(key), str):
                        folder = self.work / "vba"
                        folder.mkdir(exist_ok=True)
                        target = folder / f"{record['sequence']:03d}_{record['case_id']}_{len(list(folder.iterdir())):03d}.bas"
                        target.write_text(item[key], encoding="utf-8")
                        self.event({"event": "vba_saved", "case_id": record["case_id"],
                            "sequence": record["sequence"], "path": str(target),
                            "source": f"response.{entry['source']}.object[{index}].{key}"})
                if "cst_messages_tail" in item:
                    self.event({"checkpoint": record["case_id"], "sequence": record["sequence"],
                        "source": f"inline response.{entry['source']}", "payload": item,
                        "attribution": "Context only; not automatically attributed to this command."}, messages=True)
        if record["tool"] == "cst_read_project_log":
            self.event({"checkpoint": record["case_id"], "sequence": record["sequence"],
                "response": record["response"], "payload": record["payload"],
                "parsed_payloads": record["parsed_payloads"],
                "attribution": "Checkpoint may contain repeated messages or only a tail. Empty is not proof of success."}, messages=True)

    def validate(self, case: Case) -> None:
        if case.tool not in self.catalog:
            raise StopBatch(f"Required tool absent from catalog: {case.tool}")
        schema = self.catalog[case.tool].get("inputSchema", self.catalog[case.tool].get("input_schema"))
        validator_for(schema)(schema).validate(case.arguments)

    def own_path(self, value: Any) -> Path:
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise StopBatch("Server did not return an absolute saved project path")
        path = Path(value).resolve()
        if not path.is_relative_to(self.work.resolve()) or path.suffix.lower() != ".cst":
            raise StopBatch(f"Returned project path is outside this run or is not .cst: {path}")
        return path

    def require(self, record: dict) -> dict:
        if not record["command_accepted"] or record["classification"] == "offline_only":
            raise StopBatch(f"{record['case_id']}: required connected operation failed")
        return record["payload"] or {}

    def probe_import(self) -> None:
        # Import compatibility only: neither this probe nor the parent instantiates a DE.
        script = '''import importlib.util, json, os, pathlib
libs = pathlib.Path(os.environ["CST_PATH"]) / "AMD64" / "python_cst_libraries"
result = {"library_directory": str(libs), "library_found": (libs / "cst").is_dir(),
          "extension_candidates": [str(p) for p in libs.parent.glob("_cst_interface.cp312*.pyd")],
          "import_attempted": True, "import_succeeded": False, "instance_created": False}
try:
    spec = importlib.util.find_spec("cst")
    result["package_spec_origin"] = spec.origin if spec else None
    import cst.interface
    result["import_succeeded"] = True
    result["module_origin"] = cst.interface.__file__
except Exception as exc:
    result["exception"] = {"type": type(exc).__name__, "message": str(exc)}
print(json.dumps(result))
'''
        try:
            response = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=self.env,
                                      capture_output=True, text=True, timeout=self.options.call_timeout, check=False)
            lines = response.stdout.splitlines()
            probe = json.loads(lines[-1]) if lines else {"import_succeeded": False}
            probe.update(returncode=response.returncode, stdout=response.stdout, stderr=response.stderr)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            probe = {"import_succeeded": False, "exception": {"type": type(exc).__name__, "message": str(exc)}}
        self.metadata["cst_import_compatibility"] = probe
        write_json(self.work / "metadata.json", self.metadata)

    async def catalog_and_plan(self, session: ClientSession) -> None:
        await self.request(session, Case("initialize", "initialize", kind="protocol"), protocol=True)
        pages = []
        cursor = None
        seen = set()
        while True:
            args = {} if cursor is None else {"cursor": cursor}
            page = await self.request(session, Case(f"catalog_{len(pages) + 1}", "list_tools", args,
                                                   kind="protocol"), protocol=True)
            raw = page["response"]
            pages.append(raw)
            for tool in raw.get("tools", []):
                if tool["name"] in self.catalog:
                    raise StopBatch(f"Duplicate tool in paginated catalog: {tool['name']}")
                self.catalog[tool["name"]] = tool
            write_json(self.work / "tool_catalog.json", {"pages": pages, "tools": list(self.catalog.values()), "retrieved": True})
            cursor = raw.get("nextCursor", raw.get("next_cursor"))
            if not cursor:
                break
            if cursor in seen:
                raise StopBatch("Tool catalog pagination repeated its cursor")
            seen.add(cursor)
        failures = []
        for tool in self.catalog.values():
            schema = tool.get("inputSchema", tool.get("input_schema"))
            try:
                validator_for(schema).check_schema(schema)
            except (SchemaError, TypeError) as exc:
                failures.append({"tool": tool["name"], "error": str(exc)})
        for case in self.plan:
            try:
                self.validate(case)
            except (StopBatch, SchemaValidationError, SchemaError, TypeError) as exc:
                failures.append({"case_id": case.case_id, "tool": case.tool, "error": str(exc)})
        self.metadata["plan_validation"] = {"cases": len(self.plan), "schemas": len(self.catalog), "failures": failures}
        write_json(self.work / "metadata.json", self.metadata)
        self.event({"event": "plan_validation", **self.metadata["plan_validation"]})
        if failures:
            raise StopBatch("Catalog/plan validation failed; see metadata.json")

    async def recover(self, session: ClientSession, failed: dict) -> None:
        await self.request(session, Case(f"{failed['case_id']}_failure_log", "cst_read_project_log", kind="diagnostic"))
        state = await self.request(session, Case(f"{failed['case_id']}_state", "cst_connection_status", kind="state"))
        payload = self.require(state)
        okay = payload.get("project_open") is True and payload.get("solver_running") is False
        okay = okay and self.own_path(payload.get("project_path")) == self.actual_path
        self.check(state["case_id"], "readback: same owned project reachable and solver idle", okay, payload)
        if not okay:
            raise StopBatch("State check did not confirm the owned project is reachable and idle")

    async def step(self, session: ClientSession, case_id: str, *, required=False) -> dict | None:
        case = self.by_id[case_id]
        unmet = [dependency for dependency in case.dependencies if not (
            self.results.get(dependency, {}).get("command_accepted") and
            self.results[dependency]["classification"] != "offline_only")]
        if unmet:
            self.blocked(case, f"Dependency not executed: {', '.join(unmet)}")
            return None
        if case.tool == "cst_assign_material" and not self.results.get("brick", {}).get("effect_confirmed"):
            self.blocked(case, "Brick was not independently confirmed by shape enumeration")
            return None
        record = await self.request(session, case)
        if record["classification"] == "offline_only":
            raise StopBatch(f"{case_id}: connection/project became offline")
        if record["classification"] == "failure":
            await self.recover(session, record)
            if required:
                self.require(record)
        return record

    def decode_readback(self, record: dict) -> Any:
        payload = self.require(record)
        case = self.by_id[record["case_id"]]
        try:
            parsed = parse_units(payload["output"]) if case.kind == "units" else parse_shapes(payload["output"])
        except (ValueError, KeyError) as exc:
            self.check(case.case_id, "readback: complete query output", False, str(exc))
            raise StopBatch(f"{case.case_id}: readback output incomplete") from exc
        self.snapshots[case.case_id] = parsed
        self.check(case.case_id, "readback: effective units" if case.kind == "units" else
                   "readback: names and material associations only", True, parsed)
        if case.kind == "units":
            correct = parsed == EXPECTED_UNITS
            self.check(case.case_id, "effective unit comparison mm/GHz/ns", correct, parsed)
            if not correct:
                raise StopBatch("Effective units differ from mm/GHz/ns; stopping construction")
        return parsed

    def confirm_solids(self, snapshot_id: str) -> None:
        shapes = self.snapshots[snapshot_id]
        for case in self.plan:
            name = case.expected.get("solid")
            record = self.results.get(case.case_id)
            if name and record and record["executed_in_real_cst"]:
                self.check(case.case_id, "readback: named solid exists; dimensions/shape pending manual inspection",
                           name in shapes, {"source_case_id": snapshot_id, "solid": name,
                                            "material": shapes.get(name)})

    async def preflight(self, session: ClientSession) -> None:
        # Explicit allowlist: no connect, project opening, save, close, or disconnect calls.
        status = await self.request(session, self.by_id["initial_status"])
        payload = status["payload"] or {}
        if not (payload.get("mode") == "offline" and payload.get("cst_available") is False and
                payload.get("project_open") is False):
            raise StopBatch("Disabled-mode server did not report a fully offline connection")
        selected = ["create_project", "created_info", "created_log", "set_units", "effective_units",
            "dielectric", "copper", "database_metals", "database_copper", "materials_log",
            "brick", "cylinder", "cone", "sphere", "torus", "ecylinder", "primitives_log",
            "wire", "outline", "analytical_circle", "extrude_l", "polygon_up", "polygon_down",
            "curves_extrusions_log", "before_assignment_shapes", "assign_copper", "assign_dielectric",
            "face_qualified_reference", "face_log", "loft_contract_probe", "loft_log", "before_reopen_tree"]
        for case_id in selected:
            record = await self.request(session, self.by_id[case_id])
            allowed = {"offline_only", "database_query", "no_messages_available"}
            if case_id == "face_qualified_reference":
                allowed = {"known_limitation"}
            if record["classification"] not in allowed:
                raise StopBatch(f"{case_id}: unexpected preflight classification")
            if self.by_id[case_id].kind in {"mutation", "loft", "create_project", "tree", "units", "shapes"}:
                has_vba = any(isinstance(item.get(key), str) and item[key].strip()
                    for item in walk_dicts(record["parsed_payloads"]) for key in ["vba", "vba_script"])
                self.check(case_id, "offline generation returned VBA; no geometry validation", has_vba)
                if not has_vba:
                    raise StopBatch(f"{case_id}: expected offline VBA missing")
        # Deliberately invalid controls bypass local argument validation, in disabled mode only.
        for case in [Case("negative_missing_path", "cst_create_project", kind="negative"),
                     Case("negative_unknown_tool", "batch01_nonexistent_tool", kind="negative")]:
            record = await self.request(session, case)
            if record["classification"] != "expected_rejection":
                raise StopBatch("Negative preflight control was not rejected")
        for case in self.plan:
            if case.case_id not in self.results:
                self.blocked(case, "Preflight omits connected lifecycle/readback assertions")
        self.verify_log_pairs()
        self.completed = True
        self.exit_code = 0 if self.metadata["cst_import_compatibility"].get("import_succeeded") else 1
        self.reason = "Offline stdio preflight completed; no CST instance or geometric validation" + (
            "" if self.exit_code == 0 else "; CST library import compatibility failed")

    async def live(self, session: ClientSession) -> None:
        initial = await self.request(session, self.by_id["initial_status"])
        if (initial["payload"] or {}).get("mode") != "offline":
            raise StopBatch("Manual-mode startup unexpectedly connected; refusing construction")
        if not self.metadata["cst_import_compatibility"].get("import_succeeded"):
            raise StopBatch("CST package was not successfully imported in the compatibility probe")
        record = await self.request(session, self.by_id["connect_new"])
        connection = self.require(record)
        self.metadata["connection"] = connection
        write_json(self.work / "metadata.json", self.metadata)
        isolated = (connection.get("newly_started") is True and connection.get("open_projects") == 0
                    and connection.get("open_project_paths") == [] and connection.get("project_path") is None)
        self.check("connect_new", "readback: new DE with no previous projects", isolated, connection)
        if not isolated:
            raise StopBatch("New DE isolation not confirmed; no fallback to mode=any")
        self.connected = True
        created = self.require(await self.request(session, self.by_id["create_project"]))
        self.actual_path = self.own_path(created.get("path"))
        self.metadata["actual_project_path"] = str(self.actual_path)
        write_json(self.work / "metadata.json", self.metadata)
        self.check("create_project", "returned path belongs to this run", True, str(self.actual_path))
        for case in self.plan[3:]:
            if case.case_id == "reopen_owned_project":
                self.verify_saved_file()
                case.arguments["path"] = str(self.actual_path)
            if case.case_id == "close_owned_project":
                self.verify_saved_file()
            required = case.kind in {"info", "save", "close", "open", "units", "disconnect"} or case.case_id == "set_units"
            record = await self.step(session, case.case_id, required=required)
            if record is None:
                continue
            if case.kind in {"create_project", "save", "open"} and record["command_accepted"]:
                path = self.own_path((record["payload"] or {}).get("path"))
                if case.kind == "save":
                    self.actual_path = path
                    self.metadata["actual_project_path"] = str(path)
                    write_json(self.work / "metadata.json", self.metadata)
                elif path != self.actual_path:
                    raise StopBatch("Reopen returned a different project path")
            if case.kind == "info" and record["command_accepted"]:
                path = self.own_path((record["payload"] or {}).get("project_path"))
                self.check(case.case_id, "readback: owned project open", path == self.actual_path, str(path))
                if path != self.actual_path:
                    raise StopBatch("Project info references another project")
            if case.kind in {"units", "shapes"} and record["command_accepted"]:
                try:
                    parsed = self.decode_readback(record)
                except StopBatch:
                    if case.kind == "units":
                        raise
                    record["classification"] = "failure"
                    await self.recover(session, record)
                    continue
                if case.case_id == "effective_units":
                    self.check("set_units", "readback: effective unit setting mm/GHz/ns", True,
                               {"source_case_id": case.case_id, "units": parsed})
                if case.kind == "shapes":
                    self.confirm_solids(case.case_id)
                    for name, material in case.expected.get("association", {}).items():
                        okay = parsed.get(name) == material
                        assigned_id = case.dependencies[0]
                        self.check(assigned_id, "readback: assigned material", okay,
                                   {"source_case_id": case.case_id, "solid": name, "actual": parsed.get(name), "expected": material})
                    if case.case_id == "before_assignment_shapes":
                        self.check("brick", "readback: initial PEC assignment", parsed.get("Batch:BatchBrick") == "PEC",
                                   {"source_case_id": case.case_id, "actual": parsed.get("Batch:BatchBrick")})
            if case.case_id == "disconnect":
                self.connected = False
        for kind in ["units", "shapes", "tree"]:
            before_id, after_id = f"before_reopen_{kind}", f"after_reopen_{kind}"
            if kind == "tree":
                before = (self.results[before_id]["payload"] or {}).get("items")
                after = (self.results[after_id]["payload"] or {}).get("items")
                okay = self.results[before_id]["command_accepted"] and self.results[after_id]["command_accepted"] and before == after
                scope = "comparison of returned tree items only (may be incomplete)"
            else:
                before, after = self.snapshots.get(before_id), self.snapshots.get(after_id)
                okay = before is not None and after is not None and before == after
                scope = f"readback: persisted {kind} match before/after reopening"
            self.check(after_id, scope, okay, {"before": before, "after": after})
        self.verify_log_pairs()
        self.completed = True
        issues = any(record["classification"] in {"failure", "blocked_dependency"} or record.get("verification_failed")
                     for record in self.results.values())
        self.exit_code = 1 if issues else 0
        self.reason = "Live assessment completed; reopened project left available for inspection" + (
            "; failures or blocked cases were recorded" if issues else "; known limitations remain documented")

    def verify_saved_file(self) -> None:
        if self.actual_path is None:
            raise StopBatch("No actual saved path")
        path = self.own_path(str(self.actual_path))
        okay = path.is_file() and path.stat().st_size > 0
        self.event({"event": "local_file_check", "path": str(path), "exists_nonempty": okay,
                    "size_bytes": path.stat().st_size if path.is_file() else None,
                    "scope": "File existence/size only, not project content verification"})
        if not okay:
            raise StopBatch("Saved .cst file is missing or empty; refusing close/reopen")

    def verify_log_pairs(self) -> None:
        events = [json.loads(line) for line in (self.work / "mcp_calls.jsonl").read_text(encoding="utf-8").splitlines()]
        starts = [event["sequence"] for event in events if event["event"] == "start"]
        finishes = [event["sequence"] for event in events if event["event"] in {"completion", "error"}]
        okay = starts == finishes and len(starts) == len(set(starts))
        self.metadata["log_validation"] = {"start_events": len(starts), "terminal_events": len(finishes), "paired": okay}
        if not okay:
            raise StopBatch("Call log start/completion pairing failed")

    def finalize(self) -> None:
        for case in self.plan:
            if case.case_id not in self.results:
                self.blocked(case, f"Batch stopped before case: {self.reason}")
        tools = []
        for name in sorted({case.tool for case in self.plan}):
            cases = [case for case in self.plan if case.tool == name]
            records = [record for record in self.results.values() if record["tool"] == name]
            tools.append({"tool": name, "present_in_catalog": name in self.catalog,
                "contract_assessment": sorted({case.contract for case in cases}),
                "tested_offline_or_substitute": {"offline": self.options.preflight and any(r.get("response") is not None for r in records), "substitute_in_this_run": False},
                "executed_in_real_cst": any(r["executed_in_real_cst"] for r in records),
                "effect_confirmed_by_readback": any(r["effect_confirmed"] for r in records),
                "manual_inspection": "pending; no automatic manual approval",
                "classifications": sorted({r["classification"] for r in records})})
        checklist = [
            "Confirm effective mm/GHz/ns units in CST and after reopening (compare query evidence).",
            "Inspect Batch solid names, separate positions and dimensions against case_plan.json and numeric arguments.",
            "Brick 10x6x2; cylinder r2/h6; cone r3->r1/h5; sphere r3; elliptical cylinder radii 4/2 and h3.",
            "Torus: inspect outer/hole radii 5/3, implied major/tube radii 4/1; schema descriptions disagree with installed diagram.",
            "Check BatchCopper conductivity/mu and BatchDielectric epsilon/mu/loss tangent/conductivity in the project material editor; response properties are input echoes.",
            "Inspect BatchBrick assignment history: PEC -> BatchCopper -> BatchDielectric; compare separate shape/material readbacks.",
            "Wire is one inclined straight cylinder (95,0,1) -> (101,4,6), radius .3; inspect endpoints and rotation.",
            "Inspect Curves:BatchOutline closure/rectangle and Curves:BatchCircle displaced radius-3 circle; solids query does not enumerate curves.",
            "Inspect L extrusion Z=1..4, polygon up Z=5..7, polygon down Z=3..5; tool ranges are predictions, not measurements. No holes.",
            "Face qualified-reference rejection is a limitation, with no geometric execution. Inspect any loft probe remnants; distinct planes are not expressible.",
            "Check saved project and reopened geometry/materials/curves; tree reports only its returned items. Readbacks cover units, solid names and associations, not full dimensions or EM properties.",
            "If interrupted/timeout: inspect preserved CST and last saved checkpoint manually; do not infer the last mutation completed.",
        ]
        summary = {"run_id": self.run_id, "mode": self.metadata["mode"], "completed": self.completed,
            "exit_code": self.exit_code, "reason": self.reason, "state": "indeterminate" if self.unknown else "known",
            "actual_project_path": str(self.actual_path) if self.actual_path else None,
            "saved_checkpoints": [r["case_id"] for r in self.results.values() if r["tool"] == "cst_save_project" and r["executed_in_real_cst"]],
            "tools": tools, "cases": list(self.results.values()), "snapshots": self.snapshots,
            "case_plan": [asdict(case) for case in self.plan], "manual_inspection_checklist": checklist,
            "evidence_limits": "Accepted/executed responses do not measure shape or EM properties. Offline VBA and default tree items are not real CST evidence. Diagnostic tails may repeat; empty does not prove success. Successful geometry may omit VBA; internal command history is incomplete."}
        self.metadata.update(finished_at=timestamp(), exit_code=self.exit_code, reason=self.reason,
                             state=summary["state"], actual_project_path=summary["actual_project_path"])
        write_json(self.work / "metadata.json", self.metadata)
        write_json(self.work / "summary.json", summary)
        lines = [f"# CST batch 01 — {self.run_id}", "", self.reason, "",
                 f"Mode: {summary['mode']}. State: {summary['state']}. Exit code: {self.exit_code}.", "",
                 f"Project: {summary['actual_project_path'] or 'No real project created.'}", "",
                 summary["evidence_limits"], "", "## Tool assessment", "",
                 "| Tool | Catalog | Contract | Offline | Real CST executed | Readback effect |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for tool in tools:
            lines.append(f"| {tool['tool']} | {tool['present_in_catalog']} | {'; '.join(tool['contract_assessment'])} | {tool['tested_offline_or_substitute']['offline']} | {tool['executed_in_real_cst']} | {tool['effect_confirmed_by_readback']} |")
        lines += ["", "## Cases", "", "| Case | Classification | Separate checks |", "| --- | --- | --- |"]
        for record in self.results.values():
            checks = "; ".join(f"{check['scope']}: {check['passed']}" for check in record["checks"])
            lines.append(f"| {record['case_id']} | {record['classification']} | {checks or record.get('reason', '')} |")
        lines += ["", "## Manual inspection (pending)", "", *[f"- [ ] {item}" for item in checklist], ""]
        (self.work / "summary.md").write_text("\n".join(lines), encoding="utf-8")
        for stream in [self.calls, self.messages, self.stderr]:
            stream.flush()
            stream.close()

    async def run(self) -> int:
        try:
            if sys.version_info[:2] != (3, 12):
                raise StopBatch("Python 3.12 is required for the local CST 2025 cp312 extension")
            self.probe_import()
            params = StdioServerParameters(command=sys.executable, args=["-m", "cst_mcp.server"],
                cwd=str(ROOT), env=self.env, encoding="utf-8", encoding_error_handler="strict")
            with preserve_cst_processes():
                async with (
                    stdio_client(params, errlog=self.stderr) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    await self.catalog_and_plan(session)
                    if self.options.preflight:
                        await self.preflight(session)
                    else:
                        await self.live(session)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.exit_code = 130
            self.reason = "Interrupted; no cleanup calls sent to CST; saved checkpoints preserved"
        except UnknownState as exc:
            self.exit_code = 2
            self.reason = str(exc) + "; no retries, queries, saves, close or disconnect calls sent"
        except Exception as exc:  # noqa: BLE001 - Unexpected local errors still need reports.
            self.exit_code = 2 if self.unknown else 1
            leaves = list(exception_leaves(exc))
            underlying = next((item for item in leaves if isinstance(item, StopBatch)), leaves[0])
            self.reason = f"{type(underlying).__name__}: {underlying}"
            if self.unknown:
                self.reason += "; no further CST calls sent; saved checkpoint and CST preserved"
            self.event({"event": "batch_error", "exception": {"type": type(exc).__name__,
                       "message": str(exc), "traceback": traceback.format_exc()}})
        finally:
            self.finalize()
        print(f"Reports: {self.work}\nExit code: {self.exit_code}", flush=True)
        return self.exit_code


def positive_timeout(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("Timeout must be finite and greater than zero")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="Real MCP transport; CST disabled; no connection or instance creation")
    parser.add_argument("--cst-path", default=DEFAULT_CST_PATH, help="CST 2025 installation root (subprocess only)")
    parser.add_argument("--connection-timeout", type=positive_timeout, default=120,
                        help="Client connection timeout in seconds (default 120)")
    parser.add_argument("--call-timeout", type=positive_timeout, default=60,
                        help="Client handshake/call/import timeout in seconds (default 60; native calls still 30)")
    return asyncio.run(Batch(parser.parse_args()).run())


if __name__ == "__main__":
    raise SystemExit(main())
