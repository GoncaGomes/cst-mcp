"""Fixed thin copper dipole workflow. Live execution is exclusively user triggered."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import inspect
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import timedelta
from pathlib import Path

from jsonschema.validators import validator_for
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import PaginatedRequestParams

from common import (
    DEFAULT_CST_PATH, EXPECTED_UNITS, FIXED_VBA, ROOT, SHAPES_QUERY, UNITS_QUERY,
    Incomplete, Stop, ascii_table, atomic_text, bandwidth, compare, complex_curve,
    derive, dipole_physics, exception_leaves, executable_version, farfield_grid, ffs_source, indeterminate, interpret,
    parse_shapes, parse_units, prepare_outputs, preserve_cst_processes, read_text,
    reject_links, safe_json, serialize, sha256, sphere_mean, timestamp, touchstone,
    walk_dicts, workspace_lock, write_json,
)

BATCH = Path(__file__).resolve().parent
TOOLSETS = "connection,official,project,geometry,materials,ports,boundaries,mesh,solvers,simulation,results,import_export,diagnostics,vba,workflows,figures"
MANIFEST = json.loads(r'''[
  {"arguments":{},"tool":"cst_connection_status","phase":"A"},
  {"arguments":{"mode":"new"},"tool":"cst_connect","phase":"A"},
  {"arguments":{},"tool":"cst_connection_status","phase":"A"},
  {"arguments":{"path":"<PROJECT>","project_type":"MWS"},"tool":"cst_create_project","phase":"A"},
  {"arguments":{},"tool":"cst_project_info","phase":"A"},
  {"arguments":{"code":"With Units\n  .SetUnit \"Length\", \"mm\"\n  .SetUnit \"Frequency\", \"GHz\"\n  .SetUnit \"Time\", \"ns\"\nEnd With"},"tool":"cst_execute_vba","phase":"A"},
  {"arguments":{"code":"Debug.Print \"Length\" & vbTab & Units.GetUnit(\"Length\")\nDebug.Print \"Frequency\" & vbTab & Units.GetUnit(\"Frequency\")\nDebug.Print \"Time\" & vbTab & Units.GetUnit(\"Time\")"},"tool":"cst_execute_vba","phase":"A"},
  {"arguments":{"path":"<PROJECT>"},"tool":"cst_save_project","phase":"A"},
  {"arguments":{},"tool":"cst_read_project_log","phase":"A"},
  {"arguments":{"name":"DipoleCopper","library_name":"Copper (annealed)"},"tool":"cst_load_material","phase":"B"},
  {"arguments":{"component":"Dipole","name":"ArmNegative","material":"DipoleCopper","axis":"z","outer_radius":0.15,"inner_radius":0,"center_x":0,"center_y":0,"center_z":0,"range_min":-74.95,"range_max":-0.15},"tool":"cst_create_cylinder","phase":"B"},
  {"arguments":{"component":"Dipole","name":"ArmPositive","material":"DipoleCopper","axis":"z","outer_radius":0.15,"inner_radius":0,"center_x":0,"center_y":0,"center_z":0,"range_min":0.15,"range_max":74.95},"tool":"cst_create_cylinder","phase":"B"},
  {"arguments":{"port_number":1,"impedance":50,"x1":0,"y1":0,"z1":-0.15,"x2":0,"y2":0,"z2":0.15,"port_type":"SParameter"},"tool":"cst_add_discrete_port","phase":"B"},
  {"arguments":{"code":"Dim n As Long, i As Long\nDim fullName As String\nn = Solid.GetNumberOfShapes()\nDebug.Print \"COUNT\" & vbTab & CStr(n)\nFor i = 0 To n - 1\n  fullName = Solid.GetNameOfShapeFromIndex(i)\n  Debug.Print \"SOLID\" & vbTab & CStr(i) & vbTab & fullName & vbTab & Solid.GetMaterialNameForShape(fullName)\nNext i\nDebug.Print \"DONE\""},"tool":"cst_execute_vba","phase":"B"},
  {"arguments":{},"tool":"cst_list_ports","phase":"B"},
  {"arguments":{"tree_path":"Components","max_depth":3},"tool":"cst_project_tree","phase":"B"},
  {"arguments":{"path":"<PROJECT>"},"tool":"cst_save_project","phase":"B"},
  {"arguments":{},"tool":"cst_read_project_log","phase":"B"},
  {"arguments":{"material":"Normal","epsilon":1,"mu":1},"tool":"cst_set_background","phase":"C"},
  {"arguments":{"f_min":0.8,"f_max":1.2},"tool":"cst_set_frequency_range","phase":"C"},
  {"arguments":{"x_min":"expanded open","x_max":"expanded open","y_min":"expanded open","y_max":"expanded open","z_min":"expanded open","z_max":"expanded open","waveguide_port_faces":[]},"tool":"cst_set_boundary","phase":"C"},
  {"arguments":{"x_plane":"none","y_plane":"none","z_plane":"none"},"tool":"cst_set_symmetry","phase":"C"},
  {"arguments":{"mesh_type":"Hexahedral"},"tool":"cst_set_mesh_type","phase":"C"},
  {"arguments":{"mesh_type":"Hex","cells_per_wavelength":20,"min_cells":5,"ratio_limit":20},"tool":"cst_set_mesh_density","phase":"C"},
  {"arguments":{"component":"Dipole","solid":"ArmNegative","refinement_factor":2},"tool":"cst_add_mesh_refinement","phase":"C"},
  {"arguments":{"component":"Dipole","solid":"ArmPositive","refinement_factor":2},"tool":"cst_add_mesh_refinement","phase":"C"},
  {"arguments":{"enabled":false,"max_passes":1,"threshold":0.02},"tool":"cst_set_adaptive_mesh","phase":"C"},
  {"arguments":{"accuracy":-40,"max_time_steps":0,"stimulation_port":1,"excitation_type":"Gaussian","normalize_to_fixed_impedance":true,"fixed_impedance":50,"activate_power_loss_1d":true,"power_loss_1d_use_field_monitors":true,"power_loss_1d_use_farfield_monitors":true,"power_loss_1d_extra_frequencies":[],"power_loss_1d_per_solid":false},"tool":"cst_configure_time_domain_solver","phase":"C"},
  {"arguments":{"monitor_type":"Farfield","frequency":0.9,"name":"farfield (f=0.9)"},"tool":"cst_add_field_monitor","phase":"C"},
  {"arguments":{"monitor_type":"Farfield","frequency":1,"name":"farfield (f=1)"},"tool":"cst_add_field_monitor","phase":"C"},
  {"arguments":{"monitor_type":"Farfield","frequency":1.1,"name":"farfield (f=1.1)"},"tool":"cst_add_field_monitor","phase":"C"},
  {"arguments":{"monitor_type":"Efield","frequency":1,"name":"e-field (f=1)"},"tool":"cst_add_field_monitor","phase":"C"},
  {"arguments":{"monitor_type":"Hfield","frequency":1,"name":"h-field (f=1)"},"tool":"cst_add_field_monitor","phase":"C"},
  {"arguments":{"boundaries":{"x_min":"expanded open","x_max":"expanded open","y_min":"expanded open","y_max":"expanded open","z_min":"expanded open","z_max":"expanded open"},"ports":[{"port_number":1,"type":"discrete"}],"structure_bbox":{"xmin":-0.15,"xmax":0.15,"ymin":-0.15,"ymax":0.15,"zmin":-74.95,"zmax":74.95},"power_loss_1d":true,"farfield_frequencies_ghz":[0.9,1,1.1],"field_monitor_frequencies_ghz":[1]},"tool":"cst_check_model_setup","phase":"C"},
  {"arguments":{},"tool":"cst_get_solver_info","phase":"C"},
  {"arguments":{},"tool":"cst_get_mesh_info","phase":"C"},
  {"arguments":{},"tool":"cst_get_mesh_quality","phase":"C"},
  {"arguments":{"path":"<PROJECT>"},"tool":"cst_save_project","phase":"C"},
  {"arguments":{},"tool":"cst_read_project_log","phase":"C"},
  {"arguments":{"solver_type":"Time Domain","timeout_s":"<SOLVER_TIMEOUT_S>"},"tool":"cst_run_simulation","phase":"D"},
  {"arguments":{"timeout_s":30},"tool":"cst_get_simulation_status","phase":"D"},
  {"arguments":{},"tool":"cst_read_project_log","phase":"D"},
  {"arguments":{},"tool":"cst_get_solver_info","phase":"D"},
  {"arguments":{},"tool":"cst_get_mesh_info","phase":"D"},
  {"arguments":{},"tool":"cst_get_mesh_quality","phase":"D"},
  {"arguments":{"path":"<PROJECT>"},"tool":"cst_save_project","phase":"D"},
  {"arguments":{},"tool":"cst_project_info","phase":"D"},
  {"arguments":{"tree_path":"1D Results"},"tool":"cst_list_results","phase":"D"},
  {"arguments":{"project_path":"<PROJECT>","contains":"","offset":0,"limit":50},"tool":"cst_list_saved_results","phase":"D"},
  {"arguments":{"tree_path":"2D/3D Results","max_depth":6},"tool":"cst_project_tree","phase":"D"},
  {"arguments":{},"tool":"cst_discover_farfield_monitors","phase":"D"},
  {"arguments":{"project_path":"<PROJECT>","tree_path":"<TREE_S11>","run_id":"<RUN_ID>","format":"real_imag","max_points":0},"tool":"cst_read_saved_result","phase":"E"},
  {"arguments":{"project_path":"<PROJECT>","tree_path":"<TREE_S11>","run_id":"<RUN_ID>","format":"db","max_points":0},"tool":"cst_read_saved_result","phase":"E"},
  {"arguments":{"port_in":1,"port_out":1,"format":"real_imag","max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_s_parameters","phase":"E"},
  {"arguments":{"port_in":1,"port_out":1,"format":"db","max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_s_parameters","phase":"E"},
  {"arguments":{"port_in":1,"port_out":1,"format":"mag","max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_s_parameters","phase":"E"},
  {"arguments":{"port_in":1,"port_out":1,"format":"phase","max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_s_parameters","phase":"E"},
  {"arguments":{"port_in":1,"port_out":1,"format":"db","max_points":200,"run_id":"<RUN_ID>"},"tool":"cst_get_s_parameters","phase":"E"},
  {"arguments":{"port_in":1,"port_out":1,"unwrap":false,"max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_s_parameter_phase","phase":"E"},
  {"arguments":{"port_in":1,"port_out":1,"unwrap":true,"max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_s_parameter_phase","phase":"E"},
  {"arguments":{"port":1,"max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_vswr","phase":"E"},
  {"arguments":{"port":1,"z0":50,"max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_smith_chart_data","phase":"E"},
  {"arguments":{"port":1},"tool":"cst_get_impedance","phase":"E"},
  {"arguments":{"port":1,"threshold_db":-10,"criterion":"S11","max_points":0,"run_id":"<RUN_ID>"},"tool":"cst_get_bandwidth","phase":"E"},
  {"arguments":{"port":1,"signal_type":"incident","port_out":1},"tool":"cst_get_time_domain_signal","phase":"E"},
  {"arguments":{"port":1,"signal_type":"reflected","port_out":1},"tool":"cst_get_time_domain_signal","phase":"E"},
  {"arguments":{"frequency_ghz":0.9,"try_farfield_plot":true},"tool":"cst_get_farfield_metrics","phase":"F"},
  {"arguments":{"frequency_ghz":1,"try_farfield_plot":true},"tool":"cst_get_farfield_metrics","phase":"F"},
  {"arguments":{"frequency_ghz":1.1,"try_farfield_plot":true},"tool":"cst_get_farfield_metrics","phase":"F"},
  {"arguments":{"frequency":1},"tool":"cst_get_gain","phase":"F"},
  {"arguments":{"frequency":1},"tool":"cst_get_efficiency","phase":"F"},
  {"arguments":{"frequency":1},"tool":"cst_get_efficiency_breakdown","phase":"F"},
  {"arguments":{"frequency":1,"monitor_name":"<FF_MONITOR_1>"},"tool":"cst_get_farfield","phase":"F"},
  {"arguments":{"frequency":1,"plane":"custom","phi_cut":0,"theta_cut":90},"tool":"cst_get_pattern_cut","phase":"F"},
  {"arguments":{"frequency":1,"resolution_deg":5,"coordinate":"spherical"},"tool":"cst_get_radiation_pattern_3d","phase":"F"},
  {"arguments":{"file_path":"<EXPORTS>/dipole.s1p","format":"s1p"},"tool":"cst_export_touchstone","phase":"F"},
  {"arguments":{"result_path":"<TREE_S11>","output_file":"<EXPORTS>/s11_native.csv","format":"csv"},"tool":"cst_export_result","phase":"F"},
  {"arguments":{"result_path":"<TREE_S11>","output_file":"<EXPORTS>/s11_native.txt","format":"txt"},"tool":"cst_export_result","phase":"F"},
  {"arguments":{"result_path":"<TREE_EFIELD_1>","output_file":"<EXPORTS>/efield_1GHz.txt","format":"txt"},"tool":"cst_export_result","phase":"F"},
  {"arguments":{"result_path":"<TREE_HFIELD_1>","output_file":"<EXPORTS>/hfield_1GHz.txt","format":"txt"},"tool":"cst_export_result","phase":"F"},
  {"arguments":{"result_path":"<TREE_SURFACE_CURRENT_1>","output_file":"<EXPORTS>/surface_current_1GHz.txt","format":"txt"},"tool":"cst_export_result","phase":"F"},
  {"arguments":{"file_path":"<EXPORTS>/farfield_realized_gain_1GHz.csv","frequency":1,"format":"csv","monitor_name":"<FF_MONITOR_1>"},"tool":"cst_export_farfield","phase":"F"},
  {"arguments":{"file_path":"<EXPORTS>/dipole_1GHz.ffs","frequency":1,"format":"ffs","monitor_name":"<FF_MONITOR_1>"},"tool":"cst_export_farfield","phase":"F"},
  {"arguments":{"project_path":"<PROJECT>","frequencies_ghz":[0.9,1,1.1],"excitation":"Excitation [1]","run_id":"<RUN_ID>","threshold":0.03,"farfield_grids":[{"frequency_ghz":1,"data_file":"<REALIZED_GRID>"}]},"tool":"cst_check_power_balance","phase":"F"},
  {"arguments":{"project_path":"<PROJECT>","quantities":["s11_db","vswr","smith","impedance","phase","efficiency"],"run_ids":["<RUN_ID>"],"max_runs":1,"freq_range_ghz":[0.8,1.2],"threshold_db":-10,"mark_resonance":true,"formats":["pdf","png"],"width":"single","out_dir":"<FIGURES>/one_d","title":"Thin copper dipole","simulate_if_missing":false},"tool":"cst_plot_1d_results","phase":"G"},
  {"arguments":{"project_path":"<PROJECT>","frequency_ghz":1,"quantity":"realized_gain","polarization_basis":"spherical","step_deg":5,"cuts":["phi=0","theta=90"],"plots":["polar","rect","heatmap","3d"],"heatmap_projection":"theta_phi","dynamic_range_db":30,"formats":["pdf","png"],"width":"double","out_dir":"<FIGURES>/farfield_realized_gain","file_stem":"dipole_realized_gain_1GHz"},"tool":"cst_plot_farfield","phase":"G"},
  {"arguments":{"project_path":"<PROJECT>","frequency_ghz":1,"quantity":"directivity","polarization_basis":"spherical","step_deg":5,"cuts":["phi=0","theta=90"],"plots":["polar","rect","heatmap"],"heatmap_projection":"theta_phi","dynamic_range_db":30,"formats":["pdf","png"],"width":"double","out_dir":"<FIGURES>/farfield_directivity","file_stem":"dipole_directivity_1GHz"},"tool":"cst_plot_farfield","phase":"G"},
  {"arguments":{"data_file":"<REALIZED_GRID>","quantity":"realized_gain","polarization_basis":"spherical","cuts":["phi=0","theta=90"],"plots":["polar","rect"],"dynamic_range_db":30,"formats":["pdf","png"],"width":"double","out_dir":"<FIGURES>/farfield_from_file","file_stem":"dipole_realized_gain_from_file"},"tool":"cst_plot_farfield","phase":"G"},
  {"arguments":{"frequency":1.05},"tool":"cst_get_farfield","phase":"H"},
  {"arguments":{"frequency":1,"resolution_deg":5,"coordinate":"cartesian"},"tool":"cst_get_radiation_pattern_3d","phase":"H"},
  {"arguments":{"file_path":"<EXPORTS>/unsupported_1GHz.nsf","frequency":1,"format":"nsf","monitor_name":"<FF_MONITOR_1>"},"tool":"cst_export_farfield","phase":"H"},
  {"arguments":{"project_path":"<PROJECT>","tree_path":"1D Results\\S-Parameters\\S99,99","run_id":"<RUN_ID>","format":"real_imag","max_points":0},"tool":"cst_read_saved_result","phase":"H"},
  {"arguments":{"frequency":1,"definition":"Ludwig3"},"tool":"cst_get_cross_polarization","phase":"H"},
  {"arguments":{},"tool":"cst_read_project_log","phase":"I"},
  {"arguments":{"path":"<PROJECT>"},"tool":"cst_save_project","phase":"I"},
  {"arguments":{},"tool":"cst_close_project","phase":"I"},
  {"arguments":{},"tool":"cst_connection_status","phase":"I"},
  {"arguments":{"project_path":"<PROJECT>","contains":"S-Parameters","offset":0,"limit":50},"tool":"cst_list_saved_results","phase":"I"},
  {"arguments":{"project_path":"<PROJECT>","tree_path":"<TREE_S11>","run_id":"<RUN_ID>","format":"real_imag","max_points":0},"tool":"cst_read_saved_result","phase":"I"},
  {"arguments":{"path":"<PROJECT>"},"tool":"cst_open_project","phase":"I"},
  {"arguments":{"code":"Debug.Print \"Length\" & vbTab & Units.GetUnit(\"Length\")\nDebug.Print \"Frequency\" & vbTab & Units.GetUnit(\"Frequency\")\nDebug.Print \"Time\" & vbTab & Units.GetUnit(\"Time\")"},"tool":"cst_execute_vba","phase":"I"},
  {"arguments":{"code":"Dim n As Long, i As Long\nDim fullName As String\nn = Solid.GetNumberOfShapes()\nDebug.Print \"COUNT\" & vbTab & CStr(n)\nFor i = 0 To n - 1\n  fullName = Solid.GetNameOfShapeFromIndex(i)\n  Debug.Print \"SOLID\" & vbTab & CStr(i) & vbTab & fullName & vbTab & Solid.GetMaterialNameForShape(fullName)\nNext i\nDebug.Print \"DONE\""},"tool":"cst_execute_vba","phase":"I"},
  {"arguments":{},"tool":"cst_list_ports","phase":"I"},
  {"arguments":{},"tool":"cst_project_info","phase":"I"},
  {"arguments":{},"tool":"cst_connection_status","phase":"I"}
]''')

PHASES = {
    "A": "Connect, create project and verify units",
    "B": "Load library copper and construct arms and port",
    "C": "Configure domain, mesh, solver and monitors",
    "D": "One blocking simulation and readiness",
    "E": "Full 1D curves and consistency",
    "F": "Radiation metrics and numerical exports",
    "G": "MCP figures and source validation",
    "H": "Read-only capability-limit probes",
    "I": "Saved-result persistence and reopen",
}
MANDATORY = {
    "native construction", "native solve idle", "full S11", "Touchstone consistency",
    "radiation source quantities", "directivity peak", "meridional HPBW",
    "broadside maximum", "axial nulls", "azimuth ripple", "normalized meridional shape",
    "S11 persistence",
    "power conservation", "efficiency definitions and identities",
    "full S11 derived consistency",
}


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("Timeout must be positive and finite")
    return value


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="Disabled real-server catalog verification only")
    parser.add_argument("--cst-path", default=None, help=f"Selected installation. Default discovers CST 2025, preferring {DEFAULT_CST_PATH}")
    parser.add_argument("--cst-version", default="2025")
    parser.add_argument("--solver-timeout-s", type=positive, default=1800)
    return parser.parse_args(argv)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


class Dipole:
    def __init__(self, args, parent=BATCH):
        self.args, self.parent = args, parent.resolve()
        self.base = self.parent / ("preflight/dipole" if args.preflight else "dipole")
        self.results, self.reports = self.base / "results", self.base / "reports"
        self.project = self.base / "project/dipole.cst"
        self.catalog, self.records, self.checks, self.phases = {}, [], [], []
        self.logs, self.inventory, self.responses = [], [], []
        self.bindings, self.entries, self.tree_items = {}, [], []
        self.phase, self.unknown, self.connected, self.idle = "preparation", False, False, False
        self.seq, self.exit_code, self.reason, self.prepared = 0, 2, "Assessment incomplete", False
        self.active_manifest_index = None
        self.closed_verified = False
        self.project_closed = False
        self.baseline, self.realized, self.directivity = None, None, None
        self.baseline_source = None
        self.metadata = {
            "started_utc": timestamp(), "mode": "preflight" if args.preflight else "live",
            "output_directory": str(self.base), "python": sys.version, "executable": sys.executable,
            "model": {"axis": "z", "endpoints_mm": [-74.95, 74.95], "gap_mm": [-0.15, 0.15],
                      "arm_length_mm": 74.8, "radius_mm": 0.15, "library": "Copper (annealed)",
                      "alias": "DipoleCopper", "port_ohm": 50, "frequency_ghz": [0.8, 1.2],
                      "units": EXPECTED_UNITS, "solver_accuracy_db": -40, "adaptive_mesh": False,
                      "mesh_cells_per_wavelength": 20, "refinement_factor": 2},
            "transport_policy": "SDK stdio framing with established server-only process lifecycle hooks",
            "live_phases_executed": False, "solver_timeout_s": args.solver_timeout_s,
            "limitations": [
                "One simulation supplies no mesh convergence evidence",
                "Global cells per wavelength and factor-two arm refinement do not prove feed-gap resolution",
                "Setup diagnostic validates supplied settings, not independent native application",
                "Material file provenance does not independently read project conductivity",
                "Project log snapshots may be tails with partial or unknown coverage",
                "Figure semantics need visual inspection of the produced plots and their axes and legends",
                "FFS reuse as a CST excitation remains untested",
            ],
        }

    def check(self, name, verdict, expected=None, obtained=None, tolerance=None, evidence=None, reason=None, mandatory=False):
        if isinstance(verdict, bool):
            verdict = "passed" if verdict else "failed"
        self.checks.append({"name": name, "verdict": verdict, "phase": self.phase,
                            "expected": expected, "obtained": obtained, "tolerance": tolerance,
                            "evidence": evidence, "reason": reason, "mandatory": mandatory or name in MANDATORY})
        return verdict == "passed"

    def require(self, name, condition, **kwargs):
        if not self.check(name, bool(condition), **kwargs):
            raise Stop(name)

    def persist(self):
        self.metadata.update(ended_utc=timestamp(), phase_timings=self.phases, project_path=str(self.project),
                             state_unknown=self.unknown)
        write_json(self.results / "metadata.json", self.metadata)
        write_json(self.results / "validation.json", {
            "mode": self.metadata["mode"], "exit_code": self.exit_code, "outcome": self.reason,
            "checks": self.checks, "calls": self.records, "phases": self.phases,
            "project_logs": self.logs, "artifact_inventory": self.inventory,
        })
        self.report()

    def report(self):
        def cell(value):
            return str(safe_json(value)).replace("|", "\\|").replace("\n", " ") if value is not None else "pending"
        command = "uv run --extra figures capabilities_test\\04_antenna_simulation\\run_dipole.py"
        if self.args.preflight:
            command += " --preflight"
        command += f' --cst-path "{self.metadata.get("installation", {}).get("path", self.args.cst_path or DEFAULT_CST_PATH)}" --cst-version {self.args.cst_version}'
        if not self.args.preflight:
            command += f" --solver-timeout-s {self.args.solver_timeout_s:g}"
        text = ["# Thin copper dipole validation", "", f"Outcome: {self.reason}. Exit code {self.exit_code}.", "",
                f"UTC invocation: {self.metadata['started_utc']}. Mode: {self.metadata['mode']}.", "",
                f"Repository: {cell(self.metadata.get('repository'))}", "",
                f"Python and packages: {cell(self.metadata.get('packages'))}. {cell(self.metadata['python'])}", "",
                f"Installation: {cell(self.metadata.get('installation', {}).get('path'))}. Selected version: {self.args.cst_version}.", "",
                f"Import compatibility: {cell(self.metadata.get('installation', {}).get('probe', {}).get('modules'))}. Full evidence: results/protocol/import_probe.json.", "",
                f"Native CST version evidence: {cell(self.metadata.get('native_version'))}", "",
                "## Exact fixed model", "",
                "Z-oriented vacuum dipole. Each arm has radius 0.15 mm and metallic length 74.80 mm. Negative arm spans -74.95 to -0.15 mm. Positive arm spans 0.15 to 74.95 mm. The 0.30 mm gap is included in the 149.90 mm outer length.", "",
                "The specified material is native library Copper (annealed) with project alias DipoleCopper. The specified port is one 50 ohm discrete S-parameter port across the gap. Expected units are mm, GHz and ns. The specified frequency range is 0.8 to 1.2 GHz. Applied-state evidence is assessed separately below.", "",
                "The specified time-domain solver has -40 dB accuracy, Gaussian excitation and fixed 50 ohm normalization. The specified hexahedral mesh has 20 cells per wavelength, minimum 5 cells and ratio limit 20. Both arms have planned local factor-two refinement. Adaptive meshing is disabled. All six planned boundaries are expanded open and all symmetry planes are none.", "",
                "Finite-conductivity copper can have loss. No exact 1 GHz resonance, 73+j0 ohm impedance, or -10 dB matching requirement is imposed.", "",
                "## Verdicts", "", "| Outcome | Count |", "| --- | --- |"]
        for verdict in ("passed", "failed", "unsupported", "inconclusive", "blocked", "not_executed"):
            text.append(f"| {verdict} | {sum(c['verdict'] == verdict for c in self.checks)} |")
        text += ["", "| Check | Outcome | Reason and evidence |", "| --- | --- | --- |"]
        text += [f"| {cell(c['name'])} | {c['verdict']} | {cell(c.get('reason'))}. {cell(c.get('evidence'))} |" for c in self.checks]
        text += ["", "## Chronological phase summary", "", "| Phase | UTC start | Duration (s) | Outcome |", "| --- | --- | --- | --- |"]
        text += [f"| {cell(p['phase'])} | {p['started_utc']} | {p['elapsed_s']:.3f} | {p['outcome']} |" for p in self.phases]
        text += ["", "## CST-native messages", "",
                 "Snapshots preserve original JSON and native text. Coverage is partial or unknown. Server stderr and client exceptions are separate protocol evidence. Snapshots do not prove perfect per-call causality.", ""]
        for log in self.logs:
            text += [f"### {log['phase']} at {log['captured_utc']}", "",
                     f"Source: {log['source']}. Coverage: {log['coverage']}. Snapshot: {log['json']}.", "",
                     "```text", log["text"].replace("```", "'''"), "```", ""]
        if not self.logs:
            text += ["No native messages captured. Live operations are not executed in preflight.", ""]
        text += ["## Numerical comparisons", "",
                 "Initial physical criteria apply to this fixed model. They are not manufacturer specifications or convergence evidence.", "",
                 "| Check | Expected | Obtained | Tolerance | Outcome | Evidence |", "| --- | --- | --- | --- | --- | --- |"]
        text += [f"| {cell(c['name'])} | {cell(c['expected'])} | {cell(c['obtained'])} | {cell(c['tolerance'])} | {c['verdict']} | {cell(c['evidence'])} |" for c in self.checks if c["expected"] is not None]
        text += ["", "## Export and figure inventory", "", "| File | Format and quantity | Units and samples | Readability and freshness | SHA-256 |", "| --- | --- | --- | --- | --- |"]
        text += [f"| {cell(i['archived'])} | {cell(i.get('inspection'))} | {cell(i.get('units'))}, {cell(i.get('sample_count'))} | bytes={i['bytes']}, readable={cell(i.get('readable'))}, fresh={i['fresh']} | {i['sha256']} |" for i in self.inventory]
        text += ["", "## Limitations and pending checks", ""]
        text += [f"- {s}" for s in self.metadata["limitations"]]
        text += ["", "## Reproducible command", "", "```powershell", command, "```", ""]
        atomic_text(self.reports / "validation.md", "\n".join(text))

    def resolve(self, template, representative=False):
        bindings = {
            "PROJECT": str(self.project), "EXPORTS": str(self.results / "exports"),
            "FIGURES": str(self.results / "figures"), "SOLVER_TIMEOUT_S": self.args.solver_timeout_s,
        }
        if representative:
            bindings.update(RUN_ID=0, TREE_S11=r"1D Results\S-Parameters\S1,1",
                            TREE_EFIELD_1=r"2D/3D Results\E-Field\e-field (f=1) [1]",
                            TREE_HFIELD_1=r"2D/3D Results\H-Field\h-field (f=1) [1]",
                            TREE_SURFACE_CURRENT_1=r"2D/3D Results\Surface Current\h-field (f=1) [1]",
                            FF_MONITOR_1="farfield (f=1) [1]", REALIZED_GRID=str(self.results / "exports/representative_realized.txt"))
        else:
            bindings.update(self.bindings)
        def visit(value):
            if isinstance(value, dict):
                return {k: visit(v) for k, v in value.items()}
            if isinstance(value, list):
                return [visit(v) for v in value]
            if isinstance(value, str):
                if re.fullmatch(r"<[A-Z0-9_]+>", value):
                    key = value[1:-1]
                    if key not in bindings:
                        raise Incomplete(f"Placeholder {value} lacks discovery evidence")
                    return bindings[key]
                for key in re.findall(r"<([A-Z0-9_]+)>", value):
                    if key not in bindings:
                        raise Incomplete(f"Placeholder <{key}> lacks discovery evidence")
                    value = value.replace(f"<{key}>", str(bindings[key]))
            return value
        return visit(template)

    def validate(self, tool, arguments):
        if tool not in self.catalog:
            raise Stop(f"Runtime catalog lacks {tool}")
        schema = self.catalog[tool]["inputSchema"]
        validator_for(schema).check_schema(schema)
        validator_for(schema)(schema).validate(arguments)
        # Some handlers reject additional properties even if the schema omits the keyword.
        extra = set(arguments) - set(schema.get("properties", {}))
        if extra:
            raise Stop(f"{tool}: undocumented arguments {sorted(extra)}")
        if tool == "cst_execute_vba" and arguments.get("code") not in FIXED_VBA:
            raise Stop("Only the three fixed setup/read-only VBA blocks are allowed")

    def configure_environment(self):
        self.phase = "installation"
        install_start, install_utc = time.monotonic(), timestamp()
        selected = Path(self.args.cst_path) if self.args.cst_path else None
        if selected is None:
            preferred = Path(DEFAULT_CST_PATH.replace("2025", self.args.cst_version))
            candidates = [preferred]
            for drive in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                for folder in ("Program Files (x86)", "Program Files", ""):
                    candidates.append(Path(f"{drive}:/") / folder / f"CST Studio Suite {self.args.cst_version}")
            selected = next((p for p in candidates if p.is_dir()), preferred)
        selected = selected.resolve()
        libs = next((selected / p for p in ("AMD64/python_cst_libraries", "LinuxAMD64/python_cst_libraries", "python_cst_libraries")
                     if (selected / p / "cst").is_dir()), selected / "AMD64/python_cst_libraries")
        self.env = dict(os.environ)
        self.env.update(CST_CONNECT_MODE="disabled" if self.args.preflight else "manual",
                        CST_PATH=str(selected), CST_VERSION=self.args.cst_version,
                        CST_WORK_DIR=str(self.results / "work"), CST_TOOLSETS=TOOLSETS,
                        CST_ALLOW_RAW_VBA="1", PYTHONPATH=os.pathsep.join(
                            [str(ROOT / "src"), str(libs), self.env.get("PYTHONPATH", "")]))
        probe_script = '''import importlib, json, pathlib, sys, traceback
out = {"python": sys.version, "instance_created": False, "modules": {}}
for name in ("cst.interface", "cst.results", "numpy", "matplotlib", "matplotlib.pyplot"):
    try:
        module = importlib.import_module(name)
        out["modules"][name] = {"import_succeeded": True, "origin": getattr(module, "__file__", None), "version": getattr(module, "__version__", None)}
    except Exception as exc:
        out["modules"][name] = {"import_succeeded": False, "exception": str(exc), "traceback": traceback.format_exc()}
print(json.dumps(out))
'''
        info = {"path": str(selected), "version_selected": self.args.cst_version, "libraries": str(libs),
                "found": selected.is_dir(), "library_found": libs.is_dir()}
        try:
            result = subprocess.run([sys.executable, "-c", probe_script], cwd=ROOT, env=self.env,
                                    capture_output=True, text=True, timeout=60)
            info["probe"] = json.loads(result.stdout.splitlines()[-1])
            info.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except Exception as exc:
            info["probe_exception"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        modules = info.get("probe", {}).get("modules", {})
        self.check("Python ABI", sys.version_info[:2] == (3, 12), expected="Python 3.12", obtained=sys.version)
        version_resource = executable_version(selected / "CST DESIGN ENVIRONMENT.exe")
        info["executable_version_resource"] = version_resource
        actual_version = str(version_resource["product_version"][0]) if version_resource else None
        self.check("selected installation version", actual_version == self.args.cst_version,
                   expected=self.args.cst_version, obtained=version_resource,
                   reason="Native executable version resource is read without launching the application")
        for name in ("cst.interface", "cst.results", "numpy", "matplotlib.pyplot"):
            module = modules.get(name, {})
            compatible = module.get("import_succeeded", False)
            if name.startswith("cst.") and compatible:
                compatible = Path(module.get("origin") or "").resolve().is_relative_to(libs)
            self.check(f"Import {name}", compatible, expected="selected libraries import in server interpreter", obtained=module)
        material = selected / "Library/Materials/Copper (annealed).mtd"
        if material.is_file():
            raw = read_text(material)
            info["copper_provenance"] = {"path": str(material), "sha256": sha256(material),
                                         "text": raw, "source_only": True, "mcp_load_test_executed": False}
        self.metadata["installation"] = info
        self.metadata["child_environment"] = {k: self.env[k] for k in (
            "CST_CONNECT_MODE", "CST_PATH", "CST_VERSION", "CST_WORK_DIR", "CST_TOOLSETS", "CST_ALLOW_RAW_VBA", "PYTHONPATH")}
        packages = {}
        for name in ("mcp", "jsonschema", "numpy", "matplotlib", "cst-studio-mcp"):
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = "unavailable"
        self.metadata["packages"] = packages
        self.metadata["sdk_compatibility"] = {
            "version": packages["mcp"], "pagination": "params on SDK 2, cursor on SDK 1",
            "request_timeout": "numeric seconds on SDK 2, timedelta on SDK 1",
            "solver_request_budget_s": self.args.solver_timeout_s + 120,
        }
        def git(*args):
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        self.metadata["repository"] = {"revision": git("rev-parse", "HEAD"), "dirty_state": git("status", "--short")}
        write_json(self.results / "protocol/import_probe.json", info)
        self.phases.append({"phase": "installation", "started_utc": install_utc,
                            "elapsed_s": time.monotonic() - install_start, "outcome": "passed" if not any(
                                c["phase"] == "installation" and c["verdict"] == "failed" for c in self.checks) else "failed"})
        self.persist()

    async def protocol(self, session):
        self.phase = "protocol"
        start = time.monotonic()
        utc = timestamp()
        init = await asyncio.wait_for(session.initialize(), 120)
        write_json(self.results / "protocol/initialize.json", {"started_utc": utc, "ended_utc": timestamp(),
                   "elapsed_s": time.monotonic() - start, "request": {"method": "initialize"}, "response": serialize(init)})
        self.metadata["server_initialization"] = serialize(init)
        cursor, seen, number = None, set(), 0
        while True:
            page_start, page_utc = time.monotonic(), timestamp()
            if "params" in inspect.signature(session.list_tools).parameters:
                request = session.list_tools(params=PaginatedRequestParams(cursor=cursor))
            else:
                request = session.list_tools() if cursor is None else session.list_tools(cursor=cursor)
            page = await asyncio.wait_for(request, 120)
            raw = serialize(page)
            number += 1
            write_json(self.results / f"protocol/catalog_{number:03d}.json", {"cursor": cursor, "response": raw,
                       "started_utc": page_utc, "ended_utc": timestamp(), "elapsed_s": time.monotonic() - page_start,
                       "request": {"method": "tools/list", "params": {} if cursor is None else {"cursor": cursor}}})
            for tool in raw.get("tools", []):
                if tool["name"] in self.catalog:
                    raise Stop(f"Duplicate catalog tool {tool['name']}")
                self.catalog[tool["name"]] = tool
            cursor = raw.get("nextCursor")
            if cursor is None:
                break
            if cursor in seen:
                raise Stop("Catalog cursor repeated")
            seen.add(cursor)
        write_json(self.results / "protocol/schemas.json", self.catalog)
        planned, mismatches = [], []
        for index, item in enumerate(MANIFEST, 1):
            arguments = self.resolve(item["arguments"], representative=True)
            try:
                self.validate(item["tool"], arguments)
                outcome = "passed"
            except Exception as exc:
                outcome = "failed"
                mismatches.append({"manifest_index": index, "tool": item["tool"], "arguments": arguments,
                                   "discrepancy": str(exc)})
            planned.append({"manifest_index": index, **item, "representative_arguments": arguments,
                            "schema_validation": outcome, "execution": "not_executed"})
        write_json(self.results / "protocol/planned_manifest.json", planned)
        self.metadata["schema_discrepancies"] = mismatches
        self.check("complete planned manifest schemas", not mismatches, expected=f"all {len(MANIFEST)} calls", obtained=mismatches)
        self.phases.append({"phase": "protocol", "started_utc": self.metadata["started_utc"],
                            "elapsed_s": time.monotonic() - start, "outcome": "failed" if mismatches else "passed"})
        if self.args.preflight:
            state = await self.request(session, "cst_connection_status", {})
            self.require("disabled connection status", state.get("mode") == "offline" and state.get("project_open") is False)
            for phase, name in PHASES.items():
                self.check(f"{phase}: {name}", "not_executed", reason="Preflight permits connection status only")
            for name in sorted(MANDATORY):
                self.check(name, "not_executed", reason="Future user live execution required")
            if mismatches or any(c["verdict"] == "failed" for c in self.checks):
                raise Stop("Preflight schema or installation checks failed")
        elif mismatches or any(c["verdict"] == "failed" for c in self.checks):
            raise Stop("Schema or installation prerequisites failed before connecting")

    def file_state(self):
        return {str(p.resolve()): (p.stat().st_mtime_ns, p.stat().st_size, sha256(p))
                for area in ("exports", "figures", "work") for p in (self.results / area).rglob("*") if p.is_file()}

    def archive(self, raw, before, call_id):
        artifacts = []
        for value in sorted(set(strings(raw))):
            if "\n" in value or len(value) > 1024:
                continue
            try:
                path = Path(value)
                if not path.is_absolute() or not path.is_file():
                    continue
                reject_links(path)
                path = path.resolve()
                if not path.is_relative_to(self.results.resolve()):
                    continue
                if path.suffix.lower() not in {".txt", ".csv", ".s1p", ".ffs", ".png", ".pdf", ".json", ".nsf"}:
                    continue
                state = (path.stat().st_mtime_ns, path.stat().st_size, sha256(path))
                fresh = before.get(str(path)) != state
                dest = path if path.is_relative_to((self.results / "figures").resolve()) else (
                    self.results / "exports" / f"{call_id:04d}_{path.name}")
                if dest != path:
                    shutil.copy2(path, dest)
                entry = {"call_id": call_id, "phase": self.phase, "original": str(path), "archived": str(dest),
                         "sha256": sha256(dest), "bytes": dest.stat().st_size, "fresh": fresh,
                         "captured_utc": timestamp(), "readable": None, "inspection": "pending"}
                self.inventory.append(entry)
                artifacts.append(entry)
            except (OSError, ValueError) as exc:
                self.check("archive returned file", "inconclusive", obtained=value, reason=str(exc))
        return artifacts

    async def request(self, session, tool, arguments, probe=False):
        if self.unknown:
            raise Incomplete("Native state unknown. All subsequent CST operations blocked")
        if self.args.preflight and tool != "cst_connection_status":
            raise Stop(f"Preflight forbids {tool}")
        self.validate(tool, arguments)
        before = self.file_state()
        self.seq += 1
        call_id = self.seq
        request = {"id": call_id, "tool": tool, "arguments": arguments}
        with (self.reports / "calls.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(request, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        record = {"request": request, "phase": self.phase, "manifest_index": self.active_manifest_index, "started_utc": timestamp(),
                  "protocol_success": False, "tool_execution": "unknown", "native_state": "unverified",
                  "artifacts": [], "project_log_snapshot_ids": [l["call_id"] for l in self.logs]}
        path = self.results / f"mcp/{call_id:04d}_{tool}.json"
        record["evidence"] = str(path)
        start = time.monotonic()
        payload = {}
        try:
            budget = self.args.solver_timeout_s + 120 if tool == "cst_run_simulation" else 120
            sdk_major = int(importlib.metadata.version("mcp").split(".")[0])
            timeout_argument = budget if sdk_major >= 2 else timedelta(seconds=budget)
            response = await session.call_tool(tool, arguments, read_timeout_seconds=timeout_argument)
            raw = serialize(response)
            decoded = interpret(raw)
            payload = decoded["payload"] or {}
            record.update(response=raw, parsed_text_json=[p for p in decoded["parsed_payloads"] if p["source"] != "structuredContent"],
                          structuredContent=raw.get("structuredContent"), protocol_success=True,
                          isError=decoded["isError"], tool_status=payload.get("status"), tool_message=payload.get("message"),
                          payload=payload)
            if indeterminate(raw, decoded) or payload.get("status") == "busy" or (
                "running" in payload and payload["running"] is None
            ) or (tool == "cst_get_simulation_status" and payload.get("running") is True):
                self.unknown = True
                raise Incomplete("Native timeout, busy state or indeterminate response")
            rejected = decoded["isError"] or payload.get("status") in {"error", "failed", "failure", "offline", "no_results", "unavailable", "unsupported", "partial"}
            if self.args.preflight and tool == "cst_connection_status":
                rejected = decoded["isError"]
            record["tool_execution"] = "rejected" if rejected else "accepted"
            record["artifacts"] = self.archive(raw, before, call_id)
            self.responses.append({"id": call_id, "phase": self.phase, "tool": tool, "arguments": arguments,
                                   "payload": payload, "artifacts": record["artifacts"], "evidence": str(path), "rejected": rejected})
            if tool == "cst_read_project_log":
                self.snapshot_log(payload, call_id)
            if rejected:
                msg = str(payload.get("message", ""))
                unsupported = bool(re.search(r"not implemented|unsupported|dedicated.*export|not supported", msg, re.I))
                verdict = "unsupported" if unsupported else "failed"
                if probe:
                    expected_rejection = self.probe_rejection(tool, arguments, msg)
                    self.check(f"probe {call_id}: {tool} rejection contract", "passed" if expected_rejection else "failed", obtained=payload,
                               evidence=str(path), reason="Rejection must describe the requested capability or absent result")
                    self.check(f"probe {call_id}: {tool} feature support", "unsupported" if unsupported else "inconclusive",
                               reason=msg, evidence=str(path))
                    return payload
                mandatory = tool in {"cst_export_touchstone", "cst_run_simulation"} or (
                    tool == "cst_read_saved_result" and arguments.get("format") == "real_imag")
                self.check(f"call {call_id}: {tool}", verdict, reason=msg or str(payload), evidence=str(path), mandatory=mandatory)
                if tool in {"cst_read_saved_result", "cst_get_impedance", "cst_export_result"} and re.search(r"not found|missing|does not exist|no result", msg, re.I):
                    self.check(f"native prerequisite {call_id}", "inconclusive", reason="Inspect discovery and exact native prerequisite", evidence=str(path))
                raise Stop(f"{tool} rejected: {msg}")
            self.check(f"call {call_id}: {tool}", "passed", reason="Protocol delivered and handler accepted. Native and numerical checks are separate", evidence=str(path))
            return payload
        except BaseException as exc:
            if not isinstance(exc, (Stop, Incomplete)):
                self.unknown = True
            record["exception"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
            raise
        finally:
            record.update(ended_utc=timestamp(), elapsed_s=time.monotonic() - start,
                          native_state="unknown" if self.unknown else "not independently verified")
            write_json(path, record)
            self.records.append({k: v for k, v in record.items() if k not in {"response", "payload", "parsed_text_json", "structuredContent"}})

    def snapshot_log(self, payload, call_id):
        json_path = self.results / f"cst_logs/{call_id:04d}_{self.phase}.json"
        write_json(json_path, payload)
        native = []
        for obj in walk_dicts(payload):
            for key in ("content", "text", "tail", "log_content", "log_text", "output"):
                if isinstance(obj.get(key), str):
                    native.append(obj[key])
        text = "\n".join(native)
        source_files = []
        for obj in walk_dicts(payload):
            value = obj.get("path") or obj.get("log_file")
            if isinstance(value, str) and Path(value).is_absolute():
                source = Path(value)
                if source.is_file() and source.resolve().is_relative_to((self.base / "project").resolve()):
                    reject_links(source)
                    dest = self.results / "cst_logs" / f"{call_id:04d}_original_{source.name}"
                    shutil.copy2(source, dest)
                    source_files.append({"original": str(source), "snapshot": str(dest), "sha256": sha256(dest),
                                         "coverage": "original file at capture time, message-history completeness unknown"})
        text_path = json_path.with_suffix(".txt")
        atomic_text(text_path, text)
        self.logs.append({"call_id": call_id, "phase": self.phase, "captured_utc": timestamp(),
                          "source": "cst_read_project_log native payload", "coverage": "partial or unknown",
                          "json": str(json_path), "native_text": str(text_path), "text": text, "original_files": source_files})

    @staticmethod
    def probe_rejection(tool, arguments, message):
        patterns = {
            "cst_get_farfield": r"1\.05|not found|no.*farfield|missing.*monitor|no.*result",
            "cst_get_radiation_pattern_3d": r"cartesian|spherical|resampl",
            "cst_export_farfield": r"nsf|not supported|unsupported|not implemented",
            "cst_read_saved_result": r"S99|not found|no.*result|does not exist|missing",
            "cst_get_cross_polarization": r"dedicated.*export|cross.*polar|not implemented",
        }
        return bool(re.search(patterns.get(tool, r"(?!)"), message, re.I))

    async def diagnose(self, session):
        if self.connected and not self.unknown and not self.args.preflight:
            old_index = self.active_manifest_index
            self.active_manifest_index = None
            for tool, arguments in (("cst_read_project_log", {}), ("cst_get_simulation_status", {"timeout_s": 30})):
                try:
                    await self.request(session, tool, arguments)
                except (Stop, Incomplete):
                    if self.unknown:
                        break
            self.active_manifest_index = old_index

    async def saved_pages(self, session, arguments, first):
        entries = list(first.get("entries", []))
        offset = first.get("next_offset")
        previous = arguments["offset"]
        while offset is not None:
            if not isinstance(offset, int) or offset <= previous:
                raise Stop("Saved-results pagination did not increase offset")
            page = await self.request(session, "cst_list_saved_results", {**arguments, "offset": offset})
            entries.extend(page.get("entries", []))
            previous, offset = offset, page.get("next_offset")
        return entries

    def discover(self):
        matches = [e for e in self.entries if e.get("tree_path", "").replace("/", "\\").lower().endswith("s-parameters\\s1,1")]
        if len(matches) == 1:
            entry = matches[0]
            runs = entry.get("run_ids", [])
            if len(runs) == 1 and isinstance(runs[0], int):
                self.bindings.update(TREE_S11=entry["tree_path"], RUN_ID=runs[0])
            else:
                self.check("single native result run", "inconclusive", obtained=runs,
                           reason="Single unswept solve needs an unambiguous integer result run")
        paths = sorted(set(self.tree_items + [e.get("tree_path", "") for e in self.entries]))
        for key, category in (("TREE_EFIELD_1", "e-field"), ("TREE_HFIELD_1", "h-field"), ("TREE_SURFACE_CURRENT_1", "surface current")):
            candidates = [p for p in paths if category in p.lower() and re.search(r"\(f\s*=\s*1(?:\.0*)?\)", p, re.I)]
            if key == "TREE_HFIELD_1":
                candidates = [p for p in candidates if "surface current" not in p.lower()]
            roots = [p for p in candidates if re.search(r"\(f\s*=\s*1(?:\.0*)?\)(?:\s*\[1\])?$", p, re.I)]
            if len(roots) == 1:
                candidates = roots
            if len(candidates) == 1:
                self.bindings[key] = candidates[0]
            elif len(candidates) != 1:
                self.check(f"discovery {key}", "inconclusive", obtained=candidates,
                           evidence="results/mcp discovery responses", reason="Absent or ambiguous exact field item")

    def verify_native(self, tool, arguments, payload):
        if tool == "cst_connect":
            self.connected = payload.get("status") == "connected"
            before = payload.get("running_des_before")
            if not isinstance(before, list) or payload.get("de_pid") is None or "newly_started" not in payload:
                raise Incomplete("Connection response lacks new-instance provenance")
            self.require("new isolated CST instance", self.connected and payload.get("newly_started") is True
                         and payload.get("de_pid") not in before and not payload.get("open_project_paths"), obtained=payload)
        elif tool == "cst_create_project":
            value = payload.get("path")
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise Incomplete("Create-project response lacks actual absolute saved path")
            actual = Path(value).resolve()
            reject_links(actual)
            self.require("owned fresh native project", actual.parent == (self.base / "project").resolve()
                         and actual.suffix.lower() == ".cst" and actual.is_file(), obtained=str(actual))
            self.project = actual
        elif tool == "cst_project_info":
            self.require("native project ownership", payload.get("mode") == "connected" and payload.get("project_open") is True
                         and Path(payload.get("project_path") or "").resolve() == self.project.resolve(), obtained=payload)
            self.metadata["native_version"] = payload.get("cst_version")
        elif tool == "cst_connection_status":
            if self.phase == "A" and not self.connected:
                self.require("initial server does not adopt CST", payload.get("mode") == "offline" and not payload.get("project_open"))
            elif self.connected:
                self.require("connection remains isolated and idle", payload.get("mode") == "connected"
                             and payload.get("solver_running") is False, obtained=payload)
                if self.phase == "I" and self.project_closed:
                    self.require("project independently closed", payload.get("project_open") is False and not payload.get("project_path"), obtained=payload)
                    self.closed_verified = True
                    self.check("saved read without active project", "passed", obtained=payload)
        elif tool == "cst_close_project":
            self.project_closed = True
        elif tool == "cst_open_project":
            self.project_closed = False
        elif tool == "cst_execute_vba" and arguments["code"] == UNITS_QUERY:
            units = parse_units(payload.get("output", ""))
            self.require("native units readback", units == EXPECTED_UNITS, expected=EXPECTED_UNITS, obtained=units)
        elif tool == "cst_execute_vba" and arguments["code"] == SHAPES_QUERY:
            shapes = parse_shapes(payload.get("output", ""))
            expected = {"Dipole:ArmNegative": "DipoleCopper", "Dipole:ArmPositive": "DipoleCopper"}
            self.require("native construction", shapes == expected, expected=expected, obtained=shapes)
        elif tool == "cst_load_material":
            self.metadata["material_load_mcp_evidence"] = {"call_id": self.seq, "payload": payload,
                                                          "library_file_is_not_project_readback": True}
        elif tool == "cst_list_ports":
            self.check("native port readback", "inconclusive" if not payload.get("ports") else "passed",
                       obtained=payload, reason="Port attributes must be present to independently assess the feed")
        elif tool == "cst_get_simulation_status":
            self.idle = payload.get("running") is False and payload.get("status") == "ok"
            self.require("native solve idle", self.idle, obtained=payload)
        elif tool == "cst_get_solver_info":
            for key, expected in (("fmin", 0.8), ("fmax", 1.2), ("number_of_ports", 1), ("stimulation_port", 1)):
                value = payload.get(key)
                self.check("native solver " + key, "inconclusive" if value is None else compare([value], [expected], 1e-6),
                           expected=expected, obtained=value, tolerance=1e-6, evidence=self.responses[-1]["evidence"])
        elif tool in {"cst_get_mesh_info", "cst_get_mesh_quality"}:
            self.check("available native mesh evidence", "passed" if payload.get("source") else "inconclusive",
                       obtained=payload, reason="Cell counts and edge metrics are not local feed-gap resolution or convergence proof",
                       evidence=self.responses[-1]["evidence"])
        elif tool == "cst_run_simulation":
            self.require("blocking native solve accepted", payload.get("status") in {"executed", "ok", "finished"},
                         obtained=payload, mandatory=True)
        elif tool == "cst_project_tree":
            self.tree_items.extend(i["path"] for i in payload.get("items", []) if isinstance(i, dict) and "path" in i)
            self.discover()
        elif tool == "cst_discover_farfield_monitors":
            candidates = [m for m in payload.get("monitors", []) if m.get("frequency_ghz") == 1]
            labels = set()
            for monitor in candidates:
                value = monitor.get("tree_path") or monitor.get("name") or monitor.get("monitor_name")
                if isinstance(value, str):
                    labels.add(value.split("\\")[-1])
            if len(labels) == 1:
                self.bindings["FF_MONITOR_1"] = next(iter(labels))
            else:
                self.check("exact 1 GHz farfield discovery", "inconclusive", obtained=candidates, reason="No unique native identifier")

    def validate_s(self, tool, arguments, payload, evidence):
        if tool == "cst_read_saved_result" and arguments.get("format") == "real_imag":
            x, values = complex_curve(payload)
            if self.baseline is None:
                if not all(key in payload for key in ("source", "project_path", "tree_path", "run_id")):
                    raise Incomplete("Full S11 lacks source, project, tree or result-run provenance")
                self.require("full S11", payload.get("source") == "cst.results" and self.idle and self.project.is_file()
                             and Path(payload["project_path"]).resolve() == self.project.resolve()
                             and payload["tree_path"] == self.bindings.get("TREE_S11") and payload["run_id"] == self.bindings.get("RUN_ID"),
                             expected="fresh nonempty full curve from this newly created saved project in GHz", obtained={
                                 "n": len(x), "min_ghz": min(x), "max_ghz": max(x), "source": payload.get("source")}, evidence=evidence)
                self.baseline = (x, values)
                self.baseline_source = payload
                self.check("S11 frequency order and duplicates", "passed" if all(b > a for a, b in zip(x, x[1:])) else "inconclusive",
                           obtained={"strictly_increasing": all(b > a for a, b in zip(x, x[1:])), "duplicates": len(x) - len(set(x)),
                                     "range": [min(x), max(x)]}, reason="No duplicate samples are silently discarded")
                self.check("S11 actual frequency range", compare([min(x), max(x)], [0.8, 1.2], 1e-6),
                           expected=[0.8, 1.2], obtained=[min(x), max(x)], tolerance=1e-6, evidence=evidence)
                write_json(self.results / "exports/independent_s11.json", {"x_ghz": x, "derived": derive(values),
                                                                          "bandwidth": bandwidth(x, values)})
            elif self.phase == "I":
                if not self.closed_verified:
                    raise Incomplete("Persistence read lacks independently verified project closure")
                self.require("S11 persistence", compare(x, self.baseline[0], 1e-10) and compare(values, self.baseline[1], 1e-10),
                             expected="baseline before close", obtained=f"{len(values)} samples after close", tolerance=1e-10, evidence=evidence)
            return
        if not self.baseline:
            return
        x, values = self.baseline
        derived = derive(values)
        fmt = arguments.get("format")
        expected = None
        if tool in {"cst_get_s_parameters", "cst_read_saved_result"}:
            if fmt == "real_imag":
                rx, rv = complex_curve(payload)
                self.check("live and saved complex S11 agree", compare(rx, x, 1e-10) and compare(rv, values), tolerance=1e-5, evidence=evidence)
                return
            expected = derived.get(fmt)
        elif tool == "cst_get_s_parameter_phase":
            expected = derived["unwrapped" if arguments.get("unwrap") else "phase"]
        elif tool == "cst_get_vswr":
            expected = derived["vswr"]
        elif tool == "cst_get_smith_chart_data":
            expected_z = derived["z"]
            self.check("Smith impedance independent identity", payload.get("z0") == 50
                       and compare(payload.get("x", []), x, 1e-10)
                       and compare(payload.get("impedance_real", []), [v.real if v is not None else None for v in expected_z])
                       and compare(payload.get("impedance_imag", []), [v.imag if v is not None else None for v in expected_z]),
                       expected="50*(1+S11)/(1-S11)", tolerance=1e-5, evidence=evidence)
        elif tool == "cst_get_impedance":
            z = derived["z"]
            if payload.get("impedance_mode") == "input":
                self.check("independent cst_get_impedance input identity",
                           payload.get("source") == "derived_from_s_parameters"
                           and payload.get("source_api") == "cst.results"
                           and payload.get("source_tree_path") == (self.baseline_source or {}).get("tree_path")
                           and payload.get("z0") == 50 and payload.get("unit") == "ohm"
                           and compare(payload.get("x", []), x, 1e-10)
                           and compare(payload.get("real", []), [v.real if v is not None else None for v in z])
                           and compare(payload.get("imag", []), [v.imag if v is not None else None for v in z]),
                           expected="50*(1+S11)/(1-S11), derived from the same native source curve",
                           obtained={"source": payload.get("source"), "z0": payload.get("z0"),
                                     "n": len(payload.get("x", []))}, tolerance=1e-5, evidence=evidence)
            elif payload.get("reference_plane") is not None and payload.get("reference_plane") == (self.baseline_source or {}).get("reference_plane") and payload.get("z0") == 50:
                self.check("native Z reference-plane comparison", compare(payload.get("x", []), x, 1e-10)
                           and compare(payload.get("real", []), [v.real if v is not None else None for v in z])
                           and compare(payload.get("imag", []), [v.imag if v is not None else None for v in z]),
                           expected="native Z versus S11-derived Z at common reference plane", tolerance=1e-5, evidence=evidence)
            else:
                self.check("native Z reference-plane comparison", "inconclusive", obtained=payload,
                           reason="Native API supplies no common reference-plane and impedance metadata", evidence=evidence)
        elif tool == "cst_get_bandwidth":
            expected_bands = bandwidth(x, values)
            actual = payload.get("bands", [])
            matched = len(actual) == len(expected_bands) and all(
                compare([a.get("f_lower_ghz"), a.get("f_upper_ghz")], [b["f_lower_ghz"], b["f_upper_ghz"]], 1e-9)
                and a.get("lower_truncated") == b["lower_truncated"] and a.get("upper_truncated") == b["upper_truncated"]
                for a, b in zip(actual, expected_bands))
            self.check("independent -10 dB bandwidth intervals", matched, expected=expected_bands, obtained=actual,
                       tolerance=1e-9, evidence=evidence, reason="Zero qualifying intervals is a valid antenna result")
        if expected is not None:
            maximum = arguments.get("max_points", 0)
            if maximum and len(x) > maximum:
                indices = sorted({round(j * (len(x) - 1) / (maximum - 1)) for j in range(maximum)})
                self.check("preview documented sampling", payload.get("sampled") is True and payload.get("total_points") == len(x)
                           and payload.get("sample_indices") == indices, evidence=evidence)
                expected_x, expected = [x[i] for i in indices], [expected[i] for i in indices]
            else:
                expected_x = x
            phase = tool == "cst_get_s_parameter_phase" or fmt == "phase"
            wrapped = phase and not arguments.get("unwrap", False)
            self.check(f"independent {tool} {fmt or 'derived'}", compare(payload.get("x", []), expected_x, 1e-10)
                       and compare(payload.get("y", []), expected, wrapped=wrapped), expected="full-curve independent formula",
                       obtained=f"{len(payload.get('y', []))} samples", tolerance=1e-5, evidence=evidence)

    async def live(self, session):
        self.metadata["live_phases_executed"] = True
        configuration_ok = True
        for phase, label in PHASES.items():
            self.phase = phase
            self.idle = self.idle if phase != "D" else False
            start, utc, outcome = time.monotonic(), timestamp(), "passed"
            try:
                for index, item in enumerate(MANIFEST, 1):
                    if item["phase"] != phase:
                        continue
                    tool = item["tool"]
                    self.active_manifest_index = index
                    if phase in {"H", "I"} and not self.idle:
                        raise Incomplete("Probes and persistence require known idle state")
                    if phase == "D" and not configuration_ok:
                        raise Stop("Native construction or configuration failed. Solver blocked")
                    try:
                        arguments = self.resolve(item["arguments"])
                    except Incomplete as exc:
                        self.check(f"manifest {index}: {tool}", "blocked", reason=str(exc), mandatory=phase == "E")
                        continue
                    if phase == "E" and arguments.get("max_points") == 200 and self.baseline and len(self.baseline[0]) <= 200:
                        self.check(f"manifest {index}: preview", "not_executed", reason="Full curve has at most 200 points")
                        continue
                    try:
                        payload = await self.request(session, tool, arguments, probe=phase == "H")
                        response = self.responses[-1]
                        if phase != "H":
                            self.artifact_contract(tool, arguments, response)
                        if tool == "cst_list_saved_results":
                            self.entries = await self.saved_pages(session, arguments, payload)
                            self.discover()
                        if phase != "H":
                            self.verify_native(tool, arguments, payload)
                        elif not response["rejected"]:
                            self.assess_supported_probe(response)
                        if phase in {"E", "I"} and tool in {"cst_read_saved_result", "cst_get_s_parameters", "cst_get_s_parameter_phase",
                                "cst_get_vswr", "cst_get_smith_chart_data", "cst_get_impedance", "cst_get_bandwidth"}:
                            self.validate_s(tool, arguments, payload, response["evidence"])
                        if phase == "F" and tool == "cst_get_radiation_pattern_3d":
                            sources = [a for a in response["artifacts"] if Path(a["archived"]).suffix.lower() in {".txt", ".csv"}]
                            if len(sources) == 1 and sources[0]["fresh"]:
                                grid = self.grid_from_source(response, sources[0], "realized_gain")
                                self.realized = grid
                                self.bindings["REALIZED_GRID"] = sources[0]["archived"]
                                self.check("actual spherical extraction angular step", compare([grid["step_theta"], grid["step_phi"]], [5, 5], 1e-6),
                                           expected=[5, 5], obtained=[grid["step_theta"], grid["step_phi"]], tolerance=1e-6)
                            else:
                                self.check("realized spherical source", "inconclusive", obtained=sources)
                        if phase == "G" and tool == "cst_plot_farfield" and arguments.get("quantity") == "directivity":
                            source = payload.get("source", {}).get("path")
                            candidates = [a for a in response["artifacts"] if a["original"] == source]
                            if len(candidates) == 1 and candidates[0]["fresh"]:
                                self.directivity = self.grid_from_source(response, candidates[0], "directivity")
                        if phase == "G" and tool.startswith("cst_plot_"):
                            self.figure_source_checks(arguments, response)
                        if tool == "cst_export_touchstone":
                            self.validate_touchstone(response)
                        if phase == "I" and tool == "cst_save_project":
                            self.active_manifest_index = None
                            await self.request(session, "cst_read_project_log", {})
                            self.active_manifest_index = index
                    except Stop:
                        outcome = "failed"
                        await self.diagnose(session)
                        ancillary = tool in {"cst_get_solver_info", "cst_get_mesh_info", "cst_get_mesh_quality", "cst_list_ports", "cst_project_tree", "cst_read_project_log"}
                        if phase in {"A", "B", "C", "D"} and not ancillary:
                            configuration_ok = False
                            raise
                        if phase == "I" and tool in {"cst_save_project", "cst_close_project", "cst_connection_status", "cst_open_project"}:
                            raise
                        # Independent result routes can still run after an ordinary failure.
                    except Incomplete as exc:
                        if self.unknown:
                            raise
                        self.check(f"manifest {index} assessment", "inconclusive", reason=str(exc),
                                   evidence=self.responses[-1]["evidence"] if self.responses else None)
                if phase == "D":
                    self.discover()
                    if "TREE_S11" not in self.bindings:
                        raise Incomplete("Fresh S11 path and result run not discovered")
                if phase == "E" and self.baseline is None:
                    raise Incomplete("Full S11 prerequisite missing")
                if phase == "E":
                    numerical = [c for c in self.checks if c["name"].startswith("independent cst_get_") or c["name"] in {
                        "live and saved complex S11 agree", "Smith impedance independent identity", "independent -10 dB bandwidth intervals"}]
                    expected_names = {"cst_get_s_parameters", "cst_get_s_parameter_phase", "cst_get_vswr", "cst_get_smith_chart_data", "cst_get_impedance", "cst_get_bandwidth"}
                    successful_tools = {r["tool"] for r in self.responses if r["phase"] == "E" and not r["rejected"]}
                    verdict = "failed" if any(c["verdict"] == "failed" for c in numerical) else (
                        "passed" if expected_names.issubset(successful_tools) and len(numerical) >= 9 and all(c["verdict"] == "passed" for c in numerical) else "inconclusive")
                    self.check("full S11 derived consistency", verdict, expected="full complex, dB, magnitude, phase, VSWR, Smith Z and bandwidth agreement",
                               obtained=[{"name": c["name"], "verdict": c["verdict"]} for c in numerical])
                if phase == "F":
                    self.active_manifest_index = None
                    await self.request(session, "cst_read_project_log", {})
                if phase == "G":
                    self.assess_radiation()
                if phase == "I":
                    self.active_manifest_index = None
                    await self.request(session, "cst_read_project_log", {})
            except BaseException:
                outcome = "incomplete" if self.unknown else "stopped"
                raise
            finally:
                self.phases.append({"phase": phase, "label": label, "started_utc": utc,
                                    "elapsed_s": time.monotonic() - start, "outcome": outcome})
                self.persist()

    def assess_supported_probe(self, response):
        tool, payload = response["tool"], response["payload"]
        name = f"probe {response['id']}: {tool} supported-data assessment"
        if tool == "cst_get_farfield":
            tree = str(payload.get("tree_path", ""))
            frequency = re.search(r"farfield\s*\(f\s*=\s*([\d.]+)\)", tree, re.I)
            files = [a for a in response["artifacts"] if Path(a["archived"]).suffix.lower() in {".txt", ".csv"}]
            if frequency and len(files) == 1:
                grid = farfield_grid(Path(files[0]["archived"]), "realized_gain")
                self.check(name, abs(float(frequency.group(1)) - 1.05) < 1e-12 and grid["sample_count"] > 0,
                           expected="genuine native 1.05 GHz grid, no substituted monitor", obtained=tree, evidence=files[0]["archived"])
            else:
                self.check(name, "inconclusive", reason="No selected exact-frequency grid evidence", evidence=response["evidence"])
        elif tool == "cst_read_saved_result":
            x, values = complex_curve(payload)
            self.check(name, "passed" if any(e.get("tree_path") == response["arguments"]["tree_path"] for e in self.entries) else "failed",
                       expected="requested saved result exists in actual discovery", obtained={"n": len(x), "tree_path": payload.get("tree_path")},
                       evidence=response["evidence"])
        else:
            fields = {k: payload.get(k) for k in ("coordinate", "definition", "unit", "quantity", "data", "values", "x", "y", "z") if k in payload}
            self.check(name, "inconclusive", obtained=fields,
                       reason="Route returned success. Assess its actual numerical contract and native prerequisites before declaring feature support",
                       evidence=response["evidence"])

    def artifact_contract(self, tool, arguments, response):
        artifacts = response["artifacts"]
        if tool in {"cst_export_result", "cst_export_farfield", "cst_export_touchstone", "cst_get_radiation_pattern_3d", "cst_get_farfield"}:
            numeric = [a for a in artifacts if Path(a["archived"]).suffix.lower() in {".txt", ".csv", ".s1p", ".ffs"}]
            self.check("numeric artifact production " + str(response["id"]), bool(numeric) and all(a["bytes"] > 0 and a["fresh"] for a in numeric),
                       expected="fresh nonempty numerical export", obtained=numeric, evidence=response["evidence"],
                       mandatory=tool == "cst_export_touchstone")
        if tool.startswith("cst_plot_"):
            figures = [a for a in artifacts if Path(a["archived"]).suffix.lower() in {".png", ".pdf"}]
            extensions = {Path(a["archived"]).suffix.lower()[1:] for a in figures}
            self.check("figure production " + str(response["id"]), bool(figures) and all(a["bytes"] > 0 and a["fresh"] for a in figures)
                       and set(arguments.get("formats", [])).issubset(extensions),
                       expected=arguments.get("formats"), obtained=sorted(extensions), evidence=response["evidence"])
            self.check("figure axes legends and content " + str(response["id"]), "inconclusive",
                       reason="Readability and source metadata alone cannot prove visible curves, axis labels, legends or 3D radiation content. Inspect produced figures",
                       evidence=[a["archived"] for a in figures])

    def figure_source_checks(self, arguments, response):
        payload = response["payload"]
        if response["tool"] == "cst_plot_1d_results":
            quantities = {f.get("quantity") for f in payload.get("figures", [])}
            self.check("1D figure quantity coverage", set(arguments["quantities"]).issubset(quantities),
                       expected=arguments["quantities"], obtained=sorted(quantities), evidence=response["evidence"])
            self.check("1D figure native source", payload.get("source", {}).get("reader") == "cst.results"
                       and payload.get("source", {}).get("run_ids") == [self.bindings.get("RUN_ID")], evidence=response["evidence"])
            self.check("1D figure numerical curves", "inconclusive", reason="Plot response provides source trees and metrics, not rendered curve samples. Full original curves are checked independently", evidence=response["evidence"])
            return
        source = payload.get("source", {}).get("path")
        archives = [a for a in response["artifacts"] if a["original"] == source]
        if not archives:
            self.check("farfield figure source archive", "inconclusive", reason="Original figure source absent", evidence=response["evidence"])
            return
        grid = self.grid_from_source(response, archives[0], arguments["quantity"])
        metrics = payload.get("metrics", {})
        self.check("farfield figure quantity and numeric peak", metrics.get("quantity") == grid["quantity"]
                   and compare([metrics.get("max_value")], [max(grid["grid"].values())], 0.00051),
                   expected="original header quantity and independently parsed maximum", obtained=metrics,
                   tolerance="0.00051 dB for documented 3-decimal metric serialization", evidence=archives[0]["archived"])
        if "step_deg" in arguments:
            self.check("actual farfield figure angular step", compare([grid["step_theta"], grid["step_phi"]], [arguments["step_deg"]] * 2, 1e-6),
                       expected=arguments["step_deg"], obtained=[grid["step_theta"], grid["step_phi"]], tolerance=1e-6)

    def grid_from_source(self, response, artifact, quantity):
        grid = farfield_grid(Path(artifact["archived"]), quantity)
        payload = response["payload"]
        source = payload.get("source", {})
        if source.get("kind") == "data_file":
            if source.get("path") != self.bindings.get("REALIZED_GRID") or self.realized is None:
                raise Incomplete("File-based figure has no previously proven realized-gain source")
        else:
            tree = payload.get("tree_path") or source.get("tree_path")
            match = re.search(r"farfield\s*\(f\s*=\s*([\d.]+)\)", str(tree), re.I)
            if not match:
                raise Incomplete("Radiation source has no actual selected native frequency identifier")
            self.require("native radiation frequency " + str(response["id"]), abs(float(match.group(1)) - 1) <= 1e-12,
                         expected="exact 1 GHz selected native tree", obtained=tree, evidence=response["evidence"], mandatory=True)
            self.bindings["FF_MONITOR_1"] = str(tree).split("\\")[-1]
        declared = re.search(r"(?:frequency|\bf)\s*[=:]\s*([\d.eE+\-]+)\s*(GHz|MHz|Hz)", grid["header"], re.I)
        if declared:
            scales = {"ghz": 1, "mhz": 1e-3, "hz": 1e-9}
            f = float(declared.group(1)) * scales[declared.group(2).lower()]
            self.require("radiation header frequency " + str(response["id"]), abs(f - 1) <= 1e-9,
                         expected=1, obtained=f, tolerance=1e-9, mandatory=True)
        artifact.update(readable=True, units=grid["unit"], sample_count=grid["sample_count"],
                        inspection={k: v for k, v in grid.items() if k not in {"grid", "theta", "phi"}})
        return grid

    def validate_touchstone(self, response):
        candidates = [a for a in response["artifacts"] if Path(a["archived"]).suffix.lower() == ".s1p"]
        if not self.baseline or len(candidates) != 1:
            raise Incomplete("Touchstone or full S11 prerequisite missing")
        artifact = candidates[0]
        ts = touchstone(Path(artifact["archived"]))
        artifact.update(inspection={k: v for k, v in ts.items() if k not in {"x", "values"}},
                        readable=True, units=ts["frequency_unit"], sample_count=ts["sample_count"])
        for key, actual, expected in (
            ("max_frequency_difference_ghz", ts["x"], self.baseline[0]),
            ("max_complex_s11_difference", ts["values"], self.baseline[1]),
        ):
            artifact["inspection"][key] = max(
                (abs(a - b) for a, b in zip(actual, expected)), default=None
            ) if len(actual) == len(expected) else None
        self.require("Touchstone consistency", artifact["fresh"] and ts["representation"] == "RI" and ts["reference_ohm"] == 50
                     and compare(ts["x"], self.baseline[0], 1e-7) and compare(ts["values"], self.baseline[1], 1e-5),
                     expected="one port, 50 ohm RI, full complex S11", obtained=artifact["inspection"],
                     tolerance={"frequency_ghz": 1e-7, "complex_s11": 1e-5},
                     evidence=artifact["archived"])

    def assess_radiation(self):
        if self.realized is None or self.directivity is None:
            self.check("radiation source quantities", "inconclusive", reason="Separate fresh header-proven realized-gain and directivity sources required")
            return
        self.check("radiation source quantities", "passed", expected="separate original headers and fresh sources", obtained={
            "realized": self.realized["header"], "directivity": self.directivity["header"]})
        d, r = self.directivity, self.realized
        metrics = dipole_physics(d)
        write_json(self.results / "exports/independent_radiation.json", {"metrics": metrics,
                   "directivity_grid": {k: v for k, v in d.items() if k != "grid"},
                   "realized_grid": {k: v for k, v in r.items() if k != "grid"},
                   "realized_sphere_mean": sphere_mean(r)})
        evidence = "results/exports/independent_radiation.json"
        for name, key, expected, tolerance, predicate in (
            ("directivity peak", "peak_dbi", 2.15, 0.3, lambda v: abs(v - 2.15) <= 0.3),
            ("meridional HPBW", "hpbw_deg", 78, 5, lambda v: abs(v - 78) <= 5),
            ("broadside maximum", "max_theta_distance_deg", 0, d["step_theta"], lambda v: v <= d["step_theta"]),
            ("axial nulls", "axial_suppression_db", ">=20 dB", None, lambda v: v >= 20),
            ("azimuth ripple", "azimuth_ripple_db", "<=0.5 dB", 0.5, lambda v: v <= 0.5),
            ("normalized meridional shape", "shape_max_error_db_above_minus20", "ideal half-wave field shape", 1.0, lambda v: v <= 1),
        ):
            value = metrics[key]
            verdict = "inconclusive" if value is None else bool(predicate(value))
            self.check(name, verdict, expected=expected, obtained=value, tolerance=tolerance, evidence=evidence)
        # Shape tolerance is an explicitly documented initial client criterion, not mesh convergence.
        self.check("directivity sphere normalization", abs(sphere_mean(d) - 1) <= 0.03,
                   expected=1, obtained=sphere_mean(d), tolerance=0.03, evidence=evidence)
        metrics_response = next((p for p in self.responses if p["tool"] == "cst_get_farfield_metrics"
                                 and p["arguments"].get("frequency_ghz") == 1 and not p["rejected"]), None)
        if metrics_response and self.baseline:
            m = metrics_response["payload"].get("metrics", {})
            rad = m.get("rad_efficiency_linear", m.get("radiation_efficiency_linear"))
            total = m.get("tot_efficiency_linear", m.get("total_efficiency_linear"))
            if rad is not None and total is not None and d["grid"].keys() == r["grid"].keys():
                self.check("efficiency overshoot observation", "passed", obtained={"radiation": rad, "total": total},
                           reason="Original values retained without clipping")
                errors = [abs(10 ** (r["grid"][k] / 10) - 10 ** (d["grid"][k] / 10) * total)
                          for k in d["grid"] if d["grid"][k] >= metrics["peak_dbi"] - 20]
                self.check("realized gain efficiency identity", bool(errors) and max(errors) <= 0.03,
                           expected="G_realized = D * eta_total", obtained=max(errors) if errors else None, tolerance=0.03)
                x, vals = self.baseline
                gamma = self.interpolate_complex(x, vals, 1)
                self.check("total efficiency mismatch identity", abs(total - rad * (1 - abs(gamma) ** 2)) <= 0.03,
                           expected="eta_total = eta_rad*(1-|S11|^2)", obtained=total - rad * (1 - abs(gamma) ** 2), tolerance=0.03)
                passed = all(c["verdict"] == "passed" for c in self.checks if c["name"] in {
                    "realized gain efficiency identity", "total efficiency mismatch identity"})
                self.check("efficiency definitions and identities", passed, expected="consistent independently returned efficiencies and grid definitions",
                           obtained={"radiation_linear": rad, "total_linear": total})
            else:
                self.check("efficiency identities", "inconclusive", obtained=m, reason="Consistent definitions, grids or independently returned efficiencies absent")
        if not any(c["name"] == "efficiency definitions and identities" for c in self.checks):
            self.check("efficiency definitions and identities", "inconclusive", reason="No consistent native efficiency definitions and values")
        self.validate_cut()
        power_outcomes = []
        for response in self.responses:
            if response["tool"] == "cst_check_power_balance" and not response["rejected"]:
                for frequency, row in response["payload"].get("frequencies", {}).items():
                    pa, pr = row.get("P_acc"), row.get("P_rad")
                    loss = row.get("P_loss")
                    if loss is None:
                        losses = [row.get("P_loss_metal"), row.get("P_loss_diel")]
                        if all(v is not None for v in losses):
                            loss = sum(losses)
                    if pa is not None and pa > 0 and pr is not None and loss is not None:
                        delta = (pa - pr - loss) / pa
                        self.check(f"power conservation {frequency} GHz", abs(delta) <= 0.03,
                                   expected="|(Pacc-Prad-Ploss)/Pacc|<=0.03", obtained=delta, tolerance=0.03, evidence=response["evidence"])
                        power_outcomes.append("passed" if abs(delta) <= 0.03 else "failed")
                    else:
                        self.check(f"power conservation {frequency} GHz", "inconclusive", obtained=row,
                                   reason="Independent numerical power values unavailable", evidence=response["evidence"])
                        power_outcomes.append("inconclusive")
        self.check("power conservation", "failed" if "failed" in power_outcomes else (
            "passed" if len(power_outcomes) == 3 and all(v == "passed" for v in power_outcomes) else "inconclusive"),
            expected="accepted = radiated + loss at 0.9, 1.0 and 1.1 GHz within 3 percent", obtained=power_outcomes)

    @staticmethod
    def interpolate_complex(x, values, frequency):
        for i, f in enumerate(x):
            if abs(f - frequency) < 1e-12:
                return values[i]
            if i and x[i - 1] < frequency < f:
                return values[i - 1] + (values[i] - values[i - 1]) * (frequency - x[i - 1]) / (f - x[i - 1])
        raise Incomplete("Required frequency lies outside full S11 range")

    def validate_cut(self):
        response = next((p for p in self.responses if p["tool"] == "cst_get_pattern_cut" and not p["rejected"]), None)
        if not response or self.realized is None:
            self.check("custom cut agrees with angular grid", "inconclusive", reason="Cut or grid absent")
            return
        payload = response["payload"]
        angles, values = payload.get("angle_deg", payload.get("theta_deg")), payload.get("value", payload.get("values"))
        if angles is None:
            angles = payload.get("theta")
        if values is None:
            values = payload.get("value_db")
        source = next((a for a in response["artifacts"] if a["original"] == payload.get("path")), None)
        declared_db = re.search(r"db", str(payload.get("unit", payload.get("quantity", ""))), re.I)
        if source and not declared_db:
            try:
                declared_db = "dbi" in farfield_grid(Path(source["archived"]), "realized_gain")["unit"].lower()
            except (Stop, Incomplete):
                pass
        if not angles or not values or not declared_db:
            self.check("custom cut agrees with angular grid", "inconclusive", obtained=payload,
                       reason="Cut angular axes and scale need original metadata", evidence=response["evidence"])
            return
        rows = [self.realized["grid"].get((abs(t), 0 if t >= 0 else 180)) for t in angles]
        matched = [(v, row) for v, row in zip(values, rows) if row is not None]
        # Current cut uses 1 degree while the grid uses 5 degrees. Compare matching samples only.
        if matched:
            values, rows = [v for v, row in matched], [row for v, row in matched]
        if None in rows:
            self.check("custom cut agrees with angular grid", "inconclusive", reason="Angular grids do not match exactly")
        else:
            self.check("custom cut agrees with angular grid", compare([v - max(values) for v in values], [v - max(rows) for v in rows], 1e-3),
                       expected="normalized phi=0 rows", tolerance=1e-3, evidence=response["evidence"])

    def inspect_exports(self):
        for artifact in self.inventory:
            path = Path(artifact["archived"])
            suffix = path.suffix.lower()
            try:
                if suffix in {".txt", ".csv"}:
                    table = ascii_table(path)
                    artifact.update(readable=True, sample_count=table["sample_count"], units=table["header"],
                                    inspection={k: v for k, v in table.items() if k != "rows"})
                    if table["extension_mismatch"]:
                        self.check("native CSV delimiter limitation", "inconclusive", reason=".csv contains whitespace or semicolon ASCII, not comma-separated values", evidence=str(path))
                    if "s11_native" in path.name and self.baseline:
                        self.validate_native_ascii(artifact, table)
                    if any(name in path.name for name in ("efield_1GHz", "hfield_1GHz", "surface_current_1GHz")):
                        header = table["header"]
                        unit = bool(re.search(r"V/m|A/m|A/m\^?2|A/m²", header, re.I))
                        vector = table["columns"] >= 6 and bool(re.search(r"[EHJ][xyz]|real|imag|component", header, re.I))
                        self.check("numeric vector field " + path.name, "passed" if unit and vector else "inconclusive",
                                   obtained={"columns": table["columns"], "header": header}, evidence=str(path),
                                   reason="Actual vector components and units required")
                elif suffix == ".ffs":
                    inspection = ffs_source(path)
                    artifact.update(readable=True, sample_count=inspection["sample_count"], units=inspection["units"], inspection=inspection)
                    self.check("FFS native structure and counts", inspection["data_type_farfield"] and inspection["counts_match"]
                               and inspection["finite_field_components"] and inspection["angles_valid"],
                               obtained=inspection, evidence=str(path))
                    self.check("FFS native frequency", "inconclusive" if not inspection["frequency_ghz"] else compare(inspection["frequency_ghz"], [1], 1e-9),
                               expected=1, obtained=inspection["frequency_ghz"], tolerance=1e-9, evidence=str(path))
                elif suffix == ".png":
                    from matplotlib.image import imread
                    pixels = imread(path)
                    artifact.update(readable=bool(pixels.size), inspection={"format": "PNG", "dimensions": list(pixels.shape),
                                    "semantics": "Visual axes, legends and plotted geometry assessment pending"})
                    self.check("figure image readable " + path.name, bool(pixels.size), evidence=str(path))
                elif suffix == ".pdf":
                    raw = path.read_bytes()
                    artifact.update(readable=None, inspection={"format": "PDF", "signature": raw.startswith(b"%PDF"),
                                     "rendered": False})
                    self.check("figure PDF rendering " + path.name, "inconclusive", reason="PDF header alone does not prove rendered content. Open for visual inspection", evidence=str(path))
            except Exception as exc:
                artifact.update(readable=False, inspection={"exception": str(exc)})
                self.check("artifact assessment " + path.name, "inconclusive", reason=str(exc), evidence=str(path))

    def validate_native_ascii(self, artifact, table):
        header = table["header"]
        rows = table["rows"]
        quantity = "db" if re.search(r"\bdB\b", header) else "mag" if re.search(r"magnitude|abs\s*\(\s*S", header, re.I) else "unknown"
        if quantity == "unknown" or not re.search(r"\bGHz\b", header, re.I) or not re.search(r"S1[, _]?1|S-Parameter", header, re.I):
            self.check("native S11 ASCII quantity", "inconclusive", obtained=header, evidence=artifact["archived"],
                       reason="Current selected display may be scalar. Complex RI is proven through Touchstone only")
            return
        expected = derive(self.baseline[1])[quantity]
        self.check("native S11 ASCII " + quantity, compare([row[0] for row in rows], self.baseline[0], 1e-5)
                   and compare([row[1] for row in rows], expected, 1e-5), expected=quantity + " from full complex S11", tolerance=1e-5,
                   evidence=artifact["archived"])

    def finalize(self):
        for phase, label in PHASES.items():
            if not any(p["phase"] == phase for p in self.phases):
                self.phases.append({"phase": phase, "label": label, "started_utc": self.metadata["started_utc"],
                                    "elapsed_s": 0, "outcome": "not_executed" if self.args.preflight else "blocked"})
        if self.args.preflight:
            for name in sorted(MANDATORY):
                if not any(c["name"] == name for c in self.checks):
                    self.check(name, "not_executed", reason="Preflight performs no live CST operations")
        if not self.args.preflight:
            self.inspect_exports()
            for name in sorted(MANDATORY):
                if not any(c["name"] == name for c in self.checks):
                    self.check(name, "blocked", reason=self.reason)
            for index, item in enumerate(MANIFEST, 1):
                if not any(r["manifest_index"] == index for r in self.records) and not any(c["name"].startswith(f"manifest {index}:") for c in self.checks):
                    self.check(f"planned {index}: {item['tool']}", "blocked", reason=self.reason)
            mandatory_failed = any(c["mandatory"] and c["verdict"] == "failed" for c in self.checks)
            if mandatory_failed or self.exit_code == 1:
                self.exit_code = 1
            elif all(any(c["name"] == name and c["verdict"] == "passed" for c in self.checks) for name in MANDATORY) and not self.unknown:
                self.exit_code = 0
                self.reason = "Mandatory native and physical criteria passed. Individual capability verdicts remain separate"
            else:
                self.exit_code = 2
                self.reason = "Incomplete mandatory assessment. " + self.reason
        self.persist()

    async def run(self):
        with workspace_lock(self.parent, self.args.preflight):
            # Rejected preparation leaves the prior outputs completely intact.
            prepare_outputs(self.base, self.parent, self.args.preflight)
            self.prepared = True
            self.persist()
            try:
                self.configure_environment()
                params = StdioServerParameters(command=sys.executable, args=["-m", "cst_mcp.server"], cwd=str(ROOT), env=self.env)
                with (self.results / "protocol/server_stderr.log").open("w", encoding="utf-8") as stderr:
                    with preserve_cst_processes():
                        async with stdio_client(params, errlog=stderr) as (read, write):
                            async with ClientSession(read, write) as session:
                                try:
                                    await self.protocol(session)
                                    if not self.args.preflight:
                                        await self.live(session)
                                except Stop:
                                    await self.diagnose(session)
                                    raise
                if self.args.preflight:
                    self.exit_code, self.reason = 0, "Disabled-server preflight passed. All live phases not_executed"
                else:
                    self.exit_code, self.reason = 2, "Live manifest completed. Mandatory assessment pending"
                self.check("preserving transport shutdown", "passed", reason="Server-only lifecycle hooks completed. Native process liveness after client exit requires user inspection")
            except BaseException as exc:
                leaves = list(exception_leaves(exc))
                cause = next((e for e in leaves if isinstance(e, (Stop, Incomplete))), leaves[0])
                if any(isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)) for e in leaves):
                    self.unknown = True
                self.exit_code = 2 if isinstance(cause, Incomplete) or self.unknown else 1
                self.reason = f"{self.phase}: {type(cause).__name__}: {cause}"
                write_json(self.results / "protocol/client_exception.json", {
                    "utc": timestamp(), "type": type(cause).__name__, "message": str(cause), "traceback": traceback.format_exc()})
            finally:
                self.finalize()
        return self.exit_code


def main(argv=None):
    args = options(argv)
    runner = Dipole(args)
    try:
        code = asyncio.run(runner.run())
    except Exception as exc:
        # Preparation refuses unsafe/locked output before replacing existing evidence.
        print(f"Preparation refused: {exc}. Close the existing project before live overwrite.", file=sys.stderr)
        return 1
    print(f"{runner.reason}\nReport: {runner.reports / 'validation.md'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())


