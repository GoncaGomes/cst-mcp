# Parameter capability assessment

This review describes the final contracts and completed CST 2025 fixture evidence.
Use the [scenario index](README.md) for commands, ownership and report locations.

## Final contracts

| Scenario | Final contract |
| --- | --- |
| [Brick](scenarios/01_brick.md) | Number-or-expression support in all six bounds |
| [Primitives](scenarios/02_primitives.md) | Selected radii, centers and axial ranges, plus Polygon3D coordinates |
| [Extrusions](scenarios/03_extrusions.md) | Height, all profile and hole coordinates, and active-axis offsets |
| [Analytical curve](scenarios/04_analytical_curve.md) | Number-or-expression support in parameter bounds |
| [Face from curves](scenarios/05_face_from_curves.md) | Exactly one qualified closed planar curve reference for CoverCurve |
| [Loft](scenarios/06_loft.md) | Explicit 3D profiles for capped LoftCurves construction |

Python serializes expressions and CST evaluates their meaning.
The builders do not evaluate arbitrary caller expressions in Python.
Names, axes, materials, segment counts and unrelated fields retain their own contracts.
Parameter-setting values are input echoes.
The clients obtain actual parameter values through independent queries.

## Important corrections

### SDK schema attributes

The handler guard reads SDK 1.x `inputSchema` and SDK 2.x `input_schema`.
Both names must reach the same checks.
Otherwise SDK attribute changes can silently bypass declared numeric restrictions.
The correction preserves existing input contracts rather than extending every numeric field.

### Native extrusion evaluation

Documented extrusion origin, coordinate and thickness arguments use typed doubles.
The builders pass symbolic values through native `Evaluate` with safely serialized literals.
Evaluation stays in model history, so rebuilding uses updated parameters.
Python does not freeze the initial values.

The builders preserve the documented axis mapping and active-offset rule.
Pointlist down reverses the plane normal and remaps local coordinates.
Symbolic polygon outlines and holes choose winding independently on every rebuild.
Native signed-area checks reject degenerate symbolic profiles.
Endpoint metadata remains a prediction.

### Qualified CoverCurve input

A curve item is identified by `curvegroup:curveitem`.
The builder validates the two names separately and accepts exactly one qualified reference.
It uses `CoverCurve.Curve` to create a planar sheet with the native Vacuum default.
This resolves the earlier Batch 01 qualified-reference limitation.
It does not add independent profile lists, holes or thickness.

### Native Boolean readback parsing

CST can represent VBA true as `True` or `-1`, and false as `False` or `0`.
The face client accepts those representations with case and whitespace normalization.
It retains raw text and rejects unexpected values.
A native numeric true value is not treated as a failed closure check.

### Raw sheet area

The fixture's analytic one-sided planar area is 24 mm².
Native shape area has no established sheet counting convention in this campaign.
Reports separate expected planar area, raw shape area and the unmeasured automatic planar area.
They compare available raw areas across save/reopen without dividing by two.
User confirmation remains separate from that automatic measurement limit.

### Explicit 3D LoftCurves profiles

The final loft builder uses Polygon3D profiles in separate auxiliary curve groups.
It preserves profile order and explicit z coordinates.
A collision guard prevents adoption of existing auxiliary groups.
LoftCurves with `Solid=True` creates the capped solid.
Legacy 2D points cannot describe profile spacing and are rejected.
This replaces the contract assessed by the failed Batch 01 loft probe.
Profile curves may be consumed by the native operation.

## Verification

The six retained live summaries identify completed scenarios with exit code 0.
This table records the local reports inspected for this assessment.

| Scenario | Live invocation | Automated evidence |
| --- | --- | --- |
| Brick | `ac6192097edc4a1d935c3880b8ab5c3c` | Parameters, units, identity, material, volume and area across rebuild and reopen |
| Primitives | `0659b7cbd45e4e019e8dda5d01d5a467` | Solid volumes, supported smooth areas, curve closure and maximum point count |
| Extrusions | `04e4fcb1bb404bde9d5570ad77ff85d8` | Parameters, identities, volume and area with holes and persistence |
| Analytical curve | `c24aaee2eaa147cc9db486e1a3c4f66b` | Parameters, units, named closure and reported maximum point count |
| Face from curves | `320827ff532b48359219c1244f60c363` | Closed profile, named sheet, type, material, face presence and raw area persistence |
| Loft | `11c00dde94bf4292b4cd6e41a1f2b805` | Parameters, units, solid type, material, volume and area across rebuild and reopen |

The local report locations are `artifacts/<scenario>/summary.json` and `summary.md`.
They are generated evidence, not files guaranteed in a fresh checkout.
Accumulated logs retain invocation-tagged responses.
A later invocation can replace a latest summary.

Offline regressions cover serialization, numeric compatibility, invalid types,
schema attributes, expression containment and builder contracts.
Client substitutes cover ownership, source copying, locks, reset scope,
readback parsing and cessation after uncertain execution.
Real MCP preflight discovers schemas and checks offline generation or rejection.
Those checks remain separate from live readbacks.

The user confirmed the agreed modeling scenarios and manual inspections.
Older pending wording remains in preserved summaries.
It does not reverse that confirmation or create automatic measurements that were not made.

## Remaining limits

- Volume and area do not independently establish every coordinate, orientation or expression association.
- Elliptical-cylinder area has no exact analytic comparison in this campaign.
- Reported maximum point counts are not exact vertex counts.
- Analytical-curve endpoints and length were not measured automatically.
- Raw sheet area does not establish one-sided planar area or its counting convention.
- The loft fixture covers a rectangular prism, not arbitrary section topology.
- Existing-project reuse without reset remains deferred where stated in the scenario guides.
- These fixtures do not establish simulation accuracy or general geometry validity.

Batch 01 remains a preserved assessment with known failures.
The later corrections and successful fixtures do not make the entire earlier batch pass.
See the [API assessment](../../docs/API_REVIEW.md) and
[reliability assessment](../../docs/RELIABILITY_REVIEW.md) for broader context.
