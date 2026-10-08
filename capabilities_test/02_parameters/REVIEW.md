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

**No live-mode script execution, CST connection, project opening, model rebuild
or simulation was executed by this implementation task. Live CST validation and manual
dimensions/position/expression inspection remain pending.**
