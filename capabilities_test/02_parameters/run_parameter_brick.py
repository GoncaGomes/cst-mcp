# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mcp>=1.29,<3", "jsonschema>=4.20"]
# ///
"""Deterministic parameter/brick test through a real local MCP stdio client."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
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
WORK = BATCH / "artifacts" / "01_brick"
FIRST_BATCH = ROOT / "capabilities_test" / "01_project_geometry_materials"
sys.path.insert(0, str(FIRST_BATCH))
# Reuse batch 01's SDK preservation and lossless response/strict output helpers.
# These imports do not import server handlers or construct a CST instance.
from preserving_stdio import preserve_cst_processes
from run_batch import (
    DEFAULT_CST_PATH,
    EXPECTED_UNITS,
    SHAPES_QUERY,
    UNITS_QUERY,
    exception_leaves,
    indeterminate,
    interpret,
    parse_shapes,
    parse_units,
    positive_timeout,
    serialize,
    timestamp,
    walk_dicts,
    write_json,
)

DEFAULT_SOURCE = FIRST_BATCH / "runs" / "run_20261008T084454_422113Z_357472198d" / "project.cst"
OWNER = "cst-mcp-parameter-brick-v1"
SOLID = "ParameterTest:ParametricBrick"
BRICK = {
    "component": "ParameterTest",
    "name": "ParametricBrick",
    "material": "PEC",
    "x_min": 200,
    "x_max": "200+PBrick_L",
    "y_min": 40,
    "y_max": 46,
    "z_min": 0,
    "z_max": "PBrick_H/2",
}
MEASURE_QUERY = '''Debug.Print "VOLUME" & vbTab & CStr(Solid.GetVolume("ParameterTest:ParametricBrick"))
Debug.Print "AREA" & vbTab & CStr(Solid.GetArea("ParameterTest:ParametricBrick"))
Debug.Print "DONE"'''
FIXED_QUERIES = frozenset({UNITS_QUERY, SHAPES_QUERY, MEASURE_QUERY})
REFERENCE_REL = (
    "Online Help/mergedProjects/VBA_3D/common_vbasolido/common_vbasolido_solid_object.htm"
)
TOOLSETS = "connection,project,geometry,parameters,diagnostics,vba"
LOCK_SUFFIXES = {".lok", ".lck", ".lock"}
MANUAL = """# Manual inspection — user notes (never overwritten by this script)

Live CST validation is pending until you execute the live command.
Use --pause-for-inspection for initial, updated, reopened and final stages.
Record invocation ID, stage, observed values, method and any discrepancies.

- [ ] Units are mm / GHz / ns; solid ParameterTest:ParametricBrick uses PEC.
- [ ] Initial bounds X=200..210, Y=40..46, Z=0..2 mm; dimensions 10 x 6 x 2 mm.
- [ ] Updated bounds X=200..214, Y=40..46, Z=0..3 mm; dimensions 14 x 6 x 3 mm.
- [ ] Save/close/reopen retains updated geometry and parameter values 14 and 6.
- [ ] Final bounds X=200..212, Y=40..46, Z=0..3 mm; dimensions 12 x 6 x 3 mm.
- [ ] Width remains 6 mm and position stays fixed through every rebuild.
- [ ] Brick history retains Xmax=200+PBrick_L and Zmax=PBrick_H/2 (not fixed numbers).
- [ ] No duplicate test solid; no unexpected edits to inherited model/history.

Volume/area readbacks do not independently prove dimensions, position or
expression association. GetLooseBoundingBoxOfShape is non-tight and is not
an exact dimensional measurement; this script does not use it.

Open artifacts/01_brick/project.cst manually after successful completion. Close it
before another invocation; avoid edits/saving during inspection because
the fixture's last saved file fingerprint is checked before reuse.

## Notes

"""


class StopTest(RuntimeError):
    """Known failure; preserve project and reports for human inspection."""


class UnknownState(StopTest):
    """In-flight outcome cannot be established; forbid all further MCP calls."""


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256")
    return digest.hexdigest()


def reject_links(path: Path) -> None:
    """Reject links/junctions before recursive copying/deletion or local writes."""
    for parent in (path, *path.parents):
        if parent.is_symlink() or parent.is_junction():
            raise StopTest(f"Links/junctions are not allowed in project/workspace paths: {parent}")
    if path.is_dir():
        for child in path.iterdir():
            reject_links(child)


def project_parts(source: Path) -> list[tuple[Path, str]]:
    """Copy the saved CST file, stem companion and same-stem sidecar files only."""
    if not source.is_file() or source.suffix.lower() != ".cst":
        raise StopTest(f"Source must be an existing saved .cst file: {source}")
    companion = source.with_suffix("")
    if not companion.is_dir():
        raise StopTest(f"Required CST companion directory is missing: {companion}")
    parts = [(source, "project.cst"), (companion, "project")]
    for child in source.parent.iterdir():
        if child != source and child.is_file() and child.name.startswith(source.stem + "."):
            parts.append((child, "project" + child.name[len(source.stem) :]))
    return parts


def ensure_closed(project: Path) -> None:
    """Never delete a lock or infer that one is stale (even a zero-byte lock)."""
    candidates = list(project.parent.glob(project.stem + ".*"))
    companion = project.with_suffix("")
    if companion.is_dir():
        reject_links(companion)
        candidates.extend(companion.rglob("*"))
    locks = [str(p) for p in candidates if p.is_file() and p.suffix.lower() in LOCK_SUFFIXES]
    if locks:
        raise StopTest(
            "Project must be saved and closed. Lock files found; their freshness is "
            "unknown and they will not be removed: " + "; ".join(locks)
        )
    if os.name == "nt" and project.exists():
        # Exclusive read probe detects OS sharing locks without changing the file.
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        api.CreateFileW.restype = wintypes.HANDLE
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.CreateFileW(str(project), 0x80000000, 0, None, 3, 0, None)
        if handle == wintypes.HANDLE(-1).value:
            raise StopTest(
                f"Cannot obtain exclusive read access; save/close project first: {project} "
                f"(Windows error {ctypes.get_last_error()})"
            )
        api.CloseHandle(handle)


def snapshot(parts: list[tuple[Path, str]]) -> list[dict]:
    records = []
    for path, target in parts:
        reject_links(path)
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for item in files:
            if item.is_file():
                stat = item.stat()
                records.append(
                    {
                        "source": str(item),
                        "target": (
                            str(Path(target) / item.relative_to(path)) if path.is_dir() else target
                        ),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sha256": sha256(item),
                    }
                )
    return records


class WorkspaceLock:
    """OS lock automatically releases on exit; the stable lock file is retained."""

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
                "The fixed workspace is already in use; wait for that invocation."
            ) from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class ParameterTest:
    def __init__(self, options):
        self.options = options
        self.invocation = uuid.uuid4().hex
        self.sequence = 0
        self.phase = "preparation"
        self.unknown = False
        self.connected = False
        self.exit_code = 1
        self.reason = "Not completed"
        self.checks = []
        self.catalog = {}
        self.results = []
        self.manifest = None
        self.project = WORK / "project.cst"
        self.source = Path(options.source_project).expanduser().absolute()
        for name in (
            "mcp_calls.jsonl",
            "cst_messages.jsonl",
            "server_stderr.log",
            "metadata.jsonl",
            "metadata.json",
            "summary.json",
            "summary.md",
            "manual_inspection.md",
            "workspace.json",
            "tool_catalog.json",
        ):
            reject_links(WORK / name)
        self.calls = (WORK / "mcp_calls.jsonl").open("a", encoding="utf-8")
        self.messages = (WORK / "cst_messages.jsonl").open("a", encoding="utf-8")
        self.stderr = (WORK / "server_stderr.log").open("a", encoding="utf-8")
        # stderr retains native server lines between tagged invocation boundaries.
        self.stderr.write(json.dumps(self.tag({"event": "stderr_start"})) + "\n")
        self.stderr.flush()
        notes = WORK / "manual_inspection.md"
        if not notes.exists():
            with notes.open("x", encoding="utf-8") as stream:
                stream.write(MANUAL)
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
            "working_project": str(self.project),
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
            "fixed_queries": sorted(FIXED_QUERIES),
            "query_source": str(Path(options.cst_path) / REFERENCE_REL),
            "selected_source_sha256": None,
            "selected_source_size": self.source.stat().st_size if self.source.is_file() else None,
            "selected_source_mtime_ns": self.source.stat().st_mtime_ns
            if self.source.is_file()
            else None,
        }
        try:
            if self.source.is_file():
                self.metadata["selected_source_sha256"] = sha256(self.source)
        except OSError as exc:
            # A locked/unreadable source must still produce a local invocation
            # report. Live preparation will reject it before any CST connection.
            self.metadata["selected_source_fingerprint_error"] = str(exc)
        self.metadata_event("invocation_start")

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

    def verify_manifest(self, *, allow_partial=False):
        manifest = self.manifest
        if (
            manifest.get("owner") != OWNER
            or manifest.get("version") != 1
            or manifest.get("project") != str(self.project)
            or manifest.get("copy_state")
            not in ({"ready", "copying"} if allow_partial else {"ready"})
        ):
            raise StopTest(
                "Workspace ownership/copy is inconsistent; no automatic deletion. "
                "Inspect workspace.json and restore a complete owned workspace."
            )
        names = manifest.get("generated_paths", [])
        if not isinstance(names, list) or not {"project.cst", "project"} <= set(names):
            raise StopTest("Invalid generated project paths in ownership manifest")
        for name in names:
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or not (name == "project" or name.startswith("project."))
                or Path(name).suffix.lower() in LOCK_SUFFIXES
            ):
                raise StopTest(f"Unsafe generated project path: {name!r}")
            path = WORK / name
            reject_links(path)
            if path.resolve().parent != WORK.resolve():
                raise StopTest(f"Generated project path escapes fixed workspace: {path}")
            if name != "project" and path.is_dir():
                raise StopTest(f"Expected generated file, found directory: {path}")

    def prepare_project(self):
        """Local file preparation only; no CST/API calls."""
        manifest_path = WORK / "workspace.json"
        if manifest_path.exists():
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.verify_manifest(allow_partial=self.options.reset)
            ensure_closed(self.project)
            if not self.options.reset:
                if (
                    not self.project.is_file()
                    or not self.project.with_suffix("").is_dir()
                    or sha256(self.project) != self.manifest.get("saved_sha256")
                ):
                    raise StopTest(
                        "Owned project is missing or changed since its saved checkpoint. "
                        "Inspect it manually; restore the checkpoint or explicitly --reset "
                        "from a saved, closed source. No automatic replacement."
                    )
                self.metadata_event("workspace_reused", source_copy=self.manifest["source_copy"])
                return
        elif self.project.exists() or self.project.with_suffix("").exists():
            raise StopTest(
                "Project paths exist without an ownership manifest; refusing to copy/reset"
            )
        reject_links(self.source)
        if self.source.resolve().is_relative_to(WORK.resolve()):
            raise StopTest("Source project must be outside the fixed generated workspace")
        ensure_closed(self.source)
        parts = project_parts(self.source)
        before = snapshot(parts)
        ensure_closed(self.source)
        owned = set(self.manifest["generated_paths"]) if self.manifest is not None else set()
        for _, name in parts:
            target = WORK / name
            reject_links(target)
            if target.exists() and name not in owned:
                raise StopTest(f"Unowned target already exists; refusing replacement: {target}")
        # All targets are checked before the first reset deletion; only manifest-owned
        # project paths are removed. Logs, reports and manual notes are never reset.
        if self.manifest is not None:
            self.verify_manifest(allow_partial=True)
            self.metadata_event("reset_start", prior_manifest=self.manifest)
            for name in self.manifest["generated_paths"]:
                target = WORK / name
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink(missing_ok=True)
        for _, name in parts:
            target = WORK / name
            reject_links(target)
            if target.exists():
                raise StopTest(f"Unowned target already exists; refusing replacement: {target}")
        self.manifest = {
            "owner": OWNER,
            "version": 1,
            "project": str(self.project),
            "copy_state": "copying",
            "fixture": "absent",
            "generated_paths": [name for _, name in parts],
            "source_copy": {
                "source": str(self.source),
                "invocation": self.invocation,
                "copied_at": timestamp(),
                "files": before,
                "note": "Actual saved source; not assumed identical to batch-01 output",
            },
        }
        self.store_manifest()
        for source, name in parts:
            target = WORK / name
            if source.is_dir():
                shutil.copytree(source, target, copy_function=shutil.copy2)
            else:
                shutil.copy2(source, target)
        ensure_closed(self.source)
        if before != snapshot(parts):
            raise StopTest(
                "Source changed during copy; workspace marked incomplete. Preserve and inspect it."
            )
        for record in before:
            target = WORK / record["target"]
            if target.stat().st_size != record["size"] or sha256(target) != record["sha256"]:
                raise StopTest(f"Copied file fingerprint mismatch: {target}")
        self.manifest.update(copy_state="ready", saved_sha256=sha256(self.project))
        self.store_manifest()
        self.metadata_event("source_copied", source_copy=self.manifest["source_copy"])

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
        if name == "cst_execute_vba" and arguments.get("code") not in FIXED_QUERIES:
            raise StopTest("Client raw VBA is restricted to fixed read-only queries")
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
        plan = [
            ("cst_connect", {"mode": "new"}),
            ("cst_open_project", {"path": str(self.project)}),
            ("cst_create_brick", BRICK),
            ("cst_set_parameter", {"name": "PBrick_L", "value": 10, "rebuild": False}),
            ("cst_set_parameter", {"name": "PBrick_H", "value": 4, "rebuild": True}),
            ("cst_get_parameter", {"name": "PBrick_L"}),
        ]
        plan += [
            (name, {})
            for name in (
                "cst_project_info",
                "cst_list_parameters",
                "cst_save_project",
                "cst_close_project",
                "cst_read_project_log",
                "cst_connection_status",
                "cst_disconnect",
            )
        ]
        plan += [("cst_execute_vba", {"code": code}) for code in FIXED_QUERIES]
        for name, args in plan:
            self.validate(name, args)
        for bound in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max"):
            for value in (1.25, "PBrick_L", "200+PBrick_L", "PBrick_H/2"):
                self.validate("cst_create_brick", {**BRICK, bound: value})
        self.check(
            "catalog presence and effective call schemas",
            True,
            tools=sorted({name for name, _ in plan}),
            implemented_support="brick bounds number|string",
        )
        status = await self.request(session, "cst_connection_status")
        self.check(
            "startup disconnected without project",
            status.get("mode") == "offline" and status.get("project_open") is False,
            payload=status,
        )
        if self.options.preflight:
            self.phase = "offline_preflight"
            for args in (BRICK, {**BRICK, "x_max": 210, "z_max": 2}):
                payload = await self.accepted(session, "cst_create_brick", args, status="offline")
                self.check(
                    "offline generated brick bounds",
                    '.Xrange "200", ' in payload.get("vba", ""),
                    vba=payload.get("vba"),
                    executed_in_cst=False,
                )
            for args in (
                {**BRICK, "x_max": True},
                {**BRICK, "z_max": None},
                {**BRICK, "y_max": ""},
                {**BRICK, "x_max": "a\nb"},
            ):
                await self.request(session, "cst_create_brick", args, negative=True)
            self.reason = "Real MCP catalog/schema and offline generation passed; CST execution remains pending"
            self.exit_code = 0

    async def messages_at(self, session):
        await self.request(session, "cst_read_project_log")

    async def owned_info(self, session):
        info = await self.request(session, "cst_project_info")
        self.check(
            "active project is owned working copy",
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

    async def units(self, session):
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": UNITS_QUERY}, status="ok"
        )
        actual = parse_units(payload.get("output", ""))
        self.check(
            "inherited units", actual == EXPECTED_UNITS, expected=EXPECTED_UNITS, actual=actual
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

    async def set_pair(self, session, length, height):
        await self.messages_at(session)
        for name, value, rebuild in (("PBrick_L", length, False), ("PBrick_H", height, True)):
            payload = await self.accepted(
                session,
                "cst_set_parameter",
                {"name": name, "value": value, "rebuild": rebuild},
                status="ok",
            )
            self.check(
                "parameter list storage and separate rebuild",
                payload.get("history_written") is False and payload.get("rebuilt") is rebuild,
                payload=payload,
            )
        await self.messages_at(session)

    async def measure(self, session, length, height, volume, area):
        await self.owned_info(session)
        await self.units(session)
        await self.parameters(session, {"PBrick_L": length, "PBrick_H": height})
        shapes = await self.shapes(session)
        self.check(
            "named solid and material independently read", shapes.get(SOLID) == "PEC", shapes=shapes
        )
        payload = await self.accepted(
            session, "cst_execute_vba", {"code": MEASURE_QUERY}, status="ok"
        )
        actual = parse_measurements(payload.get("output", ""))
        for key, value, unit in (("VOLUME", volume, "mm^3"), ("AREA", area, "mm^2")):
            self.check(
                f"native Solid.Get{key.title()} readback",
                close_number(actual[key], value),
                measured=actual[key],
                expected=value,
                units=unit,
                relative_tolerance=1e-6,
                absolute_tolerance=1e-6,
                query=MEASURE_QUERY,
                query_source=self.metadata["query_source"],
                limitations="Does not independently prove every dimension, position or history association",
            )
        await self.messages_at(session)
        if self.options.pause_for_inspection:
            self.event({"event": "manual_pause", "status": "pending manual inspection"})
            await asyncio.to_thread(
                input,
                f"{self.phase}: inspect {SOLID}, record notes in "
                f"{WORK / 'manual_inspection.md'}, then press Enter (Ctrl+C stops): ",
            )

    async def checkpoint(self, session):
        await self.owned_info(session)
        payload = await self.accepted(session, "cst_save_project", status="saved")
        self.check(
            "save returned owned path",
            Path(payload.get("path") or "").resolve() == self.project.resolve(),
        )
        await self.messages_at(session)
        await self.accepted(session, "cst_close_project", status="closed")
        state = await self.request(session, "cst_connection_status")
        self.check("owned project closed", state.get("project_open") is False, payload=state)
        ensure_closed(self.project)
        self.manifest.update(
            fixture="ready",
            saved_sha256=sha256(self.project),
            checkpoint_invocation=self.invocation,
            checkpoint_phase=self.phase,
        )
        self.store_manifest()
        self.metadata_event("saved_checkpoint", sha256=self.manifest["saved_sha256"], fixture=SOLID)

    async def live(self, session):
        self.phase = "connect_isolated"
        connected = await self.accepted(session, "cst_connect", {"mode": "new"}, status="connected")
        self.check(
            "isolated new CST instance with no adopted projects",
            connected.get("newly_started") is True
            and connected.get("mode") == "new"
            and connected.get("open_projects") == 0
            and connected.get("open_project_paths") == []
            and not connected.get("project_path"),
            payload=connected,
        )
        self.connected = True
        self.phase = "open_owned_copy"
        await self.accepted(
            session, "cst_open_project", {"path": str(self.project)}, status="opened"
        )
        await self.owned_info(session)
        await self.units(session)
        await self.messages_at(session)
        shapes = await self.shapes(session)
        existing_params = await self.parameters(session)
        fixture = self.manifest.get("fixture")
        if fixture == "absent":
            self.check(
                "dedicated fixture names available",
                SOLID not in shapes
                and not any(n.startswith("ParameterTest:") for n in shapes)
                and not ({"PBrick_L", "PBrick_H"} & existing_params.keys()),
                shapes=shapes,
                explanation="Unexpected same-name objects/parameters will never be deleted",
            )
        elif fixture == "ready":
            self.check(
                "owned fixture exists before rerun",
                shapes.get(SOLID) == "PEC" and {"PBrick_L", "PBrick_H"} <= existing_params.keys(),
                shapes=shapes,
            )
        else:
            raise StopTest(
                "Fixture creation state is incomplete. Inspect project/history manually; "
                "restore a saved checkpoint or explicitly --reset. No duplicate creation."
            )
        self.phase = "baseline_rebuild"  # Stop here on inherited history failure.
        await self.set_pair(session, 10, 4)
        if fixture == "absent":
            self.phase = "create_parametric_brick"
            self.manifest["fixture"] = "creating"
            self.store_manifest()
            await self.accepted(session, "cst_create_brick", BRICK, status="executed")
        self.phase = "initial_geometry"
        await self.measure(session, 10, 4, 120, 184)
        self.phase = "updated_rebuild"
        await self.set_pair(session, 14, 6)
        self.phase = "updated_geometry"
        await self.measure(session, 14, 6, 252, 288)
        await self.checkpoint(session)
        self.phase = "reopened_persistence"
        await self.accepted(
            session, "cst_open_project", {"path": str(self.project)}, status="opened"
        )
        await self.measure(session, 14, 6, 252, 288)
        self.phase = "final_rebuild"
        await self.messages_at(session)
        payload = await self.accepted(
            session,
            "cst_set_parameter",
            {"name": "PBrick_L", "value": 12, "rebuild": True},
            status="ok",
        )
        self.check(
            "final rebuild through parameter tool",
            payload.get("rebuilt") is True and payload.get("history_written") is False,
            payload=payload,
        )
        self.phase = "final_geometry"
        await self.measure(session, 12, 6, 216, 252)
        await self.checkpoint(session)
        self.phase = "disconnect"
        await self.accepted(session, "cst_disconnect", status="disconnected")
        self.connected = False
        self.reason = "Real CST scenario completed; dimensions/position/expression association await manual inspection"
        self.exit_code = 0

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

    def finalize(self):
        summary = {
            "invocation": self.invocation,
            "phase": self.phase,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "indeterminate": self.unknown,
            "catalog_presence": sorted(self.catalog),
            "implemented_support": "six brick bounds accept number|string; dedicated parameter tool unchanged",
            "offline_or_substitute": "preflight"
            if self.options.preflight
            else "see repository pytest results",
            "real_cst_execution_attempted": self.connected
            or any(r["tool"] == "cst_connect" for r in self.results),
            "scenario_completed": not self.options.preflight and self.exit_code == 0,
            "independently_verified_properties": [
                c
                for c in self.checks
                if c["passed"]
                and not self.options.preflight
                and c["scope"].startswith(
                    (
                        "native Solid.",
                        "actual parameter ",
                        "individual parameter ",
                        "named solid ",
                        "inherited units",
                    )
                )
            ],
            "pending_manual_inspection": str(WORK / "manual_inspection.md"),
            "project": str(self.project),
            "checks": self.checks,
            "responses": self.results,
        }
        write_json(WORK / "summary.json", summary)
        lines = [
            "# Latest parameter brick invocation",
            "",
            f"Invocation: `{self.invocation}`",
            f"Phase: `{self.phase}`; exit: {self.exit_code}",
            "",
            self.reason,
            "",
            f"Owned project: `{self.project}`",
            "",
            "Catalog presence, schema compatibility and command acceptance are recorded separately",
            "from readbacks. Offline/substitute checks are not real CST validation.",
            "",
            "| Stage | Check | Passed |",
            "| --- | --- | --- |",
        ]
        lines.extend(f"| {c['phase']} | {c['scope']} | {c['passed']} |" for c in self.checks)
        lines += [
            "",
            "Native volume/area evidence (including measured values, units, query source and",
            "tolerances) is in summary.json and mcp_calls.jsonl. Manual inspection remains",
            "pending in manual_inspection.md; user notes there are preserved.",
            "",
            "After success, open project.cst in CST to inspect it; close before the next invocation.",
            "After timeout/loss, inspect CST manually and do not infer the last command completed.",
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


def number(text: str) -> float:
    # CST CStr uses the Windows decimal separator. No thousands grouping expected.
    value = float(text.strip().replace(",", "."))
    if not math.isfinite(value):
        raise StopTest(f"Nonfinite native query value: {text!r}")
    return value


def close_number(actual, expected):
    # Exact analytic cuboid; allow small CAD kernel/decimal formatting error.
    return math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-6)


def parse_parameter_output(output, prefix=""):
    result = {}
    for line in output.splitlines():
        line = line.strip()
        if prefix and not line.startswith(prefix):
            continue
        name, sep, value = line.removeprefix(prefix).partition(" = ")
        if not sep:
            raise StopTest(f"Incomplete parameter readback: {line!r}")
        if not name or name in result:
            raise StopTest(f"Duplicate/empty parameter readback: {line!r}")
        result[name] = number(value)
    return result


def parse_measurements(output):
    result = {}
    done = False
    for line in output.splitlines():
        key, sep, value = line.strip().partition("\t")
        if key == "DONE" and not sep:
            if done:
                raise StopTest("Duplicate measurement end marker")
            done = True
        elif key in {"VOLUME", "AREA"} and sep and not done and key not in result:
            result[key] = number(value)
        else:
            raise StopTest(f"Unexpected/incomplete measurement output: {line!r}")
    if not done or set(result) != {"VOLUME", "AREA"}:
        raise StopTest("Volume/area output missing values or end marker")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Real stdio catalog/schema check; CST disabled; no project copy",
    )
    parser.add_argument(
        "--source-project",
        default=str(DEFAULT_SOURCE),
        help="Saved, closed source for initial copy/reset",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Explicitly recreate owned project only; retain logs/manual notes",
    )
    parser.add_argument(
        "--pause-for-inspection",
        action="store_true",
        help="Pause at initial, updated, reopened and final stages",
    )
    parser.add_argument("--cst-path", default=DEFAULT_CST_PATH)
    parser.add_argument("--connection-timeout", type=positive_timeout, default=120)
    parser.add_argument("--call-timeout", type=positive_timeout, default=60)
    options = parser.parse_args()
    if options.preflight and options.reset:
        parser.error("--reset cannot be combined with --preflight")
    try:
        with WorkspaceLock():
            return asyncio.run(ParameterTest(options).run())
    except StopTest as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
