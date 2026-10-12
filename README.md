# cst-mcp

This repository provides an MCP stdio server for local CST Studio Suite automation.
MCP clients can manage projects, build geometry, configure simulations and read results.
The server uses the installed CST Python API and model-history VBA.

| Name | Meaning |
| --- | --- |
| `cst-mcp` | Repository name |
| `cst-studio-mcp` | Python package and executable |
| `cst_mcp` | Python module |

This project derives from [ismailakdag/cst-studio-mcp](https://github.com/ismailakdag/cst-studio-mcp).
Gonçalo Gomes maintains this version at [GoncaGomes/cst-mcp](https://github.com/GoncaGomes/cst-mcp).
The upstream implementation and contributions retain their attribution.
The project metadata declares the MIT license.

## Requirements

- Use 64-bit Windows for live CST automation.
- Install CST Studio Suite and a license for the operations you need.
- Use a Python interpreter compatible with the installed CST Python library ABI.
- Select a writable directory for projects and result files.
- Use an MCP client that supports local stdio servers.

The package metadata declares Python 3.10 or later.
Current CI and capability clients use Python 3.12.
The examples below use Python 3.12.
Live automation must match the installed CST Python library ABI.
Installation discovery alone does not prove import compatibility.

Offline tests run without CST on Windows and Ubuntu.
Installed-help checks need local CST help files.
Saved-result checks need the compatible `cst.results` library and completed project data.

## Installation

Run these commands in PowerShell.
The example checkout path matches this repository's local workspace.

```powershell
git clone https://github.com/GoncaGomes/cst-mcp.git C:\dev\cst-studio-mcp
Set-Location C:\dev\cst-studio-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,figures]"
```

For an existing checkout, start at `Set-Location` and skip cloning.
The `dev` and `figures` extras cover the current offline test dependencies.
They include pytest, Ruff, NumPy and Matplotlib.
The runtime dependencies include MCP SDK `>=1.29,<3`, AnyIO and jsonschema.

Use the virtual environment interpreter explicitly.
You do not need to activate the environment.
The installed executable is `.\.venv\Scripts\cst-studio-mcp.exe`.
The equivalent module entry point is `-m cst_mcp.server`.

## Configuration

These examples select CST 2025.
Change `CST_PATH` to your actual installation root.
Configuration discovery defaults to version 2026 when `CST_VERSION` is unset.
Set the version explicitly when selecting CST 2025.

```powershell
$env:CST_PATH = "C:\Program Files (x86)\CST Studio Suite 2025"
$env:CST_VERSION = "2025"
$env:CST_WORK_DIR = "C:\CST-Projects"
$env:CST_CONNECT_MODE = "manual"
$env:CST_TOOLSETS = "core"
```

`CST_PATH` selects the installation root, not the Python library directory.
The code can discover and add the installation's Python library path.
A typical CST 2025 path is:

```text
C:\Program Files (x86)\CST Studio Suite 2025\AMD64\python_cst_libraries
```

If `CST_PATH` is absent, the code searches common installation locations.
It searches the requested year first, then nearby years.
Inspect connection status to see the selected installation and library path.
Resolve DLL or ABI import errors before live work.

| Variable | Behavior |
| --- | --- |
| `CST_CONNECT_MODE` | `manual` by default, with `disabled` and `auto` as alternatives |
| `CST_PATH` | Selects the CST installation root |
| `CST_VERSION` | Selects the discovery year, default `2026` |
| `CST_WORK_DIR` | Sets the work directory, default `cst_projects` under the user home |
| `CST_TOOLSETS` | Selects comma-separated tool categories or aliases |
| `CST_ALLOW_RAW_VBA` | Enables connected raw VBA execution when set to `1` |
| `CST_LOG_LEVEL` | Sets the log level, default `INFO` |
| `CST_QUIET` | Defaults to `1` and does not suppress CST dialogs |

`manual` does not connect at startup.
Call `cst_connect` explicitly for live work.
`disabled` prevents connections and suits offline acceptance checks.
`auto` connects at startup and requires an explicit configuration choice.
Restart the server after changing its environment.

`CST_TOOLSETS=core` includes connection, official, project, workflows, simulation,
results, parameters and diagnostics.
For dedicated modeling tools, add categories such as `geometry`, `materials` and `boolean`.
Connection tools remain available with any selection.
Omitting `CST_TOOLSETS` exposes all categories.
Use tool discovery to inspect the active schemas.

Raw VBA execution is opt-in through `CST_ALLOW_RAW_VBA=1`.
The argument checks are a best-effort filter, not a sandbox.
Enable it only for trusted clients and a defined task.

## MCP client setup

Use the client's local stdio configuration.
Clients may use a different configuration format from this `mcpServers` example.
Keep the interpreter path absolute.

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
        "CST_CONNECT_MODE": "manual",
        "CST_TOOLSETS": "core,geometry,materials,boolean"
      }
    }
  }
}
```

For initial offline acceptance, change the connection mode to `disabled`.
Initialize the MCP session, discover tools and call `cst_connection_status`.
Expect an offline session with no project open.
See the [agent setup guide](docs/AGENT_SETUP.md) for the acceptance sequence.

## Short usage example

After offline acceptance, restart the server in `manual` mode.
For isolated work from a disconnected session, call `cst_connect` with:

```json
{"mode": "new"}
```

The default `mode=any` may adopt an existing CST session and its active project.
Check the returned process and open project details before changing a model.
Calling `mode=new` while already connected preserves the current session.
Disconnect first if you need a fresh Design Environment.

For a new owned scratch project, call `cst_create_project` with:

```json
{"path": "C:/CST-Projects/example.cst"}
```

Set and verify project units before modeling.
Use `cst_set_parameter` to define `length=10`.
Then call `cst_create_brick` with:

```json
{
  "component": "Example",
  "name": "Brick",
  "material": "PEC",
  "x_min": 0,
  "x_max": "length",
  "y_min": 0,
  "y_max": 4,
  "z_min": 0,
  "z_max": 2
}
```

Read back the parameter and inspect the created solid.
Save and close the owned project when finished.
`cst_disconnect` releases MCP handles without closing CST or its projects.
It does not stop a running solver.

## Parameter-driven modeling

Selected geometry inputs accept finite numbers or single-line CST expressions.
Python serializes these expressions and CST evaluates their meaning.
The supported inputs include brick bounds, selected primitive radii and centers,
axial ranges and Polygon3D coordinates.
Extrusions support height, profile and hole coordinates, and the active axis offset.
Analytical curves support expressions in `t_min` and `t_max`.

`cst_create_face_from_curves` takes exactly one qualified closed planar reference,
such as `Curves:Rectangle`, in `curve_names`.
It uses CoverCurve to create a planar sheet with the native default Vacuum material.
Independent profiles, holes and thickness are outside that contract.

`cst_create_loft` uses LoftCurves to create a capped solid.
Supply at least two profiles with at least three explicit `[x, y, z]` vertices each.
Coordinates may mix numbers and supported expressions.
Legacy `[x, y]` points have no inferred spacing and are rejected.

See [parameter-driven geometry](capabilities_test/02_parameters/README.md)
for exact field coverage, fixture contracts, ownership rules and measurement limits.

## Validation status

The user confirmed the agreed modeling scenarios and manual checks.
That confirmation is separate from automated measurements.
Some preserved summaries retain older pending manual wording.
Their preserved wording does not override the user's confirmation.
Neither source establishes validation of every tool or arbitrary geometry.

- [Project, geometry and materials](capabilities_test/01_project_geometry_materials/README.md):
  Batch 01 assessed creation, assignments, units and save/reopen behavior.
  Its preserved live assessment contains known failures and an exit code of 1.
  The qualified face limitation and failed loft probe motivated later corrections.
  Do not treat the batch as uniformly successful.
- [Parameter-driven geometry](capabilities_test/02_parameters/README.md):
  Completed live reports cover bricks, primitives, extrusions, analytical curves,
  face conversion and loft within their specific fixtures.
  Readbacks cover parameters, units, identities, materials and supported native measurements.
  Volume and surface area checks cover reconstruction and persistence where implemented.
  Analytical-curve checks do not measure complete coordinates, endpoints or length.
  Face checks cover sheet type, face presence and raw shape-area persistence.
  Raw sheet area does not establish one-sided planar area or its counting convention.
- [Boolean operations and error handling](capabilities_test/03_booleans/README.md):
  The latest operations summary is a successful disabled-server preflight report.
  The documented earlier live invocation completed with 617 passing checks.
  It checked the selected operations, witness integrity, rebuilding and persistence.
  The errors stage has a completed live report with nine passing cases.
  Those cases check rejection responses and unchanged fixture integrity.

Offline tests check Python contracts, numerical behavior and MCP transport using substitutes.
Native readbacks measure selected properties in the fixture projects.
Manual inspection covers the agreed visible geometry and modeling checks.
Aggregate measurements do not prove every coordinate or stored expression association.
These capability checks do not establish simulation accuracy or general geometry validity.

The [API assessment](docs/API_REVIEW.md) and
[reliability assessment](docs/RELIABILITY_REVIEW.md) describe current contracts,
execution safeguards and evidence limits.
They link to the capability procedures and technical reviews.

## Development and tests

From the installed checkout, run offline tests through the Python 3.12 environment:

```powershell
$env:CST_TOOLSETS = "all"
$env:CST_CONNECT_MODE = "disabled"
.\.venv\Scripts\python.exe -m pytest -q -ra
```

There is a known cleanup issue in `tests/test_docs_catalog.py`.
It still imports a removed documentation generator and fails until updated separately.
Keep that failure visible when reporting test results.

CI runs Python 3.12 offline tests on Ubuntu and Windows.
Its matrix installs MCP SDK 1.29.0 and 2.2.0 with `.[dev,figures]`.
Formatting, lint and documentation generation are not CI steps.
For local lint inspection, run:

```powershell
.\.venv\Scripts\python.exe -m ruff check src tests
```

The [scripts guide](scripts/README.md) covers the three remaining audit utilities.
Installed-help and saved-result checks require local files and libraries.
The modeler utility performs live work and does not start a solver.
Read the capability procedures before running any live scenario or reset.

## Further documentation

- [Agent setup and acceptance](docs/AGENT_SETUP.md)
- [Markdown tool reference](docs/TOOLS.md)
- [Documentation index](docs/README.md)
- [Utility scripts](scripts/README.md)
- [Project, geometry and materials procedures](capabilities_test/01_project_geometry_materials/README.md)
- [Parameter-driven geometry procedures](capabilities_test/02_parameters/README.md)
- [Boolean operations and error procedures](capabilities_test/03_booleans/README.md)

The Markdown reference summarizes exposed tools.
Use live MCP tool discovery to inspect the active schemas and contracts.
