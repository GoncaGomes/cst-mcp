# Reliability assessment

This assessment documents current server safeguards and capability-client policies.
The completed CST 2025 campaign covers owned modeling fixtures and selected error cases.
Offline tests exercise transport and execution contracts with substitutes.

## Connection and startup

| Setting | Behavior |
| --- | --- |
| `CST_CONNECT_MODE=manual` | Default. MCP startup leaves CST disconnected |
| `CST_CONNECT_MODE=disabled` | Prevents live connection and supports offline acceptance |
| `CST_CONNECT_MODE=auto` | Explicitly connects before the stdio server starts serving |
| `cst_connect` with `mode=any` | Default. May adopt an existing Design Environment and active project, or launch CST |
| `cst_connect` with `mode=new` | Starts a fresh Design Environment when initially disconnected |
| `cst_disconnect` | Releases local handles without closing CST, projects or a running solver |

An already connected session is preserved even if a later connection request specifies `new`.
Connection responses report process and project details.
Capability clients request `new` and verify that they did not adopt another project.

The server reserves a duplicate stdout handle for JSON-RPC.
Python prints and native writes to file descriptor 1 are redirected to stderr.
This isolation begins before client construction or CST probing.
Default startup avoids a CST connection, so library availability does not require a GUI launch.
An automatic connection failure is logged and startup can continue offline.
Work-directory errors are retained for tools that need the directory.

## Schemas and protocol compatibility

The registry validates tool schemas at startup.
It checks strict JSON, object properties, required fields and declared output schemas.
It supports SDK 1.x registration and SDK 2.x explicit request handlers.
Schema access handles both `inputSchema` and `input_schema`.
The same compatibility applies to output schema attributes.

Guarded handlers check declared numeric types and string serialization constraints.
SDK request validation rejects missing or invalid schema inputs.
SDK 2.x request registration explicitly performs JSON Schema input validation.
Result handling marks recognized `error`, `busy` and `timeout` envelopes with MCP `isError`.
It validates successful payloads against declared output schemas.
An output-schema mismatch retains the raw response and reports an error.
Nonfinite numeric values are normalized for strict JSON output.

Offline regressions cover real stdio initialization and discovery, schema errors,
worker execution, connection modes, error envelopes and output schemas.
Native error cases in the boolean campaign also verify schema and argument rejection layers.

## CST access and execution state

The server routes tool handlers and automatic startup connection through one dedicated worker thread.
Blocking native calls therefore serialize on that thread.
The protocol event loop remains separate.
Handlers that yield can interleave on the worker loop.
This mechanism is not a cross-process lock between different MCP clients.
Request cancellation cannot interrupt a blocking native call already in progress.

History mutations and query paths check whether the solver is idle.
Active or unknown solver state blocks guarded operations.
Parameter changes, rebuilding, result deletion and project closing have their own state checks.
History changes are refused when existing results would trigger the incompatible-results dialog.
Unavailable state is not treated as idle.

Native execution paths report errors and preserve available context.
A timed-out history call returns `execution_state=unknown`.
Silent VBA execution does not replay a partially attempted call through another entry point.
Generated `offline` VBA is not an execution result.
The server does not apply the capability clients' permanent stop-after-uncertainty rule to every later request.

## Timeouts

Timeouts belong to specific layers and operations.

| Layer or operation | Current behavior |
| --- | --- |
| Capability-client connection request | Default 120 seconds, configurable with `--connection-timeout` |
| Other capability-client MCP requests | Default 60 seconds, configurable with `--call-timeout` |
| History VBA and silent VBA entry points | Pass 30 seconds to the native API |
| Native query capture | Uses silent VBA without history fallback, plus its idle-state probe |
| Native rebuild | Defaults to 30 seconds and accepts an operation timeout |
| Blocking solver execution | Session default 3600 seconds, subject to the calling tool's selected timeout |
| Asynchronous solver startup | Session default 30 seconds. The VBA solver-start probe defaults to 10 seconds |
| `cst_wait_for_simulation` | Defaults to 45 seconds, caps the wait budget at 55 seconds and allows about 2 seconds of polling overrun |

A full solver-status request can make several native calls, each with its own timeout.
An idle guard uses a five-second solver-state probe.
A client timeout does not extend a native timeout or prove that native execution stopped.
Solver start timeouts can retain a running solver or an unknown execution state.
A wait report of `running` or `starting` calls for another wait, not another start.

## Capability-client ownership and stop rules

These policies belong to the capability clients:

- Use fixed, owned workspaces and uniquely identified invocations.
- Hold an OS file lock during a workspace invocation.
- Record ownership, generated paths and saved project fingerprints in manifests.
- Reject unexpected objects, changed saved files, open-project locks and unsafe paths.
- Scope reset to verified owned project paths after validating all targets.
- Preserve reports, accumulated logs and manual inspection records during reset.
- Stop further MCP calls after timeout, transport loss, interrupted requests or unknown execution.
- Permit only local reporting and Python-server teardown after that stop.

The stop rule includes diagnostics, save, close and disconnect.
Known errors may collect readbacks only while execution remains known and the procedure permits them.
The boolean errors coordinator continues to another case only after normal closure.
Fingerprints detect saved-file changes and do not measure physical geometry.

The clients use the scoped `preserving_stdio.py` transport helper.
It keeps the MCP SDK framing and streams while replacing two process lifecycle hooks.
On Windows it avoids a Job Object that could terminate CST descendants.
Shutdown escalation targets the Python server PID.
Missing private SDK hooks block transport startup.
This preservation policy does not automatically apply to every external MCP client.

## Dialogs and window classification

The Windows dialog inventory uses process membership and title matching.
It can include nonmodal tool windows.
Inventory membership alone does not establish modality or blocking behavior.
`CST_QUIET` does not suppress dialogs.

For expected boolean-error responses, the client narrowly recognizes an informational window when:

- The title is exactly `Messages`.
- The class starts with `Qt` and ends with `QWindowToolSaveBits`.
- The inventory match is `cst_process`.
- Child texts are empty and `full_text` is empty.
- Neither `modal` nor `blocking` is truthy.

The client records this assessment without dismissing the window.
A completed native rejection can then proceed through the existing idle and ownership checks.
Other reported windows and malformed inventories retain the unresolved-state stop behavior.
This correction does not establish universal modal detection or license-popup recovery.

## Evidence and CI

The six parameter fixtures completed live within their readback coverage.
Reviewed boolean operations invocation `54fa1e64b7f14d8bb9c0d4095a4593cd` passed its recorded checks.
The latest operations summary is preflight.
Errors invocation `6e5830985528449094d6860c39018633` passed all nine cases,
including integrity checks and normal closure.
Batch 01 retains known failures.

The user confirmed the agreed manual inspections.
That confirmation is separate from automated measurements and older pending wording.
The [parameter](../capabilities_test/02_parameters/REVIEW.md) and
[boolean](../capabilities_test/03_booleans/REVIEW.md) reviews describe the evidence.

Configured CI runs Python 3.12 offline tests on Ubuntu and Windows
with MCP SDK 1.29.0 and 2.2.0.
It installs `.[dev,figures]` and runs pytest.
Formatting, lint and documentation generation are not workflow steps.
A successful workflow validates its specific commit, not every later cleanup state.

Use [agent acceptance](AGENT_SETUP.md) and the
[parameter ownership guide](../capabilities_test/02_parameters/README.md#ownership-and-reset)
before native work.
