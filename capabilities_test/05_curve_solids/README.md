# Batch 05: solids from curves

This batch decides how task 18 ([solid wire and trace from a curve](../../docs/tasks/mcp/18-solid-from-curve.md))
should build its tools. It has two phases:

1. **Probe** (`run_curve_solid_probe.py`, this client). Each native method that can turn a curve
   into a solid runs as a fixed raw VBA block through a real MCP stdio session. No new MCP tool is
   needed. The outcome of every method is recorded, including native errors and stalls.
2. **Acceptance** (added with the task 18 tools). The same geometry is built with
   `cst_create_curve_wire` and `cst_create_trace_from_curve` and checked against the task 18
   criteria.

## Why a probe first

`cst_create_wire` builds a cylinder and rotates it because the wire-to-solid conversion
(`Wire.ConvertToSolidShape`) stalled a CST 2026 live test (`src/cst_mcp/tools/geometry.py`,
`_build_wire`). That conversion is what the task's proposed `cst_create_curve_wire` would use. The
probe checks it on CST 2025 next to the alternatives, so the tool is built on a method that works.

## What runs

Each method gets its own curve group and its own copy of the curve, far from the others.

| Step | Native method | Geometry |
| --- | --- | --- |
| `D_trace` | `TraceFromCurve`, width 0.5, thickness 0.1, flat ends | Archimedean spiral, radius 2 → 6.5 mm, 3 turns, centre X = -100 |
| `C_sweep` | `SweepCurve` with `ProjectProfileToPathAdvanced`; a 0.5 mm circle at the path start, normal to the path | Helix, centre X = 0 |
| `F1_wire_missing_curve` | `Wire` (`Curvewire`) on a curve that does not exist | — |
| `F2_trace_missing_curve` | `TraceFromCurve` on a curve that does not exist | — |
| `F3_wire_disconnected_group` | `Wire` (`Curvewire`) on a curve group holding two separate lines | Two lines at X = 300 |
| `B_wire_model` | `Wire` (`Curvewire`) with `SolidWireModel True`, no conversion | Helix, centre X = 100 |
| `A1_wire_add` | `Wire` (`Curvewire`), plain wire | Helix, centre X = 200 |
| `A2_convert` | `Wire.ConvertToSolidShape` of `A1` (a thin wire), `KeepWire False` | Solid `ProbeWire:helix_solid` |
| `A3_convert_solid_model` | `Wire.ConvertToSolidShape` of `B` (a solid-model wire), `KeepWire False` | Solid `ProbeWire:helix_model_solid` |

The first live run (2026-11-27) showed that CST refuses to convert a thin wire: *"Wire for
conversion to solid is of thin wire model."* `A2` is kept to record that refusal. `A3` is the
conversion that matters for task 18.

`F1`–`F3` and `A2` are meant to fail, so CST shows four errors in its message window during a
run. The summary labels them `expected error`. If one succeeds it reads `unexpected success`.

## Results (CST 2025, 2026-11-27)

- `D_trace`, `C_sweep` and `A3_convert_solid_model` match the exact volume (0.00 %) and bounds.
- `F1`–`F3` and `A2` fail with specific messages. They leave no history entry, no shape, and
  their curves untouched.
- Every method that runs consumes its curve items (the trace path, the sweep path and profile,
  the wire path).
- `Wire.GetLength` reads 771.8 mm for the 772.47 mm helix.

Task 18 builds `cst_create_curve_wire` on `B` then `A3`: a solid-model wire, then its
conversion.

The helix is the task 18 benchmark: radius 20 mm, six turns from Z = 4 to Z = 172, wire radius
0.5 mm. Its exact length is 772.47 mm and its volume 606.70 mm³. The client computes the exact
bounds of each solid. The flat end caps tilt with the pitch, so the helix's Z range is
3.512–172.488 mm.

The steps run in the order shown. The two conversions run last, after a save, so a stall loses
only their results. The saved project then already holds every other method. A conversion is
skipped when the step that makes its wire did not run.

After each step the client reads back:

- all solids and their materials;
- each probe curve item by name (`Curve.GetNumberOfPoints`), which shows whether a method consumed
  its curve. The curve group list alone cannot show that;
- wire lengths (`Wire.GetLength`) after the wire steps;
- bounds and volume through `cst_get_solid_bounds` for `D`, `C`, `A2` and `A3`.

The curve items are also read once before the methods. If some are not found even then, the
summary says so, and the "curve items left" column can't be trusted.

These queries use output capture, so they add nothing to the model history.

A solid "matches" when its volume is within 3 % of the exact value, and its loose bounding box
contains the exact extent (0.05 mm tolerance) without exceeding it by more than 1 mm.

**Failed calls and history.** The project is saved before the conversions and again at the end. Each time,
the client reads the saved `project/Model/3D/ModelHistory.json` and records which method blocks
appear in it. That answers whether a failed call, such as `F1`–`F3`, leaves an entry in the
history.

## Acceptance (`run_curve_solid_acceptance.py`)

Builds the task 18 geometry with the MCP tools themselves. Raw VBA is used only for units,
creating the two components, the readback queries and, if CST needs it, the `Curves` group. It
shares the probe's client (owned workspace, `--reset`, stop after a timeout) in its own
workspace, `artifacts/acceptance/`.

| Case | Calls | Passes when |
| --- | --- | --- |
| `H1_helix_pec` | `cst_create_analytical_curve`, `cst_create_curve_wire` | executed, curve consumed, volume within 3 % of 606.70 mm³, bounds match, `cst_get_solid_bounds` agrees, 2 history entries |
| `H2_helix_copper` | `cst_load_material` (`Copper (annealed)`), curve, wire | as H1, and the solid's material is copper |
| `T1_spiral_on_board` | `cst_load_material` (`FR-4 (lossy)`), `cst_create_brick` (top at Z = 1.6), spiral curve at Z = 1.6, `cst_create_trace_from_curve` | volume within 3 %, Z from 1.6 to 1.7, 1 history entry |
| `P1_bent_polygon_wire` | `cst_create_polygon3d` (an L of two 10 mm segments), wire | recorded only: the volume near the corner is approximate |
| `E1_wire_missing_curve`, `E2_trace_missing_curve` | either tool on a curve that does not exist | refused, no shape and no history entry |
| `E3_wire_reused_curve` | wire on H1's curve again | refused: the curve was consumed |
| `E4_wire_zero_radius` | wire with radius 0 | refused before CST |
| `N1_new_component` | wire into a component never created | recorded: whether CST creates it or refuses. Runs last, after a save |

The first curve is made without creating the `Curves` group, as the agent would. If CST refuses,
the summary records that the group is needed, the client creates it and goes on.

A disconnected curve can't be tested through the tools: they take one `group:item`, which is
always one connected path. The probe's `F3` covered a disconnected group.

Exit codes: 0 when every case with a criterion passes, 1 when one fails or the client fails, 2
indeterminate, 130 interrupted.

```powershell
uv run python capabilities_test\05_curve_solids\run_curve_solid_acceptance.py --preflight
uv run python capabilities_test\05_curve_solids\run_curve_solid_acceptance.py
```

The live run is for the user to start. Its `summary.md` has one row per case.

### Acceptance results (CST 2025, 2026-11-27)

All cases passed (exit 0), and CST logged no errors.

- H1 and H2: volume 606.70 mm³ (0.00 %) in PEC and copper. The loose box is ±20.69 mm in X/Y
  (0.19 mm beyond the exact ±20.5) and Z 3.492–172.489. 2 history entries each.
- T1: volume 4.013 mm³ (0.00 %), Z 1.599–1.701 on the board top. 1 history entry.
- P1: volume 15.708 mm³, exactly π r² × 20 mm; box X 199.5–210, Z 0–10.5.
- E1–E4: refused with no shape and no history entry.
- N1: CST creates the missing component during the conversion; the helix is correct.
- The first analytical curve did not need the `Curves` group to exist.
- Each tool's own measured result equals the separate `cst_get_solid_bounds` readback.

## Commands

Run from the repository root. These need Python 3.12 and the CST 2025 Python libraries (default
`C:\Program Files (x86)\CST Studio Suite 2025`; use `--cst-path` for another install).

Offline checks; no CST is started:

```powershell
uv run python capabilities_test\05_curve_solids\run_curve_solid_probe.py --preflight
uv run --extra dev --extra figures pytest capabilities_test\05_curve_solids
```

The preflight starts a real MCP server with `CST_CONNECT_MODE=disabled`. It checks:

- that the planned tools are in the catalog, and their schemas;
- that the installed help documents each method;
- that every fixed VBA block passes the server's raw VBA screen unchanged.

Its reports go to `artifacts/preflight/`.

The live probe is for the user to start:

```powershell
uv run python capabilities_test\05_curve_solids\run_curve_solid_probe.py
uv run python capabilities_test\05_curve_solids\run_curve_solid_probe.py --reset
```

The live probe:

- starts a new Design Environment (`cst_connect` with `mode=new`) and refuses one with open
  projects;
- creates `artifacts/probe/project.cst`, sets mm/GHz/ns and runs the steps;
- saves, closes and disconnects at the end.

Every run starts from a new project. If a previous probe project exists, the client stops, so you
can inspect it first. Then pass `--reset`, which deletes only the probe's own project paths
(`project.cst`, `project/` and `project.*`), after checking that the project is closed.

Raw VBA is enabled only in the child server. The client sends only the fixed blocks defined in the
script.

`--call-timeout` (default 120 s) and `--connection-timeout` (default 120 s) bound each request. The
server already stops waiting for a history entry after 30 s and reports `execution_state:
unknown`. After any timeout or transport loss, the client makes no further calls and exits with
code 2. Its summary still records every step that ran. If `A3_convert_solid_model` stalls, that
is the probe's answer for that method. Inspect CST by hand before closing it or running again.

Exit codes:

- 0: the probe ran to the end, whatever each method's verdict;
- 1: a known failure of the client or setup;
- 2: indeterminate;
- 130: interrupted.

## Outputs

`artifacts/probe/` holds:

| File | Content |
| --- | --- |
| `summary.md` | One row per step: outcome, time, verdict, volume and its error, bounds check, wire lengths, which of its curve items are left, whether it is in the saved history |
| `summary.json` | The same, plus every readback, the saved-history analysis, checks and expected geometry |
| `mcp_calls.jsonl` | Every request and response, appended across invocations |
| `metadata.json` | Revision, script hash, environment, VBA blocks, help references and their hashes |
| `tool_catalog.json`, `server_stderr.log` | Catalog and server stderr |

## Limitations

- One probe per method on one CST version. A method that works here may still need confirmation
  on other versions.
- The curves and the profile are fixed. Other curve shapes are not covered.
- The bounding box is loose, so volume is the main check.
- History presence is read from the file CST writes on save.
- CST messages may be inherited or repeated. They do not prove which call caused them.
