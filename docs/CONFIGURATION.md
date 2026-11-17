# Configuration and troubleshooting

See the [project README](../README.md#quick-start) for installation and a complete
MCP client configuration example. The Python package and executable are named
`cst-studio-mcp`; the Python module is `cst_mcp`.

## Environment variables

Set these variables in the MCP client's server configuration and restart the
server after changes. The examples below use CST 2025; select your actual installation.

| Variable | Behavior |
| --- | --- |
| `CST_CONNECT_MODE` | `manual` by default; also accepts `disabled` and `auto` |
| `CST_PATH` | CST installation root, not the Python library directory |
| `CST_VERSION` | Installation discovery year, default `2026` |
| `CST_WORK_DIR` | Writable project and result directory, default `~/cst_projects` |
| `CST_TOOLSETS` | Comma-separated tool categories or aliases; unset exposes all categories |
| `CST_ALLOW_RAW_VBA` | Enables connected raw VBA execution when set to `1` |
| `CST_LOG_LEVEL` | Logging level, default `INFO` |
| `CST_QUIET` | Defaults to `1`; does not suppress CST dialogs |

For a server launched directly from PowerShell:

```powershell
$env:CST_PATH = "C:\Program Files (x86)\CST Studio Suite 2025"
$env:CST_VERSION = "2025"
$env:CST_WORK_DIR = "C:\CST-Projects"
$env:CST_CONNECT_MODE = "manual"
$env:CST_TOOLSETS = "core,geometry,materials,boolean"
.\.venv\Scripts\python.exe -m cst_mcp.server
```

Use an absolute interpreter path in client configuration. The equivalent installed
executable is `.\.venv\Scripts\cst-studio-mcp.exe`; environment activation is not required.

## CST installation and Python compatibility

The package requires Python 3.10 or later. CI and capability clients use Python 3.12.
Live automation requires 64-bit Windows and an interpreter compatible with the
installed CST Python library ABI. Installation discovery does not prove compatibility.

`CST_PATH` selects the installation root. The server discovers and adds the Python
library directory, typically:

```text
C:\Program Files (x86)\CST Studio Suite 2025\AMD64\python_cst_libraries
```

Without `CST_PATH`, discovery searches common locations, trying the requested year
before nearby years. Call `cst_connection_status` to inspect the selected installation
and library paths. Resolve DLL and ABI import errors before live work.
Installed-help checks require local CST help files. Saved-result reading requires
a compatible `cst.results` library and completed project data.

## Connection modes and project safety

- `manual`: no startup connection. Call `cst_connect` explicitly for live work.
- `disabled`: prevents connections. Use it for initial offline acceptance.
- `auto`: connects at startup only when explicitly configured.

The `cst_connect` tool has a separate `mode` argument:

- `new` starts an isolated Design Environment and never adopts an existing project.
- `any` attaches to a running Design Environment and may adopt its active project,
  or starts one if none is running.

Inspect the returned PID, open project paths and warnings before changing a model.
An already-connected session is preserved; disconnect before requesting a new instance.
`cst_disconnect` does not close CST or projects and does not stop a solver.
Follow the [agent acceptance guide](AGENT_SETUP.md#disabled-mode-acceptance) before live work.

### Automatic education notice handling

On Windows, both modes automatically invoke only the recognized education notice's
unique enabled OK button for the selected or started CST PID.
Recognition requires an exact `CST Studio Suite YYYY` title, with any four-digit year,
and both identifying message fragments:

- `education version`
- `this software is licensed for non-commercial use only`

Matching ignores case and normalizes whitespace. Unrelated dialogs are not dismissed.
The observer starts before attachment or startup and is stopped when that call finishes.
Its 60-second observation deadline is not a timeout on the vendor connection call.
If the CST API cannot provide an authoritative PID, or UI Automation is unavailable,
connection still proceeds. Dismiss the notice manually if needed and check server logs.

## Tool selection and raw VBA

`CST_TOOLSETS=core` includes connection, official, project, workflows, simulation,
results, parameters and diagnostics. Add categories such as `geometry`, `materials`
and `boolean` for dedicated modeling tools. Connection tools remain available with
any selection. Use MCP tool discovery to inspect active schemas.

Raw VBA execution is opt-in through `CST_ALLOW_RAW_VBA=1`. Its argument checks are a
best-effort filter, not a sandbox. Enable it only for trusted clients and a defined task.

## Optional dependencies

Install `.[figures]` for plotting, or `.[dev,figures]` for development and the full offline
test dependencies. With uv, include the plotting extra when checking antenna runners:

```powershell
uv run --extra figures capabilities_test\04_antenna_simulation\run_dipole.py --preflight
```

Missing NumPy or Matplotlib is a prerequisite failure, not a schema mismatch.
A successful preflight does not authorize a native workflow run.
