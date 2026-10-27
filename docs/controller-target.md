# Opt-in Controller live target protocol

The paired cst-agentic #24 implementation uses existing tools and unchanged JSON
Schemas. Public MCP v2 `tools/call` request metadata carries `cst_controller`:
`version: 1`, `stage`, at selection/bound stages absolute `.cst` `path`, and (after launch) `connection` and
(after selection) `project` tokens. Metadata is application-owned, not model
arguments or a credential/authentication mechanism. The Controller owns human
task permission; this server supplies destination enforcement and API readback.

Stages are read-only `probe` (`cst_connection_status`), explicit destination-free `launch`
(`cst_connect`, exactly `mode=new`), `connection` (held-instance status before selection),
`select` (create/open exact path) and `bound`
(expected-target operations). The worker validates immediately before invoking
the actual handler; successful responses carry `controller` evidence with ordered
sequence, connection/project tokens and `source=held-project.filename` readback.
Missing filename/instance support, changed objects/connections, mismatched targets,
conflicting directories, ambiguous names and hard-link destinations fail closed.
No-path save explicitly uses the live checked path, not the legacy requested cache.

Start with CST closed; no additional instance or concurrent manual editing is
supported. The official instance-list API is a limited conflict check, not complete
process/service accounting or isolation. New mode never adopts an active/first
project. An already connected request for a new instance is an error, not success.
Project creation no longer retries an existing or failed destination with a
timestamp suffix; a failed save retains its live handle for human inspection,
without implicit close/discard or rollback claims.

No new function/group/schema is advertised. Legacy calls without metadata retain
their existing route (except the explicit new-request and suffix corrections).
The metadata integration is MCP v2; v1/old-server calls do not supply the protocol
probe response, so the paired Controller refuses before launch. The protocol does
not replace central recovery: unresolved incidents block all new Controller work
and replacement transports until explicit local human reconciliation. No recovery
channel, replay, process-close shortcut or cross-restart identity proof is added.
The session owns one
typed `ControllerBinding` containing held objects, connection/project identity,
the supported instance check and readback acquisition order.

`tests/test_controller_target.py` exercises real registry/wrappers with controlled
CST API objects. These are offline tests, not native CST effect/lifetime evidence.

## Separate requests and healthy task boundaries

Launch needs no project path or modeling objective. Its evidence contains a connection
token and `stage=launch`; no project is selected. Send `stage=connection` with that
token to `cst_connection_status`. Unselected evidence contains `project=null`,
`stage=connection` and an increasing sequence. This route cannot perform project work
and never adopts an active/first project. Once a project is held, status also requires
the `bound` route, because the status handler inspects solver/project state.
Selection requires the exact path and
connection token. Successful selection returns the held API object's actual path,
project token and sequence; subsequent `bound` requests reuse those tokens and path.

Healthy operations do not consume or invalidate the binding. The application must
replace task permission at each new task, recheck permission after queueing and send
the expected binding at final dispatch. The server neither maintains exact-call
manifests nor treats successful selection/tool availability as permission to edit,
save, delete results or simulate. Bound save is dispatched only at the checked live
destination. Ordinary continuation must not reopen an uncertain client: central
recovery admission remains application-owned and mandatory.

## CAD and structure-view output destinations

Guarded routes are `cst_export_cad` (`file_path`), `cst_export_project` (`path`),
and `cst_export_structure_views` (`out_dir`, actual `view_<name>.png` files).
No model-visible tool, group, argument or schema was added or removed.

- Require explicit absolute outputs and existing parent directories. No implicit
  work-directory default is accepted for Controller structure exports.
- Canonicalize actual handler arguments. Refuse project-file outputs, mismatched
  CAD format/extensions, linked files/directories, hard links, reserved/ambiguous
  Windows names and directory conflicts. Parent aliases are inspected before use.
- Check every requested image before any export. Named views are perspective,
  front/back/top/bottom/left/right and xy/xz/yz; arbitrary/duplicate names are refused.
- Existing files require application-owned metadata `replace_outputs: [<absolute
  actual file path>, ...]`. These exact per-request paths must be a subset of actual
  outputs. A model argument, boolean flag or directory grant cannot authorize
  replacement. The application supplies this only after checking applicable user
  permission, selected task/project, consequences and canonical arguments.
- Controller view exports do not unlink old files, retry a failed view, fall back
  to model history or continue to subsequent views after failure. Partial results
  and held project handles remain available as evidence; a failed export is not
  permission to replay. File existence does not prove successful native rendering.
  If a post-handler target check fails, the error retains the handler's
  `operation_response` with `execution_state=unknown`; this is not success or rollback.

Unsupported Controller routes remain `cst_workflow_patch_antenna`,
`cst_workflow_run_and_s11`, `cst_workflow_design_report` and
`cst_workflow_simulate_and_report` (nested destinations/effects are not guarded
by this change), connection replacement and
project reselection while a project is held. Unknown view names and implicit view
output directories need explicit supported choices. This guard is not a filesystem
sandbox or protection against concurrent external filesystem edits; that concurrency
is outside the approved operating scope. Native format-specific output behavior
still needs separate target-authorized evidence under #37.

## cst-agentic handoff / remaining acceptance

The inspected companion `working_project.py` still requires `permission.path` in
`metadata()`, moves launch immediately to selection, consumes `_remaining` call
signatures and blocks both CAD/view tools. It must integrate the stages and exact
replacement-path metadata above, separate instance/project/task state, preserve
bindings through healthy task renewal, and enforce user-provenance-backed
permissions (including actual `delete_results=true` semantics) at its real final
protected boundary. Do not derive replacement grants from model output.

Application acceptance must cover conversational launch/selection/clarification,
successive tasks, permission replacement, explicit save, queued target/permission
changes, central recovery refusal and operation-outcome interpretation. Server
offline tests alone do not complete cst-agentic #24. No application code or recovery
policy is changed here; no native acceptance is claimed.
