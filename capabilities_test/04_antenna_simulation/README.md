# Batch 04: dipole, rectangular patch and WR-90 antennas

This client implements the first antenna scenario through a real stdio MCP session.
Only a user-triggered live invocation constructs the model, executes one blocking
time-domain solve, exports results and produces native execution evidence.
Implementation verification uses offline tests and the real server with CST disabled.
The rectangular patch and WR-90 open-ended waveguide scenarios are documented below.

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
uv run --extra dev --extra figures -m pytest capabilities_test\04_antenna_simulation\test_run_dipole.py
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

## Rectangular patch scenario

`run_patch.py` implements the fixed thin-substrate example from
[MathWorks, Patch Antenna with Dielectric Substrate](https://www.mathworks.com/help/antenna/ug/patch-antenna-on-dielectric-substrate.html).
The expected resonance is near 2.37 GHz and the reference peak directivity is
approximately 5.84 dBi at that frequency. The reference describes linear
polarization and radiation predominantly above the ground plane.
No agreement is claimed before user live validation.

| Property | Fixed patch configuration |
| --- | --- |
| Component | `Patch` |
| Patch | X -20 to 20, Y -15 to 15, Z 1.57 to 1.605 mm |
| Substrate | X -40 to 40, Y -30 to 30, Z 0 to 1.57 mm |
| Ground | X -40 to 40, Y -30 to 30, Z -0.035 to 0 mm |
| FeedPin | X 5 to 6, Y -0.5 to 0.5, Z 0.10 to 1.57 mm |
| Conductors | `cst_load_material`, native `PEC`, alias `PatchPEC` |
| Substrate material | Native `Rogers RT-duroid 5870 (loss free)`, alias `PatchSubstrate` |
| Installed substrate properties | Epsilon 2.33, mu 1, zero electrical losses |
| Discrete port | 50 ohm SParameter, from (5.5, 0, 0) to (5.5, 0, 0.10) mm |
| Units and domain | mm, GHz, ns, Normal background with epsilon 1 and mu 1 |
| Boundaries and symmetry | Six expanded-open faces, no symmetry, no waveguide-port faces |
| Mesh | Tetrahedral, Tet density map, 20 cells per wavelength and minimum 5 cells |
| Relative refinement | Substrate 2, patch 2, feed pin 4 |
| Solver | Frequency Domain, residual 1e-6, discrete samples only, adaptation disabled |
| Frequency samples | 21 equidistant solved samples from 2.2 to 2.5 GHz, monitor samples enabled |
| Farfield monitors | `farfield (f=2.3)`, `farfield (f=2.37)`, `farfield (f=2.45)` |
| Field monitors | `e-field (f=2.37)` and `h-field (f=2.37)` |

Each of the four bricks is created individually through `cst_create_brick`.
`cst_boolean_insert` inserts `Patch:FeedPin` into `Patch:Substrate` and must
retain the PEC probe. The probe touches the patch and stops 0.10 mm above ground.
The discrete port spans that excitation interruption. There is no continuous
metallic ground-to-patch connection.

The 0.035 mm metal thickness and 0.10 mm excitation interruption are CST modeling
choices that differ from the idealized reference. Dimensions are fixed.
There are no antenna design formulas, template generators, tuning, optimizers,
parameter sweeps, automatic solve retries or solver comparisons.
Native material loading must succeed. There is no copper, lossy-substrate,
manually entered property or alternate-material fallback. Rogers CuClad 233 is
not used because its installed definition has epsilon 2.4.

The runner uses the same documented unit VBA and native solid/material enumeration
as the dipole runner. It checks all four retained shape names and assignments
before solving and after reopening. Bundled-catalog material tools are not used
as applied-project evidence. Library files and their hashes are source provenance.
The available port-list response is retained, including its lack of structured
discrete-port attributes and possible native message box.

### Patch commands

Use the repository environment with the same Python interpreter for client and
server. No dependency installation, inline script metadata or separate environment
is required. Default installation selection is CST 2025, with the dipole options
`--cst-path` and `--cst-version` also supported.

```powershell
uv run --extra figures capabilities_test\04_antenna_simulation\run_patch.py
uv run --extra figures capabilities_test\04_antenna_simulation\run_patch.py --preflight
uv run --extra figures capabilities_test\04_antenna_simulation\run_patch.py --solver-timeout-s 1800
uv run --extra figures capabilities_test\04_antenna_simulation\run_patch.py --resume
```

Initial implementation review was static. Subsequent user-authorized live runs
are recorded in the fixed patch evidence directories. Unit tests were not run.

`--resume` continues from an idle phase D discovery stop when a completed saved
solve exists. It selects the main `1D Results\S-Parameters\S1,1` branch and excludes
adaptive-meshing histories. It verifies the recorded CST instance or opens the
owned saved project in a fresh instance when that process has exited. It does
not rebuild, reconfigure or start a solver. Existing call evidence is retained
and new calls continue its numbering. Prior blocked assessments are marked as
superseded, with their original verdicts retained. Protocol filenames use a
resume prefix in the same fixed directory. There are no extra primary reports
or run folders. A failed native solve cannot be resumed as a completed result set.

### Patch solver and monitoring

The corrected `cst_configure_frequency_domain_solver` uses documented CST 2025
methods: `SetMethod("Tetrahedral", "Discrete samples only")`, `AccuracyTet=1e-6`,
`ResetSampleIntervals("all")`, and `AddSampleInterval(2.2, 2.5, 21, "Equidistant", False)`.
The originally specified baseline sets `MeshAdaptionTet=False`. The current
runner's `SOLVER.adaptive_mesh` setting controls both native tetrahedral adaptation
and the adaptation flag on the requested 21-frequency interval. Setting it to
true enables adaptation across that interval, rather than at a separate single
frequency. The execution evidence records the actual setting. It also sets `AddMonitorSamples=True`,
`AutoNormImpedance=False` and `NormingImpedance=50`.
The global frequency interval uses `Solver.FrequencyRange` through the existing
frequency-range route. The runner has no raw VBA solver fallback and does not
use the time-domain adaptive-mesh tool to represent tetrahedral adaptation.

The installed help describes `AutoNormImpedance=False` as port normalization,
despite the requested fixed-normalization flag convention. The 50 ohm discrete
port and independent exported-curve consistency checks are therefore essential.
The result reference is not presumed from successful configuration alone.

Exactly one `cst_run_simulation_async` call requests `solver_type="Frequency Domain"`.
Repeated `cst_wait_for_simulation` calls use `max_wait_s=45` while status is
`starting` or `running`. The overall budget defaults to 1800 seconds.
A final bounded wait can cross that deadline by its remaining bounded call time.
Budget exhaustion leaves a possibly running solver untouched and blocks further
CST operations. Unknown states and unresponsive sessions also block further calls.
The existing preserving stdio transport protects other CST processes and sessions.
There is no stop, terminate, disconnect or application-close cleanup action.

After waiting, `cst_get_simulation_status` records available status evidence.
Idle alone is insufficient. A unique native saved S11 result run and all requested
farfield monitor results must be discovered before numerical validation.
An error reported for an observed native solve is recorded separately from MCP
request acceptance and from result-discovery errors. Original CST error messages
are preserved. Memory-allocation failures require available machine resources,
rather than relaxed result validation or an automatic solver retry.
Project logs are captured after configuration and after completion or a known
failure. Mesh information and quality retain available counts, edges and query
limitations. Relative refinement does not guarantee an absolute element size or
a specific number of elements through the substrate. Setup diagnostics inspect
supplied settings and generic solver information does not independently read all
FDSolver settings.

### Patch outputs and evidence

All paths are resolved from the script location. Every live invocation replaces
only the fixed patch-owned `project`, `results` and `reports` directories after
the existing resolved-path, link, junction and native-project-lock checks.
Dipole source and generated outputs are preserved. Patch locks are `.patch.lock`
and `.preflight-patch.lock` in the batch directory.

```text
patch/
  project/
    patch.cst
    associated CST project files
  results/
    mcp/
    protocol/
    cst_logs/
    exports/
    figures/
      one_d/
      farfield_realized_gain/
      farfield_directivity/
    work/
    metadata.json
    validation.json
  reports/
    calls.jsonl
    validation.md
```

There are exactly two primary reports. `calls.jsonl` records actual MCP function
calls and arguments sequentially. `validation.md` includes execution outcome,
model, configuration, numerical assessments, limitations and original CST messages.
Complete requests, responses, protocol records, original logs and JSON assessments
are supporting evidence under `results`. Automatic exports are copied immediately
with their producing call number before a later call can overwrite them.
There are no timestamped folders, backups, history folders or latest-run links.

Preflight overwrites only `preflight/patch/results` and `preflight/patch/reports`.
It never clears live patch or dipole outputs. It starts the real server with CST
disabled, initializes MCP, follows every catalog cursor and validates representative
manifest arguments against the returned schemas. Its only tool call is
`cst_connection_status`. No connection or model-building call is executed.

Discovery uses `cst_list_results`, paginated `cst_list_saved_results`,
`cst_project_tree` and `cst_discover_farfield_monitors`. Native result-run IDs,
monitor labels and field paths must come from discovery. The Hfield monitor's
actual surface-current item is discovered before export. No invented monitor type
or assumed excitation suffix is used. Missing or ambiguous fields are inconclusive.

Requested MCP exports under `patch/results/exports` are:

- `patch.s1p`
- `s11_native.csv` and `s11_native.txt`
- `efield_2p37GHz.txt`, `hfield_2p37GHz.txt` and `surface_current_2p37GHz.txt`
- `farfield_realized_gain_2p37GHz.csv`
- `patch_2p37GHz.ffs`

MCP figures use PNG and PDF. The 1D figures request S11, impedance, VSWR, phase,
Smith and available efficiency curves with `simulate_if_missing=False`.
Separate realized-gain and directivity figures request phi=0 and phi=90 principal
cuts, angular maps and 3D radiation. Native headers, frequency identifiers, source
metadata, file existence and numerical content are checked. Figure readability
does not prove visible axes, legends or geometry. User visual review remains needed.
Secondary metric or export failures can remain unsupported or inconclusive while
independent routes continue in a responsive idle session.

### Patch numerical and physical criteria

- Resonance is found from a zero crossing of imaginary input impedance. All
  crossings and interpolation brackets are recorded, with the crossing closest
  to 2.37 GHz selected for the reference diagnostic. Minimum S11 frequency is
  recorded separately. The initial resonance tolerance is 3 percent.
- Peak directivity at 2.37 GHz is compared with 5.84 dBi using an initial 1 dB
  tolerance. These are our diagnostic criteria, not published uncertainty bounds.
- The requested base grid spacing is 0.015 GHz. Monitor samples may add points.
  Actual minimum and maximum steps and sample count are recorded. Resonance and
  bandwidth estimates depend on interpolation within those brackets.
- Full complex S11 is compared across independent saved and live read routes,
  native ASCII where its quantity is proven, and the complex Touchstone export.
  Magnitude, dB, wrapped/unwrapped phase, impedance, VSWR and Smith impedance are
  independently derived using 50 ohms. Touchstone checks units, representation,
  reference impedance, full frequency grid and complex values. Frequency and
  complex tolerances follow the dipole conventions of 1e-7 GHz and 1e-5.
- The -10 dB band uses magnitude-crossing interpolation and retains all intervals
  and truncated edges. An absent matched band is reported honestly. No invented
  minimum S11 is imposed.
- Solid-angle integration assesses the fraction of radiation above ground in +Z.
  Predominant upper-half-space radiation means more than half of integrated power.
  No reference leakage bound is invented.
- Polarization uses common-angle components in a declared Ludwig3 basis. If the
  dedicated route is unavailable, a validated FFS export can supply complex
  Etheta and Ephi with proven column order. The client rotates those components
  to Ludwig3 with +X co-polarization at boresight. The initial diagnostic is XPD
  at least 10 dB at upper-half-space main-lobe angles within 3 dB of peak field
  power. It never divides unrelated global maxima or labels FFS fields as gain.
- Radiation efficiency should approach unity for this lossless model. An initial
  0.03 linear tolerance is diagnostic. Gain equals directivity times radiation
  efficiency, realized gain equals directivity times total efficiency, and total
  efficiency equals radiation efficiency times `1-abs(S11)^2` when independently
  identified data exist. Missing loss curves are capability limitations, not
  measured zero losses. Gain fallback values require original native evidence
  before they can be used as IEEE gain.

The owned project is saved and closed. Closure is independently checked before
the saved full S11 is read without an active project. It is then reopened, its
units and retained solids/materials are checked, and S11 is compared again.
The project is left available for user inspection.

Exit 0 requires mandatory native, numerical, physical and persistence checks to
pass. Exit 1 indicates a demonstrated mandatory failure or failed prerequisite.
Exit 2 indicates an incomplete assessment, including missing mandatory physical
evidence. Optional capability limitations remain visible in all cases.

## WR-90 open-ended waveguide

`run_wr90.py` implements one fixed finite-flange PEC model using the
[Ansys rectangular probe antenna reference](https://optics.ansys.com/hc/en-us/articles/360042538073-Rectangular-probe-antenna).
The reference uses a PEC sheet. The 30 mm guide length and 0.10 mm flange
thickness below are implementation choices, so this is not an exact reproduction.
No agreement with the reference is claimed before live validation.

| Property | Fixed WR-90 configuration |
| --- | --- |
| Component and material | `WR90`, native library `PEC` loaded as `WR90PEC` |
| Vacuum aperture | X -11.43 to 11.43, Y -5.08 to 5.08 mm |
| Outer guide | X -13.93 to 13.93, Y -7.58 to 7.58, Z -30 to 0 mm |
| Wall thickness | 2.5 mm |
| Finite flange | X -20 to 20, Y -10 to 10, Z -0.10 to 0 mm |
| Aperture and radiation | Z=0, towards +Z |
| Bore cutter | X -11.43 to 11.43, Y -5.08 to 5.08, Z -30.01 to 0.01 mm |
| Construction | Three bricks, add Flange to Body, subtract Bore from Body |
| Expected final shape | Only `WR90:Body` assigned `WR90PEC` |
| Port | Port 1, Free coordinates, zmin, internal at Z=-30 mm, aperture-sized |
| Modes | `mode_number=1` calculates one mode, intended fundamental TE10 |
| Units and background | mm, GHz, ns, Normal background with epsilon=1 and mu=1 |
| Frequency interval | 8.2 to 12.4 GHz |
| Domain | Six expanded-open faces, `waveguide_port_faces=[]`, no symmetry |
| Mesh | Hexahedral, Hex density, 20 cells per wavelength, minimum 5, ratio limit 20 |
| Refinement | Factor 2 on `WR90:Body` |
| Adaptation | Disabled, max passes 1, threshold 0.02 |
| Solver | Time Domain, -40 dB accuracy, Gaussian, calculated modal normalization |
| Power losses | Native 1D power loss enabled using field and farfield monitors |
| Monitors | Explicit farfields at 9, 10 and 11 GHz, Efield and Hfield at 10 GHz |

Library loading must succeed. There is no copper or manually defined material
fallback. The longitudinal bore overshoot produces a through-opening and the
removed cutter leaves the vacuum background in the cavity. The documented native
solid enumeration checks retained shape names and material assignments. It does
not independently prove connectivity or every dimension.

### WR-90 commands and implementation review

Use the repository interpreter for both the client and its stdio MCP server.
Installation selection defaults to CST 2025. `--cst-path` and `--cst-version`
retain the existing selection behavior. Paths resolve from the script location.
No dependencies, environment, inline metadata or lockfile are added.

```powershell
uv run --extra figures capabilities_test\04_antenna_simulation\run_wr90.py
uv run --extra figures capabilities_test\04_antenna_simulation\run_wr90.py --preflight
uv run --extra figures capabilities_test\04_antenna_simulation\run_wr90.py --solver-timeout-s 1800
```

Initial implementation review was static. A subsequent user-authorized live run
on 2026-10-14 completed one time-domain solve and the full save, close and reopen
sequence. Its reports retain the original calls and the later client reassessment.
The approved MCP reader correction was verified with native scalar cutoff data
and the unchanged full S11 curve. No unit tests were run.

### WR-90 port, modes and normalization

`cst_add_waveguide_port` now accepts `port_on_bound`, defaulting to `true` to
preserve existing behavior. The existing VBA builder emits `Port.PortOnBound`.
The WR-90 request sets it to `false`, preserving the supplied Z=-30 mm normal
coordinate inside the expanded domain. The installed CST 2025 Port object help
documents this setting. A zmin port injects towards +Z. The mode argument is the
number of calculated modes, not an arbitrary mode-index selector.

Theoretical vacuum cutoffs are about 6.557 GHz for TE10, 13.114 GHz for TE20 and
14.754 GHz for TE01. The operating interval is above the fundamental cutoff and
below both higher cutoffs. The intended TE10 electric field is predominantly Y,
with a cosine profile across X in the uniform guide. These are predictions,
kept separate from native modal data. Available port-information curves, cutoff,
propagation constants and mode fields are discovered through the actual tree and
saved-result inventory, then read or exported with existing generic tools.
Unavailable extraction is reported as a capability limitation. No dedicated modal
tool or undocumented VBA fallback is added.

CST mode information distinguishes wave and line impedances. The installed
Reference and Normalizing help describes wave impedance as the reference for
non-TEM modes and line impedance for (Q)TEM modes. The runner still requires
actual native reference information, such as a discovered `ZRef 1` curve, before
converting S11. It never substitutes an analytical TE wave impedance.
`cst_list_ports` retains listing evidence but does not independently prove the
feed coordinate or TE10 profile. The current listing route may show a native
message box during user live execution.

Modal S11 is acquired as full complex curves with both live and saved readers.
Magnitude, dB, wrapped and unwrapped phase and VSWR are checked in that same
reference. Native Z-parameter extraction is retained where available and is not
compared with an assumed 50-ohm S11-derived input impedance. Impedance and Smith
figures are omitted because their default scalar reference is not suitable for
an unidentified modal reference.

`wr90_50ohm.s1p` uses the existing exporter, which explicitly renormalizes to
50 ohms. The file is parsed for syntax, units, RI representation, declared
reference and full frequency grid. Its complex values are not directly compared
with modal S11. With a real positive native reference and an understood
travelling-wave convention, conversion uses
`Z=Zref*(1+S)/(1-S)` and `S50=(Z-50)/(Z+50)`. Native reference samples may be
interpolated onto the S11 grid, with that operation recorded. Missing reference
data or an unsupported convention makes the cross-normalization comparison
inconclusive while preserving the export.

### WR-90 execution, evidence and outputs

The runner reuses the preserving stdio transport, paginated MCP catalog capture,
schema validation, installation probes, request and response recording, native
log snapshots and numerical parsers. Only the three existing documented unit
setup, unit readback and shape enumeration VBA blocks are permitted.

Live execution creates one isolated owned project and starts exactly one
`cst_run_simulation_async` call with `solver_type="Time Domain"`. It repeats
`cst_wait_for_simulation` with `max_wait_s=45`, reserving the documented native
wait overrun within the overall solver budget. It records simulation status,
solver and mesh information, saves and discovers results. Idle alone does not
prove success. A unique main S11 result run and all three requested farfield
results must exist before numerical assessment.

Construction, configuration and solver failures block dependent phases.
Secondary extraction can continue while CST is known idle and responsive.
Timeout or unknown native state blocks subsequent CST calls and records whether
CST may still be running. There is no solve retry, replacement session, tuning,
optimizer, parameter sweep or infinite-flange comparison.

The uniform guide should provide the three equidistant FIT mesh steps in front
of the port. Global cell counts and edge statistics do not measure that condition.
Setup diagnostics check the supplied description, not independent native state.
Native farfields and power balance are used without a fabricated Lumerical
source-window setting or a PEC boundary behind the antenna.

Each invocation replaces only the fixed owned generated directories after the
existing resolved-path, link, junction, project-lock and ownership checks:

```text
wr90/
  project/wr90.cst
  results/
    mcp/
    protocol/
    cst_logs/
    exports/
    figures/
    work/
    metadata.json
    validation.json
  reports/
    calls.jsonl
    validation.md
```

There are exactly two primary reports. `calls.jsonl` records actual tool requests
in sequence, including arguments. `validation.md` contains the outcome, model,
configuration, checks, normalization assumptions, capability limitations and
original CST messages. Complete responses, protocol records, original logs and
JSON assessments belong under `results/`. No timestamped run folders, backups,
history folders or latest-run links are created. Dipole and patch outputs remain
outside WR-90 cleanup targets.

Preflight overwrites only `preflight/wr90/results/` and
`preflight/wr90/reports/`. It starts the disabled server, initializes MCP,
follows every tool-list cursor and validates schema examples. Its only tool call
is `cst_connection_status`. It never connects to CST or clears live outputs.

Requested exports are `s11_modal.csv`, `s11_modal.txt`, `wr90_50ohm.s1p`,
`efield_10GHz.txt`, `hfield_10GHz.txt`, `surface_current_10GHz.txt`,
`farfield_realized_gain_10GHz.csv` and `wr90_10GHz.ffs`. Available modal curves
and fields are also exported. Field and result-run identifiers come from actual
discovery. Surface current is discovered from the Hfield results, with no invented
Surfacecurrent monitor type.

MCP figures request S11, phase and VSWR with simulation disabled. Separate
native directivity and realized-gain sources produce principal-plane cuts,
angular maps and 3D radiation figures as PNG and PDF in the existing figure
folder conventions. E-plane is YZ at phi=90 degrees. H-plane is XZ at phi=0 degrees.
Signed profiles use opposite azimuth rows to retain the rear hemisphere.
User inspection of visible axes, legends and figure content remains necessary.

### WR-90 diagnostic criteria at 10 GHz

| Diagnostic | Our initial criterion |
| --- | --- |
| Peak directivity | 8.13 dBi +/- 1 dB |
| Peak realized gain | 7.94 dBi +/- 1 dB |
| Axial front-to-back | About 12 dB +/- 3 dB |
| Radiation efficiency, radiated/accepted | 1.0 +/- 0.03 |
| Total efficiency, radiated/incident | 0.956 +/- 0.05 |
| Main beam | Within one actual angular grid step of +Z |
| Principal-plane beamwidths | H-plane HPBW smaller than E-plane HPBW |
| Modal S11 | Inferred about -13 dB +/- 3 dB |
| Power consistency | Fractional residual at most 0.03 |
| Common-reference curve/export comparison | Frequency 1e-6 GHz, complex S11 1e-5 |
| Native cutoff, if extracted | Within 2 percent of theoretical TE10 cutoff |
| Uniform-guide E profile, if supported | Relative complex fit RMS at most 0.15, Ey energy fraction at least 0.95 |

These tolerances are our diagnostic criteria, not published uncertainty bounds.
The frequency comparison allows 1 kHz for native single-precision frequency axes
and export roundoff at X band. The live run had a maximum 476 Hz difference.
Reference interpolation clamps only endpoint differences within this tolerance,
with the actual adjustment recorded. Supplementary modal reads do not inherit
the mandatory full-S11 requirement. Native cutoff scalars retain their absent
frequency axis through the approved generic reader extension.
The Ansys article reports radiation efficiency 100.6 percent because of numerical
error. The physical target is unity and any overshoot remains visible.
The approximately -13 dB S11 value is inferred from published incident and
accepted powers. It is not a 50-ohm matching requirement.

Directivity, gain, realized gain, radiation efficiency and total efficiency are
identified separately. Independent checks use modal reflected power,
incident-minus-reflected accepted power, radiated plus measured losses and
solid-angle integration. Missing loss curves are not measured zero losses.
Available field samples are assessed inside the uniform guide, away from the
aperture. A perfectly pure TE10 aperture field is not required because the
radiating discontinuity can produce evanescent components.

The owned project is saved, closed, independently checked for closure and read
through the saved-result API. It is reopened, units and retained shape/material
are checked, and full S11 is compared again. The project remains available for
inspection. Exit 0 requires the mandatory native, numerical, physical and
persistence criteria to pass. Exit 1 indicates a demonstrated failure. Exit 2
indicates incomplete evidence. Optional capability limitations remain explicit.
