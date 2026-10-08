# Review record

Inspected baseline: `main`, commit
`e027735f0f61e666801fe18a6e0f93cfbfff9f69`. Tracked working tree/index matched
HEAD. Batch 01's five files were untracked and were preserved. Batch 02 was
empty. No applicable `AGENTS.md` was found. No branch switch, commit or push
was performed.

Server functions/helpers changed:

- `geometry._build_brick`: uses the new expression-aware range method for
  X/Y/Z instead of the number-only range method. Only this primitive changes.
- `VBABuilder.set_expression_pair` (new): emits two quoted CST values while
  keeping number-only builder methods intact.
- `_format_expression` (new): preserves expression text, retains numeric
  formatting, rejects invalid types/nonfinite/control-character values and
  applies existing string escaping/injection checks.
- `vba_safety.check_arguments`: fallback to SDK 2.x `input_schema`, restoring
  schema-based validation that SDK 2.x previously bypassed. Shared numeric-only
  restrictions are therefore effective under both supported SDK layouts.
- `cst_create_brick` catalog schema/descriptions: all six bounds explicitly
  accept number/string and document persistence and CST-side semantic errors.

The public geometry handler and its history execution path are reused without
modification. No changes to parameters, workflows, other primitives, solver or
optimization behavior were made. The existing workflow `_brick_expr` confirms
that quoted expressions are the established CST-history representation.
The parameter tool already performs storage and rebuild as separate operations.

Review focused on literal containment, expression dependency preservation,
numeric compatibility, SDK schema lookup, fixed-workspace ownership/reset
boundaries, original-source preservation, independent query evidence and
cessation after unknown native execution. No unresolved implementation finding
was identified in those reviewed paths; actual CST compatibility and model
behavior remain unverified until the live test is executed.

Offline checks executed: the full repository pytest suite passed with **615
passed, 7 skipped**; the focused selection passed with **343 passed**; the seven
existing batch-01 response/transport unittest tests passed. Focused Ruff checks,
Python compilation and `git diff --check` passed. The real MCP `--preflight`
passed with `CST_CONNECT_MODE=disabled`. Substitute and offline checks cannot
validate CST geometry/rebuilds. The full suite reported one existing SyntaxWarning
in `tests/test_live_regressions.py` (an invalid escape sequence).
The final preflight artifact audit confirmed two append-preserved invocations,
nine complete request pairs each, no CST connection calls and no project copies.
After the final client-only reporting adjustment, its 27 substitute tests passed again.
Nine Ruff diagnostics in `geometry.py` were reproduced from HEAD and were left
unchanged; no unrelated formatting fixes were applied to server functions.

The potential live initialization blocker observed initially was the source
companion's `project/Model.lok`, a zero-byte lock. It was absent on the final
filesystem recheck; no CST operation was performed to clear it. The original file is
45,283 bytes, compared with 42,505 in its old report. Save/close the source and
investigate any remaining lock before live initialization. The script records
the actual source hash/size and copy-time file provenance; it neither deletes
locks nor assumes they are stale. A baseline rebuild failure may expose inherited
history problems; it is reported and stopped without reconstruction.

Actual source SHA-256 recorded by the final preflight (45,283 bytes):
`1eae47647fcd4723c38d43aecdf5bc9b81254dd1ff684e102faa36459ba2ddf8`.
This is an observation of the current saved source, not copy-time provenance;
the live initializer separately records and verifies copy-time fingerprints.

## Primitive extension and scope relocation, 8 October 2026

The earlier sections describe the original brick implementation and its historical
baseline. This extension was inspected on `feat/brick-parameter-expressions` at
`59b52b7f9673b7932b981f2ee45bf2541b9fbd96`. No applicable `AGENTS.md` was found in
the repository or its ancestors. Existing root README edits, Batch 02 documentation
edits and unrelated presentation/site deletions were present. Unrelated work was
preserved. No branch switch, commit, push or discard was performed.

The cylinder, cone, sphere, elliptical cylinder, torus and Polygon3D schemas and
builders now support the selected expression dimensions. The helpers are
`geometry._expression_field`, `geometry._radius_expression`,
`VBABuilder.set_expression` and `VBABuilder.set_expression_triple`; the existing
expression formatter and pair helper are reused. The formatter additionally
rejects non-line-breaking forbidden controls. Numeric formatting, defaults and
numeric radius sign rules are retained. Number-only builder methods,
`vba_safety.py` and `cst_set_parameter` were not changed by this extension.

Installed CST 2025 Torus VBA help, the torus dialog/creation-mode help and its
diagram were inspected. CST's large/small radii are outer/inner surface extents.
The misleading major/tube-radius descriptions in the geometry catalog and bundled
VBA reference were corrected without converting numeric inputs. The six entries
in the static tool catalog and embedded documentation were updated in place.
Other tool contracts remain unchanged.

The primitive client uses only `artifacts\02_primitives`, starts its own new
DesignEnvironment, creates a blank MWS project and guards subsequent reuse with
ownership, OS/project locks, checkpoint hashes and companion inventories. Its
reset is confined to verified project paths. Imported helpers were inspected for
side effects; no imported client is constructed. Parameter storage remains outside
model history and assignments rebuild only when required. Read-only queries use
native output capture. Unknown execution stops every further MCP call and transport
shutdown is restricted to the server process.

The brick relocation inspected 15 identified top-level entries: `project.cst`,
the 106-file `project` companion, empty `Cache` and `Temp`, MCP/CST/stderr logs,
reports, metadata, catalog, manifest, user notes and retained `workspace.lock`.
All 138 original files/directories were moved into `artifacts\01_brick` through
a collision-checked staging directory and verified by inventories and SHA-256.
Links, junctions, ambiguous entries and collisions were refused. Exclusive access
to every file and an OS lock on the retained workspace lock established inactivity.
There were no CST project locks. No locks were removed or judged stale.

The original manifest bytes are in `workspace.before_relocation.json`; the local
`relocation.json` records progress, both inventories and verification. Only the
operational project path changed in `workspace.json`. Historical logs/reports,
metadata, notes and source-copy evidence remain byte-preserved. The original
saved hash, fixture state and checkpoint ID remain unchanged:

- Saved: `490f5597d9f2dd1083367c21db1ad0a18e949ee9448343426507924eae53b49c`
- Current project: `92fb5dde4d9b11eb4c8c50f052431adf91dbd0195049fcfeb5c6e31563de8065`
- Checkpoint invocation: `4bb6ff44ae794d35910ede3e9bffe822`, phase `final_geometry`

The mismatch still blocks ordinary brick reuse. The existing checkpoint recovery
or explicit reset-from-closed-source procedure is required. Relocation did not
adopt the current project fingerprint. Brick preflight was not rerun against the
real workspace, preserving its existing latest historical reports.

The final semantic audit caught automatic date normalization by the relocation
serializer. The operational manifest was restored from its preserved original
bytes with only the project-path token replaced. The correction is recorded in
`relocation.json`; every original provenance/checkpoint field now compares exactly.

Offline verification for this extension:

- Focused handler/helper, client ownership/isolation, VBA/security and registry
  selection: **453 passed**. Tests used temporary files and substitutes without CST.
- Primitive real MCP `--preflight`: **passed**, CST access disabled. It checked all
  six effective schemas, numeric/mixed generation and invalid inputs. No project
  was created, opened or reset.
  The final audit found three append-preserved preflight invocations, each with
  45 complete request/response pairs. The latest metadata matches the final script.
- Changed/new Python files pass focused Ruff checks. Full-file `geometry.py`
  retains the nine pre-existing Ruff diagnostics, with no new diagnostics.
- Python compilation, numeric-output compatibility and `git diff --check` are
  checked offline. The final artifact audit verifies original brick fingerprints
  and preserved manifest fields, ignored scope files and preflight call isolation.

Volume is checked analytically for all five solids and exact area for four.
Elliptical-cylinder area is recorded without an approximate exact reference.
Curve closure and its documented maximum point count are recorded. Installed help
does not specify complete point-ID enumeration, so coordinate verification is
unsupported. Volume/area do not prove all positions, dimensions or expression
associations. No loose-box or manual-validation suite was added.

Live CST connection, project opening, model rebuild or simulation was performed
by this extension task. 
