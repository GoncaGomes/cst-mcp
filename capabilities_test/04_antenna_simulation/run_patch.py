"""Fixed rectangular patch workflow. Live execution is exclusively user triggered."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
import traceback
from itertools import pairwise
from pathlib import Path

from common import (
    DEFAULT_CST_PATH,
    EXPECTED_UNITS,
    ROOT,
    SHAPES_QUERY,
    UNITS_BLOCK,
    UNITS_QUERY,
    Incomplete,
    Stop,
    ascii_table,
    atomic_text,
    bandwidth,
    compare,
    complex_curve,
    derive,
    ensure_closed,
    exception_leaves,
    farfield_grid,
    ffs_source,
    initialize_catalog,
    parse_shapes,
    prepare_outputs,
    preserve_cst_processes,
    read_text,
    reject_links,
    safe_json,
    sha256,
    sphere_mean,
    timestamp,
    workspace_lock,
    write_json,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from run_dipole import Dipole

BATCH = Path(__file__).resolve().parent
REFERENCE = "https://www.mathworks.com/help/antenna/ug/patch-antenna-on-dielectric-substrate.html"
FREQUENCIES = [2.30, 2.37, 2.45]
TARGET_FREQUENCY = 2.37
TARGET_DIRECTIVITY_DBI = 5.84
RESONANCE_TOLERANCE = 0.03
DIRECTIVITY_TOLERANCE_DB = 1.0
BOUNDARIES = {
    face: "expanded open" for face in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
}
BRICKS = [
    {
        "name": "Substrate",
        "material": "PatchSubstrate",
        "x_min": -40,
        "x_max": 40,
        "y_min": -30,
        "y_max": 30,
        "z_min": 0,
        "z_max": 1.57,
    },
    {
        "name": "Ground",
        "material": "PatchPEC",
        "x_min": -40,
        "x_max": 40,
        "y_min": -30,
        "y_max": 30,
        "z_min": -0.035,
        "z_max": 0,
    },
    {
        "name": "Patch",
        "material": "PatchPEC",
        "x_min": -20,
        "x_max": 20,
        "y_min": -15,
        "y_max": 15,
        "z_min": 1.57,
        "z_max": 1.605,
    },
    {
        "name": "FeedPin",
        "material": "PatchPEC",
        "x_min": 5,
        "x_max": 6,
        "y_min": -0.5,
        "y_max": 0.5,
        "z_min": 0.10,
        "z_max": 1.57,
    },
]
PORT = {
    "port_number": 1,
    "impedance": 50,
    "x1": 5.5,
    "y1": 0,
    "z1": 0,
    "x2": 5.5,
    "y2": 0,
    "z2": 0.10,
    "port_type": "SParameter",
}
SOLVER = {
    "accuracy": 0.000001,
    "f_min": 2.2,
    "f_max": 2.5,
    "samples": 21,
    "sweep_type": "Discrete",
    "mesh_type": "Tetrahedral",
    "adaptive_mesh": True,
    "include_monitor_frequencies": True,
    "normalize_to_fixed_impedance": True,
    "fixed_impedance": 50,
}
MATERIALS = [("PatchPEC", "PEC"), ("PatchSubstrate", "Rogers RT-duroid 5870 (loss free)")]
PHASES = {
    "A": "Create owned project and verify units",
    "B": "Load native materials and construct probe-fed patch",
    "C": "Configure domain, tetrahedral mesh, solver and monitors",
    "D": "One asynchronous solve, bounded waits and result discovery",
    "E": "Full complex S11 and derived consistency",
    "F": "Radiation metrics and numerical exports",
    "G": "MCP figures and patch physical assessment",
    "I": "Save, close, independently read saved S11 and reopen",
}
MANDATORY = {
    "native construction",
    "native solve idle",
    "expected native results",
    "full S11",
    "Touchstone consistency",
    "full S11 derived consistency",
    "S11 persistence",
    "patch resonance",
    "patch directivity",
    "upper half-space radiation",
    "linear polarization",
}


def planned(phase, tool, **arguments):
    return {"phase": phase, "tool": tool, "arguments": arguments}


# Model calls are individually exposed. No antenna workflow generator is used.
MANIFEST = [
    planned("A", "cst_connection_status"),
    planned("A", "cst_connect", mode="new"),
    planned("A", "cst_connection_status"),
    planned("A", "cst_create_project", path="<PROJECT>", project_type="MWS"),
    planned("A", "cst_project_info"),
    planned("A", "cst_execute_vba", code=UNITS_BLOCK),
    planned("A", "cst_execute_vba", code=UNITS_QUERY),
    planned("A", "cst_save_project", path="<PROJECT>"),
    planned("A", "cst_read_project_log"),
    *[
        planned("B", "cst_load_material", name=alias, library_name=library)
        for alias, library in MATERIALS
    ],
    *[planned("B", "cst_create_brick", component="Patch", **brick) for brick in BRICKS],
    planned("B", "cst_boolean_insert", solid1="Patch:Substrate", solid2="Patch:FeedPin"),
    planned("B", "cst_add_discrete_port", **PORT),
    planned("B", "cst_execute_vba", code=SHAPES_QUERY),
    planned("B", "cst_list_ports"),
    planned("B", "cst_project_tree", tree_path="Components", max_depth=3),
    planned("B", "cst_save_project", path="<PROJECT>"),
    planned("B", "cst_read_project_log"),
    planned("C", "cst_set_background", material="Normal", epsilon=1, mu=1),
    planned("C", "cst_set_frequency_range", f_min=2.2, f_max=2.5),
    planned("C", "cst_set_boundary", **BOUNDARIES, waveguide_port_faces=[]),
    planned("C", "cst_set_symmetry", x_plane="none", y_plane="none", z_plane="none"),
    planned("C", "cst_set_mesh_type", mesh_type="Tetrahedral"),
    planned("C", "cst_set_mesh_density", mesh_type="Tet", cells_per_wavelength=20, min_cells=5),
    *[
        planned(
            "C", "cst_add_mesh_refinement", component="Patch", solid=solid, refinement_factor=factor
        )
        for solid, factor in (("Substrate", 2), ("Patch", 2), ("FeedPin", 4))
    ],
    planned("C", "cst_configure_frequency_domain_solver", **SOLVER),
    *[
        planned(
            "C",
            "cst_add_field_monitor",
            monitor_type="Farfield",
            frequency=f,
            name=f"farfield (f={f:g})",
        )
        for f in FREQUENCIES
    ],
    planned(
        "C", "cst_add_field_monitor", monitor_type="Efield", frequency=2.37, name="e-field (f=2.37)"
    ),
    planned(
        "C", "cst_add_field_monitor", monitor_type="Hfield", frequency=2.37, name="h-field (f=2.37)"
    ),
    planned(
        "C",
        "cst_check_model_setup",
        boundaries=BOUNDARIES,
        ports=[{"port_number": 1, "type": "discrete"}],
        structure_bbox={
            "xmin": -40,
            "xmax": 40,
            "ymin": -30,
            "ymax": 30,
            "zmin": -0.035,
            "zmax": 1.605,
        },
        farfield_frequencies_ghz=FREQUENCIES,
        field_monitor_frequencies_ghz=[2.37],
    ),
    *[
        planned("C", tool)
        for tool in ("cst_get_solver_info", "cst_get_mesh_info", "cst_get_mesh_quality")
    ],
    planned("C", "cst_save_project", path="<PROJECT>"),
    planned("C", "cst_read_project_log"),
    planned("D", "cst_run_simulation_async", solver_type="Frequency Domain"),
    planned("D", "cst_wait_for_simulation", max_wait_s=45),
    planned("D", "cst_get_simulation_status", timeout_s=30),
    planned("D", "cst_read_project_log"),
    *[
        planned("D", tool)
        for tool in ("cst_get_solver_info", "cst_get_mesh_info", "cst_get_mesh_quality")
    ],
    planned("D", "cst_save_project", path="<PROJECT>"),
    planned("D", "cst_project_info"),
    planned("D", "cst_list_results", tree_path="1D Results"),
    planned("D", "cst_list_results", tree_path="2D/3D Results"),
    planned(
        "D", "cst_list_saved_results", project_path="<PROJECT>", contains="", offset=0, limit=50
    ),
    planned("D", "cst_project_tree", tree_path="2D/3D Results", max_depth=6),
    planned("D", "cst_project_tree", tree_path="Farfields", max_depth=3),
    planned("D", "cst_discover_farfield_monitors"),
    *[
        planned(
            "E",
            "cst_read_saved_result",
            project_path="<PROJECT>",
            tree_path="<TREE_S11>",
            run_id="<RUN_ID>",
            format=fmt,
            max_points=0,
        )
        for fmt in ("real_imag", "db", "mag", "phase")
    ],
    *[
        planned(
            "E",
            "cst_get_s_parameters",
            port_in=1,
            port_out=1,
            format=fmt,
            max_points=0,
            run_id="<RUN_ID>",
        )
        for fmt in ("real_imag", "db", "mag", "phase")
    ],
    *[
        planned(
            "E",
            "cst_get_s_parameter_phase",
            port_in=1,
            port_out=1,
            unwrap=unwrap,
            max_points=0,
            run_id="<RUN_ID>",
        )
        for unwrap in (False, True)
    ],
    planned("E", "cst_get_impedance", port=1),
    planned("E", "cst_get_vswr", port=1, max_points=0, run_id="<RUN_ID>"),
    planned("E", "cst_get_smith_chart_data", port=1, z0=50, max_points=0, run_id="<RUN_ID>"),
    planned(
        "E",
        "cst_get_bandwidth",
        port=1,
        threshold_db=-10,
        criterion="S11",
        max_points=0,
        run_id="<RUN_ID>",
    ),
    *[
        planned("F", "cst_get_farfield_metrics", frequency_ghz=f, try_farfield_plot=True)
        for f in FREQUENCIES
    ],
    *[
        planned("F", tool, frequency=2.37)
        for tool in ("cst_get_gain", "cst_get_efficiency", "cst_get_efficiency_breakdown")
    ],
    planned("F", "cst_get_farfield", frequency=2.37, monitor_name="<FF_MONITOR>"),
    *[
        planned(
            "F", "cst_get_pattern_cut", frequency=2.37, plane="custom", phi_cut=phi, theta_cut=90
        )
        for phi in (0, 90)
    ],
    planned(
        "F",
        "cst_get_radiation_pattern_3d",
        frequency=2.37,
        resolution_deg=5,
        coordinate="spherical",
    ),
    planned("F", "cst_get_cross_polarization", frequency=2.37, definition="Ludwig3"),
    planned("F", "cst_export_touchstone", file_path="<EXPORTS>/patch.s1p", format="s1p"),
    *[
        planned(
            "F",
            "cst_export_result",
            result_path="<TREE_S11>",
            output_file=f"<EXPORTS>/s11_native.{fmt}",
            format=fmt,
        )
        for fmt in ("csv", "txt")
    ],
    *[
        planned(
            "F",
            "cst_export_result",
            result_path=f"<{key}>",
            output_file=f"<EXPORTS>/{stem}_2p37GHz.txt",
            format="txt",
        )
        for key, stem in (
            ("TREE_EFIELD", "efield"),
            ("TREE_HFIELD", "hfield"),
            ("TREE_SURFACE_CURRENT", "surface_current"),
        )
    ],
    planned(
        "F",
        "cst_export_farfield",
        file_path="<EXPORTS>/farfield_realized_gain_2p37GHz.csv",
        frequency=2.37,
        format="csv",
        monitor_name="<FF_MONITOR>",
    ),
    planned(
        "F",
        "cst_export_farfield",
        file_path="<EXPORTS>/patch_2p37GHz.ffs",
        frequency=2.37,
        format="ffs",
        monitor_name="<FF_MONITOR>",
    ),
    planned(
        "G",
        "cst_plot_1d_results",
        project_path="<PROJECT>",
        quantities=["s11_db", "impedance", "vswr", "phase", "smith", "efficiency"],
        run_ids=["<RUN_ID>"],
        max_runs=1,
        freq_range_ghz=[2.2, 2.5],
        threshold_db=-10,
        mark_resonance=True,
        formats=["png", "pdf"],
        width="single",
        out_dir="<FIGURES>/one_d",
        title="Rectangular patch",
        simulate_if_missing=False,
    ),
    *[
        planned(
            "G",
            "cst_plot_farfield",
            project_path="<PROJECT>",
            frequency_ghz=2.37,
            quantity=quantity,
            polarization_basis="spherical",
            step_deg=5,
            cuts=["phi=0", "phi=90"],
            plots=["polar", "rect", "heatmap", "3d"],
            heatmap_projection="theta_phi",
            dynamic_range_db=30,
            formats=["png", "pdf"],
            width="double",
            out_dir=f"<FIGURES>/farfield_{quantity}",
            file_stem=f"patch_{quantity}_2p37GHz",
        )
        for quantity in ("realized_gain", "directivity")
    ],
    planned("I", "cst_read_project_log"),
    planned("I", "cst_save_project", path="<PROJECT>"),
    planned("I", "cst_close_project"),
    planned("I", "cst_connection_status"),
    planned(
        "I",
        "cst_list_saved_results",
        project_path="<PROJECT>",
        contains="S-Parameters",
        offset=0,
        limit=50,
    ),
    planned(
        "I",
        "cst_read_saved_result",
        project_path="<PROJECT>",
        tree_path="<TREE_S11>",
        run_id="<RUN_ID>",
        format="real_imag",
        max_points=0,
    ),
    planned("I", "cst_open_project", path="<PROJECT>"),
    planned("I", "cst_execute_vba", code=UNITS_QUERY),
    planned("I", "cst_execute_vba", code=SHAPES_QUERY),
    planned("I", "cst_list_ports"),
    planned("I", "cst_project_info"),
    planned("I", "cst_connection_status"),
    planned(
        "I",
        "cst_read_saved_result",
        project_path="<PROJECT>",
        tree_path="<TREE_S11>",
        run_id="<RUN_ID>",
        format="real_imag",
        max_points=0,
    ),
]


def positive(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError("Timeout must be positive and finite")
    return value


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--preflight", action="store_true", help="Disabled-server catalog and schemas only"
    )
    mode.add_argument(
        "--resume",
        action="store_true",
        help="Continue saved results after an idle phase D stop without rebuilding or solving",
    )
    parser.add_argument(
        "--cst-path",
        default=None,
        help=f"Selected installation. Default discovers CST 2025, preferring {DEFAULT_CST_PATH}",
    )
    parser.add_argument("--cst-version", default="2025")
    parser.add_argument("--solver-timeout-s", type=positive, default=1800)
    return parser.parse_args(argv)


class Patch(Dipole):
    """Reuse generic transport/evidence methods, with a separate manifest and physics.

    Inherited methods are schema validation, request logging, immediate artifact
    preservation, project logs, pagination, ownership/unit checks, curve identities,
    Touchstone comparison and evidence persistence. Dipole radiation checks and
    its manifest, run loop, output preparation and report are never invoked.
    """

    def __init__(self, args, parent=BATCH):
        super().__init__(args, parent)
        self.base = self.parent / ("preflight/patch" if args.preflight else "patch")
        self.results, self.reports = self.base / "results", self.base / "reports"
        self.project = self.base / "project/patch.cst"
        self.pending_solve, self.solve_started = False, None
        self.ff_monitors = {}
        self.protocol_prefix = ""
        self.resume_pid = None
        self.resume_owned_pid = None
        self.resume_ready = False
        self.metadata.update(
            output_directory=str(self.base),
            model={
                "reference": REFERENCE,
                "component": "Patch",
                "bricks_mm": BRICKS,
                "materials": MATERIALS,
                "port": PORT,
                "units": EXPECTED_UNITS,
                "solver": SOLVER,
                "boundaries": BOUNDARIES,
                "symmetry": "none",
                "mesh": {
                    "type": "Tetrahedral",
                    "cells_per_wavelength": 20,
                    "min_cells": 5,
                    "refinement": {"Substrate": 2, "Patch": 2, "FeedPin": 4},
                },
                "monitors_ghz": {"farfield": FREQUENCIES, "efield": [2.37], "hfield": [2.37]},
                "modeling_differences": [
                    "0.035 mm metal thickness",
                    "0.10 mm excitation interruption below the probe",
                ],
                "diagnostic_criteria": {
                    "resonance_ghz": 2.37,
                    "relative_tolerance": 0.03,
                    "directivity_dbi": 5.84,
                    "tolerance_db": 1,
                    "published_uncertainty_bounds": False,
                },
            },
            limitations=[
                "One fixed solve supplies no mesh convergence evidence",
                "Relative local refinement does not guarantee element size or elements through the 1.57 mm substrate",
                "Native enumeration verifies shape names and assigned materials, not independent dimensions or material properties",
                "Installed material definitions are source provenance, not project property readback",
                "cst_list_ports may supply no structured discrete-port attributes",
                "cst_check_model_setup inspects supplied settings only and its power-loss warnings concern a time-domain route",
                "cst_get_solver_info has partial generic Solver queries, not full FDSolver configuration readback",
                "CST 2025 help describes AutoNormImpedance=False as port normalization. Requested native settings are emitted and 50 ohm consistency must be verified",
                "Missing loss curves are unavailable measurements, not measured zero loss",
                "Secondary metrics and field exports can be unsupported or inconclusive",
                "Figure content requires user visual inspection after the run",
            ],
        )

    def check(
        self,
        name,
        verdict,
        expected=None,
        obtained=None,
        tolerance=None,
        evidence=None,
        reason=None,
        mandatory=False,
    ):
        if isinstance(verdict, bool):
            verdict = "passed" if verdict else "failed"
        self.checks.append(
            {
                "name": name,
                "verdict": verdict,
                "phase": self.phase,
                "expected": expected,
                "obtained": obtained,
                "tolerance": tolerance,
                "evidence": evidence,
                "reason": reason,
                "mandatory": mandatory or name in MANDATORY,
            }
        )
        return verdict == "passed"

    def configure_environment(self):
        super().configure_environment()
        self.env["CST_TOOLSETS"] += ",boolean"
        self.metadata["child_environment"]["CST_TOOLSETS"] = self.env["CST_TOOLSETS"]
        self.metadata["sdk_compatibility"].update(
            solver_request_budget_s=120,
            bounded_monitor_wait_s=45,
            overall_solver_budget_s=self.args.solver_timeout_s,
        )
        info = self.metadata["installation"]
        info.pop("copper_provenance", None)
        info["patch_material_provenance"] = []
        for alias, library in MATERIALS:
            path = Path(info["path"]) / "Library/Materials" / f"{library}.mtd"
            self.check("installed material " + library, path.is_file(), obtained=str(path))
            if path.is_file():
                raw = read_text(path)
                definition = raw.split("[Definition]", 1)[-1].split("[Type]", 1)[0]
                properties = dict(
                    re.findall(r'^\s*\.([A-Za-z]+)\s+"([^"\r\n]+)"\s*$', definition, re.MULTILINE)
                )
                expected = (
                    {"Type": "PEC"}
                    if alias == "PatchPEC"
                    else {
                        "Epsilon": "2.33",
                        "Mu": "1.0",
                        "Kappa": "0.0",
                        "TanD": "0.0",
                        "KappaM": "0.0",
                        "TanDM": "0.0",
                    }
                )
                self.check(
                    "installed definition " + alias,
                    all(properties.get(k) == v for k, v in expected.items()),
                    expected=expected,
                    obtained=properties,
                    reason="Read-only source provenance. Native project assignment is enumerated separately",
                )
                info["patch_material_provenance"].append(
                    {
                        "alias": alias,
                        "library": library,
                        "path": str(path),
                        "sha256": sha256(path),
                        "text": raw,
                        "source_only": True,
                    }
                )
        write_json(self.results / "protocol/import_probe.json", info)
        for phase in self.phases:
            if phase["phase"] == "installation":
                phase["outcome"] = (
                    "failed"
                    if any(
                        c["phase"] == "installation" and c["verdict"] == "failed"
                        for c in self.checks
                    )
                    else "passed"
                )
        self.persist()

    def prepare_resume(self):
        """Restore the stopped invocation without replacing its project or evidence."""
        if self.base.resolve() != (self.parent / "patch").resolve():
            raise Stop("Resume requires the fixed patch output directory")
        reject_links(self.parent, recursive=False)
        reject_links(self.base)
        for directory in (
            self.results,
            self.reports,
            *(
                self.results / name
                for name in ("mcp", "protocol", "cst_logs", "exports", "figures", "work")
            ),
        ):
            if not directory.is_dir():
                raise Stop(f"Resume evidence directory missing: {directory}")
        metadata = json.loads(read_text(self.results / "metadata.json"))
        validation = json.loads(read_text(self.results / "validation.json"))
        if metadata.get("mode") != "live" or metadata.get("state_unknown"):
            raise Stop("Resume requires evidence from a live invocation with known native state")
        if (
            metadata.get("last_solver_wait", {}).get("run_info", {}).get("last_reported_state")
            == "ERROR"
        ):
            raise Stop(
                "The saved solve failed in CST and cannot supply a complete result set for resume"
            )
        if any(
            p["phase"] in {"E", "F", "G", "I"} and p.get("elapsed_s", 0) > 0
            for p in validation["phases"]
        ):
            raise Stop(
                "This resume route is limited to a workflow stopped at phase D result discovery"
            )
        idle = any(
            c["name"] == "native solve idle" and c["verdict"] == "passed"
            for c in validation["checks"]
        )
        if not idle or not any(
            p["phase"] == "C" and p["outcome"] == "passed" for p in validation["phases"]
        ):
            raise Stop(
                "Saved evidence does not establish completed configuration and an idle solver"
            )
        project = Path(metadata.get("project_path") or "").resolve()
        if (
            project.parent != (self.base / "project").resolve()
            or project.suffix.lower() != ".cst"
            or not project.is_file()
        ):
            raise Stop("Resume project is not an existing owned native project")
        calls = [
            json.loads(line)
            for line in read_text(self.reports / "calls.jsonl").splitlines()
            if line.strip()
        ]
        if [c["id"] for c in calls] != list(range(1, len(calls) + 1)):
            raise Stop("Existing call numbering is not contiguous")
        records = validation["calls"]
        if [r["request"] for r in records] != calls:
            raise Stop("Incomplete request evidence prevents safe resume")
        responses = []
        original_pid = None
        for record in records:
            evidence = Path(record["evidence"]).resolve()
            if evidence.parent != (self.results / "mcp").resolve() or not evidence.is_file():
                raise Stop("Resume request evidence escaped its owned directory or is missing")
            raw = json.loads(read_text(evidence))
            payload = raw.get("payload", {})
            rejected = raw.get("tool_execution") != "accepted"
            responses.append(
                {
                    "id": raw["request"]["id"],
                    "phase": raw["phase"],
                    "tool": raw["request"]["tool"],
                    "arguments": raw["request"]["arguments"],
                    "payload": payload,
                    "artifacts": raw.get("artifacts", []),
                    "evidence": str(evidence),
                    "rejected": rejected,
                }
            )
            if raw["request"]["tool"] == "cst_connect" and payload.get("newly_started") is True:
                original_pid = payload.get("de_pid")
        if not isinstance(original_pid, int):
            raise Stop("Resume lacks original owned CST process provenance")
        self.project, self.metadata, self.records, self.responses = (
            project,
            metadata,
            records,
            responses,
        )
        self.checks, self.phases = validation["checks"], validation["phases"]
        self.logs, self.inventory = validation["project_logs"], validation["artifact_inventory"]
        self.seq, self.resume_pid = len(calls), original_pid
        if self.args.cst_path is None:
            self.args.cst_path = metadata["installation"]["path"]
        if self.args.cst_version != metadata["installation"]["version_selected"]:
            raise Stop("Resume must use the original selected CST version")
        attempt = {
            "started_utc": timestamp(),
            "prior_outcome": validation["outcome"],
            "prior_exit_code": validation["exit_code"],
            "continued_after_call": self.seq,
            "project_sha256_before": sha256(project),
            "solver_execution_allowed": False,
        }
        self.metadata.setdefault("resume_attempts", []).append(attempt)
        self.protocol_prefix = f"resume_{len(self.metadata['resume_attempts']):02d}_"
        for check in self.checks:
            if check["verdict"] == "blocked" or (
                check["name"] == "expected native results" and check["verdict"] == "failed"
            ):
                check["original_verdict"] = check["verdict"]
                check["verdict"] = "superseded"
                check["superseded_reason"] = (
                    "Result discovery and remaining phases will be reassessed by explicit resume"
                )
        for phase in self.phases:
            if phase["phase"] in {"D", "E", "F", "G", "I"}:
                phase["original_outcome"] = phase["outcome"]
                phase["outcome"] = "superseded"
        self.reason, self.exit_code = "Resuming saved results. No solver execution is permitted", 2
        self.prepared = True

    def original_process_running(self):
        """Query only the recorded Windows process, without sending a signal."""
        if sys.platform != "win32":
            raise Stop("Owned-instance resume currently requires Windows process identity checks")
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.GetExitCodeProcess.restype = wintypes.BOOL
        api.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.QueryFullProcessImageNameW.restype = wintypes.BOOL
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, self.resume_pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return False
            raise Stop("Cannot verify the recorded CST process identity")
        try:
            code = wintypes.DWORD()
            if not api.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise Stop("Cannot query the recorded CST process")
            if code.value != 259:
                return False
            buffer, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                raise Stop("Cannot read the recorded CST executable path")
            installation = Path(self.metadata["installation"]["path"])
            expected = {
                installation / "CST DESIGN ENVIRONMENT.exe",
                installation / "AMD64/CST DESIGN ENVIRONMENT_AMD64.exe",
            }
            if Path(buffer.value).resolve() not in {p.resolve() for p in expected}:
                raise Stop("Recorded PID now belongs to a different executable")
            return True
        finally:
            api.CloseHandle(handle)

    async def reconnect_resume(self, session):
        self.phase = "R"
        start, utc = time.monotonic(), timestamp()
        original_running = self.original_process_running()
        if not original_running:
            ensure_closed(self.project)
        state = await self.request(session, "cst_connection_status", {})
        self.require(
            "resume server initially detached",
            state.get("mode") == "offline" and not state.get("project_open"),
        )
        args = {"mode": "any" if original_running else "new"}
        payload = await self.request(session, "cst_connect", args)
        self.verify_native("cst_connect", args, payload)
        if not original_running:
            state = await self.request(session, "cst_connection_status", {})
            self.verify_native("cst_connection_status", {}, state)
            payload = await self.request(session, "cst_open_project", {"path": str(self.project)})
            self.verify_native("cst_open_project", {"path": str(self.project)}, payload)
        for tool, args in (
            ("cst_connection_status", {}),
            ("cst_project_info", {}),
            ("cst_get_simulation_status", {"timeout_s": 30}),
        ):
            payload = await self.request(session, tool, args)
            self.verify_native(tool, args, payload)
        self.resume_ready = True
        for tool, args in (
            ("cst_execute_vba", {"code": UNITS_QUERY}),
            ("cst_execute_vba", {"code": SHAPES_QUERY}),
            ("cst_read_project_log", {}),
        ):
            payload = await self.request(session, tool, args)
            self.verify_native(tool, args, payload)
        self.phases.append(
            {
                "phase": "R",
                "label": "Verify owned saved project and resume without solving",
                "started_utc": utc,
                "elapsed_s": time.monotonic() - start,
                "outcome": "passed",
            }
        )
        self.persist()

    async def protocol(self, session):
        self.phase = "protocol"
        start, utc = time.monotonic(), timestamp()
        init, self.catalog = await initialize_catalog(
            session, self.results / "protocol", self.protocol_prefix
        )
        self.metadata["server_initialization"] = init
        planned_calls, mismatches = [], []
        for index, item in enumerate(MANIFEST, 1):
            arguments = self.resolve(item["arguments"], representative=True)
            try:
                self.validate(item["tool"], arguments)
                outcome = "passed"
            except Exception as exc:  # noqa: BLE001 - preserve every schema discrepancy
                outcome = "failed"
                mismatches.append(
                    {"manifest_index": index, "tool": item["tool"], "discrepancy": str(exc)}
                )
            planned_calls.append(
                {
                    "manifest_index": index,
                    **item,
                    "representative_arguments": arguments,
                    "schema_validation": outcome,
                    "execution": "not_executed",
                }
            )
        write_json(
            self.results / f"protocol/{self.protocol_prefix}planned_manifest.json", planned_calls
        )
        self.metadata["schema_discrepancies"] = mismatches
        self.check("complete planned manifest schemas", not mismatches, obtained=mismatches)
        self.phases.append(
            {
                "phase": "protocol",
                "started_utc": utc,
                "elapsed_s": time.monotonic() - start,
                "outcome": "failed" if mismatches else "passed",
            }
        )
        if self.args.preflight:
            state = await self.request(session, "cst_connection_status", {})
            self.require(
                "disabled connection status",
                state.get("mode") == "offline" and state.get("project_open") is False,
            )
            for phase, label in PHASES.items():
                self.check(
                    f"{phase}: {label}",
                    "not_executed",
                    reason="Preflight permits connection status only",
                )
        if mismatches or any(c["verdict"] == "failed" for c in self.checks):
            raise Stop("Schema or installation prerequisites failed before connecting")

    def resolve(self, template, representative=False):
        # Generic placeholder handling is inherited. Dummy identifiers validate schemas only.
        if representative:
            previous = self.bindings
            self.bindings = {
                "RUN_ID": 0,
                "TREE_S11": r"1D Results\S-Parameters\S1,1",
                "TREE_EFIELD": "representative E field",
                "TREE_HFIELD": "representative H field",
                "TREE_SURFACE_CURRENT": "representative surface current",
                "FF_MONITOR": "representative farfield",
            }
            try:
                return super().resolve(template)
            finally:
                self.bindings = previous
        return super().resolve(template)

    async def request(self, session, tool, arguments, probe=False):
        if self.args.resume:
            if tool in {
                "cst_run_simulation",
                "cst_run_simulation_async",
                "cst_wait_for_simulation",
                "cst_pause_simulation",
                "cst_resume_simulation",
                "cst_stop_simulation",
            }:
                raise Stop("Resume cannot start, wait for, retry or control a simulation")
            if not self.resume_ready and tool not in {
                "cst_connection_status",
                "cst_connect",
                "cst_project_info",
                "cst_get_simulation_status",
                "cst_open_project",
            }:
                raise Stop(
                    "Resume ownership and idle checks must complete before further CST calls"
                )
            if tool == "cst_execute_vba" and arguments.get("code") not in {
                UNITS_QUERY,
                SHAPES_QUERY,
            }:
                raise Stop("Resume permits read-only unit and shape queries only")
            if tool.startswith(
                (
                    "cst_create_",
                    "cst_configure_",
                    "cst_set_",
                    "cst_add_",
                    "cst_boolean_",
                    "cst_load_",
                )
            ):
                raise Stop("Resume cannot construct or reconfigure the saved model")
        if self.pending_solve and tool not in {"cst_wait_for_simulation"}:
            raise Incomplete("Only bounded waits are permitted while the owned solve is pending")
        return await super().request(session, tool, arguments, probe=probe)

    async def diagnose(self, session):
        if not self.pending_solve:
            await super().diagnose(session)

    def archive(self, raw, before, call_id):
        # Native responses can spell the same Windows path with either separator.
        # Keep the complete raw response, but count each resolved output only once.
        start = len(self.inventory)
        artifacts = super().archive(raw, before, call_id)
        unique = {}
        for artifact in artifacts:
            key = (
                str(Path(artifact["original"]).resolve()).casefold(),
                str(Path(artifact["archived"]).resolve()).casefold(),
            )
            unique.setdefault(key, artifact)
        self.inventory[start:] = list(unique.values())
        return list(unique.values())

    def artifact_contract(self, tool, arguments, response):
        super().artifact_contract(tool, arguments, response)
        if tool in {"cst_export_result", "cst_export_farfield", "cst_export_touchstone"}:
            requested = Path(arguments.get("output_file", arguments.get("file_path"))).resolve()
            produced = [
                a for a in response["artifacts"] if Path(a["original"]).resolve() == requested
            ]
            self.check(
                "requested export file " + str(response["id"]),
                requested.is_file()
                and len(produced) == 1
                and produced[0]["fresh"]
                and produced[0]["bytes"] > 0,
                expected=str(requested),
                obtained=produced,
                evidence=response["evidence"],
                mandatory=tool == "cst_export_touchstone",
            )

    def discover(self):
        for key in ("TREE_S11", "RUN_ID"):
            self.bindings.pop(key, None)
        matches = [
            e
            for e in self.entries
            if e.get("tree_path", "").replace("/", "\\").strip("\\").casefold()
            == "1d results\\s-parameters\\s1,1"
        ]
        if len(matches) == 1:
            runs = matches[0].get("run_ids", [])
            if len(runs) == 1 and isinstance(runs[0], int):
                self.bindings.update(TREE_S11=matches[0]["tree_path"], RUN_ID=runs[0])
            else:
                self.check("single native result run", "inconclusive", obtained=runs)
        paths = sorted(set(self.tree_items + [e.get("tree_path", "") for e in self.entries]))
        for key, category in (
            ("TREE_EFIELD", "e-field"),
            ("TREE_HFIELD", "h-field"),
            ("TREE_SURFACE_CURRENT", "surface current"),
        ):
            candidates = [
                p
                for p in paths
                if category in p.lower() and self.path_frequency(p) == TARGET_FREQUENCY
            ]
            if key == "TREE_HFIELD":
                candidates = [p for p in candidates if "surface current" not in p.lower()]
            # Prefer the discovered monitor item over its vector display children.
            # Any native excitation suffix is taken verbatim from discovery.
            roots = [
                p for p in candidates if re.search(r"\(f\s*=\s*[\d.]+\)\s*(?:\[[^\]]+\])?$", p)
            ]
            selected = roots or candidates
            if len(selected) == 1:
                self.bindings[key] = selected[0]
            else:
                self.bindings.pop(key, None)
        for frequency in FREQUENCIES:
            candidates = [
                p for p in paths if "farfield" in p.lower() and self.path_frequency(p) == frequency
            ]
            labels = {
                p.split("\\")[-1]
                for p in candidates
                if not any(q.startswith(p + "\\") for q in candidates)
            }
            if len(labels) == 1:
                self.ff_monitors[frequency] = next(iter(labels))
        if TARGET_FREQUENCY in self.ff_monitors:
            self.bindings["FF_MONITOR"] = self.ff_monitors[TARGET_FREQUENCY]

    @staticmethod
    def path_frequency(path):
        match = re.search(r"\(f\s*=\s*([\d.]+)\)", str(path), re.IGNORECASE)
        return float(match.group(1)) if match else None

    def verify_native(self, tool, arguments, payload):
        evidence = self.responses[-1]["evidence"]
        if tool == "cst_connect" and self.args.resume:
            self.connected = payload.get("status") == "connected"
            pid = payload.get("de_pid")
            if arguments["mode"] == "any":
                owned = (
                    self.connected
                    and pid == self.resume_pid
                    and payload.get("newly_started") is False
                )
                owned = (
                    owned
                    and Path(payload.get("project_path") or "").resolve() == self.project.resolve()
                )
            else:
                owned = (
                    self.connected
                    and payload.get("newly_started") is True
                    and not payload.get("open_project_paths")
                )
            if not owned:
                self.unknown = True
                raise Incomplete(
                    "Resume attached to an unverified CST instance or project. No further CST calls"
                )
            self.resume_owned_pid = pid
            self.require("resume owned CST instance", owned, obtained=payload, evidence=evidence)
        elif tool == "cst_execute_vba" and arguments["code"] == SHAPES_QUERY:
            shapes = parse_shapes(payload.get("output", ""))
            expected = {"Patch:" + b["name"]: b["material"] for b in BRICKS}
            self.require(
                "native construction",
                shapes == expected,
                expected=expected,
                obtained=shapes,
                evidence=evidence,
                reason="Insert must retain FeedPin with its PEC assignment",
            )
        elif tool == "cst_load_material":
            self.metadata.setdefault("material_load_mcp_evidence", []).append(
                {"call_id": self.seq, "payload": payload}
            )
        elif tool == "cst_get_simulation_status":
            if payload.get("status") != "ok" or not isinstance(payload.get("running"), bool):
                self.unknown = True
                raise Incomplete("Native simulation status is unknown")
            super().verify_native(tool, arguments, payload)
        elif tool == "cst_get_solver_info":
            for key, expected in (("fmin", 2.2), ("fmax", 2.5), ("number_of_ports", 1)):
                value = payload.get(key)
                self.check(
                    "native solver " + key,
                    "inconclusive" if value is None else compare([value], [expected], 1e-6),
                    expected=expected,
                    obtained=value,
                    evidence=evidence,
                )
            self.check(
                "FDSolver configuration readback",
                "inconclusive",
                obtained=payload.get("unavailable"),
                reason="Generic Solver queries do not independently verify tetrahedral residual, interval or adaptation settings",
                evidence=evidence,
            )
        elif tool == "cst_check_model_setup":
            self.check(
                "setup diagnostic coverage",
                "inconclusive",
                obtained=payload,
                reason="Checks supplied configuration only. No native tetrahedral adaptation or loss readback",
                evidence=evidence,
            )
        elif tool in {"cst_get_mesh_info", "cst_get_mesh_quality"}:
            self.check(
                "available native mesh evidence",
                "passed" if payload.get("source") else "inconclusive",
                obtained=payload,
                evidence=evidence,
                reason="Available counts and edges do not prove substrate resolution. Unavailable queries remain explicit",
            )
        elif tool == "cst_discover_farfield_monitors":
            for frequency in FREQUENCIES:
                labels = {
                    str(m.get("tree_path") or m.get("name") or m.get("monitor_name")).split("\\")[
                        -1
                    ]
                    for m in payload.get("monitors", [])
                    if m.get("frequency_ghz") == frequency
                    and (m.get("tree_path") or m.get("name") or m.get("monitor_name"))
                }
                if len(labels) == 1:
                    self.ff_monitors[frequency] = next(iter(labels))
            self.discover()
        elif tool == "cst_list_results":
            self.tree_items.extend(
                i["path"] for i in payload.get("items", []) if isinstance(i, dict) and "path" in i
            )
            self.discover()
        else:
            super().verify_native(tool, arguments, payload)

    async def wait_for_solve(self, session, arguments):
        while True:
            if time.monotonic() - self.solve_started >= self.args.solver_timeout_s:
                self.unknown = True
                raise Incomplete(
                    "Overall solver budget exhausted. Owned solve may still be running. No stop or further CST calls"
                )
            payload = await self.request(session, "cst_wait_for_simulation", arguments)
            state = payload.get("status")
            self.metadata["last_solver_wait"] = payload
            if state in {"starting", "running"}:
                if payload.get("running") is not (state == "running"):
                    self.unknown = True
                    raise Incomplete("Wait returned inconsistent running state")
                self.persist()
                continue
            if state == "finished" and payload.get("running") is False:
                self.pending_solve = False
                self.check(
                    "wait reports idle",
                    "passed",
                    obtained=payload,
                    reason="Idle is not success. Native results must exist before numerical validation",
                )
                return payload
            self.unknown = True
            raise Incomplete("Unrecognized native wait state. Subsequent CST operations blocked")

    async def live(self, session):
        self.metadata["live_phases_executed"] = True
        for phase, label in PHASES.items():
            if self.args.resume and phase in {"A", "B", "C"}:
                continue
            self.phase = phase
            start, utc, outcome = time.monotonic(), timestamp(), "passed"
            try:
                for index, item in enumerate(MANIFEST, 1):
                    if item["phase"] != phase:
                        continue
                    self.active_manifest_index = index
                    tool = item["tool"]
                    if (
                        self.args.resume
                        and phase == "D"
                        and tool
                        not in {
                            "cst_list_results",
                            "cst_list_saved_results",
                            "cst_project_tree",
                            "cst_discover_farfield_monitors",
                        }
                    ):
                        continue
                    if phase in {"E", "F", "G", "I"} and not self.idle:
                        raise Incomplete(
                            "Extraction and persistence require a responsive, known idle session"
                        )
                    try:
                        arguments = self.resolve(item["arguments"])
                    except Incomplete as exc:
                        self.check(
                            f"manifest {index}: {tool}",
                            "blocked" if phase == "E" else "inconclusive",
                            reason=str(exc),
                        )
                        continue
                    ancillary = tool in {
                        "cst_list_ports",
                        "cst_project_tree",
                        "cst_list_results",
                        "cst_get_solver_info",
                        "cst_get_mesh_info",
                        "cst_get_mesh_quality",
                        "cst_read_project_log",
                        "cst_check_model_setup",
                    }
                    try:
                        if tool == "cst_run_simulation_async":
                            self.solve_started = time.monotonic()
                            self.idle = False
                            payload = await self.request(session, tool, arguments)
                            self.require(
                                "single asynchronous solve accepted",
                                payload.get("status") == "started",
                                obtained=payload,
                                mandatory=True,
                            )
                            self.pending_solve = True
                        elif tool == "cst_wait_for_simulation":
                            payload = await self.wait_for_solve(session, arguments)
                        else:
                            payload = await self.request(session, tool, arguments)
                        response = self.responses[-1]
                        self.artifact_contract(tool, arguments, response)
                        if tool == "cst_list_saved_results":
                            before = dict(self.bindings)
                            self.entries = await self.saved_pages(session, arguments, payload)
                            self.discover()
                            if phase == "I":
                                self.require(
                                    "saved result identifier persistence",
                                    all(
                                        self.bindings.get(k) == before.get(k)
                                        for k in ("TREE_S11", "RUN_ID")
                                    ),
                                    obtained=self.bindings,
                                )
                        if tool not in {"cst_run_simulation_async", "cst_wait_for_simulation"}:
                            self.verify_native(tool, arguments, payload)
                        if phase in {"E", "I"}:
                            self.validate_s(tool, arguments, payload, response["evidence"])
                        if tool in {
                            "cst_get_farfield",
                            "cst_get_radiation_pattern_3d",
                            "cst_export_farfield",
                        }:
                            self.assess_farfield_export(response)
                        if tool == "cst_export_touchstone":
                            self.validate_touchstone(response)
                        if tool.startswith("cst_plot_"):
                            self.figure_source_checks(arguments, response)
                            if tool == "cst_plot_farfield":
                                source = payload.get("source", {}).get("path")
                                artifacts = [
                                    a
                                    for a in response["artifacts"]
                                    if a["original"] == source and a["fresh"]
                                ]
                                if len(artifacts) == 1:
                                    grid = self.grid_from_source(
                                        response, artifacts[0], arguments["quantity"]
                                    )
                                    if arguments["quantity"] == "directivity":
                                        self.directivity = grid
                                    else:
                                        self.realized = grid
                        if tool == "cst_export_result":
                            self.check_field_export(arguments, response)
                        if tool in {
                            "cst_get_farfield_metrics",
                            "cst_get_gain",
                            "cst_get_efficiency",
                            "cst_get_efficiency_breakdown",
                        }:
                            frequency = arguments.get("frequency_ghz", arguments.get("frequency"))
                            tree = payload.get("tree_path") or payload.get("sources", {}).get(
                                "farfield_plot", {}
                            ).get("tree_path")
                            self.check(
                                "radiation metric frequency " + str(response["id"]),
                                "inconclusive"
                                if self.path_frequency(tree) is None
                                else self.path_frequency(tree) == frequency,
                                expected=frequency,
                                obtained=tree,
                                evidence=response["evidence"],
                                reason="Nearest 1D efficiency samples alone do not prove exact monitor frequency",
                            )
                    except Stop as exc:
                        outcome = "partial"
                        if phase in {"A", "B", "C", "D"} and not ancillary:
                            await self.diagnose(session)
                            raise
                        if phase == "I" and tool in {
                            "cst_save_project",
                            "cst_close_project",
                            "cst_connection_status",
                            "cst_open_project",
                        }:
                            raise
                        self.check(
                            f"manifest {index}: {tool} capability", "inconclusive", reason=str(exc)
                        )
                        await self.diagnose(session)
                    except Incomplete as exc:
                        if self.unknown or phase in {"A", "B", "C", "D"}:
                            raise
                        outcome = "partial"
                        self.check(
                            f"manifest {index}: {tool} assessment", "inconclusive", reason=str(exc)
                        )
                if phase == "D":
                    wait = self.metadata.get("last_solver_wait", {})
                    info = wait.get("run_info", {})
                    if wait.get("solve_observed") and info.get("last_reported_state") == "ERROR":
                        message = (
                            info.get("message")
                            or "CST reported an error for the observed solver execution"
                        )
                        self.check(
                            "native solver outcome",
                            False,
                            expected="Observed solve completed without native errors",
                            obtained=info,
                            reason=message,
                            mandatory=True,
                            evidence=[
                                r["evidence"]
                                for r in self.responses
                                if r["phase"] == "D"
                                and r["tool"] in {"cst_wait_for_simulation", "cst_read_project_log"}
                            ],
                        )
                        raise Stop(f"CST solver failed: {message}")
                    self.require(
                        "expected native results",
                        self.idle
                        and "TREE_S11" in self.bindings
                        and all(f in self.ff_monitors for f in FREQUENCIES),
                        obtained=self.bindings,
                        reason="Owned project must supply a unique main S11 run and the three requested farfield results",
                    )
                if phase == "E":
                    if self.baseline is None:
                        raise Incomplete("Fresh full complex S11 missing")
                    self.assess_s11()
                if phase == "G":
                    self.assess_radiation()
            except BaseException:
                outcome = "incomplete" if self.unknown else "stopped"
                raise
            finally:
                self.phases.append(
                    {
                        "phase": phase,
                        "label": label,
                        "started_utc": utc,
                        "elapsed_s": time.monotonic() - start,
                        "outcome": outcome,
                    }
                )
                self.persist()

    def validate_s(self, tool, arguments, payload, evidence):
        if (
            tool == "cst_read_saved_result"
            and arguments.get("format") == "real_imag"
            and self.baseline is None
        ):
            x, values = complex_curve(payload)
            self.require(
                "full S11",
                payload.get("source") == "cst.results"
                and self.idle
                and self.project.is_file()
                and Path(payload.get("project_path") or "").resolve() == self.project.resolve()
                and payload.get("tree_path") == self.bindings.get("TREE_S11")
                and payload.get("run_id") == self.bindings.get("RUN_ID"),
                obtained={"n": len(x), "source": payload.get("source")},
                evidence=evidence,
            )
            self.baseline, self.baseline_source = (x, values), payload
            self.require(
                "S11 frequency order",
                all(b > a for a, b in pairwise(x)),
                obtained=x,
                reason="Duplicate or unordered samples are never silently discarded",
            )
            self.check(
                "S11 actual frequency range",
                compare([min(x), max(x)], [2.2, 2.5], 1e-6),
                expected=[2.2, 2.5],
                obtained=[min(x), max(x)],
                tolerance=1e-6,
                evidence=evidence,
                mandatory=True,
            )
            write_json(
                self.results / "exports/independent_s11.json",
                {"x_ghz": x, "derived": derive(values), "bandwidth": bandwidth(x, values)},
            )
        else:
            super().validate_s(tool, arguments, payload, evidence)

    def assess_s11(self):
        x, values = self.baseline
        base_samples = [2.2 + i * 0.015 for i in range(21)]
        self.check(
            "requested discrete sample coverage",
            all(any(abs(f - actual) <= 1e-7 for actual in x) for f in base_samples),
            expected=base_samples,
            obtained=x,
            tolerance=1e-7,
            mandatory=True,
            reason="The full discrete curve must contain the requested equidistant samples. Monitor samples may add points",
        )
        derived = derive(values)
        z = derived["z"]
        crossings = []
        for i, value in enumerate(z):
            if value is None:
                continue
            if value.imag == 0:
                crossings.append({"frequency_ghz": x[i], "bracket_ghz": [x[i], x[i]]})
            elif i and z[i - 1] is not None and z[i - 1].imag * value.imag < 0:
                a, b = z[i - 1].imag, value.imag
                crossings.append(
                    {
                        "frequency_ghz": x[i - 1] - a * (x[i] - x[i - 1]) / (b - a),
                        "bracket_ghz": [x[i - 1], x[i]],
                    }
                )
        resonance = (
            min(crossings, key=lambda c: abs(c["frequency_ghz"] - TARGET_FREQUENCY))
            if crossings
            else None
        )
        minimum = min(range(len(values)), key=lambda i: abs(values[i]))
        bands = bandwidth(x, values)
        resolution = [b - a for a, b in pairwise(x)]
        metrics = {
            "impedance_zero_crossings": crossings,
            "selected_resonance": resonance,
            "minimum_s11_frequency_ghz": x[minimum],
            "minimum_s11_db": derived["db"][minimum],
            "matched_bands_minus10db": bands,
            "actual_sample_count": len(x),
            "minimum_step_ghz": min(resolution) if resolution else None,
            "maximum_step_ghz": max(resolution) if resolution else None,
            "requested_equidistant_step_ghz": 0.015,
            "sampling_note": "Monitor frequencies can add solved samples. Resonance uses linear reactance interpolation. Band edges use magnitude interpolation. The brackets and actual steps limit resolution. No refinement run is performed",
        }
        write_json(self.results / "exports/patch_s11_assessment.json", metrics)
        self.metadata["s11_assessment"] = metrics
        self.check(
            "patch resonance",
            "inconclusive"
            if resonance is None
            else abs(resonance["frequency_ghz"] / TARGET_FREQUENCY - 1) <= RESONANCE_TOLERANCE,
            expected=TARGET_FREQUENCY,
            obtained=resonance,
            tolerance="3 percent diagnostic criterion, not published uncertainty",
            evidence="results/exports/patch_s11_assessment.json",
            reason="Zero crossing of imaginary input impedance, separate from minimum S11",
        )
        self.check(
            "minus10db matched band observation",
            "passed",
            obtained=bands,
            reason="No matched band is a valid observation. No minimum S11 is imposed",
        )
        numerical = [
            c
            for c in self.checks
            if c["phase"] == "E"
            and (
                c["name"].startswith("independent ")
                or c["name"]
                in {"live and saved complex S11 agree", "Smith impedance independent identity"}
            )
        ]
        needed = {
            "independent cst_get_s_parameters db",
            "independent cst_get_s_parameters mag",
            "independent cst_get_s_parameters phase",
            "live and saved complex S11 agree",
            "independent cst_get_s_parameter_phase derived",
            "independent cst_get_vswr derived",
            "Smith impedance independent identity",
            "independent cst_get_impedance input identity",
            "independent -10 dB bandwidth intervals",
        }
        complete = needed.issubset({c["name"] for c in numerical})
        self.check(
            "full S11 derived consistency",
            "failed"
            if any(c["verdict"] == "failed" for c in numerical)
            else "passed"
            if complete and all(c["verdict"] == "passed" for c in numerical)
            else "inconclusive",
            expected="50 ohm complex S11, magnitude, dB, phase, impedance, VSWR, Smith and bandwidth agreement",
            obtained=[{"name": c["name"], "verdict": c["verdict"]} for c in numerical],
        )

    def grid_from_source(self, response, artifact, quantity):
        grid = farfield_grid(Path(artifact["archived"]), quantity)
        payload = response["payload"]
        tree = payload.get("tree_path") or payload.get("source", {}).get("tree_path")
        if self.path_frequency(tree) is None:
            raise Incomplete("Radiation export has no actual selected native frequency identifier")
        self.require(
            "native radiation frequency " + str(response["id"]),
            self.path_frequency(tree) == TARGET_FREQUENCY,
            expected=TARGET_FREQUENCY,
            obtained=tree,
            evidence=response["evidence"],
            mandatory=True,
        )
        declared = re.search(
            r"(?:frequency|\bf)\s*[=:]\s*([\d.eE+\-]+)\s*(GHz|MHz|Hz)",
            grid["header"],
            re.IGNORECASE,
        )
        if declared:
            f = (
                float(declared.group(1))
                * {"ghz": 1, "mhz": 1e-3, "hz": 1e-9}[declared.group(2).lower()]
            )
            self.require(
                "radiation header frequency " + str(response["id"]),
                abs(f - TARGET_FREQUENCY) <= 1e-9,
                expected=TARGET_FREQUENCY,
                obtained=f,
                evidence=artifact["archived"],
            )
        artifact.update(
            readable=True,
            units=grid["unit"],
            sample_count=grid["sample_count"],
            inspection={k: v for k, v in grid.items() if k not in {"grid", "theta", "phi"}},
        )
        return grid

    def assess_farfield_export(self, response):
        for artifact in response["artifacts"]:
            path = Path(artifact["archived"])
            if not artifact["fresh"]:
                continue
            if path.suffix.lower() == ".ffs":
                info = ffs_source(path)
                artifact.update(
                    readable=True,
                    sample_count=info["sample_count"],
                    units=info["units"],
                    inspection=info,
                )
                self.check(
                    "FFS native structure and counts",
                    info["data_type_farfield"]
                    and info["counts_match"]
                    and info["finite_field_components"]
                    and info["angles_valid"],
                    obtained=info,
                    evidence=str(path),
                )
                self.check(
                    "FFS native frequency",
                    compare(info["frequency_ghz"], [TARGET_FREQUENCY], 1e-9),
                    expected=TARGET_FREQUENCY,
                    obtained=info["frequency_ghz"],
                    evidence=str(path),
                )
            elif path.suffix.lower() in {".txt", ".csv"}:
                grid = self.grid_from_source(response, artifact, "realized_gain")
                self.realized = grid

    def check_field_export(self, arguments, response):
        if "2p37GHz" not in arguments.get("output_file", ""):
            return
        tree = arguments["result_path"]
        expected_path = Path(arguments["output_file"]).resolve()
        artifacts = [
            a for a in response["artifacts"] if Path(a["original"]).resolve() == expected_path
        ]
        self.check(
            "field source selection " + str(response["id"]),
            tree in self.bindings.values()
            and self.path_frequency(tree) == TARGET_FREQUENCY
            and len(artifacts) == 1,
            obtained=tree,
            evidence=response["evidence"],
        )
        for artifact in artifacts:
            table = ascii_table(Path(artifact["archived"]))
            header = table["header"]
            expected_unit = r"V/m" if "efield" in expected_path.name else r"A/m"
            components = table["columns"] >= 6 and bool(
                re.search(r"[EHJ][xyz]|real|imag|component", header, re.IGNORECASE)
            )
            declared_frequency = self.path_frequency(header)
            if declared_frequency is not None:
                self.check(
                    "field header frequency " + str(response["id"]),
                    declared_frequency == TARGET_FREQUENCY,
                    obtained=declared_frequency,
                    expected=TARGET_FREQUENCY,
                )
            self.check(
                "numeric vector field " + expected_path.name,
                "passed"
                if components and re.search(expected_unit, header, re.IGNORECASE)
                else "inconclusive",
                obtained={"tree": tree, "columns": table["columns"], "header": header},
                evidence=artifact["archived"],
                reason="Discovered selected source supplies frequency and quantity. Original header must prove component units",
            )

    def assess_radiation(self):
        metrics_response = next(
            (
                r
                for r in self.responses
                if r["tool"] == "cst_get_farfield_metrics"
                and r["arguments"].get("frequency_ghz") == TARGET_FREQUENCY
                and not r["rejected"]
            ),
            None,
        )
        m = metrics_response["payload"].get("metrics", {}) if metrics_response else {}
        if (
            metrics_response
            and self.path_frequency(metrics_response["payload"].get("tree_path"))
            != TARGET_FREQUENCY
        ):
            self.check(
                "exact radiation summary",
                "inconclusive",
                obtained=m,
                reason="Native selected 2.37 GHz radiation source absent. Summary values are retained without using them for identities",
            )
            m = {}
        native_peak = m.get("max_directivity_dbi", m.get("directivity_dbi"))
        peak = max(self.directivity["grid"].values()) if self.directivity else native_peak
        self.check(
            "patch directivity",
            "inconclusive"
            if peak is None
            else abs(peak - TARGET_DIRECTIVITY_DBI) <= DIRECTIVITY_TOLERANCE_DB,
            expected=TARGET_DIRECTIVITY_DBI,
            obtained=peak,
            tolerance="1 dB diagnostic criterion, not published uncertainty",
            reason="Directivity at 2.37 GHz. Gain and realized gain retain their own definitions",
        )
        if self.directivity and native_peak is not None:
            self.check(
                "native peak and directivity grid",
                abs(native_peak - peak) <= 0.1,
                expected=native_peak,
                obtained=peak,
                tolerance=0.1,
            )
        grid = self.directivity or self.realized
        upper_fraction = None
        if grid:
            upper, lower = 0.0, 0.0
            for i, t in enumerate(grid["theta"]):
                lo = 0 if i == 0 else (grid["theta"][i - 1] + t) / 2
                hi = 180 if i == len(grid["theta"]) - 1 else (grid["theta"][i + 1] + t) / 2
                power = sum(10 ** (grid["grid"][(t, p)] / 10) for p in grid["phi"]) / len(
                    grid["phi"]
                )
                upper += (
                    power * max(0, math.cos(math.radians(lo)) - math.cos(math.radians(min(hi, 90))))
                    if lo < 90
                    else 0
                )
                lower += (
                    power * max(0, math.cos(math.radians(max(lo, 90))) - math.cos(math.radians(hi)))
                    if hi > 90
                    else 0
                )
            upper_fraction = upper / (upper + lower) if upper + lower > 0 else None
        self.check(
            "upper half-space radiation",
            "inconclusive" if upper_fraction is None else upper_fraction > 0.5,
            expected="more than half of integrated radiation into +Z",
            obtained=upper_fraction,
            reason="Solid-angle integration splits cells at theta=90. No reference leakage bound is invented",
        )
        rad = m.get(
            "radiation_efficiency_linear",
            m.get("rad_efficiency_linear", m.get("radiation_efficiency")),
        )
        total = m.get(
            "total_efficiency_linear", m.get("tot_efficiency_linear", m.get("total_efficiency"))
        )
        self.check(
            "lossless radiation efficiency",
            "inconclusive" if rad is None else abs(rad - 1) <= 0.03,
            expected=1,
            obtained=rad,
            tolerance=0.03,
            reason="Initial numerical diagnostic. Absence of loss curves is not zero measured loss",
        )
        if self.baseline and rad is not None and total is not None:
            gamma = self.interpolate_complex(*self.baseline, TARGET_FREQUENCY)
            mismatch = 1 - abs(gamma) ** 2
            self.check(
                "total efficiency mismatch identity",
                abs(total - rad * mismatch) <= 0.03,
                expected="eta_total = eta_rad*(1-|S11|^2)",
                obtained={"radiation": rad, "total": total, "mismatch": mismatch},
                tolerance=0.03,
            )
        else:
            self.check(
                "total efficiency mismatch identity",
                "inconclusive",
                obtained=m,
                reason="Native efficiency values unavailable",
            )
        if (
            self.directivity
            and self.realized
            and total is not None
            and self.directivity["grid"].keys() == self.realized["grid"].keys()
        ):
            errors = [
                abs(10 ** (self.realized["grid"][k] / 10) - 10 ** (v / 10) * total)
                for k, v in self.directivity["grid"].items()
                if v >= peak - 20
            ]
            self.check(
                "realized gain efficiency identity",
                max(errors) <= 0.03,
                expected="G_realized = D*eta_total at common angles",
                obtained=max(errors),
                tolerance=0.03,
            )
        else:
            self.check(
                "realized gain efficiency identity",
                "inconclusive",
                reason="Separate common-angle grids or total efficiency unavailable",
            )
        gain, realized_peak = m.get("max_gain_dbi"), m.get("max_realized_gain_dbi")
        if (
            native_peak is not None
            and realized_peak is not None
            and total is not None
            and total > 0
        ):
            self.check(
                "native realized gain total efficiency identity",
                abs(realized_peak - native_peak - 10 * math.log10(total)) <= 0.1,
                expected="peak realized gain = peak directivity + 10*log10(eta_total)",
                obtained={
                    "directivity_dbi": native_peak,
                    "realized_gain_dbi": realized_peak,
                    "total_efficiency": total,
                },
                tolerance=0.1,
            )
        # The server can fall back to GetMax (realized gain) for max_gain_dbi.
        # Only an original GetMaxGain dump proves independent IEEE gain.
        gain_proven = False
        if metrics_response:
            for artifact in metrics_response["artifacts"]:
                if Path(artifact["archived"]).suffix.lower() == ".txt":
                    gain_proven |= bool(
                        re.search(
                            r"^GetMaxGain=[+\-\d.]+\s*$",
                            read_text(Path(artifact["archived"])),
                            re.MULTILINE,
                        )
                    )
        if (
            gain_proven
            and gain is not None
            and rad is not None
            and rad > 0
            and native_peak is not None
        ):
            self.check(
                "gain radiation efficiency identity",
                abs(gain - native_peak - 10 * math.log10(rad)) <= 0.1,
                expected="G = D*eta_rad",
                obtained={
                    "directivity_dbi": native_peak,
                    "gain_dbi": gain,
                    "realized_gain_dbi": realized_peak,
                },
                tolerance=0.1,
            )
        else:
            self.check(
                "gain radiation efficiency identity",
                "inconclusive",
                obtained=m,
                reason="Independent native gain mode and radiation efficiency are required. A realized gain fallback is not IEEE gain",
            )
        self.assess_polarization()
        self.assess_cuts()
        if self.directivity:
            self.check(
                "directivity sphere normalization",
                abs(sphere_mean(self.directivity) - 1) <= 0.03,
                expected=1,
                obtained=sphere_mean(self.directivity),
                tolerance=0.03,
            )
        if self.realized and total is not None:
            self.check(
                "realized gain integrated efficiency",
                abs(sphere_mean(self.realized) - total) <= 0.03,
                expected=total,
                obtained=sphere_mean(self.realized),
                tolerance=0.03,
            )
        write_json(
            self.results / "exports/patch_radiation_assessment.json",
            {
                "peak_directivity_dbi": peak,
                "upper_radiation_fraction": upper_fraction,
                "native_metrics": m,
                "directivity_sphere_mean": sphere_mean(self.directivity)
                if self.directivity
                else None,
                "realized_gain_sphere_mean": sphere_mean(self.realized) if self.realized else None,
            },
        )

    def assess_polarization(self):
        response = next(
            (
                r
                for r in self.responses
                if r["tool"] == "cst_get_cross_polarization" and not r["rejected"]
            ),
            None,
        )
        payload = response["payload"] if response else {}
        # Accept only explicitly identified basis, angular axes, scale and components.
        theta, phi = payload.get("theta_deg"), payload.get("phi_deg")
        co, cross = payload.get("co_pol_dbi"), payload.get("cross_pol_dbi")
        declared = payload.get("polarization_basis", payload.get("definition")) == "Ludwig3"
        arrays = [theta, phi, co, cross]
        if (
            not declared
            or not all(isinstance(a, list) and a for a in arrays)
            or len({len(a) for a in arrays}) != 1
        ):
            self.assess_ffs_polarization(payload)
            return
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for a in arrays for v in a):
            self.check(
                "linear polarization",
                "inconclusive",
                reason="Nonfinite or unrecognized component values",
            )
            return
        peak = max(co)
        rows = [
            {"theta_deg": t, "phi_deg": p, "co_pol_dbi": c, "cross_pol_dbi": q, "xpd_db": c - q}
            for t, p, c, q in zip(theta, phi, co, cross)
            if t <= 90 and c >= peak - 3
        ]
        self.check(
            "linear polarization",
            "inconclusive" if not rows else all(row["xpd_db"] >= 10 for row in rows),
            expected="Ludwig3 XPD >=10 dB at common angles in the upper co-polarized main lobe",
            obtained=rows,
            tolerance="Initial diagnostic criterion, not a reference uncertainty bound",
            evidence=response["evidence"],
        )

    def assess_ffs_polarization(self, unavailable_payload):
        candidates = [
            a
            for r in self.responses
            if r["tool"] == "cst_export_farfield"
            and not r["rejected"]
            and r["arguments"].get("format") == "ffs"
            for a in r["artifacts"]
            if a["fresh"] and Path(a["archived"]).suffix.lower() == ".ffs"
        ]
        if len(candidates) != 1:
            self.check(
                "linear polarization",
                "inconclusive",
                obtained=unavailable_payload,
                reason="Ludwig3 common-angle components and a unique validated complex FFS source are unavailable",
            )
            return
        path = Path(candidates[0]["archived"])
        try:
            info = ffs_source(path)
            if not (
                info["counts_match"]
                and info["finite_field_components"]
                and info["angles_valid"]
                and info["data_type_farfield"]
                and compare(info["frequency_ghz"], [TARGET_FREQUENCY], 1e-9)
            ):
                raise Incomplete("FFS provenance or field section is unverified")
            rows, in_fields = [], False
            for line in read_text(path).splitlines():
                if line.strip().startswith("//"):
                    # Require the exact declared component order before interpreting columns.
                    in_fields = bool(
                        re.search(
                            r"Phi[,\s]+Theta[,\s]+Re\s*\(E_Theta\)[,\s]+Im\s*\(E_Theta\)[,\s]+Re\s*\(E_Phi\)[,\s]+Im\s*\(E_Phi\)",
                            line,
                            re.IGNORECASE,
                        )
                    )
                    continue
                if in_fields and line.strip():
                    row = [float(v) for v in line.split()]
                    if len(row) != 6:
                        raise Incomplete(
                            "FFS component column count differs from its declared order"
                        )
                    phi, theta, tr, ti, pr, pi = row
                    et, ep = complex(tr, ti), complex(pr, pi)
                    angle = math.radians(phi)
                    # Ludwig3 with co-polarization along +X at boresight.
                    ec = et * math.cos(angle) - ep * math.sin(angle)
                    ex = et * math.sin(angle) + ep * math.cos(angle)
                    rows.append(
                        {
                            "theta_deg": theta,
                            "phi_deg": phi,
                            "co_power": abs(ec) ** 2,
                            "cross_power": abs(ex) ** 2,
                        }
                    )
            if len(rows) != info["sample_count"]:
                raise Incomplete("FFS declared spherical component order is unsupported")
            peak = max(r["co_power"] + r["cross_power"] for r in rows if r["theta_deg"] <= 90)
            usable = [
                r
                for r in rows
                if r["theta_deg"] <= 90
                and r["co_power"] + r["cross_power"] >= peak * 10 ** (-3 / 10)
            ]
            for row in usable:
                row["xpd_db"] = (
                    10 * math.log10(row["co_power"] / row["cross_power"])
                    if row["cross_power"] > 0 and row["co_power"] > 0
                    else math.inf
                    if row["co_power"] > 0
                    else -math.inf
                )
            evidence = self.results / "exports/patch_polarization_assessment.json"
            write_json(
                evidence,
                {
                    "source": str(path),
                    "frequency_ghz": TARGET_FREQUENCY,
                    "basis": "Ludwig3, +X co-polarization at boresight",
                    "formulas": {
                        "co": "Etheta*cos(phi)-Ephi*sin(phi)",
                        "cross": "Etheta*sin(phi)+Ephi*cos(phi)",
                    },
                    "normalization": "ratio of complex field powers at identical angles. FFS is not gain",
                    "main_lobe_samples": usable,
                },
            )
            self.check(
                "linear polarization",
                "inconclusive"
                if not usable or peak <= 0
                else all(r["xpd_db"] >= 10 for r in usable),
                expected="Ludwig3 XPD >=10 dB at common upper main-lobe angles",
                obtained={
                    "samples": len(usable),
                    "minimum_xpd_db": min((r["xpd_db"] for r in usable), default=None),
                },
                tolerance="Initial diagnostic criterion, not published uncertainty",
                evidence=str(evidence),
                reason="Independent complex FFS component rotation with declared +X polarization reference",
            )
        except (Stop, Incomplete, ValueError, OSError) as exc:
            self.check("linear polarization", "inconclusive", reason=str(exc), evidence=str(path))

    def assess_cuts(self):
        for response in self.responses:
            if response["tool"] != "cst_get_pattern_cut" or response["rejected"]:
                continue
            payload = response["payload"]
            phi = response["arguments"]["phi_cut"]
            angles, values = payload.get("angle_deg"), payload.get("value")
            pairs = []
            if self.realized and angles and values and len(angles) == len(values):
                for angle, value in zip(angles, values):
                    other = self.realized["grid"].get(
                        (abs(angle), phi if angle >= 0 else (phi + 180) % 360)
                    )
                    if other is not None:
                        pairs.append((value, other))
            self.check(
                f"principal phi={phi} cut consistency",
                "inconclusive"
                if not pairs
                else compare([a for a, b in pairs], [b for a, b in pairs], 1e-3),
                expected="realized gain at common signed-angle directions",
                obtained=len(pairs),
                tolerance=1e-3,
                evidence=response["evidence"],
            )

    def inspect_exports(self):
        # Common parsers are reused without dipole frequency or radiation assertions.
        for artifact in self.inventory:
            path = Path(artifact["archived"])
            try:
                if path.suffix.lower() in {".txt", ".csv"}:
                    table = ascii_table(path)
                    artifact.update(
                        readable=True,
                        sample_count=table["sample_count"],
                        units=table["header"],
                        inspection={k: v for k, v in table.items() if k != "rows"},
                    )
                    if table["extension_mismatch"]:
                        self.check(
                            "native CSV delimiter limitation",
                            "inconclusive",
                            evidence=str(path),
                            reason="Native ASCII has a non-comma delimiter despite its csv extension",
                        )
                    if "s11_native" in path.name and self.baseline:
                        self.validate_native_ascii(artifact, table)
                elif path.suffix.lower() == ".png":
                    from matplotlib.image import imread

                    pixels = imread(path)
                    artifact.update(
                        readable=bool(pixels.size),
                        inspection={"format": "PNG", "dimensions": list(pixels.shape)},
                    )
                    self.check("PNG readable " + path.name, bool(pixels.size), evidence=str(path))
                elif path.suffix.lower() == ".pdf":
                    signature = path.read_bytes().startswith(b"%PDF")
                    artifact.update(
                        readable=None,
                        inspection={"format": "PDF", "signature": signature, "rendered": False},
                    )
                    self.check("PDF signature " + path.name, signature, evidence=str(path))
            except Exception as exc:  # noqa: BLE001 - each optional artifact is assessed independently
                # Summary text and source metadata need not be numeric tables.
                self.check(
                    "artifact assessment " + path.name,
                    "inconclusive",
                    reason=str(exc),
                    evidence=str(path),
                )

    def validate_native_ascii(self, artifact, table):
        header, rows = table["header"], table["rows"]
        real = re.search(r"\breal\b|\bRe\s*\(", header, re.IGNORECASE)
        imag = re.search(r"\bimag(?:inary)?\b|\bIm\s*\(", header, re.IGNORECASE)
        if (
            real
            and imag
            and real.start() < imag.start()
            and table["columns"] == 3
            and re.search(r"\bGHz\b", header, re.IGNORECASE)
            and re.search(r"S1[, _]?1|S-Parameter", header, re.IGNORECASE)
        ):
            self.check(
                "native S11 ASCII complex",
                compare([r[0] for r in rows], self.baseline[0], 1e-7)
                and compare([complex(r[1], r[2]) for r in rows], self.baseline[1], 1e-5),
                expected="full complex S11 in native declared real/imaginary column order",
                tolerance={"frequency_ghz": 1e-7, "complex_s11": 1e-5},
                evidence=artifact["archived"],
            )
        else:
            super().validate_native_ascii(artifact, table)

    def finalize(self):
        for phase, label in PHASES.items():
            if not any(p["phase"] == phase for p in self.phases):
                self.phases.append(
                    {
                        "phase": phase,
                        "label": label,
                        "started_utc": self.metadata["started_utc"],
                        "elapsed_s": 0,
                        "outcome": "not_executed" if self.args.preflight else "blocked",
                    }
                )
        if not self.args.preflight:
            self.inspect_exports()
        for name in sorted(MANDATORY):
            if not any(c["name"] == name for c in self.checks):
                self.check(
                    name, "not_executed" if self.args.preflight else "blocked", reason=self.reason
                )
        if not self.args.preflight:
            for index, item in enumerate(MANIFEST, 1):
                if not any(r["manifest_index"] == index for r in self.records):
                    self.check(
                        f"planned {index}: {item['tool']}",
                        "blocked",
                        reason="No actual invocation. See preceding prerequisite evidence",
                    )
            if self.exit_code == 1 or any(
                c["mandatory"] and c["verdict"] == "failed" for c in self.checks
            ):
                self.exit_code = 1
                if any(c["mandatory"] and c["verdict"] == "failed" for c in self.checks):
                    failed = sorted(
                        {
                            c["name"]
                            for c in self.checks
                            if c["mandatory"] and c["verdict"] == "failed"
                        }
                    )
                    self.reason = (
                        "Mandatory patch validation failed: "
                        + ", ".join(failed)
                        + ". "
                        + self.reason
                    )
            elif not self.unknown and all(
                any(c["name"] == name and c["verdict"] == "passed" for c in self.checks)
                for name in MANDATORY
            ):
                self.exit_code, self.reason = (
                    0,
                    "Mandatory native and patch diagnostics passed. Secondary capability limitations remain separate",
                )
            else:
                self.exit_code, self.reason = 2, "Incomplete patch assessment. " + self.reason
        self.persist()

    def report(self):
        def cell(value):
            return (
                json.dumps(safe_json(value), ensure_ascii=False)
                .replace("|", "\\|")
                .replace("\n", " ")
            )

        lines = [
            "# Rectangular patch validation",
            "",
            f"Outcome: {self.reason}. Exit code {self.exit_code}.",
            "",
            f"Mode: {self.metadata['mode']}. UTC invocation: {self.metadata['started_utc']}.",
            "",
            f"Reference: [MathWorks thin-substrate patch]({REFERENCE}).",
            "",
            "## Geometry and configuration",
            "",
            "Fixed 40 by 30 mm patch on an 80 by 60 by 1.57 mm substrate and ground plane. Feed X=5.5 mm, Y=0 with a 1 by 1 mm square PEC probe. Native library PEC and Rogers RT-duroid 5870 (loss free) are required. No replacement material or dimensional tuning is allowed.",
            "",
            "The 0.035 mm metal thickness and 0.10 mm excitation interruption differ from the idealized reference. Insert retains the probe. The probe meets the patch at Z=1.57 mm and the discrete port spans Z=0 to 0.10 mm. There is no continuous PEC connection to ground.",
            "",
            "Units mm, GHz, ns. Six expanded-open boundaries, no symmetry. Tetrahedral mesh with 20 cells per wavelength and minimum 5 cells. Relative refinements: substrate 2, patch 2, probe 4. These do not guarantee an absolute mesh size or elements through the substrate.",
            "",
            f"The original frequency-domain execution requested a discrete equidistant 21-sample interval from 2.2 to 2.5 GHz, residual 1e-6 and native MeshAdaptionTet={self.metadata['model']['solver']['adaptive_mesh']}. AddMonitorSamples=True calculates the requested monitors within that execution. Farfield monitors are at 2.30, 2.37 and 2.45 GHz. Efield and Hfield are at 2.37 GHz. Surface current is exported only from a discovered result of the Hfield monitor.",
            "",
            "Idle state alone is not success. The native S11 result run and requested farfields are required. Repeated bounded waits preserve the overall solver budget. No simulation is retried or stopped.",
            "",
            "## Numerical and physical assessment",
            "",
            "Resonance means a zero crossing of imaginary input impedance. Minimum S11 is recorded separately. Our initial diagnostics compare resonance with 2.37 GHz within 3 percent and directivity with 5.84 dBi within 1 dB. These are not published uncertainty bounds. No minimum S11 or matched bandwidth is imposed.",
            "",
            "Directivity, IEEE gain and realized gain retain distinct definitions. Lossless radiation efficiency should approach unity. Total efficiency and realized gain include mismatch. Missing loss data are unavailable measurements.",
            "",
            "Full complex S11 is checked through independent read and export routes. Impedance, magnitude, dB, phase, VSWR and bandwidth use the full curve with 50 ohm normalization. Touchstone units, frequency grid, complex values and reference impedance are checked independently. Band edges and impedance crossings are interpolated within the actual sample brackets.",
            "",
            "Upper radiation fraction uses solid-angle integration in the +Z half-space. Linear polarization needs declared Ludwig3 co/cross components at common angles. Global maxima from unrelated directions are insufficient.",
            "",
            "```json",
            json.dumps(safe_json(self.metadata.get("s11_assessment")), indent=2),
            "```",
            "",
            "| Check | Verdict | Expected | Obtained | Tolerance | Reason and evidence |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {cell(c['name'])} | {c['verdict']} | {cell(c['expected'])} | {cell(c['obtained'])} | {cell(c['tolerance'])} | {cell(c['reason'])} {cell(c['evidence'])} |"
            for c in self.checks
        ]
        lines += [
            "",
            "## Execution and installation",
            "",
            "```json",
            json.dumps(
                safe_json(
                    {
                        k: self.metadata.get(k)
                        for k in (
                            "installation",
                            "packages",
                            "repository",
                            "native_version",
                            "last_solver_wait",
                        )
                    }
                ),
                indent=2,
            ),
            "```",
            "",
            "| Phase | Duration (s) | Outcome |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {p['phase']} | {p['elapsed_s']:.3f} | {p['outcome']} |" for p in self.phases]
        lines += [
            "",
            "## Relevant original CST messages",
            "",
            "Original snapshots are supporting evidence under results/cst_logs. Coverage may be partial. Protocol errors are separate evidence.",
            "",
        ]
        for log in self.logs:
            lines += [
                f"### {log['phase']} at {log['captured_utc']}",
                "",
                f"Source: {log['source']}. Snapshot: {log['json']}.",
                "",
                "```text",
                log["text"].replace("```", "'''"),
                "```",
                "",
            ]
        if not self.logs:
            lines += [
                "No native CST messages captured. Preflight does not execute live operations.",
                "",
            ]
        lines += [
            "## Export and figure inventory",
            "",
            "| File | Bytes | Fresh | Inspection |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {cell(a['archived'])} | {a['bytes']} | {a['fresh']} | {cell(a['inspection'])} |"
            for a in self.inventory
        ]
        lines += ["", "## Capability limitations", ""] + [
            "- " + v for v in self.metadata["limitations"]
        ]
        lines += [
            "",
            "## Reproducible command",
            "",
            "```powershell",
            "uv run capabilities_test\\04_antenna_simulation\\run_patch.py"
            + (
                " --preflight"
                if self.args.preflight
                else " --resume"
                if self.args.resume
                else f" --solver-timeout-s {self.args.solver_timeout_s:g}"
            )
            + f' --cst-path "{self.metadata.get("installation", {}).get("path", self.args.cst_path or DEFAULT_CST_PATH)}" --cst-version {self.args.cst_version}',
            "```",
            "",
        ]
        atomic_text(self.reports / "validation.md", "\n".join(lines))

    async def run(self):
        with workspace_lock(self.parent, self.args.preflight, scenario="patch"):
            if self.args.resume:
                self.prepare_resume()
            else:
                prepare_outputs(self.base, self.parent, self.args.preflight, scenario="patch")
                self.prepared = True
            self.persist()
            try:
                self.configure_environment()
                params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "cst_mcp.server"],
                    cwd=str(ROOT),
                    env=self.env,
                )
                with (
                    (self.results / f"protocol/{self.protocol_prefix}server_stderr.log").open(
                        "w", encoding="utf-8"
                    ) as stderr,
                    preserve_cst_processes(),
                ):
                    async with stdio_client(params, errlog=stderr) as (read, write):  # noqa: SIM117 - explicit transport/session lifetimes
                        async with ClientSession(read, write) as session:
                            try:
                                await self.protocol(session)
                                if not self.args.preflight:
                                    if self.args.resume:
                                        await self.reconnect_resume(session)
                                    await self.live(session)
                            except Stop:
                                await self.diagnose(session)
                                raise
                self.exit_code, self.reason = (
                    (0, "Disabled-server preflight passed. All live phases not_executed")
                    if self.args.preflight
                    else (
                        2,
                        "Saved-result resume completed. Mandatory assessment pending"
                        if self.args.resume
                        else "Live manifest completed. Mandatory assessment pending",
                    )
                )
                self.check(
                    "preserving transport shutdown",
                    "passed",
                    reason="Server-only lifecycle hooks completed. Reopened owned project is left for user inspection",
                )
            except BaseException as exc:  # noqa: BLE001 - persist transport failures and cancellation
                leaves = list(exception_leaves(exc))
                cause = next((e for e in leaves if isinstance(e, (Stop, Incomplete))), leaves[0])
                if self.pending_solve or any(
                    isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)) for e in leaves
                ):
                    self.unknown = True
                self.exit_code = 2 if isinstance(cause, Incomplete) or self.unknown else 1
                self.reason = f"{self.phase}: {type(cause).__name__}: {cause}"
                write_json(
                    self.results / f"protocol/{self.protocol_prefix}client_exception.json",
                    {
                        "utc": timestamp(),
                        "type": type(cause).__name__,
                        "message": str(cause),
                        "traceback": traceback.format_exc(),
                    },
                )
            finally:
                self.finalize()
        return self.exit_code


def main(argv=None):
    runner = Patch(options(argv))
    try:
        code = asyncio.run(runner.run())
    except Exception as exc:  # noqa: BLE001 - output preparation must leave prior evidence intact
        print(
            f"Resume refused: {exc}"
            if runner.args.resume
            else f"Preparation refused: {exc}. Close the existing owned project before live overwrite.",
            file=sys.stderr,
        )
        return 1
    print(f"{runner.reason}\nReport: {runner.reports / 'validation.md'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
