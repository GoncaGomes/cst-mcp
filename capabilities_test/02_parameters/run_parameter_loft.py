# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mcp>=1.29,<3", "jsonschema>=4.20"]
# ///
"""Deterministic parameter-driven loft validation through a real MCP stdio session."""

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
from run_face_from_curves import parse_native_bool
from run_parameter_primitives import parse_records

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "artifacts" / "06_loft"
OWNER = "cst-mcp-parameter-loft-v1"
TOOLSETS = "connection,project,geometry,parameters,diagnostics,vba"
COMPONENT = "LoftValidation"
SHAPE = f"{COMPONENT}:ParametricLoft"
PARAMETERS = ("PLoft_Width", "PLoft_Depth", "PLoft_Length")
STATES = {"initial": (4, 2, 5), "updated": (6, 3, 4), "final": (6, 3, 7)}
EXPECTED = {
    "initial": {"VOLUME": 40, "AREA": 76},
    "updated": {"VOLUME": 72, "AREA": 108},
    "final": {"VOLUME": 126, "AREA": 162},
}
LOFT_ARGS = {
    "component": COMPONENT,
    "name": "ParametricLoft",
    "material": "PEC",
    "profiles": [
        [
            [0, 0, 0],
            ["PLoft_Width", 0, 0],
            ["PLoft_Width", "PLoft_Depth", 0],
            [0, "PLoft_Depth", 0],
        ],
        [
            [0, 0, "PLoft_Length"],
            ["PLoft_Width", 0, "PLoft_Length"],
            ["PLoft_Width", "PLoft_Depth", "PLoft_Length"],
            [0, "PLoft_Depth", "PLoft_Length"],
        ],
    ],
}
FIXTURES = (("cst_create_loft", LOFT_ARGS),)
# Debug.Print selects output capture, including setup, outside model history.
SETUP = UNITS_BLOCK + f'\nComponent.New "{COMPONENT}"\nDebug.Print "SETUP_DONE"'
MEASURE_QUERY = f'''Debug.Print "EXISTS" & vbTab & CStr(Solid.DoesExist("{SHAPE}"))
Debug.Print "IS_SOLID" & vbTab & CStr(Solid.IsSolidShape("{SHAPE}"))
Debug.Print "IS_HYBRID" & vbTab & CStr(Solid.IsHybridShape("{SHAPE}"))
Debug.Print "VOLUME" & vbTab & CStr(Solid.GetVolume("{SHAPE}"))
Debug.Print "AREA" & vbTab & CStr(Solid.GetArea("{SHAPE}"))
Debug.Print "DONE"'''
FIXED_VBA = frozenset({SETUP, UNITS_QUERY, SHAPES_QUERY, MEASURE_QUERY})
HELP_ROOT = "Online Help/mergedProjects"
REFERENCES = {
    "loftcurves": f"{HELP_ROOT}/VBA_3D/common_vbacurves/common_vbacurves_loftcurves_object.htm",
    "polygon3d": f"{HELP_ROOT}/VBA_3D/common_vbacurves/common_vbacurves_polygon3d.htm",
    "curve": f"{HELP_ROOT}/VBA_3D/common_vbacurves/common_vbacurves_curve_object.htm",
    "loft_dialog": f"{HELP_ROOT}/3D/common_struct/common_struct_curveloft.htm",
    "solid": f"{HELP_ROOT}/VBA_3D/common_vbasolido/common_vbasolido_solid_object.htm",
}
LIMITATIONS = [
    "Offline success is not native CST execution; native validation remains pending until a live run.",
    "Volume and area do not independently verify every coordinate, position or orientation, or every expression association.",
    "Manual inspection of dimensions, orientation, capped ends and symbolic history remains separate and pending.",
    "Profile curves may be consumed by LoftCurves; their survival is neither assumed nor required.",
    "Reuse without reset remains deferred; scopes 01 through 05 are preserved.",
    "No path, extra loft options, solver, geometry interpreter or expression interpreter is included.",
    "CST messages may be inherited, repeated or truncated; they do not establish command causality.",
]


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
            raise StopTest("Loft workspace is already in use; wait for that invocation") from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class LoftTest:
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
            "parameter_states": STATES,
            "expected_geometry": EXPECTED,
            "references": {},
            "limitations": LIMITATIONS,
        }
        self.metadata_event("invocation_start")

    def generated_paths(self):
        """Accept only same-stem files and the companion, all within this scope."""
        names = self.manifest.get("generated_paths")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise StopTest("Invalid loft generated paths")
        if not {"project.cst", "project"} <= set(names) or len(names) != len(set(names)):
            raise StopTest("Incomplete/duplicate loft generated paths")
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
                raise StopTest(f"Generated path escapes loft workspace: {path}")
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
            raise StopTest("Loft ownership/state inconsistent; no automatic deletion")
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
                raise StopTest("Invalid loft manifest")
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
                        "Loft project changed or incomplete since checkpoint; inspect and "
                        "restore it or explicitly --reset after saving/closing. No automatic adoption."
                    )
                raise StopTest("Reuse without reset is deferred; save/close and explicitly --reset")
        elif any(WORK.glob("project*")):
            raise StopTest("Project paths exist without loft ownership; refusing create/reset")
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
            "active project is owned loft project",
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
            logger.exception("Loft client stopped; finalizing local reports")
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

    async def checkpoint(self, session):
        await self.owned_info(session)
        payload = await self.accepted(session, "cst_save_project", status="saved")
        self.check(
            "save returned owned loft path",
            Path(payload.get("path") or "").resolve() == self.project.resolve(),
            payload=payload,
        )
        await self.messages_at(session)
        await self.accepted(session, "cst_close_project", status="closed")
        state = await self.request(session, "cst_connection_status")
        self.check(
            "owned loft project closed",
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

    async def parameters(self, session, expected=None):
        payload = await self.accepted(session, "cst_list_parameters", status="ok")
        values = parse_parameter_output(payload.get("output", ""))
        if expected is not None:
            self.check(
                "native parameter inventory",
                set(values) == set(expected),
                actual=values,
                expected=expected,
            )
            for name, value in expected.items():
                self.check(
                    f"native parameter list readback {name}",
                    name in values and close_number(values[name], value),
                    expected=value,
                    actual=values.get(name),
                    source="cst_list_parameters native output",
                    relative_tolerance=1e-6,
                    absolute_tolerance=1e-6,
                )
                got = await self.accepted(session, "cst_get_parameter", {"name": name}, status="ok")
                individual = parse_parameter_output(got.get("output", ""), prefix="Parameter ")
                self.check(
                    f"native individual parameter readback {name}",
                    name in individual and close_number(individual[name], value),
                    expected=value,
                    actual=individual.get(name),
                    source="cst_get_parameter native RestoreParameter output",
                    relative_tolerance=1e-6,
                    absolute_tolerance=1e-6,
                )
        return values

    async def set_state(self, session, values, *, rebuild):
        for index, (name, value) in enumerate(zip(PARAMETERS, values)):
            required = rebuild and index == len(PARAMETERS) - 1
            payload = await self.accepted(
                session,
                "cst_set_parameter",
                {"name": name, "value": value, "rebuild": required},
                status="ok",
            )
            self.check(
                "parameter storage and grouped rebuild acceptance",
                payload.get("history_written") is False and payload.get("rebuilt") is required,
                payload=payload,
                state=dict(zip(PARAMETERS, values)),
                coverage="Command acceptance only; returned value echoes are not measurements",
            )

    async def catalog_and_preflight(self, session):
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
        plan += [("cst_get_parameter", {"name": name}) for name in PARAMETERS]
        plan += [
            ("cst_set_parameter", {"name": name, "value": value, "rebuild": rebuild and i == 2})
            for values, rebuild in (
                (STATES["initial"], False),
                (STATES["updated"], True),
                (STATES["final"], True),
            )
            for i, (name, value) in enumerate(zip(PARAMETERS, values))
        ]
        plan += [("cst_execute_vba", {"code": code}) for code in sorted(FIXED_VBA)]
        for name, args in plan:
            self.validate(name, args)
        profiles = self.catalog["cst_create_loft"]["inputSchema"]["properties"]["profiles"]
        point = profiles["items"]["items"]
        self.check(
            "explicit 3D expression catalog contract",
            profiles.get("minItems") == 2
            and profiles["items"].get("minItems") == 3
            and point.get("minItems") == point.get("maxItems") == 3
            and set(point["items"].get("type", [])) == {"number", "string"},
        )
        self.check(
            "catalog presence and planned schemas", True, tools=sorted({name for name, _ in plan})
        )
        if not self.options.preflight:
            return
        self.phase = "offline_preflight"
        payload = await self.accepted(session, "cst_create_loft", LOFT_ARGS, status="offline")
        code = payload.get("vba", "")
        groups = [line.split('"')[1] for line in code.splitlines() if '.NewCurve "' in line]
        refs = [line.strip() for line in code.splitlines() if ".AddCurve" in line]
        self.check(
            "offline separate groups and qualified profile order",
            len(groups) == len(set(groups)) == 2
            and all(len(g) <= 100 for g in groups)
            and refs
            == [f'.AddCurve "{g}:ParametricLoft_profile{i}"' for i, g in enumerate(groups)],
            groups=groups,
            references=refs,
            vba=code,
            executed_in_cst=False,
        )
        blocks = code.split("With Polygon3D\n")[1:]
        actual_points = [
            [
                line.strip()
                for line in block.split("End With", 1)[0].splitlines()
                if ".Point" in line
            ]
            for block in blocks
        ]
        expected_points = [
            ['.Point "' + '", "'.join(map(str, point)) + '"' for point in [*profile, profile[0]]]
            for profile in LOFT_ARGS["profiles"]
        ]
        self.check(
            "offline ordered expression triples and single closure",
            actual_points == expected_points,
            expected=expected_points,
            actual=actual_points,
            executed_in_cst=False,
        )
        self.check(
            "offline capped LoftCurves sequence",
            code.split("With LoftCurves\n")[-1].splitlines()
            == [
                "  .Reset",
                '  .Name "ParametricLoft"',
                '  .Component "LoftValidation"',
                '  .Material "PEC"',
                '  .Solid "True"',
                *["  " + ref for ref in refs],
                "  .Create",
                "End With",
            ]
            and ".Path" not in code
            and "DeleteCurve" not in code,
            executed_in_cst=False,
        )
        for profiles in (
            [],
            ["bad", "bad"],
            [[[0, 0], [1, 0], [0, 1]]] * 2,
            [[[0, 0, 0], [1, 0, 0], [0, 1, " "]]] * 2,
            [[[0, 0, 0], [1, 0, 0], [0, 1, "x\nCreate"]]] * 2,
        ):
            await self.request(
                session, "cst_create_loft", dict(LOFT_ARGS, profiles=profiles), negative=True
            )
        for name, value in zip(PARAMETERS, STATES["initial"]):
            payload = await self.accepted(
                session,
                "cst_set_parameter",
                {"name": name, "value": value, "rebuild": False},
                status="offline",
            )
            self.check(
                "offline dedicated parameter storage",
                f'StoreParameter "{name}", "{value}"' == payload.get("vba"),
                executed_in_cst=False,
                parameter_echo_is_measurement=False,
            )
        for query in sorted(FIXED_VBA):
            payload = await self.accepted(
                session, "cst_execute_vba", {"code": query}, status="offline"
            )
            self.check(
                "offline fixed setup/readback preserved",
                payload.get("vba") == query,
                query=query,
                executed_in_cst=False,
            )
        self.exit_code = 0
        self.reason = "Real MCP catalog/schema and offline VBA checks passed; native CST execution and manual inspection pending"

    async def measure(self, session, state_name):
        await self.owned_info(session)
        await self.units(session)
        values = STATES[state_name]
        parameters = await self.parameters(session, dict(zip(PARAMETERS, values)))
        shapes = await self.shapes(session)
        self.check(
            "native named solid and PEC material readback",
            shapes == {SHAPE: "PEC"},
            expected={SHAPE: "PEC"},
            actual=shapes,
            query=SHAPES_QUERY,
            query_source=self.metadata["references"]["solid"],
        )
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": MEASURE_QUERY}, status="ok"
        )
        records = parse_records(
            payload.get("output", ""), {"EXISTS", "IS_SOLID", "IS_HYBRID", "VOLUME", "AREA"}
        )
        actual = {key: number(records[key]) for key in ("VOLUME", "AREA")}
        types = {
            key: parse_native_bool(records[key]) for key in ("EXISTS", "IS_SOLID", "IS_HYBRID")
        }
        measurement = self.tag(
            {
                "state": state_name,
                "parameters": parameters,
                "units": self.actual_units,
                "native": records,
                "shape_types": types,
                "actual": actual,
                "expected": EXPECTED[state_name],
                "quantity_units": {"VOLUME": "mm^3", "AREA": "mm^2"},
                "relative_tolerance": 1e-6,
                "absolute_tolerance": 1e-6,
                "query": MEASURE_QUERY,
                "query_source": self.metadata["references"]["solid"],
                "raw_response_payload": payload,
                "limitations": LIMITATIONS,
            }
        )
        self.measurements.append(measurement)
        self.event(dict(event="measurement", **measurement))
        self.check(
            "native solid existence and body type",
            types == {"EXISTS": True, "IS_SOLID": True, "IS_HYBRID": False},
            actual=types,
            query=MEASURE_QUERY,
            query_source=self.metadata["references"]["solid"],
        )
        for key, expected in EXPECTED[state_name].items():
            self.check(
                f"native Solid.{key} prism comparison",
                close_number(actual[key], expected),
                expected=expected,
                actual=actual[key],
                units=measurement["quantity_units"][key],
                relative_tolerance=1e-6,
                absolute_tolerance=1e-6,
                query=MEASURE_QUERY,
                query_source=self.metadata["references"]["solid"],
            )
        await self.messages_at(session)

    async def live(self, session):
        self.phase = "connect_isolated"
        self.live_attempted = True
        state = await self.request(session, "cst_connection_status")
        self.check(
            "startup disconnected without project",
            state.get("mode") == "offline" and state.get("project_open") is False,
            payload=state,
        )
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
        self.manifest["generation_state"] = "running"
        self.store_manifest()
        self.phase = "create_blank_project"
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
        await self.owned_info(session)
        await self.messages_at(session)
        shapes, parameters = await self.shapes(session), await self.parameters(session)
        self.check(
            "new blank project before fixture setup",
            not shapes and not parameters,
            shapes=shapes,
            parameters=parameters,
        )
        self.phase = "setup_and_create_loft"
        self.manifest["fixture"] = "creating"
        self.store_manifest()
        setup = await self.accepted(session, "cst_execute_vba", {"code": SETUP}, status="ok")
        self.check(
            "fixed setup captured outside history",
            setup.get("output", "").strip() == "SETUP_DONE",
            payload=setup,
        )
        await self.set_state(session, STATES["initial"], rebuild=False)
        await self.accepted(session, "cst_create_loft", LOFT_ARGS, status="executed")
        self.phase = "initial_readback"
        await self.measure(session, "initial")
        self.phase = "updated_rebuild"
        await self.set_state(session, STATES["updated"], rebuild=True)
        await self.measure(session, "updated")
        await self.checkpoint(session)
        self.phase = "reopened_updated_readback"
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
        await self.measure(session, "updated")
        self.phase = "post_reopen_length_rebuild"
        # Only length changes in this stage; the other parameters are independently read back.
        payload = await self.accepted(
            session,
            "cst_set_parameter",
            {"name": "PLoft_Length", "value": 7, "rebuild": True},
            status="ok",
        )
        self.check(
            "post-reopen length rebuild acceptance",
            payload.get("history_written") is False and payload.get("rebuilt") is True,
            payload=payload,
        )
        await self.measure(session, "final")
        await self.checkpoint(session)
        self.phase = "disconnect"
        await self.accepted(session, "cst_disconnect", status="disconnected")
        self.connected = False
        self.exit_code = 0
        self.reason = "Live loft scenario completed within recorded coverage; manual inspection remains separate and pending"

    def finalize(self):
        summary = {
            "invocation": self.invocation,
            "phase": self.phase,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "indeterminate": self.unknown,
            "project": str(self.project),
            "preflight": self.options.preflight,
            "offline_preflight_passed": self.exit_code == 0 if self.options.preflight else None,
            "real_cst_execution_attempted": self.live_attempted,
            "scenario_completed": not self.options.preflight and self.exit_code == 0,
            "manual_inspection": "pending; separate from automated native readbacks",
            "native_measurement_status": "pending native CST execution"
            if self.options.preflight
            else "see measurements and checks",
            "implementation": "Ordered, closed Polygon3D profiles in separate groups; capped LoftCurves solid. Fixed setup/readbacks outside history.",
            "expected_parameter_states": STATES,
            "expected_geometry": EXPECTED,
            "measurements": self.measurements,
            "limitations": LIMITATIONS,
            "checks": self.checks,
            "responses": self.results,
            "references": self.metadata["references"],
            "independently_verified_properties": [
                c
                for c in self.checks
                if c["passed"]
                and not self.options.preflight
                and c["scope"].startswith(("native ", "effective units "))
            ],
        }
        write_json(WORK / "summary.json", summary)
        lines = [
            "# Latest parameter loft invocation",
            "",
            f"Invocation: `{self.invocation}`",
            f"Phase: `{self.phase}`; exit: {self.exit_code}",
            "",
            self.reason,
            "",
            f"Project target: `{self.project}`",
            "",
            "Offline success is not native CST execution. Manual inspection remains separate.",
            "Expected/actual values, tolerances, full responses and provenance are in summary.json.",
            "",
            "| Stage | Check | Passed |",
            "| --- | --- | --- |",
            *[f"| {c['phase']} | {c['scope']} | {c['passed']} |" for c in self.checks],
            "",
            *LIMITATIONS,
            "",
            "After timeout/loss, inspect CST manually. Only local reports and Python-server transport teardown continue.",
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
        help="Recreate verified owned loft project only; retain logs and notes",
    )
    parser.add_argument("--cst-path", default=DEFAULT_CST_PATH)
    parser.add_argument("--connection-timeout", type=positive_timeout, default=120)
    parser.add_argument("--call-timeout", type=positive_timeout, default=60)
    options = parser.parse_args()
    if options.preflight and options.reset:
        parser.error("--reset cannot be combined with --preflight")
    try:
        with WorkspaceLock():
            return asyncio.run(LoftTest(options).run())
    except StopTest as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
