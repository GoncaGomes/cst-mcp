# Scenario 06: parameter-driven loft

This scenario checks a capped rectangular LoftCurves solid with symbolic 3D profiles.
Read the [common runtime and ownership rules](../README.md) before running it.

## Input contract

`cst_create_loft` requires at least two profiles.
Each profile needs at least three explicit `[x, y, z]` vertices.
Coordinates accept finite numbers or nonempty single-line CST expressions.
Mixed coordinates are supported.
Legacy `[x, y]` points are rejected.
The builder does not infer profile spacing or z coordinates.

The builder validates all profiles before execution.
It preserves vertex order, removes repeated closing endpoints and closes each profile once.
At least three vertices must remain after endpoint removal.
CST evaluates expressions and native geometry.

Each Polygon3D profile uses a separate auxiliary curve group.
The builder checks for existing group collisions before creating profiles.
It neither adopts nor deletes an existing group.
Qualified profile items reach `LoftCurves` in input order.
The native `Solid` option is true, producing capped solid construction.
Path and additional loft options are outside this contract.

## Commands and workspace

Run from the repository root:

```powershell
uv run capabilities_test\02_parameters\run_parameter_loft.py --preflight
uv run capabilities_test\02_parameters\run_parameter_loft.py
uv run capabilities_test\02_parameters\run_parameter_loft.py --reset
```

Other options are `--cst-path`, `--connection-timeout` and `--call-timeout`.
Preflight and reset cannot be combined.

The local workspace is `capabilities_test/02_parameters/artifacts/06_loft/`.
It owns `project.cst`, `project/` and recorded sidecars.
The manifest and saved fingerprints govern reuse and reset.
Existing-project reuse without reset remains deferred.
Generated projects and summaries are local report locations.

## Fixture

The fixture is `LoftValidation:ParametricLoft` with PEC material.
Its two identical rectangular profiles use:

```text
(0,0,z)
(PLoft_Width,0,z)
(PLoft_Width,PLoft_Depth,z)
(0,PLoft_Depth,z)
```

The profile planes use z=0 and z=`PLoft_Length`.
Dedicated loft creation records the symbolic coordinates in model history.
Dedicated parameter tools store values outside history.
The last grouped parameter update requests a separate native rebuild.

| Stage | Width, depth and length in mm | Expected volume in mm³ | Expected area in mm² |
| --- | --- | --- | --- |
| Initial | 4, 2, 5 | 40 | 76 |
| Updated | 6, 3, 4 | 72 | 108 |
| Reopened | 6, 3, 4 | 72 | 108 |
| Final length rebuild | 6, 3, 7 | 126 | 162 |

The client establishes and reads back mm/GHz/ns.
It creates once, rebuilds, saves, closes and reopens.
Only length changes after reopening.
Normal completion saves and closes the final project, then disconnects.

## Measurements and limits

Independent queries read parameters, units, named shape and PEC association.
Native existence, solid type and hybrid-shape checks verify a solid without hybrid sheet parts.
Volume and area are compared with analytic prism values.
Absolute and relative tolerances are `1e-6`.
Saved-file fingerprints and reopened readbacks establish the recorded persistence checks.

The retained live report completed successfully within this fixture coverage.
Volume and area do not establish every coordinate, position, orientation or expression association.
The campaign covers this rectangular loft, not arbitrary section topology.
Profile curves may be consumed by the native operation.
Their continued presence is not assumed or queried.

The user confirmed the agreed manual checks separately.
Inspect dimensions, profile order, position, capped ends and retained symbolic history.
Close without saving inspection edits to preserve saved fingerprints.
See the [review](../REVIEW.md#verification) for the live invocation identifier.
