# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mcp>=1.29,<3", "jsonschema>=4.20"]
# ///
"""Deterministic planar sheet validation through a real MCP stdio session."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import logging
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

# Inspected imports: only definitions, constants and an import-search-path setup.
# Import stateless helpers only. Never construct ParameterTest or use its WORK.
from run_parameter_brick import (
    DEFAULT_CST_PATH,
    EXPECTED_UNITS,
    LOCK_SUFFIXES,
    SHAPES_QUERY,
    UNITS_QUERY,
    StopTest,
    UnknownState,
    close_number,
    ensure_closed,
    exception_leaves,
    indeterminate,
    interpret,
    number,
    parse_shapes,
    parse_units,
    positive_timeout,
    preserve_cst_processes,
    reject_links,
    serialize,
    sha256,
    snapshot,
    timestamp,
    walk_dicts,
    write_json,
)

# isort: split
# The preceding import installs batch 01's import-search path.
from run_batch import TIMEOUT_RE, UNITS_BLOCK
from run_parameter_primitives import parse_records

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "artifacts" / "05_face_from_curves"
OWNER = "cst-mcp-face-from-curves-v1"
TOOLSETS = "connection,project,geometry,parameters,diagnostics,vba"
COMPONENT = "FaceFromCurves"
CURVE = "Curves:Rectangle"
SHAPE = f"{COMPONENT}:RectangleSheet"
POLYGON_ARGS = {
    "name": "Rectangle",
    "points": [[0, 0, 2], [6, 0, 2], [6, 4, 2], [0, 4, 2], [0, 0, 2]],
}
FACE_ARGS = {"component": COMPONENT, "name": "RectangleSheet", "curve_names": [CURVE]}
FIXTURES = (("cst_create_polygon3d", POLYGON_ARGS), ("cst_create_face_from_curves", FACE_ARGS))
SETUP = f'Component.New "{COMPONENT}"\nCurve.NewCurve "Curves"'
CURVE_QUERY = f'''Debug.Print "CLOSED" & vbTab & CStr(Curve.IsClosed("{CURVE}"))
Debug.Print "DONE"'''
SHEET_QUERY = f'''Debug.Print "EXISTS" & vbTab & CStr(Solid.DoesExist("{SHAPE}"))
Debug.Print "IS_SOLID" & vbTab & CStr(Solid.IsSolidShape("{SHAPE}"))
Debug.Print "FACE_ID" & vbTab & CStr(Solid.GetAnyFaceIdFromSolid("{SHAPE}"))
Debug.Print "DONE"'''
# Isolate native area errors so an unavailable sheet measurement stays pending.
# Debug.Print selects output capture, outside model history, in CSTClient.
AREA_QUERY = f'''Dim faceArea As Double, areaError As Long, areaDescription As String
On Error Resume Next
Err.Clear
faceArea = Solid.GetArea("{SHAPE}")
areaError = Err.Number
areaDescription = Err.Description
On Error GoTo 0
Debug.Print "AREA_ERROR_NUMBER" & vbTab & CStr(areaError)
Debug.Print "AREA_ERROR_DESCRIPTION" & vbTab & "[" & Replace(Replace(areaDescription, vbCr, " "), vbLf, " ") & "]"
Debug.Print "AREA" & vbTab & CStr(faceArea)
Debug.Print "DONE"'''
FIXED_VBA = frozenset(
    {UNITS_BLOCK, SETUP, UNITS_QUERY, SHAPES_QUERY, CURVE_QUERY, SHEET_QUERY, AREA_QUERY}
)
HELP_ROOT = "Online Help/mergedProjects/VBA_3D"
REFERENCES = {
    "covercurve": f"{HELP_ROOT}/common_vbacurves/common_vbacurves_covercurve_object.htm",
    "polygon3d": f"{HELP_ROOT}/common_vbacurves/common_vbacurves_polygon3d.htm",
    "curve": f"{HELP_ROOT}/common_vbacurves/common_vbacurves_curve_object.htm",
    "solid": f"{HELP_ROOT}/common_vbasolido/common_vbasolido_solid_object.htm",
}
LIMITATIONS = [
    "Offline generation is not native geometry validation; live CST validation remains pending until execution.",
    "CoverCurve creates one planar sheet/face. No volume measurement is used as proof of success.",
    "Solid.GetArea reports shape surface area with an unconfirmed sheet counting convention. Its raw result is separate from the expected one-sided planar face area of 24 mm^2.",
    "One-sided face area remains pending manual inspection. No division by two or other normalization is applied; a raw value of 48 mm^2 only suggests counting both sides.",
    "Available raw shape areas are compared across save/reopen for persistence, not for proof of one-sided face area. Unavailable or invalid raw areas leave that persistence check pending.",
    "Named shape, sheet type, face presence and area do not independently measure every coordinate, width, height, z plane or exact face count.",
    "The original curve may be consumed by conversion; no post-conversion curve readback is required.",
    "Reuse without reset is outside required live validation and remains unvalidated.",
    "CST messages may be inherited, repeated or truncated; they do not establish command causality.",
]


def parse_native_bool(value: str) -> bool:
    """Decode CST's textual or numeric VBA Boolean output; reject unknown values."""
    normalized = value.strip().lower()
    if normalized in {"true", "-1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise StopTest(f"Unexpected native Boolean value: {value!r}")


def interpret_area_readback(payload: dict) -> dict:
    """Keep raw shape area separate from unverified one-sided planar face area."""
    result = {
        "expected_planar_area": 24,
        "measured_planar_area": None,
        "native_shape_area": None,
        "native_area_status": "unavailable",
        "measurement_status": "pending manual inspection",
        "normalization_applied": False,
        "native_to_expected_planar_area_ratio": None,
        "area_interpretation": "unconfirmed sheet-area counting convention",
        "pending_reason": "Solid.GetArea's sheet counting convention has not been established",
    }
    if payload.get("status") != "ok":
        result["native_area_reason"] = "Native area query unavailable; see full MCP response"
        return result
    if TIMEOUT_RE.search(payload.get("output", "")):
        raise UnknownState("Native area query reported a timeout; execution state unknown")
    try:
        values = parse_records(
            payload.get("output", ""), {"AREA", "AREA_ERROR_NUMBER", "AREA_ERROR_DESCRIPTION"}
        )
        result["native_records"] = values
        error_number = int(values["AREA_ERROR_NUMBER"])
    except (StopTest, ValueError) as exc:
        result.update(native_area_status="invalid output", native_area_reason=str(exc))
        return result
    if error_number != 0:
        result.update(
            native_area_status="native error",
            native_area_reason="Native Solid.GetArea reported an error for this sheet",
        )
        return result
    try:
        actual = number(values["AREA"])
    except (StopTest, ValueError) as exc:
        result.update(native_area_status="invalid output", native_area_reason=str(exc))
        return result
    result["native_shape_area"] = actual
    if actual <= 0:
        result.update(
            native_area_status="nonpositive", native_area_reason="Native shape area was nonpositive"
        )
        return result
    result["native_area_status"] = "available"
    result["native_to_expected_planar_area_ratio"] = actual / result["expected_planar_area"]
    if close_number(actual, 2 * result["expected_planar_area"]):
        result["area_interpretation"] = (
            "consistent with counting both sides; unconfirmed hypothesis"
        )
    return result


def compare_native_area_persistence(before: dict, after: dict) -> dict:
    """Compare like raw quantities without asserting their sheet-area semantics."""
    result = {
        "before": before["native_shape_area"],
        "after": after["native_shape_area"],
        "units": "mm^2",
        "relative_tolerance": 1e-6,
        "absolute_tolerance": 1e-6,
        "passed": None,
        "status": "pending: raw area unavailable at one or both stages",
        "one_sided_area_verified": False,
    }
    if before["native_area_status"] == after["native_area_status"] == "available":
        result["passed"] = close_number(result["before"], result["after"])
        result["status"] = "matched" if result["passed"] else "changed"
    return result


class WorkspaceLock:
    """Retained file with an invocation-scoped, nonblocking OS lock."""

    def __enter__(self):
        reject_links(WORK)
        WORK.mkdir(parents=True, exist_ok=True)
        target = WORK / "workspace.lock"
        reject_links(target)
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
            raise StopTest(
                "Face-from-curves workspace is already in use; wait for that invocation"
            ) from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class FaceTest:
    def __init__(self, options):
        self.options = options
        self.invocation = uuid.uuid4().hex
        self.sequence = 0
        self.phase = "preparation"
        self.unknown = False
        self.connected = False
        self.live_attempted = False
        self.exit_code = 1
        self.reason = "Not completed"
        self.checks = []
        self.catalog = {}
        self.results = []
        self.measurements = []
        self.actual_units = None
        self.message_seen = set()
        self.message_baseline_taken = False
        self.manifest = None
        self.project = WORK / "project.cst"
        reject_links(WORK)
        for name in (
            "mcp_calls.jsonl",
            "cst_messages.jsonl",
            "server_stderr.log",
            "metadata.jsonl",
            "metadata.json",
            "summary.json",
            "summary.md",
            "workspace.json",
            "tool_catalog.json",
        ):
            reject_links(WORK / name)
            reject_links((WORK / name).with_suffix(Path(name).suffix + ".tmp"))
        self.calls = (WORK / "mcp_calls.jsonl").open("a", encoding="utf-8")
        self.messages = (WORK / "cst_messages.jsonl").open("a", encoding="utf-8")
        self.stderr = (WORK / "server_stderr.log").open("a", encoding="utf-8")
        self.stderr.write(json.dumps(self.tag({"event": "stderr_start"})) + "\n")
        self.stderr.flush()
        self.env = dict(os.environ)
        libs = Path(options.cst_path) / "AMD64" / "python_cst_libraries"
        self.env.update(
            CST_CONNECT_MODE="disabled" if options.preflight else "manual",
            CST_VERSION="2025",
            CST_PATH=options.cst_path,
            CST_WORK_DIR=str(WORK),
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
            "workspace": str(WORK),
            "project": str(self.project),
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
            "fixed_vba": sorted(FIXED_VBA),
            "fixtures": FIXTURES,
            "expected_geometry": {
                "width_mm": 6,
                "height_mm": 4,
                "z_mm": 2,
                "one_sided_planar_face_area_mm2": 24,
            },
            "references": {},
            "limitations": LIMITATIONS,
        }
        self.metadata_event("invocation_start")

    def generated_paths(self):
        """Accept only same-stem files and the companion, all within this scope."""
        names = self.manifest.get("generated_paths")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise StopTest("Invalid face-from-curves generated paths")
        if not {"project.cst", "project"} <= set(names) or len(names) != len(set(names)):
            raise StopTest("Incomplete/duplicate face-from-curves generated paths")
        paths = []
        for name in names:
            if (
                Path(name).name != name
                or not (name == "project" or name.startswith("project."))
                or Path(name).suffix.lower() in LOCK_SUFFIXES
            ):
                raise StopTest(f"Unsafe generated path: {name!r}")
            path = WORK / name
            reject_links(path)
            if path.resolve().parent != WORK.resolve():
                raise StopTest(f"Generated path escapes face-from-curves workspace: {path}")
            if path.exists() and ((name == "project") != path.is_dir()):
                raise StopTest(f"Unexpected generated path type: {path}")
            paths.append(path)
        return paths

    def project_snapshot(self):
        return snapshot([(p, p.name) for p in self.generated_paths() if p.exists()])

    def verify_manifest(self):
        if (
            self.manifest.get("owner") != OWNER
            or self.manifest.get("version") != 1
            or self.manifest.get("project") != str(self.project)
            or self.manifest.get("generation_state") not in {"creating", "running", "ready"}
            or self.manifest.get("fixture") not in {"absent", "creating", "ready"}
            or not isinstance(self.manifest.get("creation_requested"), bool)
            or self.manifest.get("created_path", str(self.project)) != str(self.project)
        ):
            raise StopTest("Face-from-curves ownership/state inconsistent; no automatic deletion")
        self.generated_paths()
        owned = set(self.manifest["generated_paths"])
        unexpected = [p.name for p in WORK.glob("project*") if p.name not in owned]
        if unexpected:
            raise StopTest(f"Unidentified project paths; refusing reuse/reset: {unexpected}")

    def prepare_project(self):
        """Local ownership, lock and checkpoint guards before any CST connection."""
        path = WORK / "workspace.json"
        if path.exists():
            self.manifest = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.manifest, dict):
                raise StopTest("Invalid face-from-curves manifest")
            self.verify_manifest()
            ensure_closed(self.project)
            if self.options.reset:
                paths = self.generated_paths()  # Check every target before the first deletion.
                if any(p.exists() for p in paths) and not self.manifest["creation_requested"]:
                    raise StopTest(
                        "Project files appeared before client creation; ownership unverified"
                    )
                self.metadata_event("reset_start", prior_manifest=self.manifest)
                for target in paths:
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink(missing_ok=True)
                self.manifest = None
            else:
                if (
                    self.manifest["generation_state"] != "ready"
                    or self.manifest["fixture"] != "ready"
                    or not self.project.is_file()
                    or not self.project.with_suffix("").is_dir()
                    or sha256(self.project) != self.manifest.get("saved_sha256")
                    or self.project_snapshot() != self.manifest.get("saved_files")
                ):
                    raise StopTest(
                        "Face-from-curves project changed or incomplete since checkpoint; inspect and "
                        "restore it or explicitly --reset after saving/closing. No automatic adoption."
                    )
                self.metadata_event("workspace_reused", checkpoint=self.manifest)
                return
        elif any(WORK.glob("project*")):
            raise StopTest(
                "Project paths exist without face-from-curves ownership; refusing create/reset"
            )
        self.manifest = {
            "owner": OWNER,
            "version": 1,
            "project": str(self.project),
            "generation_state": "creating",
            "fixture": "absent",
            "generated_paths": ["project.cst", "project"],
            "creation_invocation": self.invocation,
            "created_at": timestamp(),
            "creation_requested": False,
            "source": "new blank MWS project via cst_create_project; no source copy",
        }
        self.store_manifest()
        self.metadata_event("blank_workspace_reserved", manifest=self.manifest)

    def tag(self, record):
        return dict(
            record,
            invocation=self.invocation,
            timestamp=timestamp(),
            sequence=self.sequence,
            phase=self.phase,
        )

    def event(self, record, *, messages=False):
        stream = self.messages if messages else self.calls
        stream.write(
            json.dumps(serialize(self.tag(record)), ensure_ascii=False, allow_nan=False) + "\n"
        )
        stream.flush()

    def metadata_event(self, event, **details):
        with (WORK / "metadata.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    serialize(self.tag(dict(event=event, **details, metadata=self.metadata)))
                )
                + "\n"
            )
        write_json(WORK / "metadata.json", self.metadata)

    def check(self, scope, passed, **evidence):
        record = self.tag(dict(scope=scope, passed=bool(passed), **evidence))
        self.checks.append(record)
        self.event(dict(event="check", **record))
        if not passed:
            raise StopTest(f"{self.phase}: {scope} failed; inspect reports and owned project")

    def store_manifest(self):
        write_json(WORK / "workspace.json", self.manifest)

    def validate(self, name, arguments):
        if name not in self.catalog:
            raise StopTest(f"Required tool missing from effective MCP catalog: {name}")
        schema = self.catalog[name]["inputSchema"]
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(arguments)

    async def request(self, session, name, arguments=None, *, protocol=False, negative=False):
        if self.unknown:
            raise UnknownState("Further MCP requests forbidden after indeterminate execution")
        arguments = arguments or {}
        if not protocol and not negative:
            self.validate(name, arguments)
        if name == "cst_execute_vba" and arguments.get("code") not in FIXED_VBA:
            raise StopTest("Client raw VBA is restricted to fixed setup/read-only blocks")
        self.sequence += 1
        timeout = (
            self.options.connection_timeout if name == "cst_connect" else self.options.call_timeout
        )
        base = {"tool": name, "arguments": arguments, "timeout_seconds": timeout}
        self.event(dict(event="request_start", **base))
        start = time.monotonic()
        try:
            if protocol:
                response = await asyncio.wait_for(getattr(session, name)(**arguments), timeout)
            else:
                response = await asyncio.wait_for(session.call_tool(name, arguments), timeout)
        except BaseException as exc:
            logger.exception("MCP request interrupted; execution state is unknown")
            self.unknown = True
            self.event(
                dict(
                    event="request_exception",
                    **base,
                    duration_seconds=time.monotonic() - start,
                    exception={"type": type(exc).__name__, "message": str(exc)},
                    response=None,
                    isError=None,
                    execution_state="unknown",
                )
            )
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
                raise
            raise UnknownState(
                f"{self.phase}/{name}: timeout/transport loss; no further CST calls"
            ) from exc
        raw = serialize(response)
        decoded = interpret(raw)
        unknown = not protocol and indeterminate(raw, decoded)
        if unknown:
            self.unknown = True
        record = dict(
            event="request_complete",
            **base,
            response=raw,
            **decoded,
            duration_seconds=time.monotonic() - start,
            exception=None,
            execution_state="unknown" if unknown else "response_received",
            evidence="protocol"
            if protocol
            else ("offline" if self.options.preflight else "real MCP response"),
        )
        self.event(record)
        self.results.append(self.tag(record))
        for item in walk_dicts(decoded["parsed_payloads"]):
            if "cst_messages_tail" in item:
                self.event(
                    {
                        "event": "inline_messages",
                        "payload": item,
                        "attribution": "May be inherited/repeated; no command attribution",
                    },
                    messages=True,
                )
        if name == "cst_read_project_log":
            self.event(
                dict(
                    record,
                    event="message_checkpoint",
                    attribution="Context only; may include repeated/inherited messages",
                ),
                messages=True,
            )
        print(f"{self.sequence:03d} {self.phase}: {name}", flush=True)
        if unknown:
            raise UnknownState(
                f"{self.phase}/{name}: server timeout/unknown native execution state"
            )
        if protocol:
            return raw
        payload = decoded["payload"]
        errors = [
            item
            for item in walk_dicts(decoded["parsed_payloads"])
            if item.get("status") in {"error", "timeout", "busy"}
        ]
        if negative:
            self.check(
                "invalid input rejected over real MCP",
                bool(decoded["isError"] or errors),
                tool=name,
                response=raw,
            )
            return payload
        if not isinstance(payload, dict) and not decoded["isError"] and not errors:
            self.unknown = True
            raise UnknownState(f"{self.phase}/{name}: ambiguous response; native outcome unknown")
        if decoded["isError"] or errors or not isinstance(payload, dict):
            if name == "cst_execute_vba" and arguments.get("code") == AREA_QUERY:
                # Only a known, read-only area failure is optional. Unknown outcomes
                # have already stopped all subsequent calls above.
                return {
                    "status": "unavailable",
                    "native_payload": payload,
                    "isError": decoded["isError"],
                }
            raise StopTest(f"{self.phase}/{name}: rejected or ambiguous response; see MCP log")
        if not self.options.preflight and payload.get("status") == "offline":
            raise StopTest(f"{self.phase}/{name}: offline response is not real CST execution")
        return payload

    async def accepted(self, session, name, arguments=None, *, status):
        payload = await self.request(session, name, arguments)
        self.check(
            f"{name} command acceptance",
            payload.get("status") == status,
            expected_status=status,
            payload=payload,
        )
        return payload

    async def owned_info(self, session):
        info = await self.request(session, "cst_project_info")
        self.check(
            "active project is owned face-from-curves project",
            info.get("mode") == "connected"
            and info.get("project_open") is True
            and Path(info.get("project_path") or "").resolve() == self.project.resolve(),
            payload=info,
        )
        state = await self.request(session, "cst_connection_status")
        self.check(
            "owned project idle",
            state.get("mode") == "connected"
            and state.get("project_open") is True
            and state.get("solver_running") is False,
            payload=state,
        )

    async def shapes(self, session):
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": SHAPES_QUERY}, status="ok"
        )
        return parse_shapes(payload.get("output", ""))

    async def parameters(self, session):
        # Blank-project confirmation only; this fixture has no model parameters.
        payload = await self.accepted(session, "cst_list_parameters", status="ok")
        return payload.get("output", "").strip()

    async def run(self):
        try:
            if sys.version_info[:2] != (3, 12):
                raise StopTest("Python 3.12 required; run using uv and the inline script metadata")
            if not self.options.preflight:
                self.prepare_project()
            params = StdioServerParameters(
                command=sys.executable, args=["-m", "cst_mcp.server"], cwd=str(ROOT), env=self.env
            )
            with preserve_cst_processes():
                async with (
                    stdio_client(params, errlog=self.stderr) as (read, write),
                    ClientSession(read, write) as session,
                ):
                    try:
                        await self.catalog_and_preflight(session)
                        if not self.options.preflight:
                            await self.live(session)
                    except (StopTest, ValueError):
                        # Only known failures get a diagnostic checkpoint. No save,
                        # close, reset or mutation recovery is attempted on failure.
                        if self.connected and not self.unknown:
                            await self.messages_at(session)
                        raise
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.exit_code = 130
            self.reason = "Interrupted; no CST cleanup calls; project and logs preserved"
        except BaseException as exc:
            logger.exception("Face-from-curves client stopped; finalizing local reports")
            leaves = list(exception_leaves(exc))
            interrupted = any(
                isinstance(item, (KeyboardInterrupt, asyncio.CancelledError)) for item in leaves
            )
            cause = next((item for item in leaves if isinstance(item, StopTest)), leaves[0])
            self.exit_code = 130 if interrupted else (2 if self.unknown else 1)
            self.reason = f"{self.phase}: {type(cause).__name__}: {cause}"
            if self.unknown:
                self.reason += (
                    "; no further calls, retries, save, close, query, reset or CST termination"
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
        print(f"{self.reason}\nReports: {WORK}\nExit: {self.exit_code}", flush=True)
        return self.exit_code

    async def catalog_and_preflight(self, session):
        self.phase = "catalog"
        await self.request(session, "initialize", protocol=True)
        cursor = None
        seen = set()
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
        write_json(WORK / "tool_catalog.json", list(self.catalog.values()))
        for name, rel in REFERENCES.items():
            path = Path(self.options.cst_path) / rel
            if not path.is_file():
                raise StopTest(f"Installed reference unavailable; inspect before live use: {path}")
            self.metadata["references"][name] = {"path": str(path), "sha256": sha256(path)}
        self.metadata_event("reference_provenance")
        plan = list(FIXTURES) + [
            ("cst_connect", {"mode": "new"}),
            ("cst_create_project", {"path": str(self.project), "project_type": "MWS"}),
            ("cst_open_project", {"path": str(self.project)}),
        ]
        plan += [
            (name, {})
            for name in (
                "cst_project_info",
                "cst_connection_status",
                "cst_read_project_log",
                "cst_list_parameters",
                "cst_save_project",
                "cst_close_project",
                "cst_disconnect",
            )
        ]
        plan += [("cst_execute_vba", {"code": code}) for code in sorted(FIXED_VBA)]
        for name, args in plan:
            self.validate(name, args)
        references = self.catalog["cst_create_face_from_curves"]["inputSchema"]["properties"][
            "curve_names"
        ]
        self.check(
            "single-reference catalog restriction",
            references.get("minItems") == references.get("maxItems") == 1,
        )
        self.check(
            "catalog presence and effective planned schemas",
            True,
            tools=sorted({name for name, _ in plan}),
        )
        state = await self.request(session, "cst_connection_status")
        self.check(
            "startup disconnected without project",
            state.get("mode") == "offline" and state.get("project_open") is False,
            payload=state,
        )
        if not self.options.preflight:
            return
        self.phase = "offline_preflight"
        polygon = await self.accepted(
            session, "cst_create_polygon3d", POLYGON_ARGS, status="offline"
        )
        code = polygon.get("vba", "")
        points = ['  .Point "' + '", "'.join(map(str, p)) + '"' for p in POLYGON_ARGS["points"]]
        self.check(
            "offline closed rectangular Polygon3D sequence",
            code.splitlines()
            == [
                "With Polygon3D",
                "  .Reset",
                '  .Name "Rectangle"',
                '  .Curve "Curves"',
                *points,
                "  .Create",
                "End With",
            ],
            vba=code,
            executed_in_cst=False,
        )
        face = await self.accepted(
            session, "cst_create_face_from_curves", FACE_ARGS, status="offline"
        )
        code = face.get("vba", "")
        self.check(
            "offline documented CoverCurve sequence",
            code.splitlines()
            == [
                "With CoverCurve",
                "  .Reset",
                '  .Name "RectangleSheet"',
                '  .Component "FaceFromCurves"',
                '  .Curve "Curves:Rectangle"',
                "  .Create",
                "End With",
            ]
            and ".AddCurve" not in code,
            vba=code,
            executed_in_cst=False,
        )
        for references in (
            ["Rectangle"],
            [":Rectangle"],
            ["Curves:"],
            ["Curves:Rectangle:Other"],
            ["Bad/Group:Rectangle"],
            ["Curves:Rectangle", "Curves:Other"],
        ):
            await self.request(
                session,
                "cst_create_face_from_curves",
                dict(FACE_ARGS, curve_names=references),
                negative=True,
            )
        # Retrieve fixed blocks through the disabled server, without executing setup or queries.
        for query in sorted(FIXED_VBA):
            payload = await self.accepted(
                session, "cst_execute_vba", {"code": query}, status="offline"
            )
            self.check(
                "offline fixed setup/query preserved",
                payload.get("vba") == query,
                query=query,
                executed_in_cst=False,
            )
        self.exit_code = 0
        self.reason = (
            "Real MCP catalog/schema and offline VBA checks passed; live CST validation pending"
        )

    async def messages_at(self, session):
        payload = await self.request(session, "cst_read_project_log")
        # Preserve full payloads. Content novelty does not establish command causality.
        entries = payload.get("messages", payload.get("tail", payload.get("cst_messages_tail", [])))
        if isinstance(entries, str):
            entries = entries.splitlines()
        entries = entries if isinstance(entries, list) else [entries]
        new, repeated = [], []
        baseline = not self.message_baseline_taken
        self.message_baseline_taken = True
        for entry in entries:
            key = json.dumps(serialize(entry), sort_keys=True, ensure_ascii=False)
            (repeated if key in self.message_seen else new).append(entry)
            self.message_seen.add(key)
        self.event(
            {
                "event": "message_comparison",
                "payload": payload,
                "baseline_or_inherited": baseline,
                "newly_observed": new,
                "repeated": repeated,
                "attribution": "Newly observed content is not proof of a new error or command causality; server tails may be truncated.",
            },
            messages=True,
        )

    async def units(self, session):
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": UNITS_QUERY}, status="ok"
        )
        self.actual_units = parse_units(payload.get("output", ""))
        self.check(
            "effective units readback",
            self.actual_units == EXPECTED_UNITS,
            expected=EXPECTED_UNITS,
            actual=self.actual_units,
            query=UNITS_QUERY,
        )

    async def closure(self, session):
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": CURVE_QUERY}, status="ok"
        )
        records = parse_records(payload.get("output", ""), {"CLOSED"})
        self.check(
            "native profile closed before conversion",
            parse_native_bool(records["CLOSED"]),
            native=records,
            query=CURVE_QUERY,
            query_source=self.metadata["references"]["curve"],
        )

    async def measure(self, session):
        await self.owned_info(session)
        await self.units(session)
        shapes = await self.shapes(session)
        self.check(
            "named sheet and default material readback",
            shapes == {SHAPE: "Vacuum"},
            actual=shapes,
            query=SHAPES_QUERY,
            query_source=self.metadata["references"]["solid"],
        )
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": SHEET_QUERY}, status="ok"
        )
        records = parse_records(payload.get("output", ""), {"EXISTS", "IS_SOLID", "FACE_ID"})
        self.check(
            "native named sheet existence and face presence",
            parse_native_bool(records["EXISTS"])
            and not parse_native_bool(records["IS_SOLID"])
            and int(records["FACE_ID"]) >= 0,
            native=records,
            query=SHEET_QUERY,
            query_source=self.metadata["references"]["solid"],
        )
        area = await self.request(session, "cst_execute_vba", {"code": AREA_QUERY})
        measurement = self.tag(
            {
                "shape": SHAPE,
                "expected_planar_area": 24,
                "measured_planar_area": None,
                "native_shape_area": None,
                "measurement_status": "pending manual inspection",
                "units": "mm^2",
                "actual_project_units": self.actual_units,
                "native_response": area,
                "query": AREA_QUERY,
                "query_source": self.metadata["references"]["solid"],
                "reference_scope": "GetArea documents shape surface area; the sheet counting convention is unconfirmed",
                "relative_tolerance": 1e-6,
                "absolute_tolerance": 1e-6,
                "prediction": {"width_mm": 6, "height_mm": 4, "z_mm": 2},
                "prediction_source": "fixed closed rectangular profile, analytic area = width * height",
            }
        )
        previous = self.measurements[0] if self.measurements else None
        self.measurements.append(measurement)
        try:
            measurement.update(interpret_area_readback(area))
        except UnknownState:
            self.unknown = True
            measurement.update(
                native_area_status="unknown",
                pending_reason="Native area query reported a timeout; no subsequent MCP calls",
            )
            self.event(dict(event="measurement", **measurement))
            raise
        persistence = None
        if self.phase == "reopened_persistence" and previous is not None:
            persistence = compare_native_area_persistence(previous, measurement)
            measurement["native_shape_area_persistence"] = persistence
        self.event(dict(event="measurement", **measurement))
        if persistence is not None and persistence["passed"] is not None:
            self.check(
                "native raw shape area persistence",
                persistence["passed"],
                comparison=persistence,
                query=AREA_QUERY,
                query_source=self.metadata["references"]["solid"],
            )
        await self.messages_at(session)

    async def checkpoint(self, session):
        await self.owned_info(session)
        payload = await self.accepted(session, "cst_save_project", status="saved")
        self.check(
            "save returned owned face-from-curves path",
            Path(payload.get("path") or "").resolve() == self.project.resolve(),
            payload=payload,
        )
        await self.messages_at(session)
        await self.accepted(session, "cst_close_project", status="closed")
        state = await self.request(session, "cst_connection_status")
        self.check(
            "owned face-from-curves project closed",
            state.get("project_open") is False,
            payload=state,
        )
        ensure_closed(self.project)
        self.check(
            "saved project and companion exist",
            self.project.is_file() and self.project.with_suffix("").is_dir(),
        )
        sidecars = [p.name for p in WORK.glob("project.*") if p.is_file()]
        self.manifest["generated_paths"] = ["project", *sorted(sidecars)]
        self.verify_manifest()
        self.manifest.update(
            generation_state="ready",
            fixture="ready",
            saved_sha256=sha256(self.project),
            saved_files=self.project_snapshot(),
            checkpoint_invocation=self.invocation,
            checkpoint_phase=self.phase,
        )
        self.store_manifest()
        self.metadata_event("saved_checkpoint", checkpoint=self.manifest)

    async def live(self, session):
        self.phase = "connect_isolated"
        self.live_attempted = True
        connected = await self.accepted(session, "cst_connect", {"mode": "new"}, status="connected")
        self.check(
            "isolated new CST instance without adopted projects",
            connected.get("newly_started") is True
            and connected.get("mode") == "new"
            and connected.get("open_projects") == 0
            and connected.get("open_project_paths") == []
            and not connected.get("project_path"),
            payload=connected,
        )
        self.connected = True
        fresh = self.manifest["fixture"] == "absent"
        self.manifest["generation_state"] = "running"
        self.store_manifest()
        self.phase = "create_blank_project" if fresh else "open_owned_project"
        if fresh:
            if any(WORK.glob("project*")):
                raise StopTest("Project path appeared after reservation; refusing creation")
            self.manifest["creation_requested"] = True
            self.store_manifest()
            created = await self.accepted(
                session,
                "cst_create_project",
                {"path": str(self.project), "project_type": "MWS"},
                status="created",
            )
            self.manifest["created_path"] = created.get("path")
            self.store_manifest()
            self.check(
                "creation returned reserved project path",
                Path(created.get("path") or "").resolve() == self.project.resolve(),
                payload=created,
            )
        else:
            await self.accepted(
                session, "cst_open_project", {"path": str(self.project)}, status="opened"
            )
        await self.owned_info(session)
        await self.messages_at(session)
        if fresh:
            shapes = await self.shapes(session)
            parameters = await self.parameters(session)
            self.check(
                "new blank project before fixture setup",
                not shapes and not parameters,
                shapes=shapes,
                parameter_output=parameters,
            )
            self.phase = "setup_and_closed_profile"
            self.manifest["fixture"] = "creating"
            self.store_manifest()
            await self.accepted(
                session, "cst_execute_vba", {"code": UNITS_BLOCK}, status="executed"
            )
            await self.units(session)
            await self.accepted(session, "cst_execute_vba", {"code": SETUP}, status="executed")
            await self.accepted(session, "cst_create_polygon3d", POLYGON_ARGS, status="executed")
            await self.closure(session)
            self.phase = "convert_to_sheet"
            await self.accepted(
                session, "cst_create_face_from_curves", FACE_ARGS, status="executed"
            )
        self.phase = "sheet_readback"
        await self.measure(session)
        await self.checkpoint(session)
        self.phase = "reopened_persistence"
        self.check(
            "saved checkpoint unchanged before reopen",
            sha256(self.project) == self.manifest["saved_sha256"]
            and self.project_snapshot() == self.manifest["saved_files"],
        )
        self.manifest["generation_state"] = "running"
        self.store_manifest()
        await self.accepted(
            session, "cst_open_project", {"path": str(self.project)}, status="opened"
        )
        await self.measure(session)
        await self.checkpoint(session)
        self.phase = "disconnect"
        await self.accepted(session, "cst_disconnect", status="disconnected")
        self.connected = False
        self.exit_code = 0
        pending = any(m["measured_planar_area"] is None for m in self.measurements)
        self.reason = "Live sheet scenario completed within recorded verification coverage"
        if pending:
            self.reason += "; one-sided face area remains pending manual inspection"

    def finalize(self):
        summary = {
            "invocation": self.invocation,
            "phase": self.phase,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "indeterminate": self.unknown,
            "project": str(self.project),
            "catalog_presence": sorted(self.catalog),
            "preflight": self.options.preflight,
            "implementation": "Single qualified CoverCurve reference creates one planar sheet/face; native queries stay outside history.",
            "offline_preflight_passed": self.exit_code == 0 if self.options.preflight else None,
            "real_cst_execution_attempted": self.live_attempted,
            "scenario_completed": not self.options.preflight and self.exit_code == 0,
            "independently_verified_properties": [
                c
                for c in self.checks
                if c["passed"]
                and not self.options.preflight
                and c["scope"].startswith(
                    (
                        "native ",
                        "effective units ",
                        "named sheet ",
                    )
                )
            ],
            "measurements": self.measurements,
            "area_measurement_status": "live validation pending"
            if self.options.preflight
            else [m["measurement_status"] for m in self.measurements],
            "limitations": LIMITATIONS,
            "checks": self.checks,
            "responses": self.results,
            "references": self.metadata["references"],
        }
        write_json(WORK / "summary.json", summary)
        lines = [
            "# Latest face-from-curves invocation",
            "",
            f"Invocation: `{self.invocation}`",
            f"Phase: `{self.phase}`; exit: {self.exit_code}",
            "",
            self.reason,
            "",
            f"Project target: `{self.project}`",
            "",
            "Offline checks are not live CST validation. Raw shape areas and expected",
            "one-sided face area are separate quantities. Pending measurements, persistence",
            "checks, native responses, units and provenance are in summary.json.",
            "",
            "| Stage | Check | Passed |",
            "| --- | --- | --- |",
        ]
        lines.extend(f"| {c['phase']} | {c['scope']} | {c['passed']} |" for c in self.checks)
        lines += [
            "",
            *LIMITATIONS,
            "",
            "After timeout/loss, inspect CST manually. No cleanup calls were attempted.",
            "",
        ]
        (WORK / "summary.md").write_text("\n".join(lines), encoding="utf-8")
        self.metadata.update(
            finished=timestamp(),
            exit_code=self.exit_code,
            reason=self.reason,
            indeterminate=self.unknown,
        )
        self.metadata_event("invocation_end")
        self.stderr.write(
            json.dumps(self.tag({"event": "stderr_end", "exit_code": self.exit_code})) + "\n"
        )
        for stream in (self.calls, self.messages, self.stderr):
            stream.flush()
            stream.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Real MCP schemas/offline generation; CST access disabled",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Recreate verified owned face-from-curves project only; retain logs and notes",
    )
    parser.add_argument("--cst-path", default=DEFAULT_CST_PATH)
    parser.add_argument("--connection-timeout", type=positive_timeout, default=120)
    parser.add_argument("--call-timeout", type=positive_timeout, default=60)
    options = parser.parse_args()
    if options.preflight and options.reset:
        parser.error("--reset cannot be combined with --preflight")
    try:
        with WorkspaceLock():
            return asyncio.run(FaceTest(options).run())
    except StopTest as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
