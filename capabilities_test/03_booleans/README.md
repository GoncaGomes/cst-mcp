# Boolean validation: 01_operations

This client validates successful Add, Subtract, Intersect and Insert operations,
parameter reconstruction from stored history, and save/close/reopen persistence.
It uses a real MCP stdio child server and dedicated brick, boolean and parameter
tools. Native readbacks run outside model history. There are no LLM or direct CST
API calls. Native errors and negative inputs belong to the future `02_errors` task.

## Layout and ownership

- `run_booleans.py`: Python 3.12 client, lifecycle guards and evidence collection.
- `artifacts/01_operations/project.cst` and `project/`: one retained live project.
- `workspace.json`: ownership, generated paths and saved file fingerprints.
- `workspace.lock`: retained file with an invocation-scoped OS lock.
- `mcp_calls.jsonl`, `cst_messages.jsonl`, `metadata.jsonl`, `server_stderr.log`:
  append-only evidence with invocation tags or tagged stderr boundaries.
- `metadata.json`, `tool_catalog.json`, `summary.json`, `summary.md`: latest evidence.
- `REVIEW.md`: offline results, limitations and pending manual validation.

Only `artifacts/01_operations` is used. Preflight writes local evidence and the
lock, without preparing a project or ownership manifest. Live runs create an
isolated CST instance with `cst_connect(mode="new")` and a blank owned MWS project.
`--reset` removes only verified owned project paths after closure and path checks,
retaining logs and notes. Existing-project reuse without reset is deferred.
Earlier capability workspaces and all future `02_errors` files are preserved.

## Fixtures and expectations

Effective units are mm/GHz/ns. Store only `PBool_Shift=2`, without rebuilding.
For offsets O=0, 20, 40, 60, the qualified pairs are respectively `BoolAdd:A/B`,
`BoolSubtract:A/B`, `BoolIntersect:A/B`, and `BoolInsert:A/B`.
A spans x=O..O+4; B uses the stored expressions `O+PBool_Shift` and
`O+PBool_Shift+4`. Both span y=0..2, z=0..2. Each original operand has volume
16 mm³ and surface area 40 mm². Both use PEC, except Insert A uses Vacuum.

| Operation | A at shift 2: volume / area | A at shift 3: volume / area | B afterward |
| --- | --- | --- | --- |
| Add | 24 / 56 | 28 / 64 | Absent |
| Subtract | 8 / 24 | 12 / 32 | Absent |
| Intersect | 8 / 24 | 4 / 16 | Absent |
| Insert | 8 / 24 | 12 / 32 | Present, PEC, 16 / 40 |

Volumes are mm³ and areas are mm². A retains PEC for the first three operations
and Vacuum for Insert. The installed reference defines Insert as A minus B with
B retained; the catalog's embedded-material wording is less precise.

At local points (1,1,1), (3.5,1,1), (5.5,1,1), adding O to x, membership for both
parameter states is Add A=(true,true,true), Subtract/Insert A=(true,false,false),
Intersect A=(false,true,false), and retained Insert B=(false,true,true).
The native signature is `Solid.IsPointInsideShape(x, y, z, shapeName)`.
The fixed PEC `BoolWitness:Sentinel`, x=100..102, y=0..2, z=0..2, must retain
volume 8 mm³ and area 24 mm². Relative and absolute numeric tolerances are `1e-6`.

## Commands and live sequence

Run from `C:\dev\cst-studio-mcp`:

```powershell
uv run capabilities_test\03_booleans\run_booleans.py --preflight
uv run capabilities_test\03_booleans\run_booleans.py --reset
```

Options include `--cst-path`, `--connection-timeout` (120 seconds) and
`--call-timeout` (60 seconds). `--preflight --reset` is rejected. The child alone
receives CST 2025 configuration, official Python library paths, raw VBA permission
and toolsets `connection,project,geometry,boolean,parameters,diagnostics,vba`.
Preflight sets `CST_CONNECT_MODE=disabled` and makes no connection, status or
project lifecycle calls.

The live run confirms the blank owned project, units and independent list/get
parameter readbacks, then creates and measures all nine original shapes. Each
boolean runs once, with pair measurements, complete named-shape/material inventory
and witness checks before and after. It verifies all completed results, changes
only the parameter to 3 with native rebuilding, and repeats all readbacks without
creating bricks or replaying booleans. It saves, closes, fingerprints, reopens,
checks the updated state, then saves/closes and disconnects normally.
The retained project is ready to open for manual inspection after successful live completion.

Existence is checked before measurements are requested. Deleted B operands are
expected absences. All native Boolean output uses the strict existing parser.
Full responses, expected/actual measurements, point results, units, inventories,
parameter states, tolerances, invocation IDs, script hash and installed-reference
path/hash are retained. Command acceptance, offline checks, independently verified
native properties and manual inspection are reported separately.

Timeout, transport loss, interrupted in-flight requests and unknown execution
forbid every further MCP call. Only local reporting and the established Python
server transport teardown continue, preserving CST. Known failures may collect
diagnostics while execution remains known. There are no retries or recovery mutations.

## Current validation

Disabled-server preflight passed on 2026-10-09: 48 effective catalog tools,
47 planned schema checks, nine generated brick scripts, four correct boolean calls,
two parameter-storage scripts and 21 preserved fixed setup/readback blocks.
All 79 recorded checks passed. Native execution and manual inspection remain pending.
These fixtures do not certify arbitrary boolean geometry. Messages may be inherited
or repeated and do not prove command causality. Loose bounding boxes are not used
as exact dimensions. See `REVIEW.md` for repository checks and inspection items.
