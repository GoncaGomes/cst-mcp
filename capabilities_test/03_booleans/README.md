# Boolean validation

`run_booleans.py` uses a real MCP stdio child server and dedicated brick and boolean
tools. Fixed output-capture VBA performs setup and native readbacks outside model
history. Select `--stage operations|errors`; the default is `operations`, so the
existing operations commands and fixtures remain available.

## Commands

Run from `C:\dev\cst-studio-mcp`:

```powershell
uv run capabilities_test\03_booleans\run_booleans.py --stage operations --preflight
uv run capabilities_test\03_booleans\run_booleans.py --stage errors --preflight
uv run capabilities_test\03_booleans\run_booleans.py --stage errors --reset
```

To recreate the operations project, use `--stage operations --reset`.
Options include `--cst-path`, `--connection-timeout` (120 seconds) and
`--call-timeout` (60 seconds). `--preflight --reset` is rejected.
The child receives CST 2025 configuration, official Python library paths, raw VBA
permission and toolsets `connection,project,geometry,boolean,parameters,diagnostics,vba`.
Preflight sets `CST_CONNECT_MODE=disabled`. It never prepares/resets ownership or
calls connection, status or project lifecycle tools.

## Layout and ownership

- `artifacts/01_operations/project.cst` and `project/`: the operations project.
- `artifacts/02_errors/<case_id>/<case_id>.cst` and `<case_id>/`: a separate project
  and CST companion directory for every error case.
- Each stage has a retained `workspace.lock` with an invocation-scoped OS lock.
- Each project has `workspace.json` with ownership, generated paths, lifecycle
  state and saved file fingerprints.
- `mcp_calls.jsonl`, `cst_messages.jsonl`, `metadata.jsonl`, `server_stderr.log`:
  append-only evidence. Stderr has tagged boundaries; server output remains raw.
- `metadata.json`, `tool_catalog.json`, `summary.json`, `summary.md`: latest
  reports. Error cases have their own summaries, linked by `02_errors/summary.md`.

Names are fixed, without timestamped directories. Evidence records carry the
invocation ID, stage, case ID, project path, phase and sequence. Stage-wide records
have null case/project fields. Sequence numbers are scoped to the stage client or
case client. Reports include complete responses, `isError`, decoded payloads,
text diagnostics, execution state, script hash, package versions, installed
reference provenance and, for errors, the relevant server source hashes.

Live runs start an isolated CST instance and create blank MWS projects. The errors
stage reuses that instance only after the previous project has closed successfully.
It never opens a previous project to execute another case. Existing-project reuse
without reset is deferred for both stages.

`--reset` removes only manifest-verified project files and companion directories,
retaining logs and reports. Every target across all error cases is validated before
any deletion. Checks reject escaping paths, links, junctions, other Windows reparse
points, open-project locks, unregistered project paths and case files newly added
after a saved checkpoint. Files appearing before client creation are not adopted.
Save and close CST before reset. Operations and errors ownership are independent.
Historical artifact JSONs remain preserved; latest summaries are replaced.

## Operations fixtures and coverage

Units are mm/GHz/ns. Store only `PBool_Shift=2`, without rebuilding.
For offsets O=0, 20, 40, 60, the qualified pairs are `BoolAdd:A/B`,
`BoolSubtract:A/B`, `BoolIntersect:A/B`, and `BoolInsert:A/B`.
A spans x=O..O+4; B uses the stored expressions `O+PBool_Shift` and
`O+PBool_Shift+4`. Both span y/z=0..2. Each original operand has volume 16 mm³
and area 40 mm². Both use PEC, except Insert A uses Vacuum.

| Operation | A at shift 2: volume / area | A at shift 3: volume / area | B afterward |
| --- | --- | --- | --- |
| Add | 24 / 56 | 28 / 64 | Absent |
| Subtract | 8 / 24 | 12 / 32 | Absent |
| Intersect | 8 / 24 | 4 / 16 | Absent |
| Insert | 8 / 24 | 12 / 32 | Present, PEC, 16 / 40 |

A retains its material. The installed reference defines Insert as A minus B with
B retained; the catalog's embedded-material wording is less precise.
At local points (1,1,1), (3.5,1,1), (5.5,1,1), adding O to x, membership in both
parameter states is Add A=(true,true,true), Subtract/Insert A=(true,false,false),
Intersect A=(false,true,false), and retained Insert B=(false,true,true).
The PEC witness `BoolWitness:Sentinel`, x=100..102, y/z=0..2, has volume 8 mm³
and area 24 mm². Relative and absolute numeric tolerances are `1e-6`.

The operations stage measures before and after each boolean, changes only the
parameter to 3 with native rebuilding, repeats readbacks without replaying fixtures
or booleans, then saves, closes, fingerprints, reopens, verifies, saves/closes and
disconnects. Command acceptance and native geometry verification are separate.

Reviewed live invocation `54fa1e64b7f14d8bb9c0d4095a4593cd` completed 617 passing
checks and 235 responses. It created a fresh project with `reset=false`.
The user confirmed manual success. That reviewed operations result is independent
of subsequent disabled-server preflight reports.

## Errors cases and fixture

Every live case creates three independent PEC bricks with mm/GHz/ns units and no
parameters or rebuilding:

| Shape | x bounds | y/z bounds | Volume mm³ | Area mm² | Membership at selected points |
| --- | --- | --- | --- | --- | --- |
| `BoolError:A` | 0..4 | 0..2 | 16 | 40 | true, true, false |
| `BoolError:B` | 2..6 | 0..2 | 16 | 40 | false, true, true |
| `BoolWitness:Sentinel` | 100..102 | 0..2 | 8 | 24 | true |

A/B points are (1,1,1), (3.5,1,1), (5.5,1,1); the witness point is (101,1,1).
`BoolError:MissingA` and `BoolError:MissingB` must be absent.

| Case ID | Dedicated tool | Invalid input | Expected rejection |
| --- | --- | --- | --- |
| `01_missing_argument` | `cst_boolean_add` | Omit `solid2`; valid A | Schema |
| `02_wrong_type` | `cst_boolean_add` | Integer 123 for `solid1`; valid B | Schema |
| `03_empty_reference` | `cst_boolean_add` | Empty `solid1`; valid B | Server argument |
| `04_forbidden_character` | `cst_boolean_add` | Valid A; B reference containing a newline | Server argument |
| `05_missing_solid1` | `cst_boolean_add` | MissingA; valid B | Native |
| `06_add_missing_solid2` | `cst_boolean_add` | Valid A; MissingB | Native |
| `07_subtract_missing_solid2` | `cst_boolean_subtract` | Valid A; MissingB | Native |
| `08_intersect_missing_solid2` | `cst_boolean_intersect` | Valid A; MissingB | Native |
| `09_insert_missing_solid2` | `cst_boolean_insert` | Valid A; MissingB | Native |

Only cases 1-2 bypass local schema rejection. The client records that rejection
and sends the exact declared input through MCP to verify server rejection.
Other requests retain schema validation. The component-path validator permits an
optional colon, so a missing colon is not used as a guaranteed invalid case.

Each case verifies the complete shape/material inventory, empty parameter
inventory, units, existence, body types, volumes, areas and interior-point
membership. It saves the initial fixture and captures diagnostics before exactly
one invalid dedicated call. If execution remains known and CST is idle, it repeats
the readbacks, records baseline/final differences with `1e-6` tolerances, saves and
closes normally. There is no save/reopen regression scenario in this stage.

Existence guards prevent measurement of absent shapes. Readback VBA is fixed and
allowlisted. Invalid booleans always use their dedicated tools.

## Interpreting errors evidence

A live case passes only with a useful expected rejection and all fixture integrity
checks passing. Expected and observed rejection layers are recorded separately.
Observed layers require validation diagnostics or the native exception envelope
documented in the reviewed source. Unchanged geometry does not prove history
absence. CST messages may be inherited, repeated or truncated; novelty does not
establish command causality.

The aggregate table reports communication, integrity and overall results:

- `passed`: the expected rejection and live fixture integrity both passed, with
  normal closure confirmed.
- `failed`: a completed case has a communication or integrity failure. `executed`
  for a nonexistent operand is a communication failure even with unchanged geometry.
- `inconclusive`: evidence or safe completion is incomplete. Preflight cases 1-4
  can verify rejection but cannot establish live fixture integrity.
- `not_attempted`: no live case was attempted. Preflight cases 5-9 may have passing
  offline generation checks while their native outcomes remain pending.

Preflight verifies the effective catalog/schemas, actual rejection of cases 1-4,
offline VBA for cases 5-9, all fixture scripts and fixed setup/readback blocks.
Its exit 0 means preflight checks passed; it does not establish native error handling.
`busy`, `offline` and unrelated failures never count as expected rejection.
Infrastructure failures stop the stage. Known case failures retain post-call
evidence and continue only after safe normal closure.

The server's dialog inventory can include nonmodal tool windows. The client
recognizes the observed empty CST `Messages` window with Qt class
`QWindowToolSaveBits` as informational and retains it in `window_assessments`.
A completed native rejection can then proceed to the existing owned-project and
idle checks, integrity readbacks and normal closure. Other reported windows remain
unresolved and stop the invocation. The client never dismisses any window.

Timeout, transport loss, interruption, unresolved dialogs or ambiguous native
execution stop the entire invocation. No further MCP calls occur, including
diagnostics, save, close, disconnect or the next case. Only local reports and the
existing Python-server transport teardown continue, preserving CST. Invalid calls
are never replayed and dialogs are never dismissed automatically.

For failed/inconclusive cases, inspect the linked JSON and logs first. If execution
is uncertain, inspect the still-open CST project manually before another invocation.
For safely closed cases, open the retained case project manually, inspect the
fixture, materials, units, parameters and boolean history, and record the invocation,
method and discrepancies. Automated native validation and manual inspection remain
separate. See `REVIEW.md` for actual implementation checks and remaining validation.
