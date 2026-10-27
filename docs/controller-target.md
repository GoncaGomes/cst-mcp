# Opt-in Controller live target protocol

The paired cst-agentic #24 implementation uses existing tools and unchanged JSON
Schemas. Public MCP v2 `tools/call` request metadata carries `cst_controller`:
`version: 1`, `stage`, absolute `.cst` `path`, and (after launch) `connection` and
(after selection) `project` tokens. Metadata is application-owned, not model
arguments or a credential/authentication mechanism. The Controller owns human
task permission; this server supplies destination enforcement and API readback.

Stages are read-only `probe` (`cst_connection_status`), explicit `launch`
(`cst_connect`, exactly `mode=new`), `select` (create/open exact path) and `bound`
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
CAD and structure-view export operations are refused on the Controller route until
their output/overwrite destinations have a reviewed guard. The session owns one
typed `ControllerBinding` containing held objects, connection/project identity,
the supported instance check and readback acquisition order.

`tests/test_controller_target.py` exercises real registry/wrappers with controlled
CST API objects. These are offline tests, not native CST effect/lifetime evidence.
