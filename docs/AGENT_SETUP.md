# Agent setup and acceptance

This guide covers the local MCP stdio server in `GoncaGomes/cst-mcp`.
Start with disabled-mode acceptance before connecting to CST.
Use the [project README](../README.md) for requirements and configuration details.
Use the [Markdown tool reference](TOOLS.md) and discovered schemas for tool contracts.

## Install

The package metadata requires Python 3.10 or later.
Current CI and capability clients use Python 3.12.
Live automation must match the installed CST Python library ABI.
These PowerShell examples use Python 3.12 and CST 2025.

```powershell
git clone https://github.com/GoncaGomes/cst-mcp.git C:\dev\cst-studio-mcp
Set-Location C:\dev\cst-studio-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,figures]"
$env:CST_TOOLSETS = "all"
$env:CST_CONNECT_MODE = "disabled"
.\.venv\Scripts\python.exe -m pytest -q -ra
```

For an existing checkout, skip cloning.
Stop the MCP client before updating its installed environment.
Do not interrupt a running simulation to update.
The offline suite has a known cleanup failure in `tests/test_docs_catalog.py`.
That test still imports the removed documentation generator.
Report the failure separately rather than treating the suite as passing.

## Configure the stdio client

Use an absolute virtual environment Python path with `-m cst_mcp.server`.
This example uses the `mcpServers` convention.
Use the equivalent local stdio fields if your client has another format.

```json
{
  "mcpServers": {
    "cst-studio": {
      "command": "C:/dev/cst-studio-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "cst_mcp.server"],
      "env": {
        "CST_PATH": "C:/Program Files (x86)/CST Studio Suite 2025",
        "CST_VERSION": "2025",
        "CST_WORK_DIR": "C:/CST-Projects",
        "CST_CONNECT_MODE": "disabled",
        "CST_TOOLSETS": "core,geometry,materials,boolean"
      }
    }
  }
}
```

`CST_PATH` selects the installation root.
The code can discover and add its Python library directory.
Omitting `CST_VERSION` uses the discovery default of 2026.
Check the selected path and fix ABI errors before live work.

`manual` is the default and does not connect at startup.
`disabled` prevents connections.
`auto` connects at startup only when explicitly selected.
`core` includes connection, official, project, workflows, simulation, results,
parameters and diagnostics.
Connection tools remain available under any toolset selection.
Connected raw VBA execution requires the opt-in `CST_ALLOW_RAW_VBA=1`.
Its argument filter is not a sandbox.
`CST_QUIET` does not suppress CST dialogs.

## Disabled-mode acceptance

1. Start the configured server with `CST_CONNECT_MODE=disabled`.
2. Perform MCP `initialize` and record the reported server version.
3. Perform `tools/list` and follow pagination to inspect the selected schemas.
4. Call `cst_connection_status` with an empty argument object.
5. Verify `mode=offline`, `project_open=false` and the expected paths and version.
6. Record actual responses and failures.

In disabled mode, `cst_available=false` follows from the configuration.
It does not prove that the installed libraries are absent or incompatible.
These checks establish initialization, tool discovery and disconnected status.
They do not execute model-history VBA, create geometry or validate a solver.
An `offline` response containing VBA is generated code, not a native execution result.

## Installed-help checks

Check installed help separately from disabled-mode acceptance.
These tools do not connect to CST or start a solver.
They need the selected installation's official help files.

Call `cst_search_help` with:

```json
{"query": "cst.results"}
```

Pass a returned `topic` to `cst_read_help`.
Use `offset` and `max_chars` to read long topics.
Record the installation, topic and relevant documented contract.
A text match alone does not verify a signature or runtime behavior.

The [scripts guide](../scripts/README.md#audit_installed_apipy)
describes the separate static API audit and its output.

## Saved-result checks

Use a completed, unpacked project supplied for reading.
Keep its result sidecar directory beside the `.cst` file.
Use a compatible installed `cst.results` library.
Do not read a file that is currently being solved.

Use `manual` mode while remaining disconnected for this independent acceptance step.
Call `cst_list_saved_results` with the absolute `project_path`.
Pass an exact returned `tree_path` and `run_id` to `cst_read_saved_result`.
Set `max_points=0` to read every sample.
The default is a labelled preview of up to 200 samples.
Preserve the raw real and imaginary arrays.

These calls do not require `cst_connect` or start a solver.
The [saved-results script](../scripts/README.md#verify_saved_resultspy)
checks all four complex curves of a completed two-port project.
Its optional gzip comparison requires the documented column layout and sample ordering.
Report which supplied project, paths, runs and samples you actually checked.

## Live connection and owned workspaces

Restart the server with `CST_CONNECT_MODE=manual` for authorized live work.
Call `cst_connect` explicitly.
Its default `mode=any` may adopt an existing session and active project.
Prefer `mode=new` for isolated capability work when initially disconnected:

```json
{"mode": "new"}
```

The response reports the selected process and open projects.
Inspect those details and the solver state before a mutation.
If already connected, `mode=new` preserves the current session.
Disconnect first to request a fresh Design Environment.
Disconnecting does not close CST or projects and does not stop a solver.

Create or open only a workspace owned by the current task.
Read the current capability procedures before creating, reusing or resetting artifacts:

- [Project, geometry and materials](../capabilities_test/01_project_geometry_materials/README.md)
- [Common ownership and reset rules](../capabilities_test/02_parameters/README.md#ownership-and-reset)
- [Brick source and commands](../capabilities_test/02_parameters/scenarios/01_brick.md#commands-and-source-project)
- [Primitives procedure](../capabilities_test/02_parameters/scenarios/02_primitives.md)
- [Extrusion procedure](../capabilities_test/02_parameters/scenarios/03_extrusions.md)
- [Analytical-curve procedure](../capabilities_test/02_parameters/scenarios/04_analytical_curve.md)
- [Face procedure](../capabilities_test/02_parameters/scenarios/05_face_from_curves.md)
- [Loft procedure](../capabilities_test/02_parameters/scenarios/06_loft.md)
- [Boolean workspace and error cases](../capabilities_test/03_booleans/README.md)

Follow each procedure's ownership manifests, locks and reset rules.
Close owned projects normally before reset.
Preserve historical reports and accumulated evidence.
Do not delete unknown paths or alter unrelated projects.
The [modeler utility](../scripts/README.md#verify_live_modelerpy)
can attach to or launch CST and requires zero existing projects.
It provides a limited fixture check without starting a solver.

## Responses and simulation waits

Inspect both MCP `isError` and the tool payload.
Treat `offline`, `busy`, `error` and `timeout` as unsuccessful execution outcomes.
A generated macro or accepted command does not prove a measured modeling effect.

Do not automatically replay a failed or timed-out mutation through another entry point.
A timeout, transport loss or interrupted request can leave execution uncertain.
For capability work, stop subsequent MCP calls when the procedure marks that state unknown.
Inspect actual CST state manually and resolve ownership and execution state before resuming.

For an authorized solve, use `cst_run_simulation_async`.
Call `cst_wait_for_simulation` repeatedly while it reports `running` or `starting`.
Continue until it reports `finished` or an unsuccessful outcome.
The wait defaults to `max_wait_s=45` and caps it at 55 seconds.
Each call returns within about `max_wait_s + 2` seconds, subject to native API behavior.
A wait budget expiring while a solve runs does not require starting the solve again.

Native optimizer tools configure parameters and goals.
Starting an optimizer requires an explicit start action.
Separate configuration acceptance from actual solving and result validation.

## Evidence and reporting

The user confirmed the agreed modeling scenarios and manual checks.
Some preserved summaries retain older pending manual wording.
Record user confirmation separately from automated native measurements.
Do not rewrite preserved evidence to make these categories agree.

Batch 01 retains known failures that motivated later face and loft corrections.
Parameter scenarios have completed live reports with fixture-specific readback limits.
The latest boolean operations summary is preflight.
The documented earlier live operations invocation completed successfully.
The errors stage has a completed live report with nine passing cases.
See [validation status](../README.md#validation-status) for the current overview.

The [API assessment](API_REVIEW.md) and
[reliability assessment](RELIABILITY_REVIEW.md) describe current contracts and safeguards.
The parameter and boolean technical reviews record the completed campaign evidence.
These assessments do not validate every tool or simulation accuracy.
Volume and area checks do not independently measure every coordinate or history association.

## Prompt to give an agent

> Read this checkout's README.md, docs/AGENT_SETUP.md, docs/TOOLS.md and the relevant
> capabilities_test procedures. Use the local Python 3.12 virtual environment.
> Check the installed CST library ABI before any native work.
> Start acceptance with CST_CONNECT_MODE=disabled.
> Perform MCP initialization, tool discovery and connection status.
> Run the offline tests and report the known documentation catalog failure separately.
> Check installed official help as a separate step.
> If I supplied a completed project, list its actual result paths and run IDs.
> Read its complex results without connecting to CST or starting a solver.
> Report the version, actual checks, failures, evidence sources and limitations.
> Keep user confirmation separate from native readbacks and use the current capability assessments.
> For later authorized live work, use manual mode and an explicit connection.
> Prefer mode=new from a disconnected session and follow the owned workspace rules.
> Do not automatically replay uncertain mutations or claim every tool is validated.
