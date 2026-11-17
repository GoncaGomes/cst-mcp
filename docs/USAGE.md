# First project and modeling examples

Complete [setup and offline acceptance](AGENT_SETUP.md) before live work.
Use a writable directory and a new project owned by the current task.
The examples below describe MCP tool calls, not Python function calls.

## Start an isolated session

Configure `CST_CONNECT_MODE=manual`, then call `cst_connect` with:

```json
{"mode": "new"}
```

Inspect the returned process and open project details before proceeding.
The default `mode=any` may adopt an existing session and its active project.
If already connected, disconnect before requesting an isolated instance.
See [connection modes and safety](CONFIGURATION.md#connection-modes-and-project-safety)
for education notice handling and compatibility limits.

## Create a parameter-driven brick

1. Call `cst_create_project` with a new destination:

   ```json
   {"path": "C:/CST-Projects/example.cst"}
   ```

2. Set and verify project units.
3. Use `cst_set_parameter` to define `length=10`.
4. Call `cst_create_brick`:

   ```json
   {
     "component": "Example",
     "name": "Brick",
     "material": "PEC",
     "x_min": 0,
     "x_max": "length",
     "y_min": 0,
     "y_max": 4,
     "z_min": 0,
     "z_max": 2
   }
   ```

Read back the parameter and inspect the created solid. Save and close the owned
project when finished. `cst_disconnect` releases MCP handles without closing CST
or its projects, and does not stop a running solver.

## Supported expression inputs

Selected geometry inputs accept finite numbers or single-line CST expressions.
Python serializes expressions and CST evaluates them. Supported inputs include brick
bounds, selected primitive radii and centers, axial ranges and Polygon3D coordinates.
Extrusions support height, profile and hole coordinates, and the active-axis offset.
Analytical curves support expressions in `t_min` and `t_max`.

`cst_create_face_from_curves` takes exactly one qualified closed planar reference,
such as `Curves:Rectangle`, in `curve_names`. CoverCurve creates a planar sheet with
the native default Vacuum material. Independent profiles, holes and thickness are
outside that contract.

`cst_create_loft` uses LoftCurves to create a capped solid. Supply at least two profiles
with at least three explicit `[x, y, z]` vertices each. Coordinates may mix numbers
and supported expressions. Legacy `[x, y]` points have no inferred spacing and are rejected.

See the [parameter-driven geometry procedures](../capabilities_test/02_parameters/README.md)
for exact field coverage, fixture contracts, ownership rules and measurement limits.
Use the [tool reference](TOOLS.md) for navigation and MCP tool discovery for active schemas.

## Validation and live checks

An accepted command is not independent evidence of the resulting geometry.
Offline tests, native readbacks and manual inspection establish different properties.
The [API assessment](API_REVIEW.md) records campaign outcomes and known limits;
the [reliability assessment](RELIABILITY_REVIEW.md) describes execution safeguards.
These checks do not validate every tool, arbitrary geometry or simulation accuracy.

The [utility scripts](../scripts/README.md) cover installed-help, saved-result and live
modeler checks. The modeler utility performs live work but does not start a solver.
Read the capability procedures before running any live scenario or resetting its workspace.
