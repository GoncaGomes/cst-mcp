# Review: boolean operations, reconstruction and persistence

## Scope and implementation

Only the `03_booleans` client, README, review and ignore file are added. No server,
catalog or earlier client changes are intended. The client imports inspected
stateless helpers through a sibling path derived from `__file__`, owns its lifecycle
and workspace, and does not instantiate earlier clients or modify their globals.
It preserves the loft client's request handling, ownership guards, checkpoints,
append-only reporting and Python-server-only transport teardown.

The four operations use dedicated MCP tools. Fixed output-capture VBA sets units
and creates components; fixed native queries enumerate names/materials, guard
existence, and measure body type, volume, surface area and interior membership.
All shapes use dedicated brick creation. Only `PBool_Shift` changes, first without
rebuilding, then to 3 with native rebuilding. Independent list/get readbacks verify
the parameter. Updated geometry and persistence checks cannot pass using setter
echoes or `executed` history responses alone. No boolean calls or brick creations
follow the rebuild.

## Actual offline validation

On 2026-10-09, the real stdio disabled-server preflight exited 0 with MCP 2.3.0
and jsonschema 4.26.0. It retrieved 48 tools and validated all 47 planned calls
against their effective schemas. The invocation recorded 38 protocol/tool responses
and 79 passing checks: nine exact brick scripts with symbolic B bounds, four exact
`Solid.Add/Subtract/Intersect/Insert` calls, two parameter-storage scripts, and
21 unchanged fixed setup/readback VBA blocks, plus catalog and acceptance checks.
It did not attempt native execution, prepare/reset a project, or call connection,
status or project lifecycle tools. It created no `02_errors` workspace or project.

The installed reference is
`C:\Program Files (x86)\CST Studio Suite 2025\Online Help\mergedProjects\VBA_3D\common_vbasolido\common_vbasolido_solid_object.htm`.
Its SHA-256 is `5c5545346fbf8a02076eb18e8f95ab72376d6b69a0c81c0ab9f46a74252a39e9`.
The inspected Boolean section specifies that Insert subtracts B from A and retains
B. The Queries section documents existence, inventory, material, body type,
volume, surface area and the x/y/z/name point-membership signature.
The less precise Insert catalog description is exposed as a limitation.

Repository checks:

```powershell
uv run capabilities_test\03_booleans\run_booleans.py --preflight
uv run ruff format
uv run ruff format --check
uv run ruff check
```

Preflight passed, including a rerun after formatting to refresh the script hash.
Formatting reformatted the new client only, with 143 files unchanged.
`ruff format --check` exited 0 with 146 files already formatted; `ruff check`
exited 0 with all checks passed.
No new pytest suite was added. Earlier capability clients and live CST scenarios
were not run. Latest evidence is in `artifacts/01_operations`; logs retain prior
invocations without timestamped directories.

## Pending live and manual validation

Run manually from the repository:

```powershell
uv run capabilities_test\03_booleans\run_booleans.py --reset
```

Native operation effects, witness stability, reconstruction and save/reopen
persistence remain unverified until that live run succeeds. Afterward open the
retained `artifacts/01_operations/project.cst` for manual inspection, recording the
invocation ID, method, observations and discrepancies. Save and close before reset.

- Confirm mm/GHz/ns units and the parameter value 3 after reopening.
- Confirm six final shapes: four A results, Insert B and the witness. Only Insert
  A is Vacuum; all other surviving shapes are PEC. Other B operands are absent.
- Inspect stored brick expressions and one boolean history entry for each pair.
- Confirm updated A x bounds: Add 0..7, Subtract 20..23, Intersect 43..44,
  Insert 60..63; Insert B is 63..67. All span y/z=0..2.
- Confirm the witness remains x=100..102, y/z=0..2, with volume 8 and area 24.
- Compare native measurements and point membership against the README table for
  both states, including the reopened state and `1e-6` tolerances.

Numeric measurements and selected points do not prove every coordinate, topology
or symbolic history association, and these fixtures do not certify arbitrary
geometry. No loose bounding box is treated as an exact dimension. Message novelty
does not prove command causality. Error scenarios and existing-project reuse without
reset remain deferred. On unknown execution, inspect CST manually; the client
issues no further MCP calls or recovery mutations.
