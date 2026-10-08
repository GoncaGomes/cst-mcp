# Batch 02: parameter-driven brick

This deterministic Python 3.12 client uses a real MCP stdio session with
`sys.executable -m cst_mcp.server`. No LLM/SLM is involved. Creation and parameter
changes use dedicated MCP tools; the client never imports server handlers or
calls CST APIs directly. **Live CST validation is still pending.**

## Server change

Only `cst_create_brick` gains expression inputs. All six bounds now accept a
finite JSON number or a nonempty, single-line CST parameter name/arithmetic
expression. Numeric and expression bounds can be mixed. For example:

```json
{
  "component": "ParameterTest", "name": "ParametricBrick", "material": "PEC",
  "x_min": 200, "x_max": "200+PBrick_L",
  "y_min": 40, "y_max": 46,
  "z_min": 0, "z_max": "PBrick_H/2"
}
```

`_build_brick` uses `VBABuilder.set_expression_pair` and `_format_expression`.
Numeric formatting matches the existing number-only builder. Expression text
remains quoted in the brick's model-history definition, with no Python
evaluation. Strings are serialized, not parsed: CST reports malformed
arithmetic, undefined parameters and other semantic errors. The supported
use is parameter names and arithmetic expressions in CST's expression language;
this change does not promise a complete Python-side CST grammar validator.
Quotes are doubled and cannot terminate the VBA literal; quote-containing text
may then be rejected semantically by CST. Empty/whitespace-only expressions,
line-breaking characters, NUL, booleans, null, unsupported types and nonfinite
numbers are rejected before execution. Existing builder injection checks remain.

There is no retroactive geometry-edit tool. Existing number-only builder methods,
other primitive schemas, loft, faces, sweeps, optimization and solvers are unchanged.
`cst_set_parameter` is reused without modification: it stores values outside model
history and performs a separate native rebuild. Its returned `value` is an input
echo; the test separately reads actual values using the parameter query tools.
The query tools currently provide captured numeric output, so this client parses
that output rather than assuming their descriptions promise structured expressions.

One shared compatibility fix is necessary: `vba_safety.check_arguments` now reads
both SDK 1.x `inputSchema` and SDK 2.x `input_schema`. Previously SDK 2.x silently
bypassed handler schema checks. This restores existing numeric restrictions across
guarded tools; it does not extend their input contracts.

## Commands

Run from `C:\dev\cst-studio-mcp`. Inline PEP 723 metadata selects Python 3.12
and the existing `mcp>=1.29,<3` and `jsonschema>=4.20` dependencies.
No `pyproject.toml` change is required.

```powershell
# Offline: real server/catalog/schemas and generated VBA; CST connections disabled
uv run capabilities_test\02_parameters\run_parameter_brick.py --preflight

# Live: save and close the source/working project first
uv run capabilities_test\02_parameters\run_parameter_brick.py

# Live with manual measurement pauses at meaningful stages
uv run capabilities_test\02_parameters\run_parameter_brick.py --pause-for-inspection

# Explicit reset from the saved, closed default source
uv run capabilities_test\02_parameters\run_parameter_brick.py --reset

# Explicit reset from another saved, closed source and its companion directory
uv run capabilities_test\02_parameters\run_parameter_brick.py --reset --source-project "C:\path\source.cst"

# After successful completion, open the saved owned copy for final inspection
Invoke-Item capabilities_test\02_parameters\artifacts\project.cst
notepad capabilities_test\02_parameters\artifacts\manual_inspection.md
```

`--cst-path "D:\CST Studio Suite 2025"` selects another installation for the test
subprocess. The default is `C:\Program Files (x86)\CST Studio Suite 2025`.
`--preflight` never copies/resets a project, calls `cst_connect`, opens a project
or constructs a CST instance. It validates the retrieved effective catalog,
every planned call schema and number/expression samples in all six bounds, then
checks offline brick generation and negative inputs. It cannot be combined with
`--reset`. Offline generation is not native CST validation.

## Persistent workspace, source and reset

Every invocation uses **one fixed directory**:

`C:\dev\cst-studio-mcp\capabilities_test\02_parameters\artifacts`

The owned project is `artifacts\project.cst`; its companion is `artifacts\project\`.
There are no timestamped run folders or fresh project copies on each invocation.
An OS file lock prevents concurrent script use. `workspace.lock` is retained;
its OS lock releases when the process exits. CST project locks are treated separately.

The default source is:

`C:\dev\cst-studio-mcp\capabilities_test\01_project_geometry_materials\runs\run_20261008T084454_422113Z_357472198d\project.cst`

**Save and close the source before the initial copy.** Inspection found a 45,283-byte
file and, initially, a zero-byte companion `project\Model.lok`. That lock was
absent on the final filesystem recheck; the project was not opened or copied by
this implementation task. The original batch report
recorded 42,505 bytes, so the current saved file must not be assumed to match the
original test output. A zero-byte lock is not evidence of a stale lock. The client
refuses copying/reset/opening when `.lok`, `.lck` or `.lock` files are present, and
also probes exclusive read access on Windows. It never removes source or working
project locks. Close CST normally; if a lock persists, investigate it manually
before rerunning. Preflight remains available while the source is locked.

First initialization copies the saved `.cst`, the entire same-stem companion
directory and same-stem file sidecars. Parent-level run reports, `Cache`, `Temp`
and unrelated files are not copied. Required companion data must exist. Links
and junctions are refused. Each copied file's actual size, modification time,
SHA-256, source path and target path is recorded, together with copy time and
invocation ID. Source snapshots before/after copy and copied-file fingerprints
must agree. The original project, companion and reports are never modified.

Subsequent invocations reuse the owned project even if the selected source has
changed or is unavailable. They record the selected source's available fingerprint
but never silently replace the owned copy. `--source-project` alone selects the
source for first initialization; use `--reset` to change an existing copy.

`workspace.json` records ownership, generated project paths, copy provenance,
fixture state and the last saved project fingerprint. An unexpected object,
missing fixture, changed saved project, incomplete creation or invalid ownership
stops the test with an explanation. No same-name object is deleted. A verified
owned incomplete copy can be recovered with an explicit reset. Missing/unverified
ownership requires manual investigation; reset does not authorize deleting
arbitrary files. Reset removes only verified manifest-owned project paths inside
the fixed workspace, after checking every target and both projects' locks. It
preserves accumulated logs, metadata, reports and manual notes.

After success the owned project is saved and closed. Open it manually using the
command above and close it before the next invocation. Avoid changing/saving it
during inspection; a changed saved fingerprint blocks reuse. On a failure the
owned project remains available for inspection. After an indeterminate operation,
inspect the actual CST state manually, save/close or restore an understood
checkpoint as appropriate, and resolve project locks before another invocation.
Do not assume the last mutation completed or immediately use reset as recovery.

## Scenario and independent evidence

The server subprocess uses `CST_CONNECT_MODE=manual`,
`CST_TOOLSETS=connection,project,geometry,parameters,diagnostics,vba`, CST 2025,
and the fixed workspace as `CST_WORK_DIR`. It connects with `mode="new"` and
requires a newly started DE with no open/adopted projects. There is no attach-to-any
fallback. It opens only the owned copy and verifies inherited **mm/GHz/ns** units;
it does not change units.

1. Check fixture ownership/names. Store `PBrick_L=10` without rebuilding, then
   `PBrick_H=4` with rebuilding. This is the **baseline rebuild** before first
   brick creation. Failure stops at that stage: inherited history, loft remnants
   and other original objects are not repaired, removed or reconstructed.
2. Create `ParameterTest:ParametricBrick`, material PEC, through `cst_create_brick`
   with the definition above. On reruns reuse the owned solid and restore those
   initial values instead of creating another solid.
3. Read parameters, enumerate the named solid/material and measure native volume/area.
4. Store `PBrick_L=14`, then `PBrick_H=6`, rebuilding through the parameter tool.
   Repeat the independent readbacks.
5. Save, close and reopen the owned copy. Repeat parameter, units, solid/material
   and volume/area checks to establish persistence.
6. Set only `PBrick_L=12` with rebuilding; repeat checks, save and close the owned
   project. Disconnect releases MCP handles without terminating CST.

| Stage | Expected dimensions (mm) | Volume (mm³) | Surface area (mm²) |
| --- | --- | --- | --- |
| Initial | 10 × 6 × 2 | 120 | 184 |
| Updated and reopened | 14 × 6 × 3 | 252 | 288 |
| Final | 12 × 6 × 3 | 216 | 252 |

The fixed queries call documented `Solid.GetVolume` and `Solid.GetArea` on the
named solid. Reference inspected:

`C:\Program Files (x86)\CST Studio Suite 2025\Online Help\mergedProjects\VBA_3D\common_vbasolido\common_vbasolido_solid_object.htm`

The client records the selected reference path, exact query, actual measured
values, effective units and tolerances. Comparisons use `rel_tol=1e-6` and
`abs_tol=1e-6`: a small allowance for CAD kernel and printed numeric precision
against an analytic cuboid, not a dimension inference. Locale decimal commas are
accepted; missing, duplicate, nonfinite or incomplete query values fail.

Only three fixed **read-only** raw VBA blocks are allowed by this client: unit
readback, shape/material enumeration and volume/area measurement. Raw VBA is
enabled only in the test server subprocess (`CST_ALLOW_RAW_VBA=1`); no permanent
environment/configuration changes occur. Creation and parameter changes still
use dedicated tools. Line-start `Debug.Print` triggers the existing
`CSTClient.execute_vba` → `capture_vba_output` path. `cst_get_parameter` uses the
same capture path for its `MsgBox` output. Capture executes outside model history,
without history fallback or UI popups.

Volume/area do not independently prove every dimension, position, fixed width
or expression association. `manual_inspection.md` contains separate checks for
actual bounds/dimensions and history expressions. The installed
`GetLooseBoundingBoxOfShape` reference explicitly describes a non-tight global
bounding box; this client does not use it as dimensional evidence.
`--pause-for-inspection` pauses at initial, updated, reopened and final stages.
Pressing Enter resumes but does not automatically mark manual checks passed.

## Logs, transport and failures

Stable files under `artifacts`:

| File | Behavior |
| --- | --- |
| `mcp_calls.jsonl` | Append-only starts/completions/exceptions/checks, full arguments/responses/content/`isError`, timing |
| `cst_messages.jsonl` | Append-only phase/failure checkpoints and inline CST messages |
| `server_stderr.log` | Append-only native server stderr between tagged invocation boundaries |
| `metadata.jsonl` | Append-only invocation, copy/reset and saved-checkpoint provenance |
| `metadata.json`, `tool_catalog.json` | Latest invocation metadata/effective catalog |
| `workspace.json` | Owned project/copy/fixture state and saved fingerprint |
| `summary.json`, `summary.md` | Latest invocation outcome, phase, checks and evidence |
| `manual_inspection.md` | Created once; user-entered notes are never overwritten |

JSONL records carry invocation ID, UTC timestamp, sequence and phase. Native
stderr lines remain unchanged between invocation boundary records. CST messages
may be inherited, repeated or truncated by the existing server; complete returned
payloads are retained without attributing all messages to the last command.
Catalog presence, implementation, offline/substitute checks, real execution,
independent readbacks and pending manual inspection are separate evidence categories.

The unchanged batch-01 `preserving_stdio.py` helper scopes the MCP SDK's two
process hooks so shutdown affects the Python server PID, not its CST descendants.
SDK framing, streams and error handling remain in use. Missing preservation hooks
block startup; no installed SDK or batch-01 file is edited. This batch also reuses
batch-01's lossless response and strict unit/shape parsers, so those files must
remain available alongside it.

Defaults: client connect timeout **120 seconds**, other MCP calls **60 seconds**.
Use `--connection-timeout` and `--call-timeout` to adjust them. Native server calls
often retain their own **30-second** limit; client timeout changes do not extend it.
Client/native timeout, interrupted in-flight request, transport loss or reported
unknown execution/activity state stops **all subsequent MCP calls**. No replay,
query, save, close, reset, disconnect or CST kill is attempted in that invocation.
Only local reports and server-only transport teardown proceed. Known failures
collect a diagnostic checkpoint when the connection/state is known, then stop;
they do not automatically save or close the failed project.

Exit codes: `0` completed preflight/scenario (manual checks may remain), `1` known
failure/inconsistent workspace, `2` indeterminate execution, `130` interruption.
Argparse also uses `2` for invalid CLI input before an invocation starts.

## Offline verification

Repository tests use substitutes/generated VBA and do not contact CST:

```powershell
uv run --python 3.12 --extra dev pytest tests\test_brick_expressions.py tests\test_parameter_brick_client.py tests\test_vba_and_patch.py tests\test_vba_injection.py tests\test_vba_security.py tests\test_parameter_optimization_tools.py tests\test_tools_registry.py tests\test_stdio_transport.py -q
uv run --python 3.12 --extra dev ruff check src\cst_mcp\vba_builder.py src\cst_mcp\vba_safety.py tests\test_brick_expressions.py tests\test_parameter_brick_client.py capabilities_test\02_parameters\run_parameter_brick.py
```

The new tests cover all six bounds, numeric formatting, names/arithmetic/mixed
inputs, malformed-but-contained expressions, invalid types/nonfinite numbers,
escaping and handler rejection, plus both SDK schema attribute names. Client
substitutes cover source-copy provenance, lock handling, reset scope, reruns,
fixture collision, baseline failure, strict native-output parsing, complete logs,
timeout/interruption cessation and local report finalization. An analytic scenario
substitute checks orchestration and duplicate prevention; it is not CST evidence.

Full-file Ruff checking of `geometry.py` reports nine diagnostics also present
at the inspected HEAD. They are unrelated to this change and remain untouched.
The changed helpers and new files have no new lint diagnostics. See `REVIEW.md`
for the review/verification record and pending live validation.
