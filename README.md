# cst-mcp

A local [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server
for CST Studio Suite, using the installed CST Python API and model-history VBA.

- Manage projects and build parameter-driven geometry.
- Configure simulations, monitor solvers and read results.
- Search installed CST help and inspect completed project results without connecting to CST.

## Requirements

- **Live automation:** 64-bit Windows, licensed CST Studio Suite and Python compatible
  with the installed CST library ABI. Examples and CI use Python 3.12.
- **Client:** an MCP client supporting local stdio servers.
- **Offline development:** Python 3.10+; unit tests do not require CST.

## Quick start

### 1. Install

```powershell
git clone https://github.com/GoncaGomes/cst-mcp.git C:\dev\cst-studio-mcp
Set-Location C:\dev\cst-studio-mcp
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### 2. Configure your MCP client

Adapt the absolute paths to your checkout and CST installation.
This example uses the common `mcpServers` format; your client may use different fields.

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

`manual` keeps startup disconnected. For a first offline check, use `disabled`,
initialize the client and call `cst_connection_status`.
See [configuration](docs/CONFIGURATION.md) for all settings and troubleshooting.

### 3. Connect explicitly

In `manual` mode, call `cst_connect` with the following arguments to start an isolated instance:

```json
{"mode": "new"}
```

The default `mode=any` may attach to an existing session and adopt its active project.
Inspect the returned PID and open projects before making changes.
Disconnect first if already connected and you need a fresh instance.
Both modes handle recognized education notices only for the target PID on supported Windows APIs.
`cst_disconnect` releases handles without closing CST or projects, or stopping a solver.

## Validation status

Offline tests cover Python contracts and MCP transport. Live checks cover selected
modeling fixtures, not every tool or simulation accuracy. Some historical batches
retain known failures. See the [API assessment](docs/API_REVIEW.md) for evidence and limits.

## Documentation

- [Configuration and troubleshooting](docs/CONFIGURATION.md)
- [First project and modeling examples](docs/USAGE.md)
- [Tool reference](docs/TOOLS.md), with active schemas available through MCP tool discovery
- [Agent setup and acceptance](docs/AGENT_SETUP.md)
- [Documentation index](docs/README.md), including capability reports and utility scripts

## Contributing

Open an [issue](https://github.com/GoncaGomes/cst-mcp/issues) for bugs or feature proposals.
For code changes, run the offline tests and lint checks from the checkout using [uv](https://docs.astral.sh/uv/):

```powershell
$env:CST_CONNECT_MODE = "disabled"
$env:CST_TOOLSETS = "all"
uv run --extra dev --extra figures pytest tests
uv run --extra dev ruff check src tests
```

Read the capability procedures before running live tests or resetting their workspaces.

## Attribution and license

Derived from [ismailakdag/cst-studio-mcp](https://github.com/ismailakdag/cst-studio-mcp),
with upstream attribution retained. Maintained by
[Gonçalo Gomes](https://github.com/GoncaGomes). The [package metadata](pyproject.toml)
declares the MIT license.
