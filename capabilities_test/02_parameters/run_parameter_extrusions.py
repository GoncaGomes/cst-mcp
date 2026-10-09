# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mcp>=1.29,<3", "jsonschema>=4.20"]
# ///
"""Deterministic extrusion expression validation through a real MCP stdio session."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
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
    parse_parameter_output,
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
from run_batch import UNITS_BLOCK
from run_parameter_primitives import parse_records

ROOT = Path(__file__).resolve().parents[2]
BATCH = Path(__file__).resolve().parent
WORK = BATCH / "artifacts" / "03_extrusions"
OWNER = "cst-mcp-parameter-extrusions-v1"
TOOLSETS = "connection,project,geometry,parameters,diagnostics,vba"
PARAMETERS = ("PEx_W", "PEx_H", "PEx_Offset", "PEx_Side")
STATES = {"initial": (8, 3, 2, 1), "updated": (10, 4, 5, -1), "final": (6, 2, 1, 1)}
COMPONENT = "ParameterExtrusions"
COMMON = {"component": COMPONENT, "material": "PEC", "height": "PEx_H"}
FIXTURES = (
    (
        "cst_create_extrude",
        dict(
            COMMON,
            name="PointZUp",
            axis="z",
            z_offset="PEx_Offset",
            extrude_direction="up",
            points=[[0, 0], [0, 4], ["PEx_W", 4], ["PEx_W", 0]],
            holes=[[["PEx_W/4", 1], ["3*PEx_W/4", 1], ["3*PEx_W/4", 3], ["PEx_W/4", 3]]],
        ),
    ),
    (
        "cst_create_extrude",
        dict(
            COMMON,
            name="PointXDown",
            axis="x",
            x_offset="PEx_Offset",
            extrude_direction="down",
            points=[[30, 0], ["30+PEx_W", 0], ["30+PEx_W", 4], [30, 4]],
        ),
    ),
    (
        "cst_create_polygon_extrude",
        dict(
            COMMON,
            name="PolygonYUp",
            axis="y",
            y_offset="PEx_Offset",
            extrude_direction="up",
            points=[[60, 0], [60, 4], ["60+PEx_Side*PEx_W", 4], ["60+PEx_Side*PEx_W", 0]],
            holes=[
                [
                    ["60+PEx_Side*PEx_W/4", 1],
                    ["60+3*PEx_Side*PEx_W/4", 1],
                    ["60+3*PEx_Side*PEx_W/4", 3],
                    ["60+PEx_Side*PEx_W/4", 3],
                ]
            ],
        ),
    ),
    (
        "cst_create_polygon_extrude",
        dict(
            COMMON,
            name="PolygonZDown",
            axis="z",
            z_offset="PEx_Offset",
            extrude_direction="down",
            points=[[90, 0], ["90+PEx_W", 0], ["90+PEx_W", 4], [90, 4]],
        ),
    ),
)
SOLIDS = {f"{COMPONENT}:{args['name']}": "PEC" for _, args in FIXTURES}
SETUP = f'Component.New "{COMPONENT}"'
MEASURE_QUERY = (
    "\n".join(
        f'Debug.Print "{args["name"]}.{kind}" & vbTab & '
        f'CStr(Solid.Get{method}("{COMPONENT}:{args["name"]}"))'
        for _, args in FIXTURES
        for kind, method in (("VOLUME", "Volume"), ("AREA", "Area"))
    )
    + '\nDebug.Print "DONE"'
)
FIXED_VBA = frozenset({UNITS_BLOCK, SETUP, UNITS_QUERY, SHAPES_QUERY, MEASURE_QUERY})
HELP_ROOT = "Online Help/mergedProjects/VBA_3D"
REFERENCES = {
    "solid": f"{HELP_ROOT}/common_vbasolido/common_vbasolido_solid_object.htm",
    "extrude": f"{HELP_ROOT}/common_vbaextrude/common_vbaextrudeextrude_object.htm",
    "extrudecurve": f"{HELP_ROOT}/common_vbacurves/common_vbacurves_extrudecurve_object.htm",
    "polygon3d": f"{HELP_ROOT}/common_vbacurves/common_vbacurves_polygon3d.htm",
    "evaluate": f"{HELP_ROOT}/common_vbaapp/common_vbaappapplication_object.htm",
}
LIMITATIONS = [
    "Volume and surface area independently test only those aggregate quantities, including hole effects.",
    "They do not prove offsets, direction, every coordinate, winding choice or history-expression association.",
    "Input profile and axis ranges are predictions, not measured bounds. No loose bounding box is used as exact dimensions.",
    "Native evaluation, reconstruction and persistence require live CST validation; native dialogs may expose evaluated values.",
    "Inspect stored history expressions and profile/base-plane/direction in the saved project as described in README.md.",
]


def profile_contracts(values):
    """Known fixture geometry only, never an arbitrary expression evaluator."""
    width, height, offset, side = values
    result = {}
    for _, args in FIXTURES:
        holed = bool(args.get("holes"))
        result[args["name"]] = {
            "profile_area": 3 * width if holed else 4 * width,
            "profile_boundary_length": 3 * width + 12 if holed else 2 * width + 8,
            "height": height,
            "axis": args["axis"],
            "direction": args["extrude_direction"],
            "predicted_axis_range": [offset, offset + height]
            if args["extrude_direction"] == "up"
            else [offset - height, offset],
            "prediction_source": "known fixture contract, not a native bounds measurement",
            "signed_width": side * width if args["name"] == "PolygonYUp" else width,
        }
    return result


def analytic_expected(values):
    """Prism V=A*h and S=2*A+boundary_length*h, including internal walls."""
    result = {}
    for name, profile in profile_contracts(values).items():
        area, boundary, height = (
            profile[key] for key in ("profile_area", "profile_boundary_length", "height")
        )
        result[f"{name}.VOLUME"] = area * height
        result[f"{name}.AREA"] = 2 * area + boundary * height
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
                "Extrusion workspace is already in use; wait for that invocation"
            ) from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class ExtrusionTest:
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
            "states": STATES,
            "references": {},
            "limitations": LIMITATIONS,
        }
        self.metadata_event("invocation_start")

    def generated_paths(self):
        """Accept only same-stem files and the companion, all within this scope."""
        names = self.manifest.get("generated_paths")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise StopTest("Invalid extrusion generated paths")
        if not {"project.cst", "project"} <= set(names) or len(names) != len(set(names)):
            raise StopTest("Incomplete/duplicate extrusion generated paths")
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
                raise StopTest(f"Generated path escapes extrusion workspace: {path}")
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
            raise StopTest("Extrusion ownership/state inconsistent; no automatic deletion")
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
                raise StopTest("Invalid extrusion manifest")
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
                        "Extrusion project changed or incomplete since checkpoint; inspect and "
                        "restore it or explicitly --reset after saving/closing. No automatic adoption."
                    )
                self.metadata_event("workspace_reused", checkpoint=self.manifest)
                return
        elif any(WORK.glob("project*")):
            raise StopTest("Project paths exist without extrusion ownership; refusing create/reset")
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
            "active project is owned extrusion project",
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

    async def parameters(self, session, expected=None):
        payload = await self.accepted(session, "cst_list_parameters", status="ok")
        values = parse_parameter_output(payload.get("output", ""))
        if expected:
            for name, value in expected.items():
                self.check(
                    f"actual parameter {name}",
                    name in values and close_number(values[name], value),
                    expected=value,
                    actual=values.get(name),
                    source="cst_list_parameters output",
                )
                got = await self.accepted(session, "cst_get_parameter", {"name": name}, status="ok")
                individual = parse_parameter_output(got.get("output", ""), prefix="Parameter ")
                self.check(
                    f"individual parameter readback {name}",
                    name in individual and close_number(individual[name], value),
                    expected=value,
                    actual=individual.get(name),
                    source="cst_get_parameter output",
                )
        return values

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
        except BaseException as exc:  # noqa: BLE001 - final reports required after transport exception groups
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
        plan += [
            ("cst_set_parameter", {"name": n, "value": v, "rebuild": False})
            for values in STATES.values()
            for n, v in zip(PARAMETERS, values)
        ]
        plan += [
            ("cst_set_parameter", {"name": PARAMETERS[-1], "value": values[-1], "rebuild": True})
            for values in STATES.values()
        ]
        plan += [("cst_get_parameter", {"name": n}) for n in PARAMETERS]
        for name, args in plan:
            self.validate(name, args)
        for name, args in FIXTURES:
            props = self.catalog[name]["inputSchema"]["properties"]
            leaves = [
                props["height"],
                props["points"]["items"]["items"],
                props["holes"]["items"]["items"]["items"],
                *(props[f"{axis}_offset"] for axis in "xyz"),
            ]
            self.check(
                f"effective expression schema {name}",
                all(spec.get("type") == ["number", "string"] for spec in leaves),
            )
        for name, field in (
            ("cst_create_wire", "radius"),
            ("cst_create_analytical_curve", "t_min"),
        ):
            self.check(
                f"unchanged numeric schema {name}.{field}",
                self.catalog[name]["inputSchema"]["properties"][field]["type"] == "number",
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
        for name, args in FIXTURES:
            numeric = dict(args, height=3, points=[[0, 0], [6, 0], [6, 4], [0, 4]])
            numeric[f"{args['axis']}_offset"] = 2
            if args.get("holes"):
                numeric["holes"] = [[[1, 1], [1, 3], [3, 3], [3, 1]]]
            for sample in (args, numeric):
                payload = await self.accepted(session, name, sample, status="offline")
                vba = payload.get("vba", "")
                values = [
                    sample["height"],
                    sample[f"{sample['axis']}_offset"],
                    *(x for pt in sample["points"] for x in pt),
                    *(x for hole in sample.get("holes", []) for pt in hole for x in pt),
                ]
                literals = ['"' + x + '"' for x in values if isinstance(x, str)]
                self.check(
                    f"offline retained extrusion values {name}",
                    bool(vba) and ".Create" in vba and all(literal in vba for literal in literals),
                    vba=vba,
                    executed_in_cst=False,
                )
                if sample is args:
                    self.check(
                        "native evaluation remains in history",
                        'Evaluate("PEx_Offset")' in vba
                        and payload["extrusion"]["numeric_range_evaluated"] is False,
                        numeric_range_evaluated=payload["extrusion"]["numeric_range_evaluated"],
                    )
                    if name == "cst_create_polygon_extrude":
                        comparison = "<" if args["extrude_direction"] == "up" else ">"
                        self.check(
                            "symbolic winding is selected during reconstruction",
                            f"If cstProfile0Area {comparison} 0 Then" in vba
                            and "Err.Raise 5" in vba,
                            vba=vba,
                        )
                    elif args["extrude_direction"] == "down":
                        self.check(
                            "pointlist down reverses the plane normal and remaps v",
                            '.Vvector "0", "0", "-1"' in vba
                            and '.LineTo Evaluate("30+PEx_W"), "-4"' in vba,
                            vba=vba,
                        )
                if args.get("holes"):
                    self.check(
                        "hole subtraction retained",
                        f'Solid.Subtract "{COMPONENT}:{args["name"]}"' in vba,
                    )
            for changes in (
                {"height": True},
                {"points": [[None, 0], [1, 0], [1, 1]]},
                {"holes": [[[0, 0], [1, "bad\nline"], [1, 1]]]},
                {f"{args['axis']}_offset": ""},
                {f"{'x' if args['axis'] != 'x' else 'z'}_offset": "PEx_Offset"},
            ):
                await self.request(session, name, {**args, **changes}, negative=True)
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
        actual = parse_units(payload.get("output", ""))
        self.check(
            "effective units readback",
            actual == EXPECTED_UNITS,
            expected=EXPECTED_UNITS,
            actual=actual,
            query=UNITS_QUERY,
        )

    async def set_state(self, session, values, *, rebuild):
        await self.messages_at(session)
        for index, (name, value) in enumerate(zip(PARAMETERS, values)):
            required = rebuild and index == len(PARAMETERS) - 1
            payload = await self.accepted(
                session,
                "cst_set_parameter",
                {"name": name, "value": value, "rebuild": required},
                status="ok",
            )
            self.check(
                "parameter list storage and grouped native rebuild",
                payload.get("history_written") is False and payload.get("rebuilt") is required,
                payload=payload,
                state=dict(zip(PARAMETERS, values)),
            )
        await self.messages_at(session)

    async def measure(self, session, values):
        await self.owned_info(session)
        await self.units(session)
        state = dict(zip(PARAMETERS, values))
        await self.parameters(session, state)
        shapes = await self.shapes(session)
        self.check("named solids and PEC materials readback", shapes == SOLIDS, actual=shapes)
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": MEASURE_QUERY}, status="ok"
        )
        keys = {f"{args['name']}.{kind}" for _, args in FIXTURES for kind in ("VOLUME", "AREA")}
        actual = {
            key: number(value)
            for key, value in parse_records(payload.get("output", ""), keys).items()
        }
        expected = analytic_expected(values)
        measurement = self.tag(
            {
                "state": state,
                "actual": actual,
                "expected": expected,
                "units": {key: "mm^3" if key.endswith("VOLUME") else "mm^2" for key in keys},
                "relative_tolerance": 1e-6,
                "absolute_tolerance": 1e-6,
                "query": MEASURE_QUERY,
                "query_source": self.metadata["references"]["solid"],
                "profile_contracts": profile_contracts(values),
                "limitations": LIMITATIONS,
            }
        )
        self.measurements.append(measurement)
        self.event(dict(event="measurement", **measurement))
        for key, value in expected.items():
            self.check(
                f"native Solid.{key} analytic comparison",
                close_number(actual[key], value),
                measured=actual[key],
                expected=value,
                state=state,
                units=measurement["units"][key],
                relative_tolerance=1e-6,
                absolute_tolerance=1e-6,
                query=MEASURE_QUERY,
                query_source=self.metadata["references"]["solid"],
            )
        await self.messages_at(session)

    async def checkpoint(self, session):
        await self.owned_info(session)
        payload = await self.accepted(session, "cst_save_project", status="saved")
        self.check(
            "save returned owned extrusion path",
            Path(payload.get("path") or "").resolve() == self.project.resolve(),
            payload=payload,
        )
        await self.messages_at(session)
        await self.accepted(session, "cst_close_project", status="closed")
        state = await self.request(session, "cst_connection_status")
        self.check(
            "owned extrusion project closed", state.get("project_open") is False, payload=state
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
        # A partially completed invocation must never silently reuse/create fixtures.
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
        shapes = await self.shapes(session)
        params = await self.parameters(session)
        if fresh:
            self.check(
                "new blank project before fixture setup",
                not shapes and not params,
                shapes=shapes,
                parameters=params,
            )
        else:
            self.check(
                "verified fixture reuse without duplicate creation",
                shapes == SOLIDS and set(params) == set(PARAMETERS),
                shapes=shapes,
                parameters=params,
            )
        self.phase = "establish_units_and_parameters"
        await self.accepted(session, "cst_execute_vba", {"code": UNITS_BLOCK}, status="executed")
        await self.units(session)
        await self.set_state(session, STATES["initial"], rebuild=not fresh)
        if fresh:
            self.phase = "create_fixtures"
            self.manifest["fixture"] = "creating"
            self.store_manifest()
            await self.accepted(session, "cst_execute_vba", {"code": SETUP}, status="executed")
            for name, args in FIXTURES:
                await self.accepted(session, name, args, status="executed")
            await self.messages_at(session)
        self.phase = "initial_geometry"
        await self.measure(session, STATES["initial"])
        self.phase = "updated_rebuild"
        await self.set_state(session, STATES["updated"], rebuild=True)
        self.phase = "updated_geometry"
        await self.measure(session, STATES["updated"])
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
        await self.measure(session, STATES["updated"])
        self.phase = "final_rebuild"
        await self.set_state(session, STATES["final"], rebuild=True)
        self.phase = "final_geometry"
        await self.measure(session, STATES["final"])
        await self.checkpoint(session)
        self.phase = "disconnect"
        await self.accepted(session, "cst_disconnect", status="disconnected")
        self.connected = False
        self.exit_code = 0
        self.reason = "Live extrusion scenario completed within recorded verification coverage"

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
            "implementation": "Expression-preserving history VBA with native double evaluation, independent polygon winding and pointlist plane-normal direction.",
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
                        "actual parameter ",
                        "individual parameter ",
                        "effective units ",
                        "named solids ",
                    )
                )
            ],
            "measurements": self.measurements,
            "limitations": LIMITATIONS,
            "checks": self.checks,
            "responses": self.results,
            "references": self.metadata["references"],
        }
        write_json(WORK / "summary.json", summary)
        lines = [
            "# Latest extrusion expression invocation",
            "",
            f"Invocation: `{self.invocation}`",
            f"Phase: `{self.phase}`; exit: {self.exit_code}",
            "",
            self.reason,
            "",
            f"Project target: `{self.project}`",
            "",
            "Offline checks are not live CST validation. Full measurements, parameter states,",
            "expected values, units, tolerances and query provenance are in summary.json.",
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
        help="Recreate verified owned extrusion project only; retain logs and notes",
    )
    parser.add_argument("--cst-path", default=DEFAULT_CST_PATH)
    parser.add_argument("--connection-timeout", type=positive_timeout, default=120)
    parser.add_argument("--call-timeout", type=positive_timeout, default=60)
    options = parser.parse_args()
    if options.preflight and options.reset:
        parser.error("--reset cannot be combined with --preflight")
    try:
        with WorkspaceLock():
            return asyncio.run(ExtrusionTest(options).run())
    except StopTest as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
