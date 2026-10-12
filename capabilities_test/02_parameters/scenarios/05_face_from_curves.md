# Scenario 05: face from curves

This scenario checks conversion of one closed planar profile into a sheet.
Read the [common runtime and ownership rules](../README.md) before running it.

## Input contract

`cst_create_face_from_curves` requires exactly one item in `curve_names`.
The item must be a qualified reference such as `Curves:Rectangle`.
It has one colon and two separately validated names.
The curve must be closed and planar.

The builder uses `CoverCurve.Reset`, `Name`, `Component`, `Curve` and `Create`.
It keeps the native default Vacuum material.
CST reports native geometric errors.
Connected items may be included by CST.
Independent profile lists, holes and thickness are outside this contract.

## Commands and workspace

Run from the repository root:

```powershell
uv run capabilities_test\02_parameters\run_face_from_curves.py --preflight
uv run capabilities_test\02_parameters\run_face_from_curves.py
uv run capabilities_test\02_parameters\run_face_from_curves.py --reset
```

Other options are `--cst-path`, `--connection-timeout` and `--call-timeout`.
Preflight and reset cannot be combined.

The local workspace is `capabilities_test/02_parameters/artifacts/05_face_from_curves/`.
It owns `project.cst`, `project/` and recorded sidecars.
The manifest and saved fingerprints govern reuse.
Existing-project reuse without reset remains deferred.
Generated projects and summaries are local report locations.

## Fixture and native checks

The client establishes mm/GHz/ns and creates component `FaceFromCurves`.
Dedicated Polygon3D creation makes `Curves:Rectangle` from these mm coordinates:

```text
(0,0,2)
(6,0,2)
(6,4,2)
(0,4,2)
(0,0,2)
```

The profile is a 6 by 4 mm rectangle at z=2 mm.
`Curve.IsClosed` must report true before conversion.
The dedicated face tool creates `FaceFromCurves:RectangleSheet`.

Readbacks check the named shape and Vacuum association.
`Solid.DoesExist` must report true.
`Solid.IsSolidShape` must report false.
`Solid.GetAnyFaceIdFromSolid` must return a nonnegative face ID.
These checks establish sheet type and face presence.
The source curve may be consumed.
Its survival is not required.

Boolean readbacks accept `True` and `-1`, or `False` and `0`.
Parsing ignores case and surrounding whitespace.
Unexpected values stop the check.
The client retains the original native response text.

## Raw area and persistence

The analytic one-sided planar area is 24 mm².
Reports keep this `expected_planar_area` separate from `native_shape_area`.
The automatic `measured_planar_area` remains null.
The client does not divide raw sheet area by two or normalize it.
The native shape-area counting convention is not established.

After save, close and reopen, the client repeats sheet, face, material and unit queries.
Available positive raw areas before and after reopening must agree
within absolute and relative tolerances of `1e-6`.
Unavailable or unusable raw areas cannot establish that persistence measurement.
Normal completion saves and closes the project.

The retained live report completed successfully within this coverage.
Raw area persistence does not establish one-sided planar area.
Face presence does not independently measure exact face count, width, height or z position.

The user confirmed the agreed manual checks separately.
Preserved summary fields may retain pending planar-area wording.
That confirmation does not supply a missing automatic measurement or an area normalization rule.
Inspect the sheet dimensions, plane, face count and native area convention separately.
Close without saving inspection edits to preserve fingerprints.

See the [review](../REVIEW.md#verification) for the live invocation identifier.
