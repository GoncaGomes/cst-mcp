# Utility scripts

This directory contains three utilities for installed-help, modeler and saved-result checks.
Offline tests live in `tests/`.
The staged modeling scenarios live in `capabilities_test/`.

Run commands from the repository root after installing `.[dev,figures]`.
Use Python 3.12 for these examples.
Live modeling and saved-result reading need an interpreter that matches the CST library ABI.
Set the installation root for local help and library discovery:

```powershell
Set-Location C:\dev\cst-studio-mcp
$env:CST_PATH = "C:\Program Files (x86)\CST Studio Suite 2025"
$env:CST_VERSION = "2025"
$env:CST_CONNECT_MODE = "manual"
$env:CST_TOOLSETS = "core,geometry,materials,ports,parameters"
```

These environment values apply to the current PowerShell session.
The scripts use the virtual environment interpreter or launch it as a stdio server.

## audit_installed_api.py

This script reads installed official help without connecting to CST.
It scans statically resolvable literal VBABuilder chains in the Python source.
It also checks referenced client attributes and records selected help-file hashes.
A name found in help does not verify a signature or live execution.

Prerequisites:

- An installed CST help tree under `CST_PATH\Online Help`.
- The referenced Python interface, results, Brick and ASCIIExport help files.
- The installed repository package and runtime dependencies.

```powershell
.\.venv\Scripts\python.exe scripts/audit_installed_api.py
```

Output: `build/audits/installed-api-audit.json`.
The script creates the parent directory when needed.
It overwrites that local report and prints a compact summary to stdout.
It records paths and hashes without copying vendor help text.

## verify_live_modeler.py

This script can attach to an existing CST session or launch CST.
It calls `cst_connect` with the default `mode=any`.
It refuses a session with existing projects before creating its fixture.
Check that CST is idle before running it.

Prerequisites:

- A licensed local CST installation and compatible Python library ABI.
- An idle session with zero open projects, or permission to launch a new session.
- Tool categories for the fixture, as selected in the setup block above.

```powershell
.\.venv\Scripts\python.exe scripts/verify_live_modeler.py
```

The script creates a new test project under `build/modeler-<identifier>/`.
It checks parameters, material and geometry creation, a port and a lumped element.
It also configures a parameter sweep and optimizer.
It does not start a solver.

Output: `build/modeler-<identifier>/validation.json` and the fixture `model.cst`.
The JSON file records tool arguments and responses after each call.
On the normal path, the script saves and closes its project, then disconnects.
An executed response does not independently measure every geometry property.

## verify_saved_results.py

This script reads a completed two-port project through real MCP stdio.
The project must contain S11, S12, S21 and S22 at the standard S-parameter paths.
It reads all samples, then searches installed official help for `cst.results`.
It does not call `cst_connect` or start a solver.

Prerequisites:

- A compatible installed `cst.results` library and official help files.
- A completed, unpacked project with its result sidecar directory kept beside it.
- All four complex curves in the default run, `run_id=0`.
- No active solve or write operation on the supplied project.

```powershell
.\.venv\Scripts\python.exe scripts/verify_saved_results.py C:\CST-Projects\completed.cst
```

An optional gzip CSV archive enables sample-by-sample complex comparisons:

```powershell
.\.venv\Scripts\python.exe scripts/verify_saved_results.py C:\CST-Projects\completed.cst C:\CST-Projects\archive.csv.gz
```

The archive must contain matching real and imaginary columns for all four curves.
Use `S11_re`, `S11_im`, `S12_re`, `S12_im`, `S21_re`, `S21_im`, `S22_re` and `S22_im`.
The script also accepts the suffixes `_real` and `_imag`.
Header matching ignores case and commas.
Rows must match the sample count and ordering of each returned curve.
The maximum absolute complex difference must be below `1e-12`.
The script does not compare the frequency column.

Output: a JSON summary on stdout.
The script does not save a report file.
Redirect stdout to a path you choose if you need to retain the summary.

See the [project README](../README.md), [agent guide](../docs/AGENT_SETUP.md)
and [capability procedures](../capabilities_test/02_parameters/README.md) for context.
