# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mcp>=1.29,<3", "jsonschema>=4.20"]
# ///
"""Boolean operations and isolated rejection validation through real MCP stdio."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import ClassVar

from jsonschema import ValidationError
from jsonschema.validators import validator_for
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Add the sibling helpers locally using this file, never a cwd or historical artifact.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02_parameters"))

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
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
OPERATIONS_OWNER = "cst-mcp-boolean-operations-v1"
ERRORS_OWNER = "cst-mcp-boolean-errors-v1"
TOOLSETS = "connection,project,geometry,boolean,parameters,diagnostics,vba"
PARAMETER = "PBool_Shift"
PARAMETERS = (PARAMETER,)
STATES = {"initial": 2, "updated": 3}
OPERATIONS = {"Add": 0, "Subtract": 20, "Intersect": 40, "Insert": 60}
EXPECTED = {
    2: {"Add": (24, 56), "Subtract": (8, 24), "Intersect": (8, 24), "Insert": (8, 24)},
    3: {"Add": (28, 64), "Subtract": (12, 32), "Intersect": (4, 16), "Insert": (12, 32)},
}
MEMBERSHIP = {
    "Add": (True, True, True),
    "Subtract": (True, False, False),
    "Intersect": (False, True, False),
    "Insert": (True, False, False),
}
LOCAL_POINTS = ((1, 1, 1), (3.5, 1, 1), (5.5, 1, 1))
WITNESS = "BoolWitness:Sentinel"
FIXTURES = tuple(
    (
        "cst_create_brick",
        {
            "component": f"Bool{operation}",
            "name": operand,
            "material": "Vacuum" if operation == "Insert" and operand == "A" else "PEC",
            "x_min": offset if operand == "A" else f"{offset}+{PARAMETER}",
            "x_max": offset + 4 if operand == "A" else f"{offset}+{PARAMETER}+4",
            "y_min": 0,
            "y_max": 2,
            "z_min": 0,
            "z_max": 2,
        },
    )
    for operation, offset in OPERATIONS.items()
    for operand in ("A", "B")
) + (
    (
        "cst_create_brick",
        {
            "component": "BoolWitness",
            "name": "Sentinel",
            "material": "PEC",
            "x_min": 100,
            "x_max": 102,
            "y_min": 0,
            "y_max": 2,
            "z_min": 0,
            "z_max": 2,
        },
    ),
)
BOOLEAN_CALLS = tuple(
    (
        f"cst_boolean_{operation.lower()}",
        {"solid1": f"Bool{operation}:A", "solid2": f"Bool{operation}:B"},
    )
    for operation in OPERATIONS
)
SHAPE_POINTS = {
    f"Bool{operation}:{operand}": tuple((offset + x, y, z) for x, y, z in LOCAL_POINTS)
    for operation, offset in OPERATIONS.items()
    for operand in ("A", "B")
} | {WITNESS: ((101, 1, 1),)}
# Debug.Print selects output capture for setup and readbacks, outside model history.
SETUP = "\n".join(
    [
        UNITS_BLOCK,
        *[f'Component.New "Bool{op}"' for op in OPERATIONS],
        'Component.New "BoolWitness"',
        'Debug.Print "SETUP_DONE"',
    ]
)


def shape_query(shape, points):
    """Build a fixed fixture-only readback; never measure an absent shape."""
    lines = [
        f'Debug.Print "EXISTS" & vbTab & CStr(Solid.DoesExist("{shape}"))',
        f'If Solid.DoesExist("{shape}") Then',
        f'  Debug.Print "IS_SOLID" & vbTab & CStr(Solid.IsSolidShape("{shape}"))',
        f'  Debug.Print "IS_HYBRID" & vbTab & CStr(Solid.IsHybridShape("{shape}"))',
        f'  Debug.Print "VOLUME" & vbTab & CStr(Solid.GetVolume("{shape}"))',
        f'  Debug.Print "AREA" & vbTab & CStr(Solid.GetArea("{shape}"))',
    ]
    lines += [
        f'  Debug.Print "POINT_{i}" & vbTab & CStr(Solid.IsPointInsideShape({x}, {y}, {z}, "{shape}"))'
        for i, (x, y, z) in enumerate(points)
    ]
    return "\n".join([*lines, "End If", 'Debug.Print "DONE"'])


SHAPE_QUERIES = {shape: shape_query(shape, points) for shape, points in SHAPE_POINTS.items()}
EXISTENCE_QUERIES = {
    shape: f'Debug.Print "EXISTS" & vbTab & CStr(Solid.DoesExist("{shape}"))\nDebug.Print "DONE"'
    for shape in SHAPE_POINTS
}
FIXED_VBA = frozenset(
    {SETUP, UNITS_QUERY, SHAPES_QUERY, *EXISTENCE_QUERIES.values(), *SHAPE_QUERIES.values()}
)
REFERENCES = {
    "solid": "Online Help/mergedProjects/VBA_3D/common_vbasolido/common_vbasolido_solid_object.htm"
}
LIMITATIONS = [
    "Offline success is not native CST execution; live validation and manual inspection remain pending.",
    "These overlapping cuboid fixtures do not certify arbitrary boolean geometry or material combinations.",
    "Volume, area and selected interior points do not prove every dimension, topology or expression association.",
    "The Insert catalog description says embedded material regions; the installed reference specifies A minus B with B retained. This client verifies the latter without changing the server.",
    "An executed history response proves command acceptance only; geometric effects require independent native readbacks.",
    "Manual inspection of symbolic brick history, boolean history and saved geometry remains separate.",
    "Reuse without reset remains deferred. Earlier capability workspaces and the separate errors stage are preserved.",
    "Negative inputs and native missing-solid handling are covered separately by the errors stage.",
    "CST messages may be inherited, repeated or truncated; they do not establish command causality.",
]


def expected_shapes(shift, completed):
    """Analytic fixture expectations, independent of native measurement output."""
    result = {}
    for operation in OPERATIONS:
        for operand in ("A", "B"):
            if operand == "B" and operation in completed and operation != "Insert":
                continue
            volume, area = (
                EXPECTED[shift][operation]
                if operand == "A" and operation in completed
                else (16, 40)
            )
            points = (
                MEMBERSHIP[operation]
                if operand == "A" and operation in completed
                else ((True, True, False) if operand == "A" else (False, True, True))
            )
            result[f"Bool{operation}:{operand}"] = {
                "material": "Vacuum" if operation == "Insert" and operand == "A" else "PEC",
                "VOLUME": volume,
                "AREA": area,
                "membership": points,
            }
    result[WITNESS] = {"material": "PEC", "VOLUME": 8, "AREA": 24, "membership": (True,)}
    return result


ERROR_CASES = (
    {
        "id": "01_missing_argument",
        "tool": "cst_boolean_add",
        "arguments": {"solid1": "BoolError:A"},
        "layer": "schema",
    },
    {
        "id": "02_wrong_type",
        "tool": "cst_boolean_add",
        "arguments": {"solid1": 123, "solid2": "BoolError:B"},
        "layer": "schema",
    },
    {
        "id": "03_empty_reference",
        "tool": "cst_boolean_add",
        "arguments": {"solid1": "", "solid2": "BoolError:B"},
        "layer": "server_argument",
    },
    {
        "id": "04_forbidden_character",
        "tool": "cst_boolean_add",
        "arguments": {"solid1": "BoolError:A", "solid2": "BoolError:B\n"},
        "layer": "server_argument",
    },
    {
        "id": "05_missing_solid1",
        "tool": "cst_boolean_add",
        "arguments": {"solid1": "BoolError:MissingA", "solid2": "BoolError:B"},
        "layer": "native",
    },
    *(
        {
            "id": f"{index:02d}_{operation}_missing_solid2",
            "tool": f"cst_boolean_{operation}",
            "arguments": {"solid1": "BoolError:A", "solid2": "BoolError:MissingB"},
            "layer": "native",
        }
        for index, operation in enumerate(("add", "subtract", "intersect", "insert"), 6)
    ),
)
SCHEMA_CASE_IDS = frozenset(case["id"] for case in ERROR_CASES if case["layer"] == "schema")
ERROR_FIXTURES = tuple(
    (
        "cst_create_brick",
        {
            "component": component,
            "name": name,
            "material": "PEC",
            "x_min": low,
            "x_max": high,
            "y_min": 0,
            "y_max": 2,
            "z_min": 0,
            "z_max": 2,
        },
    )
    for component, name, low, high in (
        ("BoolError", "A", 0, 4),
        ("BoolError", "B", 2, 6),
        ("BoolWitness", "Sentinel", 100, 102),
    )
)
ERROR_POINTS = {"BoolError:A": LOCAL_POINTS, "BoolError:B": LOCAL_POINTS, WITNESS: ((101, 1, 1),)}
ERROR_EXPECTED = {
    "BoolError:A": {"material": "PEC", "VOLUME": 16, "AREA": 40, "membership": (True, True, False)},
    "BoolError:B": {"material": "PEC", "VOLUME": 16, "AREA": 40, "membership": (False, True, True)},
    WITNESS: {"material": "PEC", "VOLUME": 8, "AREA": 24, "membership": (True,)},
}
ERROR_SETUP = (
    f'{UNITS_BLOCK}\nComponent.New "BoolError"\n'
    'Component.New "BoolWitness"\nDebug.Print "SETUP_DONE"'
)
ERROR_SHAPE_QUERIES = {shape: shape_query(shape, points) for shape, points in ERROR_POINTS.items()}
ERROR_EXISTENCE_QUERIES = {
    shape: f'Debug.Print "EXISTS" & vbTab & CStr(Solid.DoesExist("{shape}"))\nDebug.Print "DONE"'
    for shape in (*ERROR_POINTS, "BoolError:MissingA", "BoolError:MissingB")
}
ERROR_FIXED_VBA = frozenset(
    {
        ERROR_SETUP,
        UNITS_QUERY,
        SHAPES_QUERY,
        *ERROR_SHAPE_QUERIES.values(),
        *ERROR_EXISTENCE_QUERIES.values(),
    }
)
ERROR_LIMITATIONS = [
    "Offline missing-solid VBA generation is not native rejection evidence; those outcomes stay pending until a live run.",
    "Unchanged geometry does not prove that no history entry was written. Inspect history manually.",
    "CST messages are context and may be inherited, repeated or truncated; novelty does not prove causality.",
    "Selected interior points and scalar measurements do not certify arbitrary geometry or complete topology.",
    "Existing-project reuse without reset is deferred. Reset retains logs and reports.",
]


def boolean_vba(case):
    operation = case["tool"].removeprefix("cst_boolean_").capitalize()
    args = case["arguments"]
    return f'Solid.{operation} "{args["solid1"]}", "{args["solid2"]}"'


def assess_windows(decoded):
    """The server's window inventory includes nonmodal CST tool windows."""
    assessments = []
    for item in walk_dicts(decoded["parsed_payloads"]):
        context = item.get("cst_dialogs")
        if not isinstance(context, dict) or not context.get("count"):
            continue
        windows = context.get("dialogs")
        if not isinstance(windows, list) or len(windows) != context["count"]:
            assessments.append({"context": context, "unresolved": True})
            continue
        for window in windows:
            # Narrowly recognize the observed CST Messages tool window. Never
            # dismiss it. Other windows retain the conservative stop behavior.
            informational = (
                isinstance(window, dict)
                and window.get("title") == "Messages"
                and str(window.get("class", "")).startswith("Qt")
                and str(window.get("class", "")).endswith("QWindowToolSaveBits")
                and window.get("match") == "cst_process"
                and window.get("texts") == []
                and window.get("full_text") == ""
                and not window.get("modal")
                and not window.get("blocking")
            )
            assessments.append(
                {
                    "window": window,
                    "informational_messages_window": informational,
                    "unresolved": not informational,
                }
            )
    return assessments


def reject_reparse(path):
    """Also reject Windows reparse types beyond symlinks and junctions."""
    reject_links(path)
    for target in (path, *path.parents):
        if target.exists() and getattr(target.lstat(), "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0
        ):
            raise StopTest(f"Reparse points are forbidden: {target}")
    if path.is_dir():
        for child in path.iterdir():
            reject_reparse(child)


class WorkspaceLock:
    """Retained file with an invocation-scoped, nonblocking OS lock."""

    def __init__(self, workspace):
        self.workspace = workspace

    def __enter__(self):
        reject_reparse(self.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        target = self.workspace / "workspace.lock"
        reject_reparse(target)
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
            raise StopTest("Boolean workspace is already in use; wait for that invocation") from exc
        return self

    def __exit__(self, *args):
        self.stream.close()


class BooleanTest:
    fixtures = FIXTURES
    setup = SETUP
    fixed_vba = FIXED_VBA
    shape_points = SHAPE_POINTS
    shape_queries = SHAPE_QUERIES
    existence_queries = EXISTENCE_QUERIES
    parameter_states = STATES
    expected_geometry = EXPECTED
    boolean_calls = BOOLEAN_CALLS
    expected_membership = MEMBERSHIP
    limitations = LIMITATIONS

    def __init__(self, options, workspace, project_stem, owner, *, invocation=None, case_id=None):
        self.options = options
        if Path(project_stem).name != project_stem or not project_stem or "." in project_stem:
            raise StopTest("Project stem must be a fixed, simple name")
        self.work = workspace.absolute()
        self.stem = project_stem
        self.owner = owner
        self.case_id = case_id
        self.invocation = invocation or uuid.uuid4().hex
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
        self.inventories = []
        self.actual_units = None
        self.message_seen = set()
        self.message_baseline_taken = False
        self.manifest = None
        self.project = self.work / f"{self.stem}.cst"
        reject_reparse(self.work)
        self.work.mkdir(parents=True, exist_ok=True)
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
            reject_reparse(self.work / name)
            reject_reparse((self.work / name).with_suffix(Path(name).suffix + ".tmp"))
        self.calls = (self.work / "mcp_calls.jsonl").open("a", encoding="utf-8")
        self.messages = (self.work / "cst_messages.jsonl").open("a", encoding="utf-8")
        self.stderr = (self.work / "server_stderr.log").open("a", encoding="utf-8")
        self.stderr.write(json.dumps(self.tag({"event": "stderr_start"})) + "\n")
        self.stderr.flush()
        self.env = dict(os.environ)
        libs = Path(options.cst_path) / "AMD64" / "python_cst_libraries"
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
            "stage": options.stage,
            "case_id": case_id,
            "started": timestamp(),
            "revision": revision,
            "script_sha256": sha256(Path(__file__)),
            "python": sys.version,
            "packages": {p: importlib.metadata.version(p) for p in ("mcp", "jsonschema")},
            "options": vars(options),
            "workspace": str(self.work),
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
            "fixed_vba": sorted(self.fixed_vba),
            "fixtures": self.fixtures,
            "parameter_states": self.parameter_states,
            "expected_geometry": self.expected_geometry,
            "boolean_calls": self.boolean_calls,
            "shape_points": self.shape_points,
            "expected_membership": self.expected_membership,
            "references": {},
            "limitations": self.limitations,
        }
        self.metadata_event("invocation_start")

    def generated_paths(self):
        """Accept only same-stem files and the companion, all within this scope."""
        names = self.manifest.get("generated_paths")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise StopTest("Invalid boolean generated paths")
        if not {f"{self.stem}.cst", self.stem} <= set(names) or len(names) != len(set(names)):
            raise StopTest("Incomplete/duplicate boolean generated paths")
        paths = []
        for name in names:
            if (
                Path(name).name != name
                or not (name == self.stem or name.startswith(self.stem + "."))
                or Path(name).suffix.lower() in LOCK_SUFFIXES
            ):
                raise StopTest(f"Unsafe generated path: {name!r}")
            path = self.work / name
            reject_reparse(path)
            if path.resolve().parent != self.work.resolve():
                raise StopTest(f"Generated path escapes boolean workspace: {path}")
            if path.exists() and ((name == self.stem) != path.is_dir()):
                raise StopTest(f"Unexpected generated path type: {path}")
            paths.append(path)
        return paths

    def project_snapshot(self):
        return snapshot([(p, p.name) for p in self.generated_paths() if p.exists()])

    def verify_manifest(self):
        if (
            self.manifest.get("owner") != self.owner
            or self.manifest.get("version") != 1
            or self.manifest.get("project") != str(self.project)
            or self.manifest.get("generation_state") not in {"creating", "running", "ready"}
            or self.manifest.get("fixture") not in {"absent", "creating", "ready"}
            or not isinstance(self.manifest.get("creation_requested"), bool)
            or self.manifest.get("created_path", str(self.project)) != str(self.project)
        ):
            raise StopTest("Boolean ownership/state inconsistent; no automatic deletion")
        self.generated_paths()
        owned = set(self.manifest["generated_paths"])
        unexpected = [p.name for p in self.work.glob(self.stem + "*") if p.name not in owned]
        if unexpected:
            raise StopTest(f"Unidentified project paths; refusing reuse/reset: {unexpected}")

    def inspect_project(self):
        """Validate all reset targets without deleting or reserving anything."""
        path = self.work / "workspace.json"
        if path.exists():
            self.manifest = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(self.manifest, dict):
                raise StopTest("Invalid boolean manifest")
            self.verify_manifest()
            ensure_closed(self.project)
            if self.options.reset:
                paths = self.generated_paths()  # Check every target before the first deletion.
                if any(p.exists() for p in paths) and not self.manifest["creation_requested"]:
                    raise StopTest(
                        "Project files appeared before client creation; ownership unverified"
                    )
                return paths
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
                        "Boolean project changed or incomplete since checkpoint; inspect and "
                        "restore it or explicitly --reset after saving/closing. No automatic adoption."
                    )
                raise StopTest("Reuse without reset is deferred; save/close and explicitly --reset")
        elif any(self.work.glob(self.stem + "*")):
            raise StopTest("Project paths exist without boolean ownership; refusing create/reset")
        return []

    def reserve_project(self, paths):
        """Delete only previously verified targets and reserve a fresh blank project."""
        if self.manifest is not None:
            self.metadata_event("reset_start", prior_manifest=self.manifest)
        for target in paths:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        self.manifest = {
            "owner": self.owner,
            "version": 1,
            "project": str(self.project),
            "generation_state": "creating",
            "fixture": "absent",
            "generated_paths": [f"{self.stem}.cst", self.stem],
            "creation_invocation": self.invocation,
            "created_at": timestamp(),
            "creation_requested": False,
            "source": "new blank MWS project via cst_create_project; no source copy",
        }
        self.store_manifest()
        self.metadata_event("blank_workspace_reserved", manifest=self.manifest)

    def prepare_project(self):
        self.reserve_project(self.inspect_project())

    def tag(self, record):
        return dict(
            record,
            invocation=self.invocation,
            stage=self.options.stage,
            case_id=self.case_id,
            project=str(self.project)
            if self.case_id or self.options.stage == "operations"
            else None,
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
        with (self.work / "metadata.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    serialize(self.tag(dict(event=event, **details, metadata=self.metadata)))
                )
                + "\n"
            )
        write_json(self.work / "metadata.json", self.tag(self.metadata))

    def check(self, scope, passed, **evidence):
        record = self.tag(dict(scope=scope, passed=bool(passed), **evidence))
        self.checks.append(record)
        self.event(dict(event="check", **record))
        if not passed:
            raise StopTest(f"{self.phase}: {scope} failed; inspect reports and owned project")

    def store_manifest(self):
        write_json(self.work / "workspace.json", self.tag(self.manifest))

    def validate(self, name, arguments):
        if name not in self.catalog:
            raise StopTest(f"Required tool missing from effective MCP catalog: {name}")
        schema = self.catalog[name]["inputSchema"]
        validator = validator_for(schema)
        validator.check_schema(schema)
        validator(schema).validate(arguments)

    async def request(self, session, name, arguments=None, *, protocol=False, expected_case=None):
        if self.unknown:
            raise UnknownState("Further MCP requests forbidden after indeterminate execution")
        arguments = arguments or {}
        if (
            self.options.preflight
            and not protocol
            and name
            not in {
                "cst_create_brick",
                *[tool for tool, _ in BOOLEAN_CALLS],
                "cst_set_parameter",
                "cst_execute_vba",
            }
        ):
            raise StopTest(f"Preflight forbids connection/status/project lifecycle calls: {name}")
        if expected_case is not None and (
            protocol
            or self.options.stage != "errors"
            or self.case_id != expected_case["id"]
            or expected_case not in ERROR_CASES
            or (name, arguments) != (expected_case["tool"], expected_case["arguments"])
        ):
            raise StopTest("Expected-rejection path is limited to the declared current case")
        local_failure = None
        if not protocol:
            try:
                self.validate(name, arguments)
            except ValidationError as exc:
                if expected_case is None or expected_case["id"] not in SCHEMA_CASE_IDS:
                    raise
                local_failure = {
                    "message": exc.message,
                    "instance_path": list(exc.absolute_path),
                    "schema_path": list(exc.absolute_schema_path),
                }
            if expected_case is not None and expected_case["id"] in SCHEMA_CASE_IDS:
                self.check(
                    "declared schema-negative input rejected locally", local_failure is not None
                )
        if name == "cst_execute_vba" and arguments.get("code") not in self.fixed_vba:
            raise StopTest("Client raw VBA is restricted to fixed setup/read-only blocks")
        self.sequence += 1
        timeout = (
            self.options.connection_timeout if name == "cst_connect" else self.options.call_timeout
        )
        base = {
            "tool": name,
            "arguments": arguments,
            "timeout_seconds": timeout,
            "local_validation_failure": local_failure,
            "expected_rejection": expected_case,
        }
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
        window_assessments = []
        if expected_case is not None:
            payload = decoded["payload"]
            # Text-only validation errors are known rejections, but text-only native
            # errors or unrecognized statuses cannot establish execution safety.
            if expected_case["layer"] == "native":
                unknown |= not isinstance(payload, dict) or payload.get("status") not in {
                    "error",
                    "executed",
                    "busy",
                    "offline",
                }
                if isinstance(payload, dict) and payload.get("status") == "error":
                    # Without the reviewed native exception envelope, a generic
                    # error does not establish whether a native call completed.
                    unknown |= not (
                        payload.get("label") and payload.get("vba") == boolean_vba(expected_case)
                    )
            window_assessments = assess_windows(decoded)
            unknown |= any(item["unresolved"] for item in window_assessments)
        if unknown:
            self.unknown = True
        record = dict(
            event="request_complete",
            **base,
            response=raw,
            **decoded,
            window_assessments=window_assessments,
            text_diagnostics=[
                block.get("text", "")
                for block in raw.get("content", [])
                if block.get("type") == "text"
            ],
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
        if expected_case is not None:
            if any(
                item.get("status") in {"busy", "offline"}
                or item.get("code") in {"results_exist", "not_connected", "no_project"}
                for item in walk_dicts(decoded["parsed_payloads"])
            ):
                raise StopTest(
                    f"{self.phase}/{name}: infrastructure failure, not expected rejection"
                )
            if not isinstance(payload, dict) and not decoded["isError"]:
                self.unknown = True
                raise UnknownState(f"{self.phase}/{name}: ambiguous rejection response")
            return self.results[-1]
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
            "active project is owned boolean project",
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
                        if (
                            self.options.stage == "operations"
                            and self.connected
                            and not self.unknown
                        ):
                            await self.messages_at(session)
                        raise
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.exit_code = 130
            self.reason = "Interrupted; no CST cleanup calls; project and logs preserved"
        except BaseException as exc:
            logger.exception("Boolean client stopped; finalizing local reports")
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
        print(f"{self.reason}\nReports: {self.work}\nExit: {self.exit_code}", flush=True)
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
        return payload

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

    async def save_owned(self, session):
        await self.owned_info(session)
        payload = await self.accepted(session, "cst_save_project", status="saved")
        self.check(
            "save returned owned boolean path",
            Path(payload.get("path") or "").resolve() == self.project.resolve(),
            payload=payload,
        )
        return payload

    async def checkpoint(self, session):
        await self.save_owned(session)
        await self.messages_at(session)
        await self.accepted(session, "cst_close_project", status="closed")
        state = await self.request(session, "cst_connection_status")
        self.check(
            "owned boolean project closed",
            state.get("project_open") is False,
            payload=state,
        )
        ensure_closed(self.project)
        self.check(
            "saved project and companion exist",
            self.project.is_file() and self.project.with_suffix("").is_dir(),
        )
        sidecars = [p.name for p in self.work.glob(self.stem + ".*") if p.is_file()]
        self.manifest["generated_paths"] = [self.stem, *sorted(sidecars)]
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

    async def set_state(self, session, shift, *, rebuild):
        payload = await self.accepted(
            session,
            "cst_set_parameter",
            {"name": PARAMETER, "value": shift, "rebuild": rebuild},
            status="ok",
        )
        self.check(
            "parameter storage and native rebuild acceptance",
            payload.get("history_written") is False and payload.get("rebuilt") is rebuild,
            payload=payload,
            state={PARAMETER: shift},
            coverage="Acceptance only; independent list/get and geometry readbacks follow",
        )

    async def read_catalog(self, session):
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
        write_json(
            self.work / "tool_catalog.json", self.tag({"tools": list(self.catalog.values())})
        )
        for name, rel in REFERENCES.items():
            path = Path(self.options.cst_path) / rel
            if not path.is_file():
                raise StopTest(f"Installed reference unavailable; inspect before live use: {path}")
            self.metadata["references"][name] = {"path": str(path), "sha256": sha256(path)}
        self.metadata_event("reference_provenance")

    async def catalog_and_preflight(self, session):
        await self.read_catalog(session)
        plan = [
            *FIXTURES,
            *BOOLEAN_CALLS,
            ("cst_connect", {"mode": "new"}),
            ("cst_create_project", {"path": str(self.project), "project_type": "MWS"}),
            ("cst_open_project", {"path": str(self.project)}),
            ("cst_get_parameter", {"name": PARAMETER}),
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
        plan += [
            ("cst_set_parameter", {"name": PARAMETER, "value": shift, "rebuild": rebuild})
            for shift, rebuild in ((2, False), (3, True))
        ]
        plan += [("cst_execute_vba", {"code": code}) for code in sorted(FIXED_VBA)]
        for name, args in plan:
            self.validate(name, args)
        bounds = self.catalog["cst_create_brick"]["inputSchema"]["properties"]
        self.check(
            "brick expression catalog contract",
            all(
                set(bounds[key].get("type", [])) == {"number", "string"}
                for key in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
            ),
        )
        for name, _ in BOOLEAN_CALLS:
            schema = self.catalog[name]["inputSchema"]
            self.check(
                f"{name} qualified pair schema",
                set(schema.get("required", [])) == {"solid1", "solid2"}
                and all(
                    schema["properties"][key].get("type") == "string"
                    for key in ("solid1", "solid2")
                ),
                schema=schema,
            )
        self.check(
            "catalog presence and planned schemas",
            True,
            planned_calls=plan,
            tools=sorted({name for name, _ in plan}),
        )
        if not self.options.preflight:
            return
        self.phase = "offline_preflight"
        self.check(
            "child CST access disabled",
            self.env["CST_CONNECT_MODE"] == "disabled",
            executed_in_cst=False,
        )
        await self.offline_bricks(session)
        for (operation, _), (name, args) in zip(OPERATIONS.items(), BOOLEAN_CALLS):
            payload = await self.accepted(session, name, args, status="offline")
            expected = f'Solid.{operation} "{args["solid1"]}", "{args["solid2"]}"'
            self.check(
                f"offline Solid.{operation} dedicated call",
                payload.get("vba") == expected
                and payload.get("operation") == operation.lower()
                and all(payload.get(key) == value for key, value in args.items()),
                expected=expected,
                actual=payload,
                executed_in_cst=False,
                expected_B_afterward="present" if operation == "Insert" else "absent",
            )
        for shift, rebuild in ((2, False), (3, True)):
            payload = await self.accepted(
                session,
                "cst_set_parameter",
                {"name": PARAMETER, "value": shift, "rebuild": rebuild},
                status="offline",
            )
            self.check(
                "offline dedicated parameter storage",
                payload.get("vba") == f'StoreParameter "{PARAMETER}", "{shift}"',
                payload=payload,
                requested_rebuild=rebuild,
                executed_in_cst=False,
                parameter_echo_is_measurement=False,
            )
        await self.offline_fixed(session)
        self.exit_code = 0
        self.reason = "Real MCP catalog/schema and offline VBA checks passed; native CST execution and manual inspection pending"

    async def offline_bricks(self, session):
        for name, args in self.fixtures:
            payload = await self.accepted(session, name, args, status="offline")
            expected = "\n".join(
                [
                    "With Brick",
                    "  .Reset",
                    f'  .Name "{args["name"]}"',
                    f'  .Component "{args["component"]}"',
                    f'  .Material "{args["material"]}"',
                    *[
                        f'  .{axis.upper()}range "{args[axis + "_min"]}", "{args[axis + "_max"]}"'
                        for axis in ("x", "y", "z")
                    ],
                    "  .Create",
                    "End With",
                ]
            )
            self.check(
                "offline brick expressions and materials preserved",
                payload.get("vba") == expected,
                arguments=args,
                expected=expected,
                actual=payload.get("vba"),
                executed_in_cst=False,
            )

    async def offline_fixed(self, session):
        for query in sorted(self.fixed_vba):
            payload = await self.accepted(
                session, "cst_execute_vba", {"code": query}, status="offline"
            )
            self.check(
                "offline fixed setup/readback preserved",
                payload.get("vba") == query,
                query=query,
                executed_in_cst=False,
            )

    async def measure_shape(self, session, shape, expected, shift, completed):
        existence_query = self.existence_queries[shape]
        existence_payload = await self.accepted(
            session, "cst_execute_vba", {"code": existence_query}, status="ok"
        )
        existence_records = parse_records(existence_payload.get("output", ""), {"EXISTS"})
        exists = parse_native_bool(existence_records["EXISTS"])
        self.check(
            "native expected operand existence or removal",
            exists is (expected is not None),
            shape=shape,
            actual=exists,
            expected=expected is not None,
            expected_removal_of_B=expected is None,
            query=existence_query,
        )
        # Deleted B is an expected result. Do not even request its measurements.
        query = self.shape_queries[shape] if exists else existence_query
        payload = existence_payload
        if exists:
            payload = await self.accepted(session, "cst_execute_vba", {"code": query}, status="ok")
        keys = {"EXISTS"}
        if expected is not None and exists:
            keys |= {"IS_SOLID", "IS_HYBRID", "VOLUME", "AREA"}
            keys |= {f"POINT_{i}" for i in range(len(self.shape_points[shape]))}
        records = parse_records(payload.get("output", ""), keys)
        exists = parse_native_bool(records["EXISTS"])
        types = {
            key: parse_native_bool(records[key])
            for key in ("IS_SOLID", "IS_HYBRID")
            if key in records
        }
        actual = {key: number(records[key]) for key in ("VOLUME", "AREA") if key in records}
        points = [
            {
                "coordinates_mm": coordinates,
                "actual": parse_native_bool(records[f"POINT_{i}"]),
                "expected": expected["membership"][i],
            }
            for i, coordinates in enumerate(self.shape_points[shape])
            if f"POINT_{i}" in records
        ]
        measurement = self.tag(
            {
                "shape": shape,
                "shift": shift,
                "completed_operations": sorted(completed),
                "units": self.actual_units,
                "native": records,
                "exists": exists,
                "expected_exists": expected is not None,
                "shape_types": types,
                "actual": actual,
                "expected": expected,
                "point_membership": points,
                "expected_removal_of_B": expected is None,
                "absent_operand_measurements": "skipped by native existence guard"
                if expected is None
                else None,
                "quantity_units": {"VOLUME": "mm^3", "AREA": "mm^2"},
                "relative_tolerance": 1e-6,
                "absolute_tolerance": 1e-6,
                "query": query,
                "query_source": self.metadata["references"]["solid"],
                "existence_query": existence_query,
                "existence_response_payload": existence_payload,
                "raw_response_payload": payload,
            }
        )
        self.measurements.append(measurement)
        self.event(dict(event="measurement", **measurement))
        self.check(
            "native expected operand existence or removal",
            exists is (expected is not None),
            shape=shape,
            actual=exists,
            expected=expected is not None,
            expected_removal_of_B=expected is None,
            query=query,
        )
        if expected is None or not exists:
            return measurement
        self.check(
            "native solid body type",
            types == {"IS_SOLID": True, "IS_HYBRID": False},
            shape=shape,
            actual=types,
            expected={"IS_SOLID": True, "IS_HYBRID": False},
        )
        for key in ("VOLUME", "AREA"):
            self.check(
                f"native {shape} {key}",
                close_number(actual[key], expected[key]),
                expected=expected[key],
                actual=actual[key],
                units=measurement["quantity_units"][key],
                relative_tolerance=1e-6,
                absolute_tolerance=1e-6,
                query=query,
            )
        self.check(
            "native interior point membership",
            all(point["actual"] is point["expected"] for point in points),
            shape=shape,
            points=points,
            query=query,
        )
        return measurement

    async def measure(self, session, shift, completed, *, focus=None):
        await self.owned_info(session)
        await self.units(session)
        parameters = await self.parameters(session, {PARAMETER: shift})
        shapes = await self.shapes(session)
        expected = expected_shapes(shift, completed)
        materials = {shape: value["material"] for shape, value in expected.items()}
        removed = [shape for shape in SHAPE_QUERIES if shape not in expected]
        inventory = self.tag(
            {
                "shift": shift,
                "parameters": parameters,
                "units": self.actual_units,
                "completed_operations": sorted(completed),
                "focus": focus,
                "actual": shapes,
                "expected": materials,
                "expected_removed_operands": removed,
                "query": SHAPES_QUERY,
                "query_source": self.metadata["references"]["solid"],
            }
        )
        self.inventories.append(inventory)
        self.event(dict(event="inventory", **inventory))
        self.check(
            "native complete named shape and material inventory",
            shapes == materials,
            actual=shapes,
            expected=materials,
            expected_removed_operands=removed,
            query=SHAPES_QUERY,
        )
        targets = (
            list(SHAPE_QUERIES) if focus is None else [f"Bool{focus}:A", f"Bool{focus}:B", WITNESS]
        )
        for shape in targets:
            await self.measure_shape(session, shape, expected.get(shape), shift, completed)
        await self.messages_at(session)

    async def connect_isolated(self, session):
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

    async def create_blank(self, session):
        self.live_attempted = True
        self.manifest["generation_state"] = "running"
        self.store_manifest()
        self.phase = "create_blank_project"
        if any(self.work.glob(self.stem + "*")):
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

    async def live(self, session):
        await self.connect_isolated(session)
        await self.create_blank(session)
        self.phase = "setup"
        self.manifest["fixture"] = "creating"
        self.store_manifest()
        setup = await self.accepted(session, "cst_execute_vba", {"code": SETUP}, status="ok")
        self.check(
            "fixed setup captured outside history",
            setup.get("output", "").strip() == "SETUP_DONE",
            payload=setup,
        )
        await self.units(session)
        await self.set_state(session, 2, rebuild=False)
        await self.parameters(session, {PARAMETER: 2})
        self.phase = "create_operands_and_witness"
        for name, args in FIXTURES:
            await self.accepted(session, name, args, status="executed")
        completed = set()
        self.phase = "initial_operands_readback"
        await self.measure(session, 2, completed)
        for operation, (name, args) in zip(OPERATIONS, BOOLEAN_CALLS):
            self.phase = f"before_{operation.lower()}"
            await self.measure(session, 2, completed, focus=operation)
            self.phase = f"execute_{operation.lower()}"
            payload = await self.accepted(session, name, args, status="executed")
            self.check(
                "boolean operand acceptance",
                payload.get("operation") == operation.lower()
                and all(payload.get(key) == value for key, value in args.items()),
                payload=payload,
                expected_B_afterward="present" if operation == "Insert" else "absent",
                coverage="Acceptance only; the following native readbacks establish effects",
            )
            completed.add(operation)
            self.phase = f"after_{operation.lower()}"
            await self.measure(session, 2, completed, focus=operation)
        self.phase = "completed_initial_readback"
        await self.measure(session, 2, completed)
        self.phase = "updated_native_rebuild"
        # Only this parameter changes. No brick creation or boolean replay after this point.
        reconstruction_start = len(self.results)
        await self.set_state(session, 3, rebuild=True)
        await self.measure(session, 3, completed)
        self.check(
            "booleans invoked exactly once",
            [r["tool"] for r in self.results if r["tool"].startswith("cst_boolean_")]
            == [name for name, _ in BOOLEAN_CALLS],
        )
        self.phase = "save_updated_checkpoint"
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
        await self.measure(session, 3, completed)
        self.check(
            "history reconstruction and persistence without fixture replay",
            not any(
                r["tool"] == "cst_create_brick" or r["tool"].startswith("cst_boolean_")
                for r in self.results[reconstruction_start:]
            ),
            tools_since_reconstruction=[r["tool"] for r in self.results[reconstruction_start:]],
        )
        self.phase = "final_save_close"
        await self.checkpoint(session)
        self.phase = "disconnect"
        await self.accepted(session, "cst_disconnect", status="disconnected")
        self.connected = False
        self.exit_code = 0
        self.reason = "Live boolean operations, parameter reconstruction and persistence completed within recorded coverage; manual inspection remains separate and pending"

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
            "implementation": "Dedicated brick and boolean tools, one native parameter rebuild, guarded native readbacks outside history, save/close/fingerprint/reopen.",
            "expected_parameter_states": STATES,
            "expected_geometry": EXPECTED,
            "boolean_calls": BOOLEAN_CALLS,
            "shape_points": SHAPE_POINTS,
            "expected_membership": MEMBERSHIP,
            "measurements": self.measurements,
            "inventories": self.inventories,
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
        lines = [
            "# Latest boolean operations invocation",
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
        self.finish_reports(summary, lines)

    def finish_reports(self, summary, lines):
        write_json(self.work / "summary.json", self.tag(summary))
        (self.work / "summary.md").write_text("\n".join(lines), encoding="utf-8")
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


def fixture_differences(before, after, path="fixture"):
    """Compare native values using the established tolerance, not response echoes."""
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            if key not in before or key not in after:
                result.append(
                    {"path": f"{path}.{key}", "before": before.get(key), "after": after.get(key)}
                )
            else:
                result.extend(fixture_differences(before[key], after[key], f"{path}.{key}"))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [
            difference
            for index, (left, right) in enumerate(zip(before, after))
            for difference in fixture_differences(left, right, f"{path}[{index}]")
        ]
    numeric = all(
        isinstance(value, (int, float)) and not isinstance(value, bool) for value in (before, after)
    )
    equal = (
        close_number(before, after) if numeric else type(before) is type(after) and before == after
    )
    return [] if equal else [{"path": path, "before": before, "after": after}]


class BooleanErrorCase(BooleanTest):
    fixtures = ERROR_FIXTURES
    setup = ERROR_SETUP
    fixed_vba = ERROR_FIXED_VBA
    shape_points = ERROR_POINTS
    shape_queries = ERROR_SHAPE_QUERIES
    existence_queries = ERROR_EXISTENCE_QUERIES
    parameter_states: ClassVar[dict] = {}
    expected_geometry = ERROR_EXPECTED
    expected_membership: ClassVar[dict] = {
        shape: value["membership"] for shape, value in ERROR_EXPECTED.items()
    }
    limitations = ERROR_LIMITATIONS

    def __init__(self, options, workspace, case, invocation):
        self.case = case
        self.boolean_calls = [(case["tool"], case["arguments"])]
        self.collecting = False
        self.outcome = "not_attempted"
        self.communication = "not_attempted"
        self.integrity = "not_attempted"
        self.observation = None
        self.baseline = None
        self.final = None
        self.differences = None
        self.diagnostics = {}
        self.preflight_result = "not_attempted"
        super().__init__(
            options, workspace, case["id"], ERRORS_OWNER, invocation=invocation, case_id=case["id"]
        )
        self.metadata.update(
            case=case,
            expected_geometry=ERROR_EXPECTED,
            shape_points=ERROR_POINTS,
            parameter_states={},
            boolean_calls=[(case["tool"], case["arguments"])],
            expected_membership={
                shape: value["membership"] for shape, value in ERROR_EXPECTED.items()
            },
            limitations=ERROR_LIMITATIONS,
        )
        self.metadata_event("case_configured")

    def inspect_project(self):
        paths = super().inspect_project()
        if paths and self.manifest.get("saved_files") is not None:
            recorded = {item["target"] for item in self.manifest["saved_files"]}
            actual = {item["target"] for item in self.project_snapshot()}
            if actual - recorded:
                raise StopTest(
                    f"Unowned case files appeared after checkpoint: {sorted(actual - recorded)}"
                )
        return paths

    def check(self, scope, passed, **evidence):
        # Infrastructure, ownership, idle and command acceptance stay fatal.
        # Only post-call native integrity failures are collected to completion.
        if self.collecting and scope.startswith(("native ", "effective units ")):
            record = self.tag(dict(scope=scope, passed=bool(passed), **evidence))
            self.checks.append(record)
            self.event(dict(event="check", **record))
        else:
            super().check(scope, passed, **evidence)

    def observe_rejection(self, response):
        structured = isinstance(response["payload"], dict)
        payload = response["payload"] if structured else {}
        text = "\n".join(response["text_diagnostics"])
        message = str(payload.get("message", ""))
        # Do not treat echoed operands or inherited message tails as the error
        # diagnostic when a JSON envelope supplies its own message field.
        diagnostics = message if structured else text
        observed, source = None, None
        if "input validation error:" in diagnostics.lower():
            observed = "schema"
            source = "MCP input validation diagnostic; registry request registration"
        elif message == "Component path cannot be empty":
            observed = "server_argument"
            source = "boolean.handle -> validate_component_path, before VBA generation"
        elif (
            message.startswith("Invalid argument:")
            and "solid2" in message
            and "line feed" in message
        ):
            observed = "server_argument"
            source = "guard_handler -> check_arguments, before boolean.handle"
        elif (
            self.case["layer"] == "native"
            and payload.get("status") == "error"
            and payload.get("label")
            and payload.get("vba") == boolean_vba(self.case)
        ):
            observed = "native"
            source = "session.run_history native exception envelope (label and exact VBA)"
        expected = self.case["layer"]
        if expected == "schema":
            useful = (
                ("solid2" in diagnostics and "required" in diagnostics)
                if self.case_id == "01_missing_argument"
                else ("123" in diagnostics and "string" in diagnostics)
            )
        elif expected == "server_argument":
            useful = observed == expected
        else:
            operand = "MissingA" if self.case_id == "05_missing_solid1" else "MissingB"
            lower = diagnostics.lower()
            useful = operand.lower() in lower or (
                any(word in lower for word in ("solid", "shape", "object"))
                and any(word in lower for word in ("not exist", "not found", "missing", "unknown"))
            )
        rejected = response["isError"] is True or payload.get("status") == "error"
        passed = bool(
            rejected
            and observed == expected
            and useful
            and payload.get("status") not in {"executed", "offline", "busy"}
        )
        self.communication = "passed" if passed else "failed"
        self.observation = self.tag(
            {
                "expected_layer": expected,
                "observed_layer": observed,
                "layer_evidence": source,
                "status": payload.get("status"),
                "isError": response["isError"],
                "diagnostic": diagnostics,
                "useful_diagnostic": bool(useful),
                "execution_state": response["execution_state"],
                "communication_result": self.communication,
                "response_sequence": response["sequence"],
                "note": "An executed response is a communication failure even if fixture geometry is unchanged."
                if payload.get("status") == "executed"
                else None,
            }
        )
        self.event(dict(event="rejection_assessment", **self.observation))

    async def read_fixture(self, session):
        await self.owned_info(session)
        await self.units(session)
        parameters = await self.parameters(session, {})
        shapes = await self.shapes(session)
        materials = {shape: expected["material"] for shape, expected in ERROR_EXPECTED.items()}
        self.check(
            "native complete named shape and material inventory",
            shapes == materials,
            actual=shapes,
            expected=materials,
            query=SHAPES_QUERY,
        )
        inventory = self.tag(
            {
                "actual": shapes,
                "expected": materials,
                "parameters": parameters,
                "units": self.actual_units,
                "query": SHAPES_QUERY,
            }
        )
        self.inventories.append(inventory)
        self.event(dict(event="inventory", **inventory))
        measured = {}
        for shape, expected in ERROR_EXPECTED.items():
            measurement = await self.measure_shape(session, shape, expected, None, set())
            measured[shape] = {
                "exists": measurement["exists"],
                "body_types": measurement["shape_types"],
                "quantities": measurement["actual"],
                "membership": [point["actual"] for point in measurement["point_membership"]],
            }
        for shape in ("BoolError:MissingA", "BoolError:MissingB"):
            payload = await self.accepted(
                session, "cst_execute_vba", {"code": self.existence_queries[shape]}, status="ok"
            )
            exists = parse_native_bool(
                parse_records(payload.get("output", ""), {"EXISTS"})["EXISTS"]
            )
            self.check(
                "native missing operands absent",
                exists is False,
                shape=shape,
                actual=exists,
                query=self.existence_queries[shape],
                payload=payload,
            )
            measured[shape] = {"exists": exists}
        result = self.tag(
            {
                "units": dict(self.actual_units),
                "parameters": parameters,
                "materials": shapes,
                "shapes": measured,
            }
        )
        self.event({"event": "fixture_snapshot", "snapshot": result})
        return result

    async def preflight(self, session):
        self.phase = "offline_fixture_generation"
        await self.offline_bricks(session)
        await self.offline_fixed(session)
        self.phase = "offline_invalid_request"
        if self.case["layer"] == "native":
            payload = await self.accepted(
                session, self.case["tool"], self.case["arguments"], status="offline"
            )
            self.check(
                "offline missing-solid dedicated VBA",
                payload.get("vba") == boolean_vba(self.case)
                and payload.get("operation") == self.case["tool"].removeprefix("cst_boolean_")
                and all(payload.get(key) == value for key, value in self.case["arguments"].items()),
                payload=payload,
                executed_in_cst=False,
                native_outcome="pending",
            )
            self.observation = self.tag(
                {
                    "expected_layer": "native",
                    "observed_layer": None,
                    "status": "offline",
                    "diagnostic": "VBA generation only; native rejection not attempted",
                }
            )
            self.communication = "pending"
            self.outcome = "not_attempted"
        else:
            response = await self.request(
                session, self.case["tool"], self.case["arguments"], expected_case=self.case
            )
            self.observe_rejection(response)
            self.outcome = "inconclusive" if self.communication == "passed" else "failed"
        self.integrity = "pending"
        self.preflight_result = "passed" if self.communication != "failed" else "failed"
        self.exit_code = 0 if self.preflight_result == "passed" else 1
        self.reason = "Disabled-server checks completed; live fixture integrity and native missing-solid outcomes remain pending"

    async def live_case(self, session):
        self.connected = True
        self.outcome = "inconclusive"
        await self.create_blank(session)
        self.phase = "fixed_setup"
        self.manifest["fixture"] = "creating"
        self.store_manifest()
        setup = await self.accepted(session, "cst_execute_vba", {"code": self.setup}, status="ok")
        self.check(
            "fixed setup captured outside history",
            setup.get("output", "").strip() == "SETUP_DONE",
            payload=setup,
        )
        self.phase = "create_fixture"
        for name, arguments in self.fixtures:
            await self.accepted(session, name, arguments, status="executed")
        self.phase = "baseline_readback"
        self.baseline = await self.read_fixture(session)
        self.phase = "save_initial_fixture"
        await self.save_owned(session)
        self.diagnostics["baseline"] = await self.messages_at(session)
        self.phase = "invalid_request"
        response = await self.request(
            session, self.case["tool"], self.case["arguments"], expected_case=self.case
        )
        self.observe_rejection(response)
        self.phase = "final_readback"
        self.collecting = True
        integrity_start = len(self.checks)
        self.final = await self.read_fixture(session)
        self.diagnostics["final"] = await self.messages_at(session)
        keys = ("units", "parameters", "materials", "shapes")
        self.differences = fixture_differences(
            {key: self.baseline[key] for key in keys}, {key: self.final[key] for key in keys}
        )
        self.check(
            "native baseline/final fixture integrity",
            not self.differences,
            differences=self.differences,
            relative_tolerance=1e-6,
            absolute_tolerance=1e-6,
        )
        self.integrity = (
            "passed"
            if all(check["passed"] for check in self.checks[integrity_start:])
            else "failed"
        )
        self.collecting = False
        self.phase = "normal_save_close"
        await self.checkpoint(session)
        self.check(
            "one invalid dedicated boolean request",
            len([record for record in self.results if record["tool"].startswith("cst_boolean_")])
            == 1,
        )
        self.connected = False
        self.outcome = "passed" if self.communication == self.integrity == "passed" else "failed"
        self.exit_code = 0 if self.outcome == "passed" else 1
        self.reason = f"Live case {self.outcome}; communication {self.communication}; integrity {self.integrity}; closure confirmed"
        self.manifest["case_outcome"] = self.outcome
        self.store_manifest()

    def summary(self):
        return self.tag(
            {
                "case": self.case,
                "outcome": self.outcome,
                "communication_result": self.communication,
                "integrity_result": self.integrity,
                "observed_response": self.observation,
                "preflight_result": self.preflight_result,
                "preflight": self.options.preflight,
                "exit_code": self.exit_code,
                "reason": self.reason,
                "indeterminate": self.unknown,
                "baseline": self.baseline,
                "final": self.final,
                "differences": self.differences,
                "diagnostics": self.diagnostics,
                "checks": self.checks,
                "responses": self.results,
                "measurements": self.measurements,
                "inventories": self.inventories,
                "metadata": self.metadata,
                "limitations": ERROR_LIMITATIONS,
                "native_validation": "not attempted"
                if self.options.preflight or not self.live_attempted
                else self.outcome,
                "manual_inspection": "separate; inspect history and failed/inconclusive cases",
            }
        )

    def finalize(self):
        summary = self.summary()
        lines = [
            f"# Latest boolean error case: {self.case_id}",
            "",
            f"Invocation: `{self.invocation}`; stage: errors; phase: `{self.phase}`; sequence: {self.sequence}",
            f"Project: `{self.project}`",
            "",
            self.reason,
            "",
            f"Expected layer: {self.case['layer']}; observed: {(self.observation or {}).get('observed_layer') or 'unproven'}",
            f"Communication: {self.communication}; integrity: {self.integrity}; overall: {self.outcome}",
            f"Preflight checks: {self.preflight_result}",
            "",
            "Full responses, baseline/final differences, diagnostics and provenance: [summary.json](summary.json).",
            "Append-only evidence: [MCP calls](mcp_calls.jsonl), [CST messages](cst_messages.jsonl), [metadata](metadata.jsonl).",
            "",
            "Unchanged geometry does not prove history absence. CST message novelty does not establish causality.",
            "Manual inspection is separate from automated native validation.",
            "",
        ]
        self.finish_reports(summary, lines)


class BooleanErrors(BooleanTest):
    """One server and isolated CST instance; nine independent owned clients."""

    fixtures = ERROR_FIXTURES
    fixed_vba = ERROR_FIXED_VBA
    shape_points = ERROR_POINTS
    parameter_states: ClassVar[dict] = {}
    expected_geometry = ERROR_EXPECTED
    boolean_calls = ()
    expected_membership = BooleanErrorCase.expected_membership
    limitations = ERROR_LIMITATIONS

    def __init__(self, options, workspace):
        super().__init__(options, workspace, "errors", ERRORS_OWNER)
        self.cases = []
        self.active_case = None
        self.metadata.update(
            fixtures=ERROR_FIXTURES,
            fixed_vba=sorted(ERROR_FIXED_VBA),
            cases=ERROR_CASES,
            expected_geometry=ERROR_EXPECTED,
            parameter_states={},
            boolean_calls=[],
            shape_points=ERROR_POINTS,
            expected_membership={},
            limitations=ERROR_LIMITATIONS,
            project=None,
        )
        for case in ERROR_CASES:
            client = BooleanErrorCase(options, workspace / case["id"], case, self.invocation)
            client.env = dict(self.env)
            client.metadata["environment"] = dict(self.metadata["environment"])
            self.cases.append(client)
        self.metadata_event("errors_stage_configured")

    def prepare_project(self):
        plans = [(case, case.inspect_project()) for case in self.cases]
        # Revalidate all targets immediately before any deletion, across all cases.
        for case, paths in plans:
            ensure_closed(case.project)
            for path in paths:
                reject_reparse(path)
                if (
                    path.resolve().parent != case.work.resolve()
                    or case.work.resolve().parent != self.work.resolve()
                ):
                    raise StopTest(f"Reset target escapes owned case workspace: {path}")
        for case, paths in plans:
            case.reserve_project(paths)

    async def catalog_and_preflight(self, session):
        await self.read_catalog(session)
        self.check(
            "child CST access configuration",
            self.env["CST_CONNECT_MODE"] == ("disabled" if self.options.preflight else "manual"),
        )
        plan = [*ERROR_FIXTURES, ("cst_connect", {"mode": "new"})]
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
        plan += [("cst_execute_vba", {"code": code}) for code in sorted(ERROR_FIXED_VBA)]
        for case in self.cases:
            case.catalog = self.catalog
            case.metadata["references"] = self.metadata["references"]
            case.metadata["source_provenance"] = {
                str(path): sha256(ROOT / path)
                for path in (
                    "src/cst_mcp/tools/boolean.py",
                    "src/cst_mcp/validators.py",
                    "src/cst_mcp/vba_safety.py",
                    "src/cst_mcp/tools/registry.py",
                    "src/cst_mcp/session.py",
                )
            }
            case.metadata_event("effective_catalog_and_provenance", catalog=self.catalog)
            if case.case_id in SCHEMA_CASE_IDS:
                try:
                    case.validate(case.case["tool"], case.case["arguments"])
                except ValidationError as exc:
                    case.event(
                        {
                            "event": "planned_local_schema_rejection",
                            "message": exc.message,
                            "arguments": case.case["arguments"],
                        }
                    )
                else:
                    raise StopTest(
                        f"Declared schema-negative case accepted locally: {case.case_id}"
                    )
            plan.append(("cst_create_project", {"path": str(case.project), "project_type": "MWS"}))
            if case.case_id not in SCHEMA_CASE_IDS:
                plan.append((case.case["tool"], case.case["arguments"]))
        for name, arguments in plan:
            self.validate(name, arguments)
        for name in sorted({case["tool"] for case in ERROR_CASES}):
            schema = self.catalog[name]["inputSchema"]
            self.check(
                f"{name} pair schema",
                set(schema.get("required", [])) == {"solid1", "solid2"}
                and all(
                    schema["properties"][key].get("type") == "string"
                    for key in ("solid1", "solid2")
                ),
                schema=schema,
            )
        self.check(
            "effective catalog and planned schemas",
            True,
            planned_calls=plan,
            schema_negative_cases=sorted(SCHEMA_CASE_IDS),
        )
        if self.options.preflight:
            for case in self.cases:
                self.active_case = case
                self.phase = f"preflight_{case.case_id}"
                case.outcome = "inconclusive"
                await case.preflight(session)
            self.active_case = None
            self.exit_code = (
                0 if all(case.preflight_result == "passed" for case in self.cases) else 1
            )
            self.reason = (
                "Errors preflight checks passed; native missing-solid rejection and live fixture integrity pending"
                if self.exit_code == 0
                else "Errors preflight checks failed; inspect per-case evidence"
            )

    async def live(self, session):
        await self.connect_isolated(session)
        for case in self.cases:
            self.active_case = case
            self.phase = f"live_{case.case_id}"
            await case.live_case(session)
        self.active_case = None
        self.phase = "disconnect"
        await self.accepted(session, "cst_disconnect", status="disconnected")
        self.connected = False
        self.exit_code = 0 if all(case.outcome == "passed" for case in self.cases) else 1
        self.reason = "Live errors stage completed with all closures confirmed; " + (
            "all nine cases passed"
            if self.exit_code == 0
            else "case failures recorded; inspect evidence"
        )

    def finalize(self):
        if self.active_case is not None:
            self.unknown |= self.active_case.unknown
            self.active_case.reason = self.reason
            self.active_case.exit_code = 2 if self.unknown else self.exit_code
            self.active_case.outcome = "inconclusive"
            if self.active_case.phase == "invalid_request" and self.active_case.observation is None:
                response = next(
                    (
                        record
                        for record in reversed(self.active_case.results)
                        if record.get("expected_rejection")
                    ),
                    {},
                )
                self.active_case.observation = self.active_case.tag(
                    {
                        "expected_layer": self.active_case.case["layer"],
                        "observed_layer": None,
                        "status": (response.get("payload") or {}).get("status", "no response"),
                        "isError": response.get("isError"),
                        "diagnostic": response.get("text_diagnostics", self.reason),
                        "execution_state": "unknown"
                        if self.unknown
                        else response.get("execution_state"),
                        "response_sequence": response.get("sequence"),
                    }
                )
                self.active_case.communication = "inconclusive"
            if self.active_case.live_attempted and self.active_case.integrity == "not_attempted":
                self.active_case.integrity = "inconclusive"
        if self.unknown:
            self.exit_code = 2 if self.exit_code != 130 else 130
            self.reason += "; no further MCP calls, including diagnostics/save/close/disconnect"
        summaries = []
        for case in self.cases:
            case.finalize()
            summaries.append(case.summary())
        summary = self.tag(
            {
                "exit_code": self.exit_code,
                "reason": self.reason,
                "indeterminate": self.unknown,
                "preflight": self.options.preflight,
                "offline_preflight_passed": self.exit_code == 0 if self.options.preflight else None,
                "real_cst_execution_attempted": self.live_attempted,
                "scenario_completed": not self.options.preflight
                and all(case.outcome in {"passed", "failed"} for case in self.cases),
                "cases": summaries,
                "checks": self.checks,
                "responses": self.results,
                "metadata": self.metadata,
                "limitations": ERROR_LIMITATIONS,
                "native_validation": "pending live validation"
                if self.options.preflight
                else "see per-case outcomes",
                "manual_inspection": "separate; inspect failed/inconclusive cases and retained history",
            }
        )
        lines = [
            "# Latest boolean errors invocation",
            "",
            f"Invocation: `{self.invocation}`; stage: errors; phase: `{self.phase}`; sequence: {self.sequence}; exit: {self.exit_code}",
            "",
            self.reason,
            "",
            "| Case evidence | Expected rejection | Observed response / layer | Communication | Integrity | Overall |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for case in self.cases:
            observed = case.observation or {}
            response = observed.get("status") or (
                "error text" if observed.get("isError") else "not attempted"
            )
            lines.append(
                f"| [{case.case_id}]({case.case_id}/summary.md) ([JSON]({case.case_id}/summary.json)) | {case.case['layer']} | {response} / {observed.get('observed_layer') or 'unproven'} | {case.communication} | {case.integrity} | {case.outcome} |"
            )
        lines += [
            "",
            "Preflight verifies cases 1-4 server rejection and offline generation only. Fixture integrity requires live readbacks."
            if self.options.preflight
            else "Automated native results are recorded above. Manual inspection remains separate.",
            "",
            "Complete responses, differences, script hash, versions and source/reference provenance: [summary.json](summary.json).",
            "",
            "Unchanged geometry does not establish history absence. CST messages are contextual and may be inherited.",
            "After an inconclusive execution, inspect CST manually before any new invocation. Do not dismiss dialogs automatically.",
            "",
        ]
        self.finish_reports(summary, lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("operations", "errors"), default="operations")
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Real MCP schemas/offline generation; CST access disabled",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Recreate only verified owned stage projects and companions; retain logs and reports",
    )
    parser.add_argument("--cst-path", default=DEFAULT_CST_PATH)
    parser.add_argument("--connection-timeout", type=positive_timeout, default=120)
    parser.add_argument("--call-timeout", type=positive_timeout, default=60)
    options = parser.parse_args()
    if options.preflight and options.reset:
        parser.error("--reset cannot be combined with --preflight")
    try:
        workspace = ARTIFACTS / ("01_operations" if options.stage == "operations" else "02_errors")
        with WorkspaceLock(workspace):
            client = (
                BooleanTest(options, workspace, "project", OPERATIONS_OWNER)
                if options.stage == "operations"
                else BooleanErrors(options, workspace)
            )
            return asyncio.run(client.run())
    except StopTest as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
