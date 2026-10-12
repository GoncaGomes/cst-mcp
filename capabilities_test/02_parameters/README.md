# Parameter-driven geometry

This campaign checks selected parameter-driven geometry contracts through real MCP stdio.
Dedicated tools create geometry and change parameters.
Fixed setup and native readback blocks use output capture outside model history.
The clients do not start a solver.

## Runtime and scenario index

Run commands from the repository root.
The clients use `uv` and inline metadata selecting Python 3.12.
Their dependencies are MCP SDK `>=1.29,<3` and jsonschema `>=4.20`.
Live execution requires licensed CST 2025 and a matching Python library ABI.
The default installation is `C:\Program Files (x86)\CST Studio Suite 2025`.
Use `--cst-path` to select another installation.

| Scenario | Procedure | Client | Workspace under this directory |
| --- | --- | --- | --- |
| 01 Brick | [Brick](scenarios/01_brick.md) | `run_parameter_brick.py` | `artifacts/01_brick/` |
| 02 Primitives | [Primitives](scenarios/02_primitives.md) | `run_parameter_primitives.py` | `artifacts/02_primitives/` |
| 03 Extrusions | [Extrusions](scenarios/03_extrusions.md) | `run_parameter_extrusions.py` | `artifacts/03_extrusions/` |
| 04 Analytical curve | [Analytical curve](scenarios/04_analytical_curve.md) | `run_parameter_analytical_curve.py` | `artifacts/04_analytical_curve/` |
| 05 Face from curves | [Face from curves](scenarios/05_face_from_curves.md) | `run_face_from_curves.py` | `artifacts/05_face_from_curves/` |
| 06 Loft | [Loft](scenarios/06_loft.md) | `run_parameter_loft.py` | `artifacts/06_loft/` |

These workspace paths are local report locations.
Generated projects, summaries and logs are not guaranteed to exist in a fresh checkout.
Brick initialization needs a saved and closed source project.
Its locally generated default source is not shipped.
Use `--source-project` as described in the brick procedure.

## Preflight and live execution

Every client supports `--preflight`.
It starts a real MCP server with `CST_CONNECT_MODE=disabled`.
It discovers effective schemas, validates planned calls and checks offline generation or rejection.
It does not connect to CST, prepare a project or reset a workspace.
Preflight success is not native execution evidence.

For example:

```powershell
uv run capabilities_test\02_parameters\run_parameter_primitives.py --preflight
```

For live execution, use the selected scenario's command without `--preflight`.
The child server uses manual connection mode and CST 2025 configuration.
The client explicitly requests `cst_connect` with `mode=new`.
It verifies a fresh Design Environment without adopted projects.
Raw VBA permission applies only to fixed setup and readback blocks in that child.
Creation and parameter changes use dedicated tools.

Each scenario creates its fixture once.
It changes parameters, rebuilds and repeats native readbacks.
It saves, closes and reopens the owned project for persistence checks.
It applies the final state and normally saves, closes and disconnects.
The face scenario has no parameter-change stage.
Brick inherits and verifies units from its source.
The other scenarios establish and read back mm/GHz/ns.

## Ownership and reset

Each scenario owns a fixed workspace, one project and its recorded companion files.
An invocation-scoped OS lock prevents concurrent use of that workspace.
`workspace.json` records ownership, generated paths, fixture state and saved fingerprints.
The client rejects unexpected objects, changed saved files and unresolved project locks.
It does not adopt a same-name object or silently replace a project.

`--reset` recreates only verified manifest-owned project paths.
It validates targets and locks before deletion.
It preserves accumulated reports, logs and manual notes.
Unknown ownership requires investigation.
Reset does not authorize arbitrary deletion or recovery from uncertain execution.
`--preflight` and `--reset` cannot be combined.

Save and close an owned project normally before reset or reuse.
Close inspection sessions without saving edits if you want to preserve saved fingerprints.
The brick procedure adds source-copy checks and provenance.
Existing-project reuse without reset remains deferred where the scenario documents it.

## Reporting and uncertain execution

Local report locations include `summary.json`, `summary.md`, `metadata.json` and `tool_catalog.json`.
`mcp_calls.jsonl`, `cst_messages.jsonl`, `metadata.jsonl` and `server_stderr.log` accumulate evidence.
Records identify the invocation and phase.
They retain returned arguments, responses, diagnostics and execution state.
A later preflight can replace a latest summary without replacing earlier accumulated live evidence.

The default client connection timeout is 120 seconds.
Other MCP calls default to 60 seconds.
Use `--connection-timeout` and `--call-timeout` to change those client budgets.
Native operations have their own limits, described in the [reliability assessment](../../docs/RELIABILITY_REVIEW.md#timeouts).

Timeout, transport loss, an interrupted request or unknown execution stops further capability-client calls.
That includes diagnostics, save, close and disconnect.
Only local reporting and server-only transport teardown continue.
The scoped transport helper preserves CST descendants.
Inspect actual CST state before another invocation.
Known failures may collect diagnostics while state remains known.

Exit codes are `0` for a completed scenario or preflight, `1` for a known failure,
`2` for indeterminate execution and `130` for interruption.
Argument parsing can also return `2` before an invocation starts.

## Validation status

All six scenarios have completed live reports within their recorded fixture coverage.
The user confirmed the agreed modeling scenarios and manual checks.
That confirmation is separate from native measurements.
Preserved summaries may retain older pending manual wording.

| Scenario | Automated evidence | Measurement limits |
| --- | --- | --- |
| Brick | Parameters, units, identity, material, volume and area across rebuild and save/reopen | Aggregate quantities do not establish every bound or expression association |
| Primitives | Solid volumes, supported smooth areas, curve closure and maximum point count | Elliptical-cylinder area lacks an exact analytic comparison. Maximum point count is not vertex count |
| Extrusions | Parameters, units, materials, volume and area, including holes and persistence | Offset, direction, every coordinate and winding branch are not independently measured |
| Analytical curve | Parameters, units, named closure and reported maximum point count across rebuild and reopen | Endpoints and length are predictions, not automatic measurements |
| Face from curves | Profile closure, named sheet, material, type, face presence and raw area persistence | Raw sheet area is distinct from one-sided planar area. No counting convention is established |
| Loft | Parameters, units, material, solid type, volume and area across rebuild and reopen | The rectangular fixture does not establish arbitrary loft topology or every coordinate |

Offline tests use substitutes and generated VBA to check implementation contracts.
Real MCP preflight checks the transport and offline responses.
Native readbacks measure only the listed fixture properties.
Manual inspection supplies the separately confirmed observations.
The campaign does not establish simulation accuracy or arbitrary geometry validity.

See the [technical assessment](REVIEW.md) for final corrections and invocation identifiers.
See [agent setup](../../docs/AGENT_SETUP.md) and the [API assessment](../../docs/API_REVIEW.md)
for repository-wide context.
