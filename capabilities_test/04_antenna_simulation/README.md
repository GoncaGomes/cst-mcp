# Batch 04: thin copper dipole

This client implements the first antenna scenario through a real stdio MCP session.
Only a user-triggered live invocation constructs the model, executes one blocking
time-domain solve, exports results and produces native execution evidence.
Implementation verification uses offline tests and the real server with CST disabled.
The patch and open-ended waveguide scenarios are outside this battery.

## Fixed model

| Property | Value |
| --- | --- |
| Orientation and environment | Z axis, vacuum, no ground plane |
| Negative arm | Z = -74.95 to -0.15 mm |
| Positive arm | Z = 0.15 to 74.95 mm |
| Each metallic arm | 74.80 mm long, 0.15 mm radius |
| Feed gap | 0.30 mm, from -0.15 to 0.15 mm |
| Outer length including gap | 149.90 mm |
| Material | Native library Copper (annealed), project alias DipoleCopper |
| Port | One discrete S-parameter port, 50 ohm, endpoints on the arm end faces |
| Units | Length mm, frequency GHz, time ns |
| Frequency range | 0.8 to 1.2 GHz |
| Boundaries and symmetry | Six expanded-open faces, no symmetry |
| Mesh | Hexahedral, 20 cells per wavelength, minimum 5 cells, ratio limit 20 |
| Local mesh | Factor-two refinement on both arms |
| Adaptive mesh | Disabled, one baseline simulation |
| Solver | Time Domain, -40 dB accuracy, Gaussian excitation, fixed 50 ohm normalization |
| Farfield monitors | 0.9, 1.0 and 1.1 GHz with explicit names |
| Field monitors | e-field (f=1) and h-field (f=1) |
| Loss monitors | Native 1D power loss, using field and farfield monitors |

The installed material file is inspected read-only and its contents and SHA-256
are recorded as source provenance. It is not independent native project readback.
The RF definition in the inspected CST 2025 file uses finite conductivity
5.8e7 S/m and relative permeability 1. The live MCP library-load operation must
succeed before dependent construction. There is no material, geometry or solver
fallback and no dimensional tuning.

## Commands

Run these commands from `C:\dev\cst-studio-mcp` using the repository uv environment.
The current selected installation is CST Studio Suite 2025 and its compatible
Python runtime is Python 3.12. The client reads the executable version resource
without launching it and imports `cst.interface` and `cst.results` in a separate
process without constructing a DesignEnvironment or ProjectFile.
Plotting dependencies are imported in the same interpreter used by the server.
No inline script environment or nested virtual environment is created.

Safe implementation verification:

```powershell
uv run --extra figures capabilities_test\04_antenna_simulation\run_dipole.py --preflight
uv run --extra figures capabilities_test\04_antenna_simulation\run_dipole.py --preflight --cst-path "C:\Program Files (x86)\CST Studio Suite 2025"
uv run --extra figures --extra dev -m pytest capabilities_test\04_antenna_simulation\test_run_dipole.py
```

The following commands are for the USER's later live validation. They must not
be executed as part of implementing this battery:

```powershell
uv run --extra figures capabilities_test\04_antenna_simulation\run_dipole.py
uv run --extra figures capabilities_test\04_antenna_simulation\run_dipole.py --solver-timeout-s 1800
```

Both live commands have the same default solver budget of 1800 seconds.
The MCP request allows at least the solver budget plus 120 seconds.
`--cst-path` and `--cst-version` select another installation explicitly.
The default discovery searches conventional installation locations for 2025.
A Python ABI or selected executable-version mismatch fails prerequisites before
any connection is attempted. The server command is exactly `sys.executable`
with arguments `-m cst_mcp.server` and repository-root cwd.
Source and output paths are resolved from `__file__`, so calling the absolute
entry point from another working directory retains the same output paths.

Only the MCP child environment is configured. It receives manual connection
mode for live execution or disabled mode for preflight, the selected installation,
the owned work directory, repository source and selected CST libraries on
PYTHONPATH, and these toolsets:

```text
connection,official,project,geometry,materials,ports,boundaries,mesh,solvers,simulation,results,import_export,diagnostics,vba,workflows,figures
```

Raw VBA is enabled in that child only. The client permits exactly the existing
units setup, units readback and solid/material enumeration blocks. Enumeration
and unit readback are repeated after reopening. No other raw VBA is allowed.
User-wide environment variables and other CST sessions are not changed.

## Fixed outputs and overwrite policy

Every user-triggered live invocation replaces these three generated directories
after validating resolved paths and checking all existing native projects and
their lock files:

```text
dipole/
  project/dipole.cst
  results/
    mcp/             Complete sequential request/response records
    protocol/        Initialization, paginated catalog, schemas and stderr
    cst_logs/        Original JSON, native text and available log-file snapshots
    exports/         Touchstone, native ASCII, fields, FFS and archived sources
    figures/         Server-produced figures
    work/            Owned server intermediate and automatic exports
    metadata.json
    validation.json
  reports/
    calls.jsonl
    validation.md
```

There are exactly two primary reports: `reports/calls.jsonl` and
`reports/validation.md`. JSON metadata, validation records and raw logs are
supporting evidence. Final supporting JSON and the narrative report are replaced
atomically. The calls log is truncated during preparation and flushed before
each actual tool request. Planned but blocked calls are not placed in that log.

The script rejects links and junctions in cleanup targets and their ancestry.
It checks the entire target set and existing project locks before deleting any
output. A lock or denied exclusive read probe stops preparation and asks the
user to save and close the project. Lock files are never presumed stale.
The script does not close another CST session. Source files, this README, the
parent directory and unrelated files remain intact.

No timestamped run directories, history folders, backups or latest-run links are
created. Timestamps remain inside metadata. If CST returns another native
filename, the script accepts it only inside the cleared owned `project/` directory.
Subsequent calls use that verified actual path.

Preflight overwrites only `preflight/dipole/results/` and
`preflight/dipole/reports/`. Its server work directory is
`preflight/dipole/results/work/`. It never replaces or clears live `dipole/`
outputs. Offline tests use temporary folders exclusively.
Automatic exports are copied immediately with their producing call number to
prevent later calls in the same invocation from overwriting their evidence.
Those copies are removed by the next invocation for that mode.

## Manifest and execution boundaries

`run_dipole.py` contains the complete 105-call manifest in phase order.
Preflight initializes the real disabled server, follows every tools/list cursor,
saves original catalog pages and schemas, validates all representative resolved
arguments and sends only `cst_connection_status`. All live phases are marked
`not_executed`. No generated VBA is counted as an executed native operation.
Each live call is schema-validated again with the actual resolved arguments.
Runtime discrepancies are preserved and stop live prerequisites.

| Phase | Purpose |
| --- | --- |
| A | Request a new isolated instance, create the owned project, verify units |
| B | Load native copper, create arms and port, enumerate actual solids/materials |
| C | Configure boundaries, mesh, solver and all monitors, retain available readback |
| D | Execute one blocking solve, require idle native state, save and discover results |
| E | Read full complex S11 and independently cross-check all derived curves |
| F | Read radiation metrics, export numerical data and assess power balance |
| G | Request MCP figures using separately acquired realized gain and directivity |
| H | Probe missing or unsupported read-only routes while state is known and idle |
| I | Save, close, verify closed state, compare saved S11, reopen and read back the model |

Saved-result discovery follows the returned increasing offsets with the same
filter. The native S11 tree, result-run integer and field items come from actual
discovery. Missing or ambiguous field items block their exports with evidence.
Radiation exports must prove the selected exact 1 GHz tree and original quantity
header. A realized-gain source cannot be relabeled as directivity.

Ordinary failures in a responsive session capture project messages and bounded
simulation status. Independent result routes can continue when their prerequisites
remain known. A construction/configuration failure blocks the solve. A native
timeout, lost response or unknown/busy state forbids all subsequent CST calls.
There is no solve retry, second-instance recovery, asynchronous solve, interruption,
parameter sweep or optimizer. Local evidence finalization does not depend on CST.

The SDK transport reuses the existing preserving stdio helper. It does not place
CST descendants inside a kill-on-close job or terminate them during MCP shutdown.
Missing SDK preservation hooks fail startup. Successful shutdown of that transport
does not by itself prove the solver succeeded or that CST remained alive after exit.
On success, the created project is reopened and left for user inspection.
There is no application-close or disconnect cleanup action.

## Numerical validation

Full S11 must have matching, finite, nonempty complex axes and verified GHz units.
The report retains actual range, ordering and duplicate counts. No samples are
silently deduplicated. Derived metrics use the full curve, even when a separate
200-point preview is exercised. Singular values remain explicit nulls with
documented meaning rather than fabricated zeros.

Independent checks implement magnitude, `20*log10(abs(S11))`, wrapped and unwrapped
phase, `(1+abs(S11))/(1-abs(S11))`, and `50*(1+S11)/(1-S11)`.
The default input-impedance route is independently compared with S11-derived
impedance and must identify its native S-parameter source and 50-ohm reference.
Native Z-parameter impedance is compared only when common reference-plane metadata is present.
The -10 dB intervals use linear interpolation of reflection magnitude and retain
all disjoint bands and truncated-edge flags. No qualifying interval is a valid
result. S11 is compared across the save/close/read/reopen sequence.

Touchstone parsing uses the actual unit, representation, reference impedance and
complete grid. The current expected export is one-port, 50 ohm RI. Reconstructed
complex values are compared with full native S11 using an initial absolute
tolerance of 1e-5 and frequency tolerance of 1e-7 GHz (100 Hz). Maximum
frequency and complex S11 differences are retained in the validation report. Native scalar ASCII is
compared only if the header proves its quantity, scale and frequency units.
Frequency and complex S11 tolerances are recorded separately. The frequency
tolerance accommodates numerical round-off between the native curve and text export.
A `.csv` extension can contain whitespace-separated CST ASCII. This is reported
as a format limitation. Touchstone supplies the required complex export.

Angular parsing verifies degree units, theta/phi order, dBi or explicitly linear
scale, regular full-sphere coverage, actual steps, duplicate seams and pole samples.
Seam duplicates are excluded from independent solid-angle integration.
The signed custom cut is compared with matching positive/negative theta rows of
the full export. Its current 1-degree sampling is matched to the 5-degree grid.

Initial physical criteria for this fixed model at 1 GHz are:

| Check | Initial criterion |
| --- | --- |
| Directivity peak | 2.15 dBi +/- 0.3 dB, from proven directivity data |
| Meridional HPBW | 78 degrees +/- 5 degrees |
| Broadside | Maximum theta within one actual angular step of 90 degrees |
| Axial nulls | At least 20 dB below broadside when the source supports assessment |
| Azimuth ripple at theta = 90 | At most 0.5 dB |
| Normalized meridional shape | Maximum difference 1 dB above the -20 dB region |
| Efficiency identities | Linear realized gain = directivity * total efficiency and total efficiency = radiation efficiency * (1 - abs(S11)^2), initial absolute tolerance 0.03 |
| Power conservation | abs((Paccepted - Pradiated - Ploss)/Paccepted) <= 0.03 at all three monitored frequencies |

The normalized ideal field shape is
`abs(cos((pi/2)*cos(theta))/sin(theta))` with zero limiting value at the poles.
The 1 dB shape threshold is an explicit initial client criterion. It is not a
published mesh-convergence specification. Deep null samples are excluded.
Omnidirectional azimuth maxima do not need a unique direction. Efficiency
overshoots are recorded without clipping. Real conductor loss is allowed.

The ideal reference values and nonzero-reactance context are documented in
[NPTEL's half-wavelength dipole reference](https://archive.nptel.ac.in/content/storage2/courses/117101057/Slide7/4.5.html).
[Altair's thin-dipole example](https://help.altair.com/2025/feko/topics/feko/example_guide/antenna_synthesis_analysis/dipole_intro_feko_t.htm)
supplies the example and scaling context. These are theoretical references,
not measured outputs of this copper model. No exact resonance, exact 73+j0 ohm
input impedance or mandatory -10 dB matching at 1 GHz is imposed.

FFS inspection examines declared source sections, frequency, sample counts and
complex field components. It is not a gain CSV. Reuse as a CST excitation source
is untested. Field/current extraction requires numerical vector components and
units. File existence, a screenshot or a geometry-only plot is insufficient.

Figures are produced exclusively by the manifest's MCP tools. The client checks
PNG readability, expected quantity coverage, original source metadata and
independently parsed farfield peaks. The server's three-decimal peak serialization
uses a recorded 0.00051 dB comparison tolerance. PDF headers are inspected, but
full PDF rendering and visible axes, legends and 3D radiation content remain
explicitly inconclusive pending user visual inspection. The client never creates
replacement figures when a plotting tool fails.

## Actual contract limits and interpretation

The inspected schemas accept all 105 planned calls. The installed SDK exposes
pagination through `params` and numeric request timeouts. The client also supports
the previously inspected SDK 1 conventions and fails if the preservation hooks
are unavailable. These are client transport compatibility adjustments, not
changes to any tool name or manifest arguments.

The current port-list implementation uses a native message box and does not
return structured port attributes. It can require attention during a user live
run. The script retains the call and does not dismiss dialogs or add a workaround.
The optional native `z_parameter` impedance route lacks reference-plane metadata. Mesh statistics cannot
prove that the 0.30 mm gap is resolved. Setup diagnostics validate supplied inputs,
not independent applied settings. Log snapshots can be tails. None of these
limits is hidden by a successful protocol response.

Error-contract success and feature support are separate verdicts. An expected
missing 1.05 GHz farfield, Cartesian resampling rejection, unsupported NSF export,
missing S99,99 or dedicated cross-polarization-export rejection does not make
that feature supported. Unexpected unrelated errors fail the rejection contract.
If a probe begins returning supported data, its numerical assessment remains
separate from mere delivery. No axial-ratio benchmark applies to this antenna.

Exit 0 requires all mandatory construction, native solve/readiness, full S11,
full-curve derived consistency, complex Touchstone consistency, radiation provenance, physical and persistence
criteria to pass. Exit 1 means a demonstrated mandatory failure or failed
preflight prerequisite. Exit 2 means incomplete mandatory assessment without a
demonstrated mandatory failure. Individual optional capability failures remain
visible even when mandatory criteria pass.

Offline tests cover numerical identities, Touchstone representations, multiple
bandwidth intervals, provenance and FFS structure, scoped replacement, lock/link
refusal, preflight isolation, pagination, source preservation, error finalization,
material dependency failure and timeout blocking. Mock responses test client
behavior only. They are never native CST support evidence.

Live solver success, native library loading, usable field exports, quantity
provenance, physical agreement, saved-result persistence and visual figure review
remain for the user's live run. One simulation cannot establish mesh convergence.
