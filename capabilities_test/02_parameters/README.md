# Batch 02: parameter-driven geometry

This deterministic Python 3.12 client uses a real MCP stdio session with
`sys.executable -m cst_mcp.server`. No LLM/SLM is involved. Creation and parameter
changes use dedicated MCP tools; the client never imports server handlers or
calls CST APIs directly. The brick, primitive, extrusion and analytical-curve clients have separate fixed
workspaces. The recorded primitive live scenario completed within its stated
coverage. Extrusion live validation of creation, parametric reconstruction, hole
effects and persistence also completed within the recorded measurement coverage.

## Server change

The existing `cst_create_brick` support is the reference. All six bounds accept a
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
wires, transformations, loft, faces, workflows,
sweeps, optimization and solvers are unchanged. The six additional primitive
contracts, extrusion extension and analytical-curve bounds are listed below.
`cst_set_parameter` is reused without modification: it stores values outside model
history and performs a separate native rebuild. Its returned `value` is an input
echo; the test separately reads actual values using the parameter query tools.
The query tools currently provide captured numeric output, so this client parses
that output rather than assuming their descriptions promise structured expressions.

One shared compatibility fix is necessary: `vba_safety.check_arguments` now reads
both SDK 1.x `inputSchema` and SDK 2.x `input_schema`. Previously SDK 2.x silently
bypassed handler schema checks. This restores existing numeric restrictions across
guarded tools; it does not extend their input contracts.

## Brick commands

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
Invoke-Item capabilities_test\02_parameters\artifacts\01_brick\project.cst
notepad capabilities_test\02_parameters\artifacts\01_brick\manual_inspection.md
```

`--cst-path "D:\CST Studio Suite 2025"` selects another installation for the test
subprocess. The default is `C:\Program Files (x86)\CST Studio Suite 2025`.
`--preflight` never copies/resets a project, calls `cst_connect`, opens a project
or constructs a CST instance. It validates the retrieved effective catalog,
every planned call schema and number/expression samples in all six bounds, then
checks offline brick generation and negative inputs. It cannot be combined with
`--reset`. Offline generation is not native CST validation.

## Brick workspace, source and reset

Every brick invocation uses **one fixed directory**:

`C:\dev\cst-studio-mcp\capabilities_test\02_parameters\artifacts\01_brick`

The owned project is `artifacts\01_brick\project.cst`; its companion is `artifacts\01_brick\project\`.
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

Stable brick files under `artifacts\01_brick`:

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
uv run --python 3.12 --extra dev pytest tests\test_primitive_expressions.py tests\test_parameter_primitives_client.py tests\test_brick_expressions.py tests\test_parameter_brick_client.py tests\test_vba_and_patch.py tests\test_vba_injection.py tests\test_vba_security.py tests\test_tools_registry.py -q
uv run --python 3.12 --extra dev ruff check src\cst_mcp\vba_builder.py src\cst_mcp\vba_safety.py tests\test_primitive_expressions.py tests\test_parameter_primitives_client.py tests\test_brick_expressions.py tests\test_parameter_brick_client.py capabilities_test\02_parameters\run_parameter_primitives.py capabilities_test\02_parameters\run_parameter_brick.py
```

Brick tests cover all six bounds, numeric formatting, names/arithmetic/mixed
inputs, malformed-but-contained expressions, invalid types/nonfinite numbers,
escaping and handler rejection, plus both SDK schema attribute names. Client
substitutes cover source-copy provenance, lock handling, reset scope, reruns,
fixture collision, baseline failure, strict native-output parsing, complete logs,
timeout/interruption cessation and local report finalization. An analytic scenario
substitute checks orchestration and duplicate prevention; it is not CST evidence.

Repository-wide formatting and lint checks for the analytical-curve extension
are recorded in `REVIEW.md`.

## Additional primitive contracts

These tools now accept finite JSON numbers or nonempty, single-line CST
expressions in the listed fields. Calls may mix numbers and expressions.

| Tool | Expression-capable fields |
| --- | --- |
| `cst_create_cylinder` | `outer_radius`, `inner_radius`, `center_x`, `center_y`, `center_z`, `range_min`, `range_max` |
| `cst_create_cone` | `bottom_radius`, `top_radius`, `center_x`, `center_y`, `center_z`, `range_min`, `range_max` |
| `cst_create_sphere` | `radius`, `center_x`, `center_y`, `center_z` |
| `cst_create_ecylinder` | `x_radius`, `y_radius`, `center_x`, `center_y`, `center_z`, `range_min`, `range_max` |
| `cst_create_torus` | `outer_radius`, `inner_radius`, `center_x`, `center_y`, `center_z` |
| `cst_create_polygon3d` | Every coordinate in every `[x, y, z]` point |

The builders reuse `_format_expression` and `set_expression_pair`, with new
`set_expression` and `set_expression_triple` helpers for radii, centers and
points. Number-only builder methods are unchanged. Names, materials, axes,
segments and unrelated fields keep their existing contracts. Forbidden control
characters are rejected by the shared expression formatter, including controls
that do not break a line. Python never evaluates caller expressions.

Numeric cylinder inner radius and cone radii remain non-negative. Cylinder outer
radius, sphere radius, elliptical cylinder radii and both torus radii remain
positive. Expressions are serialized safely and CST validates their meaning.
`cst_set_parameter` is unchanged.

CST `OuterRadius` and `InnerRadius` are the large and small radii measured from
the axis to the outer and inner surfaces. They are not the major and tube radii.
Numeric tool inputs still pass through unchanged. Installed references inspected:

- `Online Help/mergedProjects/VBA_3D/common_vbabasicsolids/common_vbatorus_object.htm`
- `Online Help/mergedProjects/3D/common_struct/common_struct_torus.htm`
- `Online Help/mergedProjects/3D/common_modi/common_modi_torusmode.htm`
- `Online Help/mergedProjects/3D/image/torus.gif`, which labels both surface extents.

These paths are relative to `C:\Program Files (x86)\CST Studio Suite 2025`.

## Validation scopes and brick relocation

Scripts stay directly under `capabilities_test\02_parameters`. Each scope owns
a fixed workspace:

- `artifacts\01_brick`: `run_parameter_brick.py`, including its copied project.
- `artifacts\02_primitives`: `run_parameter_primitives.py`, with a new blank MWS project.
- `artifacts\03_extrusions`: `run_parameter_extrusions.py`, with its own new blank MWS project.
- `artifacts\04_analytical_curve`: `run_parameter_analytical_curve.py`, with its own new blank MWS project.

Each workspace owns its project and companion data, retained workspace lock,
manifest, MCP/CST/stderr logs, invocation metadata, effective tool catalog and
latest `summary.json` and `summary.md`. Logs append records with invocation IDs
and UTC timestamps. Latest reports use fixed paths. There are no timestamped
run directories. Each client preserves the other scopes.
Artifacts and generated Python files remain ignored by Git.

The existing root-level brick contents were relocated on 8 October 2026 after
inventorying all 15 top-level entries. The project companion held 106 files;
the parent `Cache` and `Temp` directories were empty. All 138 original file and
directory entries, including empty directories, were verified after relocation.
The move rejected unidentified entries, links, junctions and destination
collisions. Exclusive access checks covered every file and the retained
`workspace.lock` was held with an OS lock during the move. No CST project locks
were present. No process was terminated and no CST connection was made.

`artifacts\01_brick\workspace.before_relocation.json` preserves the original
manifest bytes. `relocation.json` records paths, inventories, SHA-256 fingerprints,
move progress and verification. Only the operational `project` path in
`workspace.json` changed. Historical calls, summaries, metadata, source-copy
provenance, fixture state and checkpoint identifiers retain their original facts
and paths. Existing user notes were preserved without editing.

The brick project still differs from the saved checkpoint:

- Retained `saved_sha256`: `490f5597d9f2dd1083367c21db1ad0a18e949ee9448343426507924eae53b49c`
- Current file: `92fb5dde4d9b11eb4c8c50f052431adf91dbd0195049fcfeb5c6e31563de8065`

Ordinary brick reuse remains blocked. Inspect and restore the understood saved
checkpoint or use the existing explicit reset procedure with a saved, closed
source. Relocation did not adopt the current file or bypass that guard.

## Primitive commands and ownership

Run from `C:\dev\cst-studio-mcp`:

```powershell
uv run capabilities_test\02_parameters\run_parameter_primitives.py --preflight
uv run capabilities_test\02_parameters\run_parameter_primitives.py

# Explicit recovery of verified owned primitive project files only
uv run capabilities_test\02_parameters\run_parameter_primitives.py --reset
```

Python 3.12 and the inline dependencies match the brick client. The new client
uses a real stdio session with `sys.executable -m cst_mcp.server`. It imports
only inspected stateless helpers and constants from the existing clients. Those
modules define classes and functions but do not construct a client or CST
instance at import time. No brick client is instantiated or brick file written.

Preflight sets `CST_CONNECT_MODE=disabled` only in the child server environment.
It validates the effective catalog and planned schemas, then checks generated
VBA for all six tools with mixed and numeric inputs and representative invalid
inputs. It never calls connect, create/open/save/close, reset or disconnect.
It writes only this scope's logs and reports, and can run without a project.

The first live invocation reserves ownership locally and creates a new blank
MWS project through `cst_create_project`. It does not copy either earlier project.
Later invocations require a complete owned checkpoint and reuse its fixtures.
The `.cst` hash and companion/sidecar file inventory must match the saved
checkpoint. Missing files, external changes, incomplete invocation state,
unrecognized project paths or invalid ownership block reuse. Locks are never
removed or declared stale. The OS workspace lock and CST project locks are
distinct. Close projects normally before another invocation.

`--reset` requires a verified primitive manifest and checks all target paths and
project locks before removing only owned `project.cst`, `project` and recorded
same-stem sidecars. Logs, reports, caches, user notes and sibling workspaces
are retained. Unidentified files require investigation, not automatic deletion.
Preflight and reset cannot be combined. Timeout options and server-only transport
preservation follow the brick client. Child environment settings include raw VBA
permission, the selected toolsets and this scope's `CST_WORK_DIR`; the parent's
environment is unchanged.

## Primitive scenario and verification coverage

Five separated PEC solids and one rectangular Polygon3D curve share these states:

| State | `PGeom_R` | `PGeom_H` | `PGeom_Shift` |
| --- | --- | --- | --- |
| Initial | 2 | 6 | 0 |
| Updated and reopened | 3 | 8 | 2 |
| Final | 2.5 | 5 | 1 |

The component is `ParameterPrimitives`. Stable solid names are `Cylinder`,
`Cone`, `Sphere`, `ECylinder` and `Torus`. Their X centers are respectively
`Shift`, `30+Shift`, `60+Shift`, `90+Shift` and `120+Shift` mm. The sphere's
Z center is `R`; the other centers use numeric zeros. Axial ranges are `0..H`.
The curve `Curves:ParametricRectangle` begins at X=`150+Shift`, Y=20, Z=0,
with width `2R` and height `H`. Its first point is repeated to close it.
The maximum torus outer radius is 9 mm, so fixtures remain separated.

The cylinder has outer radius `R` and inner radius `R/4`; the cone has bottom
radius `R` and top radius `R/2`; the sphere has radius `R`; the elliptical
cylinder has radii `R` and `R/2`; the torus uses CST radii `3R` and `2R`.
Tool arguments contain numeric constants and quoted expressions.

Live execution verifies a newly started DesignEnvironment with no adopted
projects. It creates or opens only the owned project, sets mm/GHz/ns and reads
all three units independently before creating geometry. Initial parameter
assignments precede fixture creation and need no rebuild on a blank project.
On reuse and later changes, only the last assignment triggers the native rebuild.
Fixed setup creates the component and curve group; six dedicated geometry tools
create the fixtures once. No solver runs.

The client reads actual parameters and units, enumerates named solids/materials
and measures initial geometry. It changes parameters and repeats measurements,
saves, closes and reopens, then repeats persistence checks before applying the
final state, measuring, saving, closing and disconnecting.

Read-only measurement blocks use the existing output-capture path outside model
history. `Solid.GetVolume` is compared with analytic volume for every solid.
`Solid.GetArea` is compared with exact smooth area for the hollow cylinder,
truncated cone, sphere and torus. For torus expectations only, the major radius
is `(outer+inner)/2` and tube radius is `(outer-inner)/2`. No conversion occurs
in geometry tool inputs. Elliptical cylinder area is recorded without comparison;
no approximate ellipse perimeter is used as an exact reference.

`Curve.IsClosed` verifies the named curve item's closure. The installed help
describes `GetNumberOfPoints` as a maximum; the client records it and checks only
an integer lower bound of four, without claiming an exact vertex count.
`GetPointCoordinates` takes a string point ID and returns false when absent.
The help does not define complete ID enumeration, so no point IDs or coordinates
are guessed. Complete curve coordinate verification remains unsupported.

Measurements record actual and expected values, mm^3/mm^2 units, parameter state,
absolute and relative tolerances of `1e-6`, exact query text and installed-reference
paths/hashes. Native outputs reject duplicate, missing or nonfinite records.
Volume and area cannot prove every position, dimension or expression association.
No loose bounding box is used as exact dimensional evidence. These limits remain
in reports; there is no new manual-validation suite.

MCP logs retain complete arguments, responses, errors and timing. CST checkpoints
retain complete returned payloads and compare message content with prior
checkpoints. Baseline messages may be inherited; repeated messages and newly
observed content are separated without assigning error causality to the last
command. Server tails may be truncated. After an interrupted in-flight request,
timeout, transport loss or unknown outcome, all later calls are forbidden,
including queries, save/close/reset/disconnect. Only local reporting and Python
server teardown proceed. CST is never killed to recover a test.

Focused offline tests, lint and the real disabled-server preflight are recorded
in `REVIEW.md`. Existing primitive evidence in `artifacts/02_primitives/summary.json`
and `summary.md`, invocation `0659b7cbd45e4e019e8dda5d01d5a467`, records a completed
live scenario at phase `disconnect`, exit 0. Initial, updated, reopened and final
parameter/unit/material, native volume and supported surface-area checks passed.
This evidence does not extend the verification coverage described above.

## Extrusion expression contracts

Both `cst_create_extrude` and `cst_create_polygon_extrude` now accept finite JSON
numbers or nonempty, single-line CST expressions in `height`, every coordinate
of `points`, every coordinate of every profile in `holes`, and the selected
axis's offset. Numeric and symbolic values may be mixed. Names, material defaults,
axes, profile mapping and closure remain unchanged. No new height-sign restriction
is imposed. Null, booleans, nonfinite numbers, empty expressions, control characters
and unsupported types are rejected through the shared expression validation.
Inactive offsets may be absent or numeric zero; any expression on an inactive
axis is rejected, including the string `"0"`. Invalid optional values are not
coerced through truthiness defaults.

The installed CST 2025 references inspected are under
`C:\Program Files (x86)\CST Studio Suite 2025\Online Help\mergedProjects`:

- `VBA_3D/common_vbaextrude/common_vbaextrudeextrude_object.htm`
- `VBA_3D/common_vbacurves/common_vbacurves_extrudecurve_object.htm`
- `VBA_3D/common_vbacurves/common_vbacurves_polygon3d.htm`
- `VBA_3D/common_vbaapp/common_vbaappapplication_object.htm`

`Extrude.Height` is documented as a string, with an expression example. Origin,
profile coordinates and `ExtrudeCurve.Thickness` are documented as doubles;
Polygon3D also has a coordinate-expression example. The generated history uses
native `Evaluate(serialized_literal)` where a symbolic value is supplied to a
double argument. `Evaluate(string)` returns a double. Syntax comes only from fixed
server templates; caller text is safely quoted by `_format_expression`. Python
does not evaluate expressions, query initial values or freeze dependencies.
Expressions, signed-area evaluation and winding branches remain in history.
Native evaluation and reconstruction completed in the recorded live scenario.
This does not promise editable expressions in every native geometry dialog.

Numeric profiles retain signed-area rejection and counter-clockwise normalization.
Symbolic profiles check signed area in VBA before geometry creation. Polygon
extrusion selects winding on every rebuild, separately for the outline and each
hole. Holes grow in the same direction as the outline before subtraction.
Zero signed area raises a native error. CST remains responsible for semantic
expression errors, self-intersections and invalid hole geometry; no general
topology validator was added.

The profile-to-world mapping is z: `(u,v,offset)`, x: `(offset,u,v)`,
y: `(u,offset,-v)`. Symbolic negation is parenthesized. For positive height,
`up` spans offset to offset+height and `down` spans offset-height to offset.
Polygon extrusion uses normalized winding. Pointlist extrusion reverses V and
negates local v for `down`, reversing U cross V while preserving the world
profile and base plane. This corrects its previously ignored `down` option;
default/up numeric behavior is retained. No invented native direction property
or pointlist-winding assumption is used.

Responses provide `extrusion.expected_range` when height and offset are numeric.
Symbolic values instead produce `symbolic_endpoints.base` and `.end`, with
`numeric_range_evaluated=false`. These are input-contract predictions, never
measured CST bounds; symbolic endpoints are not numerically sorted. Metadata
is prepared before execution and attached only to successful/offline responses.
Native error and timeout payloads remain complete without creation metadata.

## Extrusion client commands and fixed workspace

Run from `C:\dev\cst-studio-mcp`:

```powershell
# Offline real MCP preflight, CST access disabled
uv run capabilities_test\02_parameters\run_parameter_extrusions.py --preflight

# First live execution creates the owned blank MWS project and four fixtures
uv run capabilities_test\02_parameters\run_parameter_extrusions.py

# Later reuse, after saving/closing the owned project; validation is deferred
uv run capabilities_test\02_parameters\run_parameter_extrusions.py

# Explicit reset of verified owned extrusion project files only
uv run capabilities_test\02_parameters\run_parameter_extrusions.py --reset
```

`--cst-path "D:\CST Studio Suite 2025"`, `--connection-timeout 120` and
`--call-timeout 60` follow the existing clients. Client timeouts do not extend
native server limits. Preflight and reset cannot be combined.

Focused offline regression and lint commands:

```powershell
uv run --python 3.12 --extra dev pytest tests\test_extrusion_expressions.py tests\test_official_tools.py tests\test_campaign_lessons.py tests\test_primitive_expressions.py -q
uv run --python 3.12 --extra dev ruff check capabilities_test\02_parameters\run_parameter_extrusions.py tests\test_extrusion_expressions.py tests\test_primitive_expressions.py
```

Repository-wide formatting and lint checks use `uv run ruff format --check`
and `uv run ruff check` from the repository root.

The fixed workspace is `capabilities_test\02_parameters\artifacts\03_extrusions`.
Its owned project is `project.cst` with companion `project\`. It retains
`workspace.lock`, ownership/checkpoint `workspace.json`, append-only
`mcp_calls.jsonl`, `cst_messages.jsonl`, `server_stderr.log`, `metadata.jsonl`,
and latest `metadata.json`, `tool_catalog.json`, `summary.json`, `summary.md`.
Records carry invocation IDs and UTC timestamps; complete MCP responses, native
diagnostic payloads and stderr are retained. There are no timestamped run folders
and no duplicate fixture creation. This client imports inspected stateless helpers
without constructing either earlier client or changing their workspace globals.

First live initialization connects with `mode=new` and verifies a newly started
DesignEnvironment with no adopted projects, then creates a blank MWS project.
Later runs require the same owned project, ready fixture state, saved SHA-256
and complete companion/sidecar fingerprint inventory. OS workspace locks and
project locks are checked separately. Changed files, incomplete state, unknown
project files, links/junctions or ownership inconsistencies stop reuse. Locks
are never removed or judged stale. Reset checks all targets first and deletes
only manifest-owned project files inside `03_extrusions`, retaining logs and
reports. `01_brick` and `02_primitives` are preserved. The brick checkpoint
mismatch is neither repaired nor adopted.

Preflight sets `CST_CONNECT_MODE=disabled` in the child server, validates the
effective catalog and every planned call schema, and retrieves generated VBA
through MCP for numeric/mixed fixtures and representative invalid inputs. It
does not prepare/reset a workspace project or call connect, create/open/save/close
or disconnect. Live raw VBA is enabled only in the child environment and restricted
by this client to fixed setup and read-only blocks. Geometry creation and parameter
changes use dedicated tools. Queries use output capture outside model history.

After timeout, transport loss, interrupted in-flight requests or unknown execution
or activity state, every later MCP call stops. No retry, query, save, close, reset,
disconnect or CST termination occurs. Only local reports and Python server transport
teardown proceed, using the existing `preserve_cst_processes` mechanism. Known
failures may collect diagnostics while state is known, then stop without saving
or closing the failed project. Exit codes match the primitive client: 0 completed,
1 known failure, 2 indeterminate, 130 interruption.

## Extrusion scenario and verification limits

The four PEC solids in component `ParameterExtrusions` share these states:

| State | `PEx_W` | `PEx_H` | `PEx_Offset` | `PEx_Side` |
| --- | --- | --- | --- | --- |
| Initial | 8 | 3 | 2 | 1 |
| Updated and reopened | 10 | 4 | 5 | -1 |
| Final | 6 | 2 | 1 | 1 |

All heights use `PEx_H` and active offsets use `PEx_Offset`. Profiles have v=0..4.

| Solid | Tool / direction | Profile u extent | Hole u extent / v extent |
| --- | --- | --- | --- |
| `PointZUp` | pointlist / z up | 0..W | W/4..3W/4 / 1..3 |
| `PointXDown` | pointlist / x down | 30..30+W | none |
| `PolygonYUp` | polygon / y up | 60..60+Side*W | 60+Side*W/4..60+3Side*W/4 / 1..3 |
| `PolygonZDown` | polygon / z down | 90..90+W | none |

The outline of `PointZUp` and initially `PolygonYUp` is clockwise. The
`PolygonYUp` outline and hole have opposite initial caller winding. Switching
Side from 1 to -1 flips both winding signs and mirrors the profile about u=60;
all three states remain nondegenerate, separated and have valid internal holes.
Outer and hole normalization must therefore be independent and rebuilt.

The client establishes mm/GHz/ns and reads effective units, sets initial
parameters and creates fixtures only once. It reads actual parameters, enumerates
solids/materials and measures initial geometry. It then changes parameters,
rebuilds and repeats readbacks, saves/closes/reopens and checks persistence,
then applies the final state, rebuilds, measures, saves/closes and disconnects.

Independent analytic expectations use known fixture dimensions, never arbitrary
expression evaluation: unperforated profile area A=4W and boundary length P=2W+8;
holed profile A=3W and P=3W+12, including internal walls. Prism volume is A*H and
surface area is 2A+P*H. Both `Solid.GetVolume` and `Solid.GetArea` are compared for
all four solids with absolute and relative tolerances `1e-6`. Reports retain
complete query output, actual parameter states, expected/measured values, units,
tolerances, exact queries and installed-reference paths/hashes.

Volume/area establish only aggregate quantities, including hole effects. They
do not independently prove offset, direction, every coordinate, the selected
winding branch or expression association. No additional documented query was
used as exact dimensional evidence; the installed loose-box query is non-tight.
After a successful live run, inspect the owned saved project without saving edits:
check stored history for parameter expressions, native Evaluate and both winding
branches; check base planes/directions and the axis-y profile at z=-4..0 against
the fixture contract. The final predicted ranges are PointZUp z=1..3,
PointXDown x=-1..1, PolygonYUp y=1..3, PolygonZDown z=-1..1 mm. Close the project
before reuse. This is brief inspection guidance, not a separate validation suite.

Live invocation `98f64bf7619b4746a1f3961414ad537a` completed with exit 0 and
178 passed checks, including 32 native volume/area comparisons across initial,
updated, reopened and final stages. This validates creation, parametric
reconstruction, hole effects and save/close/reopen persistence within that
coverage. The user also manually confirmed the created solids; that confirmation
does not add coordinate, direction or history-expression measurements.

The latest retained `artifacts/03_extrusions/summary.md`, `summary.json` and
`metadata.json` now identify a later successful invocation,
`ac201e49e35d4f9aa50329fe0ec890f1`, with the same exit status, check counts and
measurement coverage. Historical reports, projects, manifests and accumulated
logs are preserved. Volume/area do not independently establish offsets,
directions, every coordinate or history-expression association.

Reuse without `--reset` is deferred and is not a completion requirement for this
task. No reuse test was performed for this documentation update. Catalog presence,
implementation, offline checks, real execution and independently confirmed
effects remain separate evidence categories in the client reports.

## Analytical-curve bounds, workspace and commands

`cst_create_analytical_curve` now accepts finite JSON numbers or nonempty,
single-line CST expressions in both `t_min` and `t_max`, including mixed bounds.
The builder reuses `_expression_field` and `set_expression_pair`. Numeric
formatting and native range semantics are preserved, with no new sign or ordering
restriction. Python does not evaluate expressions. Shared validation and escaping
reject invalid types, booleans, null, nonfinite numbers, empty expressions and
forbidden control characters before execution. Coordinate laws, names and the
existing `Curves` group contract are unchanged.

The installed AnalyticalCurve help lists double arguments for `ParameterRange`
but explicitly demonstrates `.ParameterRange "-r*pi", "r*pi"`. This extension
uses that documented quoted-expression form without adding an expression parser.
Both inspected references are relative to the selected CST 2025 installation:

- `Online Help/mergedProjects/VBA_3D/common_vbacurves/common_vbacurves_analyticalcurve_object.htm`
- `Online Help/mergedProjects/VBA_3D/common_vbacurves/common_vbacurves_curve_object.htm`

The deterministic client uses only
`C:\dev\cst-studio-mcp\capabilities_test\02_parameters\artifacts\04_analytical_curve`.
Its owned project is `project.cst` with companion `project\`. Python 3.12 and
inline `mcp>=1.29,<3` / `jsonschema>=4.20` dependencies match Batch 02. It uses
`sys.executable -m cst_mcp.server` over real MCP stdio, without an LLM/SLM,
direct CST API calls or server-handler calls. It imports inspected stateless
helpers without instantiating earlier clients or changing their globals.

Run these PowerShell commands from `C:\dev\cst-studio-mcp`:

```powershell
# Offline catalog, planned schemas and generated VBA, with CST disabled
uv run capabilities_test\02_parameters\run_parameter_analytical_curve.py --preflight

# Live initialization and scenario, pending user execution and review
uv run capabilities_test\02_parameters\run_parameter_analytical_curve.py

# Explicit scoped reset and live scenario, after saving and closing the owned project
uv run capabilities_test\02_parameters\run_parameter_analytical_curve.py --reset

# Open only after successful completion, then close without saving inspection edits
Invoke-Item "C:\dev\cst-studio-mcp\capabilities_test\02_parameters\artifacts\04_analytical_curve\project.cst"
```

Options follow the existing clients: `--cst-path` defaults to
`C:\Program Files (x86)\CST Studio Suite 2025`, `--connection-timeout` to 120
seconds and `--call-timeout` to 60 seconds. Native server timeouts are independent.
Preflight sets `CST_CONNECT_MODE=disabled` in the child environment, reads the
effective MCP catalog, validates planned schemas and retrieves numeric, symbolic
and mixed range VBA plus representative rejections. It never prepares, creates
or resets a project, connects to CST or calls project lifecycle tools.
`--preflight` and `--reset` cannot be combined.

First live initialization creates a new blank MWS project. The client owns one
fixed project, with a retained OS workspace lock, ownership/state manifest,
project-lock checks and saved project/companion/sidecar fingerprints. Ordinary
reuse requires a ready, unchanged checkpoint and skips duplicate curve creation.
Testing reuse without reset is deferred. Explicit reset checks every target
before deleting only manifest-owned project files inside `04_analytical_curve`.
Logs, reports and sibling workspaces remain. Unknown files, incomplete state,
links/junctions and locks stop reuse/reset; locks are never removed as stale.

`mcp_calls.jsonl`, `cst_messages.jsonl`, `metadata.jsonl` and `server_stderr.log`
append invocation-tagged evidence. `metadata.json`, `tool_catalog.json`,
`summary.json` and `summary.md` are fixed latest paths; `workspace.json` stores
ownership and saved fingerprints. No timestamped run folders are created.
Generic VBA is enabled only in the child server environment and limited by the
client to fixed setup and read-only query blocks. Queries use output capture
outside model history. Existing server-only transport teardown preserves CST
processes. Timeout, transport loss, interrupted in-flight requests or unknown
execution state forbid every subsequent MCP call, including queries, save,
close, reset and disconnect. Only local reports and Python transport teardown
continue. Known failures may collect diagnostics while state is known.

## Analytical-curve scenario and evidence limits

One straight curve, `Curves:ParametricLine`, uses `x_expr="t"`, `y_expr="0"`,
`z_expr="0"`, `t_min="PCurve_Start"` and
`t_max="PCurve_Start+PCurve_Length"`. A fixed setup block initializes `Curves`
only on first creation. Dedicated MCP parameter tools establish these states:

| Stage | Start | Length | Predicted X endpoints (mm) | Predicted length (mm) |
| --- | --- | --- | --- | --- |
| Initial | 2 | 5 | 2..7 | 5 |
| Updated and reopened | -1 | 10 | -1..9 | 10 |
| Final | 3 | 4 | 3..7 | 4 |

The client establishes mm/GHz/ns and reads them independently, creates the
curve once, changes parameters and rebuilds, saves/closes/reopens, applies the
final state and rebuilds again, then saves/closes and disconnects. Independent
list and individual parameter readbacks check actual values at every stage.
The saved owned project is retained for inspection.

Automated live evidence establishes tool acceptance, rebuild acknowledgements,
actual parameter values, effective units, successful named-item `Curve.IsClosed`
and `Curve.GetNumberOfPoints` readbacks, parameter/unit persistence and repeated
named-query readbacks across save/reopen. `IsClosed` must report false. `GetNumberOfPoints` is recorded as a
nonnegative integer maximum, never an exact point count. The inspected help
does not establish a direct length query or complete point-ID enumeration.
No length method, point IDs or endpoint measurements are invented. Predictions
are labeled separately, with endpoint/length measurements explicitly null.
These checks do not independently prove endpoint motion, length, straightness
or history-expression association. Analytical-curve live validation remains
pending until the user executes the scenario and reviews its evidence.

After a successful live run, select `Curves:ParametricLine` and use CST's native
measurement controls to record final endpoints `(3,0,0)` and `(7,0,0)` mm and
length 4 mm. Record the invocation ID, measurement method and actual values.
Inspect the curve's stored history for `.LawX "t"`, `.LawY "0"`, `.LawZ "0"`
and `.ParameterRange "PCurve_Start", "PCurve_Start+PCurve_Length"`. A dialog
showing evaluated values alone does not establish retained symbolic history.
Earlier endpoint/length checks also remain pending unless measured at those
states. Avoid saving inspection edits because reuse checks the saved fingerprints.

Focused offline tests and both disabled-server preflights are recorded in
`REVIEW.md`. No analytical-curve live scenario was run during implementation.
