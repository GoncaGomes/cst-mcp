# Scenario 02: parameter-driven primitives

This scenario checks five PEC solids and one Polygon3D curve.
Read the [common runtime and ownership rules](../README.md) before running it.

## Input contract

The following fields accept finite numbers or nonempty single-line CST expressions:

| Tool | Supported fields |
| --- | --- |
| `cst_create_cylinder` | `outer_radius`, `inner_radius`, `center_x`, `center_y`, `center_z`, `range_min`, `range_max` |
| `cst_create_cone` | `bottom_radius`, `top_radius`, `center_x`, `center_y`, `center_z`, `range_min`, `range_max` |
| `cst_create_sphere` | `radius`, `center_x`, `center_y`, `center_z` |
| `cst_create_ecylinder` | `x_radius`, `y_radius`, `center_x`, `center_y`, `center_z`, `range_min`, `range_max` |
| `cst_create_torus` | `outer_radius`, `inner_radius`, `center_x`, `center_y`, `center_z` |
| `cst_create_polygon3d` | Every coordinate in explicit `[x, y, z]` points |

Python serializes values and CST evaluates expressions.
Numeric radius restrictions still apply.
Names, materials, axes and segment counts retain their existing contracts.
Torus inputs are the axis-to-outer-surface and axis-to-inner-surface radii.
They are not the major radius and tube radius.

## Commands and workspace

Run from the repository root:

```powershell
uv run capabilities_test\02_parameters\run_parameter_primitives.py --preflight
uv run capabilities_test\02_parameters\run_parameter_primitives.py
uv run capabilities_test\02_parameters\run_parameter_primitives.py --reset
```

Use `--cst-path` for another installation.
Client timeout options are `--connection-timeout` and `--call-timeout`.
Preflight and reset cannot be combined.

The local workspace is `capabilities_test/02_parameters/artifacts/02_primitives/`.
It owns `project.cst`, `project/` and recorded sidecars.
`workspace.json` and the OS lock govern reuse and reset.
Generated projects and reports are local evidence, not fresh-checkout assets.

## Fixture

Component `ParameterPrimitives` contains `Cylinder`, `Cone`, `Sphere`, `ECylinder` and `Torus`.
Their X centers are `Shift`, `30+Shift`, `60+Shift`, `90+Shift` and `120+Shift`.
The sphere's Z center is `R`.
Other centers use numeric zeros and axial ranges use `0..H`.

- Cylinder radii are `R` and `R/4`.
- Cone radii are `R` and `R/2`.
- Sphere radius is `R`.
- Elliptical-cylinder radii are `R` and `R/2`.
- Torus surface radii are `3R` and `2R`.
- `Curves:ParametricRectangle` starts at `x=150+Shift`, `y=20`, `z=0`.
  Its width is `2R` and its height is `H`.
  Repeating the first point closes the profile.

| Stage | `PGeom_R` | `PGeom_H` | `PGeom_Shift` |
| --- | --- | --- | --- |
| Initial | 2 | 6 | 0 |
| Updated and reopened | 3 | 8 | 2 |
| Final | 2.5 | 5 | 1 |

The client establishes and reads back mm/GHz/ns.
It creates the fixtures once and rebuilds after grouped parameter changes.
It measures initial, updated, reopened and final states.
Normal completion saves, closes and disconnects.

## Measurements and limits

Independent queries read parameters, units, named solids and PEC associations.
Native volume is compared with analytic values for every solid.
Native smooth surface area is compared for the hollow cylinder, cone, sphere and torus.
Elliptical-cylinder area is recorded without an exact analytic comparison.
The campaign does not substitute an approximate ellipse perimeter for an exact reference.

For torus expectations, the major radius is `(outer+inner)/2`
and the tube radius is `(outer-inner)/2`.
The tool does not convert its inputs into those radii.
Numeric comparisons use absolute and relative tolerances of `1e-6`.

`Curve.IsClosed` verifies closure.
`Curve.GetNumberOfPoints` reports a maximum and is checked as an integer of at least four.
It does not establish an exact vertex count.
The client does not invent complete point-ID enumeration or coordinate readbacks.

The retained live report completed successfully within this fixture coverage.
The user confirmed the agreed manual checks separately.
Volume and area do not establish every position, dimension or expression association.
See the [review](../REVIEW.md#verification) for the live invocation identifier.
