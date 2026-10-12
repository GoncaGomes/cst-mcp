# Scenario 01: parameter-driven brick

This scenario adds one parameterized PEC brick to an owned copy of a saved source project.
Read the [common runtime and ownership rules](../README.md) before running it.

## Input contract

`cst_create_brick` accepts finite numbers or nonempty single-line CST expressions
in all six bounds: `x_min`, `x_max`, `y_min`, `y_max`, `z_min` and `z_max`.
Numbers and expressions may be mixed.
Python serializes the values and CST evaluates their meaning.
The tool creates a solid and does not retroactively edit existing geometry.

The fixture uses:

```json
{
  "component": "ParameterTest",
  "name": "ParametricBrick",
  "material": "PEC",
  "x_min": 200,
  "x_max": "200+PBrick_L",
  "y_min": 40,
  "y_max": 46,
  "z_min": 0,
  "z_max": "PBrick_H/2"
}
```

## Commands and source project

Run from the repository root:

```powershell
uv run capabilities_test\02_parameters\run_parameter_brick.py --preflight
uv run capabilities_test\02_parameters\run_parameter_brick.py --source-project "C:\CST-Projects\source.cst"
uv run capabilities_test\02_parameters\run_parameter_brick.py --pause-for-inspection --source-project "C:\CST-Projects\source.cst"
uv run capabilities_test\02_parameters\run_parameter_brick.py --reset --source-project "C:\CST-Projects\source.cst"
```

Replace the example source path with your actual source project.
The client default points to a locally generated Batch 01 project.
A fresh checkout does not contain that default project.
Save and close the selected source before the initial copy or reset.
Keep its complete same-stem companion directory and sidecars with the `.cst` file.
The source must use mm/GHz/ns and support the baseline history rebuild.

`--source-project` selects the source for first initialization.
It does not replace an existing owned copy.
Use `--reset` to deliberately recreate an existing copy from another source.
Preflight does not copy or open the source and can run while the source is locked.
Other supported options are `--cst-path`, `--connection-timeout` and `--call-timeout`.

## Workspace and copying

The local workspace is `capabilities_test/02_parameters/artifacts/01_brick/`.
It owns `project.cst`, its `project/` companion and recorded same-stem sidecars.
These generated paths are not shipped project assets.

The client checks source and target locks and exclusive file access.
It refuses links and junctions.
It copies the project data and records provenance and fingerprints.
It leaves the source and its reports untouched.
Subsequent invocations reuse the owned copy rather than silently refreshing it.

`workspace.json` records the copy, fixture and last saved checkpoint.
Reset removes only verified owned project paths and preserves evidence and manual notes.
The local `manual_inspection.md` record is created once and is not overwritten.

## Fixture and stages

The client opens only its owned copy in a fresh CST Design Environment.
It verifies inherited mm/GHz/ns units without changing them.
It sets `PBrick_L` and `PBrick_H`, then performs the baseline rebuild.
It does not repair inherited history or remove unrelated source geometry.

| Stage | Length parameter | Height parameter | Predicted dimensions in mm | Expected volume in mm³ | Expected area in mm² |
| --- | --- | --- | --- | --- | --- |
| Initial | 10 | 4 | 10 x 6 x 2 | 120 | 184 |
| Updated and reopened | 14 | 6 | 14 x 6 x 3 | 252 | 288 |
| Final | 12 | 6 | 12 x 6 x 3 | 216 | 252 |

Creation uses the dedicated brick tool once.
Parameter changes use dedicated parameter tools and native rebuilding.
The client saves, closes and reopens before the final parameter change.
Normal completion saves and closes the owned project, then disconnects.

## Measurements and limits

Independent queries read actual parameters, units, the named solid and its material.
`Solid.GetVolume` and `Solid.GetArea` are compared with analytic cuboid values.
Absolute and relative tolerances are `1e-6`.
Missing, duplicate, nonfinite or incomplete readbacks fail the check.
Parameter-setting responses are input echoes, not measurements.

The retained live report completed successfully within this fixture coverage.
The user confirmed the agreed manual checks separately.
Volume and area do not establish every dimension, position or history-expression association.
Loose bounding boxes are not used as exact dimensional evidence.
`--pause-for-inspection` pauses at initial, updated, reopened and final stages.
Resuming a pause does not automatically mark an inspection as passed.
Record observations without saving inspection edits to the owned project.

See the [review](../REVIEW.md#verification) for the live invocation identifier.
