# Batch 01: project, geometry and materials

This deterministic Python client assesses the current CST MCP contracts over
real stdio MCP. It does not use an LLM/SLM, import server handlers, configure a
solver, or repair failed geometry with VBA. Server code is unchanged.

Run from `C:\dev\cst-studio-mcp` with `uv` available. Inline PEP 723 metadata
selects Python **3.12**, matching CST 2025's `_cst_interface.cp312-win_amd64.pyd`.
Neither `python` nor `py` needs to be on PATH. `uv` can install/cache the required
runtime and client dependencies (`mcp>=1.29,<3`, `jsonschema>=4.20`).

```powershell
# Safe preparation: real MCP server, CST connections disabled
uv run capabilities_test\01_project_geometry_materials\run_batch.py --preflight

# Live assessment: starts a NEW CST instance; initiate when ready
uv run capabilities_test\01_project_geometry_materials\run_batch.py
```

For another CST 2025 installation:

```powershell
uv run capabilities_test\01_project_geometry_materials\run_batch.py --preflight --cst-path "D:\CST Studio Suite 2025"
uv run capabilities_test\01_project_geometry_materials\run_batch.py --cst-path "D:\CST Studio Suite 2025"
```

The default installation is `C:\Program Files (x86)\CST Studio Suite 2025`;
libraries are under `AMD64\python_cst_libraries`. Paths are derived from
`run_batch.py`, with the repository at `parents[2]`, so another working directory
is also supported. The client records discovery and an **actual import attempt**
in a separate Python subprocess without constructing a CST instance. Discovery
alone is not import compatibility. Failed import compatibility gives a nonzero
preflight exit and blocks a live connection after catalog preparation.

## Subprocess configuration and preservation

Only the child process environment is configured:

| Variable | Live | Preflight |
| --- | --- | --- |
| `CST_CONNECT_MODE` | `manual` | `disabled` |
| `CST_VERSION` | `2025` | `2025` |
| `CST_PATH` | Selected installation | Selected installation |
| `CST_WORK_DIR` | Unique current run directory | Unique current run directory |
| `CST_TOOLSETS` | `connection,project,geometry,materials,diagnostics,vba` | Same |
| `CST_ALLOW_RAW_VBA` | `1` | `1` |
| `PYTHONPATH` | `src/`, CST libraries, useful inherited entries | Same |

The server runs with `command=sys.executable`,
`args=["-m", "cst_mcp.server"]`, and `cwd=repository root`. Initialization and
paginated tool listing use `mcp.ClientSession` and the actual SDK
`mcp.client.stdio.stdio_client`. Every planned argument is validated against the
retrieved input schemas before construction. Schemas and complete catalog pages
are saved. Negative preflight controls intentionally bypass local validation.

`CST_ALLOW_RAW_VBA=1` enables only three fixed blocks in this client: the supplied
mm/GHz/ns units block, a separate `Units.GetUnit` query, and solid name/material
enumeration. The client rejects other blocks and has no arbitrary VBA CLI input.
Queries use line-start `Debug.Print`, which the current client routes through
non-history output capture and returns as `status="ok"` with `output`. The exact
blocks and activation are recorded in `metadata.json`. No permanent environment
or configuration is changed.

**Transport lifecycle:** the inspected MCP SDKs 1.29.0 and 2.3.0 create a Windows
Job Object that can terminate server descendants, including CST. The small local
`preserving_stdio.py` module scopes two client transport hooks: launch the same
Python server without that Job Object, and restrict shutdown escalation to the
Python server PID. The SDK still handles MCP framing, streams, stderr, EOF and
bounded shutdown. Missing hooks in a future SDK block startup before launching
the server. Installed SDK files and server code are not edited. A local test
forces SDK shutdown escalation and verifies a finite-lived Python descendant
survives; it never imports or starts CST.

## Live sequence

1. Open logs and write metadata/plan; check Python/CST import compatibility;
   initialize MCP, list tools with pagination, validate schemas/arguments, and
   query offline startup status.
2. Connect with `mode="new"`; require a real connection, `newly_started=true`,
   zero open projects, empty open paths and no adopted project. Record PID and
   connection details. There is no `mode="any"` fallback.
3. Create an MWS project in the run directory; inspect project info/messages.
   Follow the actual returned path, including an alternative saved filename;
   refuse any path outside this execution directory.
4. Set mm/GHz/ns, separately query all three effective values, and save. Any
   failure or mismatch stops construction. Query units again before/after reopen.
5. Create BatchDielectric and BatchCopper. Query bundled metals and Copper as
   database tests, separately labeled; returned custom properties are input
   echoes, not independent property verification.
6. Create PEC brick, cylinder, truncated cone, sphere, torus and elliptical
   cylinder at separate numeric coordinates under component `Batch`.
7. Create a straight inclined wire, closed `Curves:BatchOutline` rectangle,
   displaced analytical circle, L extrusion and polygon extrusions up/down.
   No holes or boolean subtractions are requested.
8. Enumerate solid names/materials and confirm the brick initially has PEC.
   Assign BatchCopper, read back the association, then assign BatchDielectric and
   read back again. Dependencies that failed are explicitly blocked; other
   geometry uses PEC and can still be assessed.
9. Save a checkpoint and collect messages. Attempt the qualified face reference
   without stripping its prefix. Run the limited loft probe last among
   construction calls; normal failures get diagnostics and a connection/project
   state check before continuing.
10. Save, read project info/tree, units and solid/material associations, collect
    messages and check that the actual `.cst` is nonempty. Close only this owned
    project, reopen its actual saved path, repeat readbacks and compare.
    On normal completion the reopened project stays available in CST;
    `cst_disconnect` releases MCP handles without closing CST.

## Evidence and limitations

The inspected baseline is `main` at
`e027735f0f61e666801fe18a6e0f93cfbfff9f69`. Each run records its current revision,
client dependency versions, script hash, requested plan and effective calls.

The report distinguishes catalog presence, implementation/contract limitations,
offline testing, real execution, and separately confirmed effects. These are
independent evidence dimensions. `status="offline"` never counts as execution.
Tool-specific response criteria handle queries without `status`, MCP `isError`,
JSON payloads across content blocks, and missing `structuredContent`. Command
acceptance is distinct from object/association confirmation. Dimensions and
electromagnetic properties remain pending manual inspection.

- **Torus:** the schema describes major/minor radii, but the builder passes
  `OuterRadius` and `InnerRadius` unchanged. Installed CST 2025 help
  `mergedProjects/VBA_3D/common_vbabasicsolids/common_vbatorus_object.htm` maps
  these to large/small radii; the `mergedProjects/3D/image/torus.gif` diagram
  shows center-to-outer-surface and center-to-hole radii. This batch supplies
  **5/3 mm**, implying major/tube radii **4/1 mm**. Verify the actual torus
  dimensions manually; the schema description conflict is recorded.
- **Wire:** one straight segment produced with cylinder/rigid transforms;
  endpoints `(95,0,1)` and `(101,4,6)`, radius `0.3 mm`. The exposed schema has
  no material field and does not describe a multisegment path.
- **Extrusions:** L profile expects Z=1..4 mm; polygon up expects Z=5..7 mm;
  polygon down expects Z=3..5 mm. The returned extrusion range is a prediction,
  not a measurement.
- **Face:** `Curves:BatchOutline` is schema-valid but current `validate_name`
  rejects the colon; its `CoverCurve.AddCurve` also differs from documented
  `CoverCurve.Curve`. An expected rejection proves rejection handling only.
  It does not execute geometry in CST or demonstrate face support.
- **Loft:** two schema-valid 2D profiles cannot specify distinct planes. The
  builder uses Polygon plus `Loft.AddCurve/Create`; the installed Loft reference
  describes surfaces and does not document AddCurve. Even an executed response
  proves no intended transition between sections on distinct planes. Failure
  can leave partial profile artifacts; inspect them manually.
- **Materials:** bundled database queries do not inspect project materials.
  Solid enumeration confirms names and assigned material names, not dimensions
  or EM properties. It does not enumerate curves.
- **Tree and messages:** only returned tree items are evidence. Project messages
  may be empty, repeated, or a tail. Preserve their source/path/tail/size and
  checkpoint; do not automatically attribute them to the last command.

## Artifacts, timeouts and exits

Every invocation creates `runs/run_<UTC timestamp>_<identifier>/` with exclusive
creation. Previous projects/logs are preserved; generated files are ignored by
the batch's local `.gitignore`.

Each run contains `metadata.json`, `case_plan.json`, `tool_catalog.json`,
`mcp_calls.jsonl`, `server_stderr.log`, `cst_messages.jsonl`, `summary.json` and
`summary.md`. A live run also contains the actual saved `.cst` and CST auxiliary
files; preflight creates no project file. Returned VBA is saved under `vba/`
with its response source recorded. Successful geometry need not return VBA:
this is not a complete history of internal CST commands.

Each MCP request has flushed start and completion/error records with run/case
IDs, sequence, UTC timestamp, duration, arguments, full serialized response,
all content blocks, parsed JSON, `isError`, exception/timeout and check scopes.
Later readback checks link to their source case. Summary/report generation runs
on normal failure, timeout and Ctrl+C. Forced OS termination/power loss cannot
run finalization; already flushed JSONL records remain available.

Client defaults are **120 seconds for connect** and **60 seconds for
initialize/list/calls/import compatibility**. Override with
`--connection-timeout` and `--call-timeout`. Many native calls have a server
timeout of **30 seconds**; raising the client timeout does not change that.

Client timeout, server timeout, `execution_state="unknown"`, an unknown activity
state (`busy` with `running=null`), transport loss or
interrupted in-flight request stops all further MCP calls. There are no retries,
queries, save, close, disconnect or CST cleanup calls after that point. Only
local reports and Python-server transport teardown proceed. CST and the last
saved checkpoint are preserved for inspection. No dialogs are dismissed,
watchers started, results deleted, solver stopped, or CST processes killed.

| Exit | Meaning |
| --- | --- |
| `0` | Preflight passed including import compatibility, or live assessment completed without unexpected failures/blocked cases/readback mismatches. Documented limitations and manual checks still remain. |
| `1` | Known failure, invalid plan/import compatibility, blocked live dependencies, or failed readback/persistence check. A normal failed loft probe is reported here. |
| `2` | Indeterminate execution state/timeout/transport loss; all CST calls stopped. Invalid CLI arguments also use argparse's standard `2`, before a run exists. |
| `130` | Ctrl+C/cancellation; reports finalized and checkpoints preserved. |

## Local validation without CST

```powershell
uv run --python 3.12 --no-project python -m py_compile capabilities_test\01_project_geometry_materials\run_batch.py capabilities_test\01_project_geometry_materials\preserving_stdio.py capabilities_test\01_project_geometry_materials\test_run_batch.py
uv run capabilities_test\01_project_geometry_materials\test_run_batch.py -v
uv run capabilities_test\01_project_geometry_materials\run_batch.py --preflight
```

Preflight performs real initialize/list/status calls and offline VBA generation
for relevant project, units, materials, primitives, curves, extrusions,
assignments, face and loft cases; it also checks deliberately invalid/unknown
tool rejection, schemas and log pairing. It never calls `cst_connect`, opens a
project, or constructs a CST instance. Offline generation does not validate
geometry, dimensions, assignments or persistence in CST. The local tests cover
multiblock error preservation, offline/timeout classification, incomplete query
output, no calls after timeout/interruption, interrupted report generation and
SDK descendant preservation. They use substitutes and contain no real CST
execution evidence.

Each `summary.md` includes a pending manual checklist for names, positions,
dimensions, materials/properties, curves/extrusions, units, loft remnants and
persistence. Complete it after the live run; successful automation alone does
not approve those items.
