# Scenario 04: analytical-curve parameter bounds

This scenario checks parameterized bounds on one straight analytical curve.
Read the [common runtime and ownership rules](../README.md) before running it.

## Input contract

`cst_create_analytical_curve` accepts finite numbers or nonempty single-line
CST expressions in `t_min` and `t_max`.
The bounds may mix numbers and expressions.
Python serializes them into the quoted native `ParameterRange` form.
CST evaluates their meaning.
The extension adds no bound ordering or sign restriction.

The coordinate laws `x_expr`, `y_expr` and `z_expr` remain string inputs.
Curve naming and the `Curves` group contract are unchanged.
This bound extension does not make every geometry field expression-capable.

## Commands and workspace

Run from the repository root:

```powershell
uv run capabilities_test\02_parameters\run_parameter_analytical_curve.py --preflight
uv run capabilities_test\02_parameters\run_parameter_analytical_curve.py
uv run capabilities_test\02_parameters\run_parameter_analytical_curve.py --reset
```

Other options are `--cst-path`, `--connection-timeout` and `--call-timeout`.
Preflight and reset cannot be combined.

The local workspace is `capabilities_test/02_parameters/artifacts/04_analytical_curve/`.
It owns `project.cst`, `project/` and recorded sidecars.
The manifest and saved fingerprints govern reuse.
Existing-project reuse without reset remains deferred.
Generated projects and summaries are local report locations.

## Fixture

`Curves:ParametricLine` uses these laws and bounds:

- `x_expr="t"`.
- `y_expr="0"`.
- `z_expr="0"`.
- `t_min="PCurve_Start"`.
- `t_max="PCurve_Start+PCurve_Length"`.

| Stage | Start parameter | Length parameter | Predicted X endpoints in mm | Predicted length in mm |
| --- | --- | --- | --- | --- |
| Initial | 2 | 5 | 2..7 | 5 |
| Updated and reopened | -1 | 10 | -1..9 | 10 |
| Final | 3 | 4 | 3..7 | 4 |

The client establishes and reads back mm/GHz/ns.
It creates the curve once, changes parameters and rebuilds,
then saves, closes and reopens before the final parameter change.
Normal completion saves, closes and disconnects.

## Measurements and limits

Independent list and individual queries verify actual parameter values.
Native queries read units, the named closure flag and reported maximum point count.
`Curve.IsClosed` must report false.
`Curve.GetNumberOfPoints` must return a nonnegative integer maximum.
It is not an exact vertex count.

The retained live report completed successfully.
Its checks cover parameter and unit persistence and repeated named-query readbacks.
The automatic checks do not measure endpoints, length, straightness or complete coordinates.
Endpoint and length predictions remain separate from null automatic measurements.
The client does not invent a length method or complete point-ID enumeration.

The user confirmed the agreed manual checks separately.
Use native measurement controls to compare the final curve with predicted endpoints
`(3,0,0)` and `(7,0,0)` mm and length 4 mm.
Inspect retained history laws and symbolic bounds.
A dialog showing evaluated values alone does not establish symbolic history.
Close without saving inspection edits to preserve fingerprints.

See the [review](../REVIEW.md#verification) for the live invocation identifier.
