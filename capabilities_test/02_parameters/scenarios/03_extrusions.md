# Scenario 03: parameter-driven extrusions

This scenario checks pointlist and polygon extrusions with parameterized profiles and holes.
Read the [common runtime and ownership rules](../README.md) before running it.

## Input contract

Both `cst_create_extrude` and `cst_create_polygon_extrude` accept finite numbers
or nonempty single-line CST expressions in:

- `height`.
- Every coordinate in `points`.
- Every coordinate of every profile in `holes`.
- The selected axis's `x_offset`, `y_offset` or `z_offset`.

Inactive offsets must be absent or numeric zero.
An inactive expression, including `"0"`, is rejected.
Names, materials, axes and profile closure keep their own contracts.
The extension does not impose a new height-sign restriction.

Python serializes expressions.
Native `Evaluate` supplies symbolic values to typed double arguments.
Evaluation stays in model history so a rebuild uses the current parameters.
CST handles semantic expression errors and geometric validity.

Numeric profiles reject zero signed area and normalize winding.
Symbolic profiles check signed area in VBA before geometry creation.
Polygon winding is selected independently for the outline and each hole on every rebuild.
Holes extrude in the outline direction before subtraction.
The builder does not provide a general self-intersection or hole-topology validator.

| Axis | World coordinates for a profile point `(u,v)` |
| --- | --- |
| z | `(u,v,offset)` |
| x | `(offset,u,v)` |
| y | `(u,offset,-v)` |

For positive height, `up` spans `offset..offset+height`.
`down` spans `offset-height..offset`.
Pointlist down reverses the plane normal and remaps local coordinates.
Returned numeric ranges or symbolic endpoints are predictions, not measured bounds.

## Commands and workspace

Run from the repository root:

```powershell
uv run capabilities_test\02_parameters\run_parameter_extrusions.py --preflight
uv run capabilities_test\02_parameters\run_parameter_extrusions.py
uv run capabilities_test\02_parameters\run_parameter_extrusions.py --reset
```

Other options are `--cst-path`, `--connection-timeout` and `--call-timeout`.
Preflight and reset cannot be combined.

The local workspace is `capabilities_test/02_parameters/artifacts/03_extrusions/`.
It owns `project.cst`, `project/` and recorded sidecars.
The manifest, lock and saved fingerprints govern reuse and reset.
Existing-project reuse without reset remains deferred.
Generated reports and projects are not guaranteed in a fresh checkout.

## Fixture

Component `ParameterExtrusions` contains four separated PEC solids.
All heights use `PEx_H` and active offsets use `PEx_Offset`.
Profiles use `v=0..4`.

| Solid | Tool and direction | Profile u extent | Hole u extent and v extent |
| --- | --- | --- | --- |
| `PointZUp` | Pointlist, z up | `0..W` | `W/4..3W/4` and `1..3` |
| `PointXDown` | Pointlist, x down | `30..30+W` | None |
| `PolygonYUp` | Polygon, y up | `60..60+Side*W` | `60+Side*W/4..60+3Side*W/4` and `1..3` |
| `PolygonZDown` | Polygon, z down | `90..90+W` | None |

| Stage | `PEx_W` | `PEx_H` | `PEx_Offset` | `PEx_Side` |
| --- | --- | --- | --- | --- |
| Initial | 8 | 3 | 2 | 1 |
| Updated and reopened | 10 | 4 | 5 | -1 |
| Final | 6 | 2 | 1 | 1 |

Switching `Side` changes the winding signs and mirrors the polygon profile.
The outline and hole require independent normalization.
The client establishes mm/GHz/ns and creates fixtures once.
It rebuilds after parameter changes, repeats readbacks after save/reopen,
then saves and closes the final state.

## Measurements and limits

Queries read actual parameters, units, identities and materials.
For unperforated profiles, area `A=4W` and boundary length `P=2W+8`.
For holed profiles, `A=3W` and `P=3W+12`, including internal walls.
Expected prism volume is `A*H` and surface area is `2A+P*H`.
Native volume and area comparisons use absolute and relative tolerances of `1e-6`.

The retained live report completed successfully within this fixture coverage.
These readbacks cover aggregate reconstruction, hole effects and persistence.
They do not independently prove offsets, direction, every coordinate,
the selected winding branch or stored expression association.
Native dialogs can display evaluated values rather than editable expressions.

The user confirmed the agreed manual checks separately.
For inspection, compare saved history and base planes with the fixture contract.
Final predicted ranges are z=1..3 for `PointZUp`, x=-1..1 for `PointXDown`,
y=1..3 for `PolygonYUp` and z=-1..1 for `PolygonZDown`, in mm.
The y-axis profile maps to z=-4..0.
Close without saving inspection edits to preserve saved fingerprints.

See the [review](../REVIEW.md#verification) for the live invocation identifier.
