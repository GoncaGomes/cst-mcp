# API and capability assessment

This assessment describes the maintained server and the current CST 2025 capability campaign.
It separates implemented contracts, offline checks, native readbacks and manual inspection.
The repository README retains upstream implementation attribution.

## Current contracts and evidence

| Capability or tool group | Implemented input contract | Current validation evidence | Remaining limits |
| --- | --- | --- | --- |
| Project lifecycle | Explicit connection, project creation or opening, save, close and detach | Batch 01 and later fixtures exercise owned projects, units and save/reopen readbacks | Batch 01 contains known failures. Detach does not close CST or projects |
| Materials | Dedicated material creation and assignment tools take named materials and their declared properties | Batch 01 reads back solid material associations, including reassignment | Parameter echoes and material names do not measure electromagnetic properties. Advanced material models lie outside the completed campaign |
| Brick | All six bounds accept finite numbers or nonempty single-line CST expressions | Completed brick scenario reads parameters, units, identity, material, volume and area across rebuild and save/reopen | Aggregate quantities do not establish every bound or expression association |
| Selected primitives and Polygon3D | Selected radii, dimensions, centers, axial ranges and point coordinates accept numbers or CST expressions | Completed primitives scenario compares solid volumes and supported smooth surface areas, then repeats after rebuild and reopen | Elliptical-cylinder area has no exact analytic comparison. Curve maximum point count is not an exact vertex count |
| Extrusions | Height, every profile and hole coordinate, and only the active-axis offset accept numbers or expressions | Completed fixture covers both extrusion tools, holes, parameter reconstruction and volume/area persistence | Offsets, direction and winding branches are not independently measured by aggregate readbacks |
| Analytical curve | Coordinate laws remain string inputs. Only parameter bounds gain number-or-expression support | Completed live report reads parameters, units, named closure and reported maximum point count across rebuild and reopen | Automatic checks do not measure endpoints, length, straightness or complete coordinates |
| Face from curves | Exactly one qualified closed planar reference in `curve_names`, passed to CoverCurve | Completed live report confirms the named sheet, Vacuum material, sheet type, face presence and raw shape-area persistence | Raw sheet area is not one-sided planar area. Its counting convention remains unestablished |
| Loft | At least two profiles with at least three explicit `[x, y, z]` vertices each. Coordinates accept numbers or expressions. LoftCurves creates a capped solid | Completed rectangular fixture checks parameters, solid type, material, volume and area after rebuilding and reopening | The fixture does not cover arbitrary loft topology, every coordinate or orientation |
| Boolean operations | Add, Subtract, Intersect and Insert take two validated string solid references | Reviewed live operations run completed 617 passing checks. The errors run passed all nine cases with integrity and closure | The latest operations summary is preflight. Selected scalar and point checks do not certify arbitrary topology |
| Installed help and saved results | Local help search/read and completed-file result paths, run IDs and sample selection | Implemented contracts and offline regressions | The current modeling campaign does not provide a new saved-result comparison study |
| Other exposed groups | Dedicated schemas cover transforms, ports, boundaries, mesh, solvers, simulation, import/export, optimization, workflows, antenna helpers and plotting | Implementation and relevant offline tests | These groups fall outside the completed capability campaign unless a specific fixture documents them |

The [Markdown tool reference](TOOLS.md) summarizes the exposed groups.
MCP tool discovery supplies the active schemas.
Catalog presence alone does not establish native execution.

## Expression scope

Python serializes supported values into quoted VBA literals.
CST evaluates parameter references, arithmetic and native geometric validity.
Python does not evaluate caller expressions or implement a general CST expression parser.
The shared formatter rejects empty expressions, invalid types, nonfinite numbers and forbidden controls.
Numeric and expression values may be mixed in one supported call.

Expression support applies only to these selected fields:

- Brick: `x_min`, `x_max`, `y_min`, `y_max`, `z_min` and `z_max`.
- Cylinder: `outer_radius`, `inner_radius`, `center_x`, `center_y`, `center_z`, `range_min` and `range_max`.
- Cone: `bottom_radius`, `top_radius`, centers and axial ranges.
- Sphere: `radius` and centers.
- Elliptical cylinder: `x_radius`, `y_radius`, centers and axial ranges.
- Torus: `outer_radius`, `inner_radius` and centers.
- Polygon3D: every coordinate in its explicit `[x, y, z]` points.
- Both extrusion tools: `height`, coordinates in `points` and `holes`, and the selected axis offset.
- Analytical curve: `t_min` and `t_max`.
- Loft: every coordinate in its explicit 3D profiles.

Names, materials, axes, segment counts, wires and unrelated numeric fields retain their own contracts.
Torus radii are CST's axis-to-outer-surface and axis-to-inner-surface radii.
They are not the major radius and tube radius.

Extrusion builders use native `Evaluate` for symbolic values passed to typed double arguments.
They retain evaluation and winding decisions in model history for rebuilding.
Inactive offsets must be absent or numeric zero.
An inactive expression, including `"0"`, is rejected.
Extrusion endpoint metadata predicts the input contract and does not measure native bounds.

Face conversion validates the group and item on either side of the single colon.
It creates a planar sheet through `CoverCurve.Curve` and keeps the native Vacuum default.
Independent profile lists, holes and thickness are outside this contract.

Loft uses separate auxiliary curve groups for explicit 3D Polygon3D profiles.
It preserves profile order, closes profiles once and rejects existing auxiliary-group collisions.
Legacy 2D points receive no inferred spacing.
CST may consume the profile curves during the capped LoftCurves operation.

`cst_set_parameter` stores values outside model history and can request a separate rebuild.
Its returned value echoes the input.
Independent parameter queries and native measurements provide the scenario evidence.

## Current campaign outcomes

Batch 01 contains the qualified face limitation and failed loft probe that motivated later corrections.
Its preserved completed assessment has exit code 1.
The later six parameter scenarios completed live within their recorded fixtures.

The reviewed boolean operations invocation is `54fa1e64b7f14d8bb9c0d4095a4593cd`.
Its live evidence is separate from the latest successful preflight summary.
The live errors invocation is `6e5830985528449094d6860c39018633`.
All nine cases passed with fixture integrity and normal closure confirmed.

The user confirmed the agreed modeling scenarios and manual checks.
Some preserved summaries retain pending manual wording.
That confirmation remains separate from the properties measured automatically.
It does not turn a null endpoint or planar-area measurement into an automated result.

These fixtures do not establish simulation accuracy, convergence or general geometry validity.
Other operations need their own version-specific acceptance evidence.

## Procedures and technical reviews

- [Project, geometry and materials](../capabilities_test/01_project_geometry_materials/README.md)
- [Parameter scenario index](../capabilities_test/02_parameters/README.md)
- [Parameter technical assessment](../capabilities_test/02_parameters/REVIEW.md)
- [Boolean procedures](../capabilities_test/03_booleans/README.md)
- [Boolean technical assessment](../capabilities_test/03_booleans/REVIEW.md)
- [Reliability assessment](RELIABILITY_REVIEW.md)
- [Agent setup and acceptance](AGENT_SETUP.md)
