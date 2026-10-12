# Boolean capability assessment

This review covers the final operations and errors clients and their CST 2025 evidence.
Use the [procedure guide](README.md) for commands and scoped workspace rules.

## Implemented contract

Add, Subtract, Intersect and Insert take two string solid references.
The handler validates the references and issues the corresponding native `Solid` command.
Fixture calls use qualified `Component:Solid` names.
The validator's optional-colon behavior is not used as a guaranteed invalid-input test.

Native Insert removes B's overlap from A while retaining B.
A retains its material.
This assessed behavior is more precise than the catalog's embedded-material description.

## Operations fixture and persistence

Four separated A/B pairs use offsets 0, 20, 40 and 60 mm.
A spans x=O..O+4.
B spans x=O+`PBool_Shift`..O+`PBool_Shift`+4.
Both span y/z=0..2.
All operands are PEC except Insert A, which is Vacuum.
A distant PEC witness provides an unchanged reference.

| Operation | A volume and area at shift 2 | A volume and area at shift 3 | B after operation |
| --- | --- | --- | --- |
| Add | 24 mm³ and 56 mm² | 28 mm³ and 64 mm² | Absent |
| Subtract | 8 mm³ and 24 mm² | 12 mm³ and 32 mm² | Absent |
| Intersect | 8 mm³ and 24 mm² | 4 mm³ and 16 mm² | Absent |
| Insert | 8 mm³ and 24 mm² | 12 mm³ and 32 mm² | Retained, PEC, 16 mm³ and 40 mm² |

The client reads inventories, material associations, body types, volume, area and selected interior points.
It checks the witness before and after native operations.
Changing only `PBool_Shift` from 2 to 3 triggers native history reconstruction.
The client does not replay fixture creation or boolean calls for that update.
It saves, closes, fingerprints and reopens, then repeats readbacks.
Absolute and relative numeric tolerances are `1e-6`.

## Error cases

Each case owns a fresh blank project with A, B and the distant witness.
The client verifies mm/GHz/ns, empty parameters and the full fixture inventory.
It saves a baseline, submits exactly one invalid dedicated-tool request,
then repeats integrity readbacks when execution remains known and idle.

| Cases | Invalid request | Expected rejection layer |
| --- | --- | --- |
| 01 Missing argument | Omitted `solid2` | MCP schema |
| 02 Wrong type | Integer `solid1` | MCP schema |
| 03 Empty reference | Empty `solid1` | Server argument |
| 04 Forbidden character | Newline in a reference | Server argument |
| 05 Missing first solid | Add with nonexistent A | Native |
| 06 through 09 Missing second solid | Add, Subtract, Intersect or Insert with nonexistent B | Native |

Only the two declared schema cases bypass local input validation.
Other requests retain schema checks.
The client records expected and observed rejection layers separately.
A useful rejection, unchanged fixture integrity and confirmed normal closure are required for a live pass.
The errors stage does not add a save/reopen persistence regression.

Known case failures can continue only after safe normal closure.
Timeout, transport loss, interruption, unresolved windows or ambiguous native execution stop the stage.
The stop includes diagnostics, save, close, disconnect and the next case.
Only local reports and Python-server transport teardown continue.

## Messages-window classification

The server inventory can contain nonmodal CST tool windows.
For expected-error responses, the client recognizes only the observed empty Messages pattern:
title `Messages`, a Qt class ending in `QWindowToolSaveBits`, process match `cst_process`,
empty child texts and full text, and no truthy modal or blocking flag.

That window is recorded as informational and is never dismissed.
The client still checks execution state, ownership and solver idleness before integrity readbacks.
Other reported windows and malformed inventories remain unresolved and stop the invocation.
The correction does not establish universal modal detection or license-dialog recovery.

## Verification

| Evidence | Invocation | Recorded outcome |
| --- | --- | --- |
| Reviewed live operations | `54fa1e64b7f14d8bb9c0d4095a4593cd` | 617 passing checks with rebuilding and persistence. User manually confirmed success |
| Latest operations preflight | `b07debb60edb4a7db0d6c7839d4fab50` | Successful offline preflight. No native operations scenario completed in this invocation |
| Completed live errors | `6e5830985528449094d6860c39018633` | All nine cases passed with fixture integrity and normal closure confirmed |

The live operations invocation created a fresh project with `reset=false`.
It is separate from the latest operations summary, which is preflight.
The errors summary records a completed live stage and exit code 0.

Local report locations are `artifacts/01_operations/summary.json` and
`artifacts/02_errors/summary.json`, with per-case summaries under the errors directory.
These generated files are not guaranteed in a fresh checkout.
Invocation-tagged logs preserve detailed responses.
Preserved evidence is not rewritten to remove older pending wording.

Offline preflight checks effective schemas, server rejection for cases 01 through 04,
offline native-command generation for cases 05 through 09 and fixed fixture blocks.
It does not establish live fixture integrity.
The user confirmed the agreed manual modeling and inspection checks separately.

## Remaining limits

Scalar geometry measurements and selected points do not certify arbitrary topology.
Unchanged fixture geometry does not prove that no history entry was written.
Messages may be inherited, repeated or truncated and do not prove command causality.
Existing-project reuse without reset remains deferred for both stages.
The errors stage does not test reopening.
These fixtures do not establish simulation accuracy.

See the [API assessment](../../docs/API_REVIEW.md) and
[reliability assessment](../../docs/RELIABILITY_REVIEW.md) for repository-wide context.
