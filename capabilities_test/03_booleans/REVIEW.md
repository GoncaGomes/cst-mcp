# Review: boolean operations and errors

## Scope and shared implementation

Only `run_booleans.py`, `README.md` and this review are changed. No server, ignore
file, imported helper or historical artifact JSON is modified. No testing framework
is added. Operations remain the default and retain their fixture, parameter rebuild,
save/reopen workflow and `artifacts/01_operations/project.cst`.

Workspace, stem and owner are explicit in locks, clients and ownership helpers.
The errors coordinator shares one real stdio session and, when safe, one isolated
CST instance with nine case clients. Each case owns a fresh blank project, its
companion, manifest and append-only evidence. Reset validates every target across
all cases before deletion, preserving reports and rejecting unknown paths,
links/reparse points and open locks. Saved fingerprints remain recorded. Files
added to a saved case's generated paths after checkpoint are refused as unowned.
Reuse without reset remains deferred.

Shared transport retains serialization, complete responses, decoded payloads,
`isError`, diagnostics and execution-state checks. The expected-rejection route is
limited to the exact declared current case. Only the missing-argument and wrong-type
cases may transmit locally schema-invalid input. The empty reference and newline
cases retain local schema validation and verify server argument rejection.

Native cases use dedicated Add, Subtract, Intersect and Insert tools with valid
syntax and nonexistent operands. Fixed setup and guarded readbacks run outside
history; all bricks use dedicated creation. Every case checks empty parameters,
complete materials/inventory, existence, body type, volume, area, selected points
and the distant witness before and after the single invalid request. The baseline
is saved before that request. Expected and observed layers are separate. Known
case failures retain final evidence, then save/close normally before continuation.
Infrastructure failures stop; unknown execution permits no further MCP calls.

## Reviewed operations result

The already reviewed live invocation `54fa1e64b7f14d8bb9c0d4095a4593cd` recorded
617 passing checks and 235 completed responses. It created a fresh project with
`reset=false`; it was not a reset run. Native operations, witness stability,
parameter reconstruction and persistence passed within the client coverage, and
the user confirmed manual success. Historical JSON evidence remains preserved.
New preflight reports are independent of that reviewed live result.

## Server reference findings

The targeted source review covered boolean schemas/handler,
`validate_component_path`, `guard_handler`, registry error recognition/request
registration, and `session.run_history`.

- Boolean schemas require two strings. MCP schema rejection can return error text
  without the usual JSON payload, which the client records explicitly.
- An empty reference raises `Component path cannot be empty`. The newline is
  rejected by the argument guard before VBA generation. A colon is optional in the
  component-path validator and is not used as a negative-input assumption.
- Registry error recognition uses JSON `error`, `busy` and `timeout` statuses.
- `run_history` returns `executed` whenever `add_to_history` returns normally,
  preserving the returned value as text. Exceptions produce a native error envelope
  with label, VBA and contextual diagnostics; timeouts mark execution unknown.

No native propagation mismatch has been established during implementation because
live CST was not run. If a missing-solid call returns `executed`, the client records
a communication failure even if geometry is unchanged. The focused follow-up is
to inspect the recorded native return value and the installed `add_to_history`
contract, then correct `session.run_history` error propagation if that evidence
warrants it. No speculative server patch is included.

The installed Solid reference supplies the inventory, material, existence,
body-type, volume, area and x/y/z/name point signatures. Its path/hash is retained
per invocation. Insert's installed definition is A minus B with B retained; the
operations client exposes the catalog description discrepancy as a limitation.

## Actual implementation verification

On 2026-10-09, the following commands were run against the final client:

```powershell
uv run ruff format
uv run capabilities_test\03_booleans\run_booleans.py --stage operations --preflight
uv run capabilities_test\03_booleans\run_booleans.py --stage errors --preflight
uv run ruff format --check
uv run ruff check
```

All five commands exited 0 in the final verification:

- Formatting reformatted one file, with 145 unchanged. The final format check
  reported 146 files already formatted. Ruff lint reported all checks passed.
- Operations preflight invocation `b07debb60edb4a7db0d6c7839d4fab50` retrieved 48
  effective tools and validated the existing 47 planned schemas. All 79 checks
  passed across 38 completed protocol/tool responses. Nine brick scripts, four
  boolean scripts, two parameter scripts and 21 fixed VBA blocks were verified.
- Errors preflight invocation `59ab4b83a3ff4de681d6a6da57dd0d64` validated 38
  planned schemas plus the two declared local schema failures. All 270 checks
  passed across 137 completed protocol/tool responses. Each case verified three
  fixture scripts and 11 fixed setup/readback blocks. Cases 1-2 received actual
  schema rejection; cases 3-4 received actual server argument rejection, all with
  `isError=true` and useful diagnostics. Cases 5-9 generated the exact dedicated
  VBA offline and retain pending native outcomes. Live integrity was not attempted.
- Both invocations used MCP 2.3.0 and jsonschema 4.26.0. The script SHA-256 for that verification
  is `92825ec205f280b40abe188bb5d4be46d0042dc08022ed114ae818eade54aecf`.
  The installed Solid reference hash is
  `5c5545346fbf8a02076eb18e8f95ab72376d6b69a0c81c0ab9f46a74252a39e9`.

The initial lint run found FLY002 and C408; a subsequent metadata refinement
exposed RUF012. All were corrected. Checks were repeated after those client
changes to refresh provenance and validate the final script; no pytest suite or
other test framework was added.

No live CST invocation was run during implementation. The errors preflight
evidence contains zero connection/status/project lifecycle calls and created zero
case ownership manifests. Preflight did not prepare/reset projects. Latest reports
are in `artifacts/01_operations` and `artifacts/02_errors`; evidence logs append
and retain earlier invocations. Native outcomes for the five missing-solid calls
remain pending; offline VBA generation is not native error evidence.

## Follow-up: false stop on the Messages window

The user's live invocation `afc8a2400f19479890c56cea7da725da` passed cases 1-4,
including fixture integrity and normal closure. Case 5 returned `isError=true`
with the native diagnostic `Shape does not exist: BoolError:MissingA`, exact
`Solid.Add` VBA and a history label. The response completed in 0.063 seconds.
It also reported a window titled `Messages`, class `Qt51511QWindowToolSaveBits`,
matched by CST process, with empty texts. The client incorrectly treated every
reported window as proof of unknown execution and stopped before final readbacks.
Cases 6-9 were not attempted. This was a client classification defect.

Targeted review of `cst_client.read_dialogs` and
`dialog_handler.find_cst_dialogs` confirmed that the inventory includes all visible
non-main windows belonging to CST, without proving they are modal. The client now
narrowly recognizes the observed empty `Messages` Qt tool window as informational,
records its assessment and proceeds to the existing ownership/idle checks before
readbacks. Other windows, incomplete window inventories, timeouts and ambiguous
native responses retain the stop behavior. No window is dismissed and no server
code was changed.

An in-memory regression check used the captured case 5 response and passed. It
verified that the expected native rejection is accepted, a different dialog stops
execution, timeout/ambiguous responses stop execution, a subsequent request after
unknown state is refused, and an `executed` response still fails communication.
The check made no server or CST calls, added no test framework and did not replace
the user's latest live reports. `uv run ruff format` exited 0 with one file
reformatted and 145 unchanged; `uv run ruff format --check` exited 0 with 146
formatted files; `uv run ruff check` exited 0 with all checks passed.
The follow-up script SHA-256 is
`218b3605da561984d07684d6c83e441c9a99208ff11b963fe7a188d26a9f0701`.

Live continuation through cases 5-9 still requires the user's rerun. Save and close
the currently retained case 5 project before running `--stage errors --reset`.

## Remaining native validation and manual inspection

Run manually from `C:\dev\cst-studio-mcp`:

```powershell
uv run capabilities_test\03_booleans\run_booleans.py --stage errors --reset
```

Review `artifacts/02_errors/summary.md` and each linked case summary. Live passes
require useful rejection plus unchanged fixture integrity and confirmed closure.
Failures retain communication and integrity results separately. Inconclusive and
not-attempted cases do not certify native behavior. Exit 0 in preflight means only
its catalog/rejection/generation checks passed.

For safely closed failed cases, manually open the retained case project. Confirm
three PEC fixtures and no MissingA/MissingB, mm/GHz/ns units, no parameters, A/B
volume 16 and area 40, and witness volume 8 and area 24. Inspect brick and boolean
history; unchanged geometry alone does not prove no history entry was written.
Record invocation ID, method, observations and discrepancies separately from the
automated results. For unknown execution or a dialog, inspect the existing CST
state manually before starting another invocation. Save/close before reset.

Selected points and scalar measurements do not prove all geometry or topology.
CST messages may be inherited and novelty does not establish command causality.
There are no same-solid, non-overlap, deliberate timeout, busy solver or
simulation-result scenarios in this task.
