"""Fixed WR-90 finite-flange workflow. Live execution is exclusively user triggered."""

from __future__ import annotations

import argparse
import asyncio
import itertools
import math
import re
import sys
import time
import traceback
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
    compare,
    complex_curve,
    derive,
    exception_leaves,
    farfield_grid,
    ffs_source,
    initialize_catalog,
    parse_shapes,
    prepare_outputs,
    preserve_cst_processes,
    read_text,
    safe_json,
    sha256,
    sphere_mean,
    timestamp,
    touchstone,
    workspace_lock,
    write_json,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from run_dipole import Dipole, positive

BATCH = Path(__file__).resolve().parent
REFERENCE = "https://optics.ansys.com/hc/en-us/articles/360042538073-Rectangular-probe-antenna"
FREQUENCIES = [9, 10, 11]
FREQUENCY_RANGE = [8.2, 12.4]
TARGET_FREQUENCY = 10
BOUNDARIES = {
    face: "expanded open" for face in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
}
BRICKS = {
    "Body": {
        "x_min": -13.93,
        "x_max": 13.93,
        "y_min": -7.58,
        "y_max": 7.58,
        "z_min": -30,
        "z_max": 0,
    },
    "Flange": {"x_min": -20, "x_max": 20, "y_min": -10, "y_max": 10, "z_min": -0.10, "z_max": 0},
    "Bore": {
        "x_min": -11.43,
        "x_max": 11.43,
        "y_min": -5.08,
        "y_max": 5.08,
        "z_min": -30.01,
        "z_max": 0.01,
    },
}
PORT = {
    "port_number": 1,
    "orientation": "zmin",
    "coordinates": "Free",
    "port_on_bound": False,
    "x_min": -11.43,
    "x_max": 11.43,
    "y_min": -5.08,
    "y_max": 5.08,
    "z_min": -30,
    "z_max": -30,
    "mode_number": 1,
}
SOLVER = {
    "accuracy": -40,
    "stimulation_port": 1,
    "excitation_type": "Gaussian",
    "normalize_to_fixed_impedance": False,
    "activate_power_loss_1d": True,
    "power_loss_1d_use_field_monitors": True,
    "power_loss_1d_use_farfield_monitors": True,
    "power_loss_1d_extra_frequencies": [],
    "power_loss_1d_per_solid": False,
}
THEORY = {
    "source": "Analytical vacuum rectangular-guide predictions, not native measurements",
    "te10_cutoff_ghz": 299792458 / (2 * 0.02286) / 1e9,
    "te20_cutoff_ghz": 299792458 / 0.02286 / 1e9,
    "te01_cutoff_ghz": 299792458 / (2 * 0.01016) / 1e9,
    "te10_electric_field": "Dominant Ey, proportional to cos(pi*x/22.86) in the uniform guide",
    "beta_10GHz_rad_per_m": 2
    * math.pi
    * 10e9
    / 299792458
    * math.sqrt(1 - (299792458 / (2 * 0.02286) / 10e9) ** 2),
    "te_wave_impedance_is_not_assumed_s_reference": True,
}
REFERENCE_VALUES = {
    "frequency_ghz": 10,
    "directivity_dbi": 8.13,
    "realized_gain_dbi": 7.94,
    "front_to_back_db": 12,
    "radiated_over_incident": 0.956,
    "radiation_efficiency_physical_target": 1.0,
    "reported_radiation_efficiency": 1.006,
    "modal_s11_db_inferred": 10 * math.log10(1 - 107.51 / 113.17),
    "s11_note": "Inferred from published incident and accepted powers, not a 50-ohm matching requirement",
}
CRITERIA = {
    "directivity_db": 1.0,
    "realized_gain_db": 1.0,
    "front_to_back_db": 3.0,
    "radiation_efficiency_absolute": 0.03,
    "total_efficiency_absolute": 0.05,
    "modal_s11_db": 3.0,
    "power_fraction": 0.03,
    "frequency_ghz": 1e-6,
    "frequency_precision_note": "1 kHz accommodates the observed single-precision native frequency axes and text roundoff at X band",
    "complex_s11": 1e-5,
    "cutoff_relative": 0.02,
    "te10_profile_relative_rms": 0.15,
    "te10_ey_energy_fraction": 0.95,
    "note": "Our initial diagnostic criteria, not published uncertainty bounds or convergence evidence",
}
PHASES = {
    "A": "Create owned project and verify units",
    "B": "Load library PEC, construct hollow guide and internal port",
    "C": "Configure domain, hexahedral mesh, solver and monitors",
    "D": "One asynchronous solve, bounded waits and native result discovery",
    "E": "Full modal S11, derived curves and available modal information",
    "F": "Radiation metrics, fields, power and numerical exports",
    "G": "MCP figures and WR-90 diagnostics",
    "I": "Save, close, read saved S11, reopen and verify persistence",
}
MANDATORY = {
    "native construction",
    "native solve idle",
    "expected native results",
    "full S11",
    "full S11 derived consistency",
    "S11 persistence",
    "Touchstone syntax and reference",
    "WR90 directivity",
    "WR90 realized gain",
    "WR90 front-to-back",
    "main beam towards +Z",
    "H-plane narrower than E-plane",
    "radiation efficiency",
    "modal S11 reference diagnostic",
    "incident reflected accepted power",
    "accepted radiated loss power",
    "efficiency identities",
}
POWER_ITEMS = {
    "power stimulated": "P_stim",
    "power accepted": "P_acc",
    "power radiated": "P_rad",
    "power reflected": "P_ref",
    "loss in dielectrics": "P_loss_diel",
    "loss in metals": "P_loss_metal",
}


def options(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight", action="store_true", help="Disabled real-server catalog verification only"
    )
    parser.add_argument(
        "--cst-path",
        default=None,
        help=f"Selected installation. Default discovers CST 2025, preferring {DEFAULT_CST_PATH}",
    )
    parser.add_argument("--cst-version", default="2025")
    parser.add_argument("--solver-timeout-s", type=positive, default=1800)
    return parser.parse_args(argv)


def planned(phase, tool, **arguments):
    return {"phase": phase, "tool": tool, "arguments": arguments}


def brick(name):
    return planned(
        "B", "cst_create_brick", component="WR90", name=name, material="WR90PEC", **BRICKS[name]
    )


# Discovery-dependent calls carry placeholders. Representative values are schema examples only.
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
    planned("B", "cst_load_material", name="WR90PEC", library_name="PEC"),
    brick("Body"),
    brick("Flange"),
    planned("B", "cst_boolean_add", solid1="WR90:Body", solid2="WR90:Flange"),
    brick("Bore"),
    planned("B", "cst_boolean_subtract", solid1="WR90:Body", solid2="WR90:Bore"),
    planned("B", "cst_execute_vba", code=SHAPES_QUERY),
    planned("B", "cst_add_waveguide_port", **PORT),
    planned("B", "cst_list_ports"),
    planned("B", "cst_project_tree", tree_path="Components", max_depth=3),
    planned("B", "cst_project_tree", tree_path="Ports", max_depth=4),
    planned("B", "cst_save_project", path="<PROJECT>"),
    planned("B", "cst_read_project_log"),
    planned("C", "cst_set_background", material="Normal", epsilon=1, mu=1),
    planned("C", "cst_set_frequency_range", f_min=8.2, f_max=12.4),
    planned("C", "cst_set_boundary", **BOUNDARIES, waveguide_port_faces=[]),
    planned("C", "cst_set_symmetry", x_plane="none", y_plane="none", z_plane="none"),
    planned("C", "cst_set_mesh_type", mesh_type="Hexahedral"),
    planned(
        "C",
        "cst_set_mesh_density",
        mesh_type="Hex",
        cells_per_wavelength=20,
        min_cells=5,
        ratio_limit=20,
    ),
    planned("C", "cst_add_mesh_refinement", component="WR90", solid="Body", refinement_factor=2),
    planned("C", "cst_set_adaptive_mesh", enabled=False, max_passes=1, threshold=0.02),
    planned("C", "cst_configure_time_domain_solver", **SOLVER),
    *[
        planned(
            "C",
            "cst_add_field_monitor",
            monitor_type="Farfield",
            frequency=f,
            name=f"farfield (f={f})",
        )
        for f in FREQUENCIES
    ],
    planned(
        "C", "cst_add_field_monitor", monitor_type="Efield", frequency=10, name="e-field (f=10)"
    ),
    planned(
        "C", "cst_add_field_monitor", monitor_type="Hfield", frequency=10, name="h-field (f=10)"
    ),
    planned(
        "C",
        "cst_check_model_setup",
        boundaries=BOUNDARIES,
        ports=[
            {
                "port_number": 1,
                "type": "waveguide",
                "orientation": "zmin",
                "port_on_bound": False,
                "plane": -30,
            }
        ],
        structure_bbox={"xmin": -20, "xmax": 20, "ymin": -10, "ymax": 10, "zmin": -30, "zmax": 0},
        power_loss_1d=True,
        farfield_frequencies_ghz=FREQUENCIES,
        field_monitor_frequencies_ghz=[10],
    ),
    *[
        planned("C", tool)
        for tool in ("cst_get_solver_info", "cst_get_mesh_info", "cst_get_mesh_quality")
    ],
    planned("C", "cst_save_project", path="<PROJECT>"),
    planned("C", "cst_read_project_log"),
    planned("D", "cst_run_simulation_async", solver_type="Time Domain"),
    planned("D", "cst_wait_for_simulation", max_wait_s=45),
    planned("D", "cst_get_simulation_status", timeout_s=30),
    planned("D", "cst_read_project_log"),
    *[
        planned("D", tool)
        for tool in ("cst_get_solver_info", "cst_get_mesh_info", "cst_get_mesh_quality")
    ],
    planned("D", "cst_save_project", path="<PROJECT>"),
    planned("D", "cst_project_info"),
    *[planned("D", "cst_list_results", tree_path=tree) for tree in ("1D Results", "2D/3D Results")],
    planned(
        "D", "cst_list_saved_results", project_path="<PROJECT>", contains="", offset=0, limit=50
    ),
    *[
        planned("D", "cst_project_tree", tree_path=tree, max_depth=8)
        for tree in ("1D Results", "2D/3D Results", "Farfields", "Ports")
    ],
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
    planned("E", "cst_get_vswr", port=1, max_points=0, run_id="<RUN_ID>"),
    planned("E", "cst_get_impedance", port=1, mode="z_parameter"),
    # Templates for bounded, discovery-driven supplementary 1D reads and exports.
    planned(
        "E",
        "cst_read_saved_result",
        project_path="<PROJECT>",
        tree_path="<AUX_TREE>",
        run_id="<AUX_RUN>",
        format="real_imag",
        max_points=0,
    ),
    planned(
        "E",
        "cst_export_result",
        result_path="<AUX_TREE>",
        output_file="<EXPORTS>/modal_information.txt",
        format="txt",
    ),
    *[
        planned(
            "F",
            "cst_get_farfield_metrics",
            frequency_ghz=f,
            monitor_name=f"<FF_MONITOR_{f}>",
            try_farfield_plot=True,
        )
        for f in FREQUENCIES
    ],
    *[
        planned("F", tool, frequency=10)
        for tool in ("cst_get_gain", "cst_get_efficiency", "cst_get_efficiency_breakdown")
    ],
    planned("F", "cst_get_farfield", frequency=10, monitor_name="<FF_MONITOR>"),
    *[
        planned("F", "cst_get_pattern_cut", frequency=10, plane="custom", phi_cut=phi, theta_cut=90)
        for phi in (90, 0)
    ],
    planned(
        "F", "cst_get_radiation_pattern_3d", frequency=10, resolution_deg=5, coordinate="spherical"
    ),
    planned("F", "cst_get_cross_polarization", frequency=10, definition="Ludwig3"),
    planned("F", "cst_export_touchstone", file_path="<EXPORTS>/wr90_50ohm.s1p", format="s1p"),
    *[
        planned(
            "F",
            "cst_export_result",
            result_path="<TREE_S11>",
            output_file=f"<EXPORTS>/s11_modal.{fmt}",
            format=fmt,
        )
        for fmt in ("csv", "txt")
    ],
    *[
        planned(
            "F",
            "cst_export_result",
            result_path=f"<{key}>",
            output_file=f"<EXPORTS>/{stem}_10GHz.txt",
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
        file_path="<EXPORTS>/farfield_realized_gain_10GHz.csv",
        frequency=10,
        format="csv",
        monitor_name="<FF_MONITOR>",
    ),
    planned(
        "F",
        "cst_export_farfield",
        file_path="<EXPORTS>/wr90_10GHz.ffs",
        frequency=10,
        format="ffs",
        monitor_name="<FF_MONITOR>",
    ),
    planned(
        "F",
        "cst_check_power_balance",
        power_curves="<POWER_CURVES>",
        frequencies_ghz=[10],
        threshold=0.03,
    ),
    planned(
        "G",
        "cst_plot_1d_results",
        project_path="<PROJECT>",
        quantities=["s11_db", "phase", "vswr"],
        run_ids=["<RUN_ID>"],
        max_runs=1,
        freq_range_ghz=FREQUENCY_RANGE,
        formats=["png", "pdf"],
        width="single",
        out_dir="<FIGURES>/one_d",
        title="WR-90 modal reference",
        simulate_if_missing=False,
    ),
    *[
        planned(
            "G",
            "cst_plot_farfield",
            project_path="<PROJECT>",
            farfield_name="<FF_MONITOR>",
            frequency_ghz=10,
            quantity=quantity,
            polarization_basis="spherical",
            step_deg=5,
            cuts=["phi=90", "phi=0"],
            plots=["polar", "rect", "heatmap", "3d"],
            heatmap_projection="theta_phi",
            dynamic_range_db=30,
            formats=["png", "pdf"],
            width="double",
            out_dir=f"<FIGURES>/farfield_{quantity}",
            file_stem=f"wr90_{quantity}_10GHz",
        )
        for quantity in ("directivity", "realized_gain")
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


def modal_derived(values):
    """Reuse dimensionless curve formulas without publishing the helper's 50-ohm Z."""
    result = derive(values)
    result.pop("z")
    result["undefined"] = "null dB and phase at zero, null VSWR at |S|>=1"
    return result


class WR90(Dipole):
    """Reuse client infrastructure only. All antenna checks and manifest live here."""

    def __init__(self, args, parent=BATCH):
        super().__init__(args, parent)
        self.base = self.parent / ("preflight/wr90" if args.preflight else "wr90")
        self.results, self.reports = self.base / "results", self.base / "reports"
        self.project = self.base / "project/wr90.cst"
        self.pending_solve, self.solve_started = False, None
        self.ff_monitors, self.auxiliary, self.power_curves = {}, [], {}
        self.reference_curve, self.touchstone_data = None, None
        self.derived_checks, self.persistence_reads = [], []
        self.ambiguous_power = set()
        self.ambiguous_reference = False
        self.metadata.update(
            output_directory=str(self.base),
            scenario="wr90",
            reference=REFERENCE,
            reference_values=REFERENCE_VALUES,
            theory=THEORY,
            diagnostic_criteria=CRITERIA,
            model={
                "component": "WR90",
                "aperture_mm": [22.86, 10.16],
                "wall_mm": 2.5,
                "flange_mm": [40, 20, 0.10],
                "guide_length_mm": 30,
                "aperture_z_mm": 0,
                "radiation_direction": "+Z",
                "bricks": BRICKS,
                "library": "PEC",
                "alias": "WR90PEC",
                "boolean_sequence": ["add Body Flange", "subtract Body Bore"],
                "port": PORT,
                "units": EXPECTED_UNITS,
                "frequency_ghz": FREQUENCY_RANGE,
            },
            configuration={
                "boundaries": BOUNDARIES,
                "waveguide_port_faces": [],
                "symmetry": "none",
                "mesh_type": "Hexahedral",
                "mesh_density": {"cells_per_wavelength": 20, "min_cells": 5, "ratio_limit": 20},
                "local_refinement": 2,
                "adaptive_mesh": False,
                "solver": SOLVER,
                "farfield_frequencies_ghz": FREQUENCIES,
                "field_frequency_ghz": 10,
            },
            limitations=[
                "Guide length 30 mm and flange thickness 0.10 mm are implementation choices. The reference uses a PEC sheet",
                "One fixed solve supplies no mesh convergence evidence",
                "The uniform guide is intended to support three equidistant FIT mesh steps in front of the port. Global mesh queries do not measure this condition",
                "Shape enumeration verifies names and materials, not topology or all dimensions independently",
                "Port listing does not independently prove position, TE10 field profile or modal reference impedance",
                "Setup diagnostic checks supplied settings, not independent native application",
                "Calculated wave and line impedances are distinct. Analytical TE wave impedance is not substituted for the S-parameter reference",
                "Touchstone export is renormalized to 50 ohms. Comparison requires actual native reference data and its normalization convention",
                "Unavailable modal, loss or secondary extraction remains a capability limitation",
                "No perfectly pure TE10 aperture field is required at the radiating discontinuity",
                "Project log snapshots may have partial or unknown coverage",
                "Figure axes, legends and visible content require user inspection",
                "FFS reuse as a CST excitation remains untested",
            ],
        )

    def check(self, name, verdict, **kwargs):
        # Avoid inheriting the dipole's mandatory radiation assertions.
        if (
            self.phase == "E"
            and self.active_manifest_index is None
            and name.endswith(": cst_read_saved_result")
        ):
            # A supplementary modal read must not inherit the primary S11 requirement.
            kwargs["mandatory"] = False
        result = super().check(name, verdict, **kwargs)
        self.checks[-1]["mandatory"] = kwargs.get("mandatory", False) or name in MANDATORY
        return result

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
        selected = Path(info["path"])
        for label, relative in (
            ("pec_provenance", "Library/Materials/PEC.mtd"),
            (
                "port_documentation",
                "Online Help/mergedProjects/VBA_3D/special_vbaports/special_vbaports_port_object.htm",
            ),
            (
                "mode_documentation",
                "Online Help/mergedProjects/3D/special_solvopt/special_solvopt_port_mode_information.htm",
            ),
            (
                "normalization_documentation",
                "Online Help/mergedProjects/3D/special_theory/special_theory_reference_value_and_normalizing.htm",
            ),
        ):
            path = selected / relative
            info[label] = {"path": str(path), "available": path.is_file(), "source_only": True}
            if path.is_file():
                info[label].update(sha256=sha256(path))
                if label == "pec_provenance":
                    info[label]["text"] = read_text(path)
                if label == "normalization_documentation":
                    plain = re.sub(r"<[^>]+>", " ", read_text(path))
                    plain = re.sub(r"\s+", " ", plain)
                    info[label]["non_tem_wave_reference_documented"] = (
                        "For (Q)TEM-modes the line-impedance is chosen as reference, in all other cases the wave-impedance"
                        in plain
                    )
        write_json(self.results / "protocol/import_probe.json", info)

    async def protocol(self, session):
        self.phase = "protocol"
        start, utc = time.monotonic(), timestamp()
        init, self.catalog = await initialize_catalog(session, self.results / "protocol")
        self.metadata["server_initialization"] = init
        calls, mismatches = [], []
        for index, item in enumerate(MANIFEST, 1):
            arguments = self.resolve(item["arguments"], representative=True)
            try:
                self.validate(item["tool"], arguments)
                outcome = "passed"
            except Exception as exc:  # noqa: BLE001 - retain every schema discrepancy
                outcome = "failed"
                mismatches.append(
                    {"manifest_index": index, "tool": item["tool"], "discrepancy": str(exc)}
                )
            calls.append(
                dict(
                    manifest_index=index,
                    **item,
                    representative_arguments=arguments,
                    schema_validation=outcome,
                    execution="not_executed",
                )
            )
        write_json(self.results / "protocol/planned_manifest.json", calls)
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
        if mismatches or any(c["verdict"] == "failed" for c in self.checks):
            raise Stop("Schema or installation prerequisites failed before connecting")

    def resolve(self, template, representative=False):
        if not representative:
            return super().resolve(template)
        previous = self.bindings
        self.bindings = {
            "RUN_ID": 0,
            "TREE_S11": "schema example S11",
            "TREE_EFIELD": "schema example E",
            "TREE_HFIELD": "schema example H",
            "TREE_SURFACE_CURRENT": "schema example current",
            "FF_MONITOR": "schema example farfield",
            "AUX_TREE": "schema example modal data",
            "AUX_RUN": 0,
            "POWER_CURVES": {"P_acc": {"x": [10], "y": [1]}},
        }
        self.bindings.update(
            {f"FF_MONITOR_{f}": f"schema example farfield {f}" for f in FREQUENCIES}
        )
        try:
            return super().resolve(template)
        finally:
            self.bindings = previous

    async def request(self, session, tool, arguments, probe=False):
        if self.pending_solve and tool != "cst_wait_for_simulation":
            raise Incomplete("Only bounded waits are permitted while the owned solve is pending")
        return await super().request(session, tool, arguments, probe=probe)

    async def diagnose(self, session):
        if not self.pending_solve:
            await super().diagnose(session)

    def archive(self, raw, before, call_id):
        # Preserve each producing call's files once despite alternate Windows separators.
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

    @staticmethod
    def path_frequency(path):
        match = re.search(r"\(f\s*=\s*([\d.]+)\)", str(path), re.IGNORECASE)
        return float(match.group(1)) if match else None

    def discover(self):
        for key in ("TREE_S11", "RUN_ID"):
            self.bindings.pop(key, None)
        matches = [
            e
            for e in self.entries
            if e.get("tree_path", "").replace("/", "\\").casefold()
            == "1d results\\s-parameters\\s1,1"
        ]
        if len(matches) == 1:
            runs = matches[0].get("run_ids", [])
            if len(runs) == 1 and type(runs[0]) is int:
                self.bindings.update(TREE_S11=matches[0]["tree_path"], RUN_ID=runs[0])
        paths = sorted(set(self.tree_items + [e.get("tree_path", "") for e in self.entries]))
        for key, category in (
            ("TREE_EFIELD", "e-field"),
            ("TREE_HFIELD", "h-field"),
            ("TREE_SURFACE_CURRENT", "surface current"),
        ):
            candidates = [
                p for p in paths if category in p.casefold() and self.path_frequency(p) == 10
            ]
            if key == "TREE_HFIELD":
                candidates = [p for p in candidates if "surface current" not in p.casefold()]
            roots = [
                p for p in candidates if re.search(r"\(f\s*=\s*[\d.]+\)\s*(?:\[[^\]]+\])?$", p)
            ]
            selected = roots or candidates
            self.bindings.pop(key, None)
            if len(selected) == 1:
                self.bindings[key] = selected[0]
        for frequency in FREQUENCIES:
            candidates = [
                p
                for p in paths
                if "farfield" in p.casefold() and self.path_frequency(p) == frequency
            ]
            roots = [
                p for p in candidates if re.search(r"\(f\s*=\s*[\d.]+\)\s*(?:\[[^\]]+\])?$", p)
            ]
            labels = {p.replace("/", "\\").split("\\")[-1] for p in roots}
            if len(labels) == 1:
                self.ff_monitors[frequency] = next(iter(labels))
        if 10 in self.ff_monitors:
            self.bindings["FF_MONITOR"] = self.ff_monitors[10]
        self.bindings.update({f"FF_MONITOR_{f}": label for f, label in self.ff_monitors.items()})
        self.metadata["discovered_bindings"] = dict(self.bindings)

    def verify_native(self, tool, arguments, payload):
        evidence = self.responses[-1]["evidence"]
        if tool == "cst_execute_vba" and arguments["code"] == SHAPES_QUERY:
            expected = {"WR90:Body": "WR90PEC"}
            self.require(
                "native construction",
                parse_shapes(payload.get("output", "")) == expected,
                expected=expected,
                obtained=parse_shapes(payload.get("output", "")),
                evidence=evidence,
                reason="Add and subtraction must leave only Body with its PEC assignment",
            )
        elif tool == "cst_get_solver_info":
            for key, expected in (
                ("fmin", 8.2),
                ("fmax", 12.4),
                ("number_of_ports", 1),
                ("stimulation_port", 1),
            ):
                value = payload.get(key)
                self.check(
                    "native solver " + key,
                    "inconclusive" if value is None else compare([value], [expected], 1e-6),
                    expected=expected,
                    obtained=value,
                    tolerance=1e-6,
                    evidence=evidence,
                    mandatory=value is not None,
                )
            self.check(
                "modal normalization readback",
                "inconclusive",
                obtained=payload,
                reason="Configuration requests AutoNormImpedance False. Generic readback does not prove the native modal reference",
                evidence=evidence,
            )
        elif tool in {"cst_get_mesh_info", "cst_get_mesh_quality"}:
            self.check(
                "available native mesh evidence",
                "passed" if payload.get("source") else "inconclusive",
                obtained=payload,
                evidence=evidence,
                reason="Counts and edge statistics do not measure the three equidistant port steps",
            )
        elif tool == "cst_list_ports":
            self.check(
                "native port listing",
                "passed" if payload.get("ports") else "inconclusive",
                obtained=payload,
                evidence=evidence,
                reason="Listing alone does not prove position or TE10 mode profile",
            )
        elif tool == "cst_check_model_setup":
            self.check(
                "setup diagnostic coverage",
                "inconclusive",
                obtained=payload,
                evidence=evidence,
                reason="Checks the supplied description only, with no independent native readback",
            )
        elif tool in {"cst_list_results", "cst_project_tree"}:
            self.tree_items.extend(
                i["path"] for i in payload.get("items", []) if isinstance(i, dict) and "path" in i
            )
            self.discover()
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
        elif tool == "cst_get_simulation_status":
            if payload.get("status") != "ok" or not isinstance(payload.get("running"), bool):
                self.unknown = True
                raise Incomplete("Native simulation status is unknown")
            super().verify_native(tool, arguments, payload)
        else:
            super().verify_native(tool, arguments, payload)

    async def wait_for_solve(self, session):
        while True:
            remaining = self.args.solver_timeout_s - (time.monotonic() - self.solve_started)
            # Reserve the documented two-second native wait overrun while retaining 45-second calls.
            if remaining < 47:
                self.unknown = True
                raise Incomplete(
                    "Solver budget cannot cover another 45-second wait. CST may still be running"
                )
            payload = await self.request(session, "cst_wait_for_simulation", {"max_wait_s": 45})
            self.metadata["last_solver_wait"] = payload
            state = payload.get("status")
            if state in {"starting", "running"} and payload.get("running") is (state == "running"):
                self.persist()
                continue
            if state == "finished" and payload.get("running") is False:
                self.pending_solve = False
                self.check(
                    "wait reports idle",
                    True,
                    obtained=payload,
                    reason="Idle alone is not simulation success. Expected native results are required",
                )
                if (
                    payload.get("solve_observed")
                    and payload.get("run_info", {}).get("last_reported_state") == "ERROR"
                ):
                    raise Stop(
                        "Observed native solve reported ERROR: " + str(payload.get("run_info"))
                    )
                return payload
            self.unknown = True
            raise Incomplete("Unrecognized native wait state. CST may still be running")

    async def live(self, session):
        self.metadata["live_phases_executed"] = True
        for phase, label in PHASES.items():
            self.phase = phase
            start, utc, outcome = time.monotonic(), timestamp(), "passed"
            try:
                for index, item in enumerate(MANIFEST, 1):
                    if item["phase"] != phase or "<AUX_TREE>" in str(item["arguments"]):
                        continue
                    self.active_manifest_index = index
                    tool = item["tool"]
                    if phase in {"E", "F", "G", "I"} and not self.idle:
                        raise Incomplete(
                            "Extraction and persistence require a responsive known idle session"
                        )
                    try:
                        arguments = self.resolve(item["arguments"])
                    except Incomplete as exc:
                        self.check(f"manifest {index}: {tool}", "inconclusive", reason=str(exc))
                        continue
                    ancillary = tool in {
                        "cst_list_ports",
                        "cst_project_tree",
                        "cst_list_results",
                        "cst_get_solver_info",
                        "cst_get_mesh_info",
                        "cst_get_mesh_quality",
                        "cst_read_project_log",
                    }
                    try:
                        if tool == "cst_run_simulation_async":
                            self.solve_started, self.idle = time.monotonic(), False
                            payload = await self.request(session, tool, arguments)
                            self.require(
                                "single asynchronous solve accepted",
                                payload.get("status") == "started",
                                obtained=payload,
                                mandatory=True,
                            )
                            self.pending_solve = True
                        elif tool == "cst_wait_for_simulation":
                            payload = await self.wait_for_solve(session)
                        else:
                            payload = await self.request(session, tool, arguments)
                        response = self.responses[-1]
                        self.artifact_contract(tool, arguments, response)
                        if tool == "cst_list_saved_results":
                            previous = dict(self.bindings)
                            self.entries = await self.saved_pages(session, arguments, payload)
                            self.discover()
                            if phase == "I":
                                self.require(
                                    "saved result identifier persistence",
                                    all(
                                        self.bindings.get(k) == previous.get(k)
                                        for k in ("TREE_S11", "RUN_ID")
                                    ),
                                    obtained=self.bindings,
                                )
                        if tool not in {"cst_run_simulation_async", "cst_wait_for_simulation"}:
                            self.verify_native(tool, arguments, payload)
                        if phase in {"E", "I"}:
                            self.validate_s(tool, arguments, payload, response["evidence"])
                        if tool == "cst_export_touchstone":
                            self.validate_touchstone(response)
                        if tool in {
                            "cst_get_farfield",
                            "cst_get_radiation_pattern_3d",
                            "cst_export_farfield",
                        }:
                            self.assess_farfield_export(response)
                        if tool.startswith("cst_plot_"):
                            self.figure_source_checks(arguments, response)
                            if tool == "cst_plot_farfield":
                                source = payload.get("source", {}).get("path")
                                for artifact in response["artifacts"]:
                                    if (
                                        source
                                        and Path(artifact["original"]).resolve()
                                        == Path(source).resolve()
                                        and artifact["fresh"]
                                    ):
                                        grid = self.grid_from_source(
                                            response, artifact, arguments["quantity"]
                                        )
                                        if arguments["quantity"] == "directivity":
                                            self.directivity = grid
                                        else:
                                            self.realized = grid
                        if tool == "cst_export_result":
                            self.inspect_numeric_export(arguments, response)
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
                                reason="Nearest efficiency samples alone do not prove an exact monitor frequency",
                            )
                    except Stop as exc:
                        outcome = "partial"
                        if phase in {"A", "B", "C", "D"} and not ancillary:
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
                if phase in {"C", "D"}:
                    mismatches = [
                        c["name"]
                        for c in self.checks
                        if c["phase"] == phase
                        and c["name"].startswith("native solver ")
                        and c["verdict"] == "failed"
                    ]
                    if mismatches:
                        raise Stop(
                            "Native configuration readback differs from the fixed model: "
                            + ", ".join(mismatches)
                        )
                if phase == "D":
                    self.require(
                        "expected native results",
                        self.idle
                        and "RUN_ID" in self.bindings
                        and "TREE_S11" in self.bindings
                        and all(f in self.ff_monitors for f in FREQUENCIES),
                        obtained={"bindings": self.bindings, "farfield_monitors": self.ff_monitors},
                        reason="Unique main S11 run and all three native farfield results required",
                    )
                if phase == "E":
                    self.check(
                        "full S11 derived consistency",
                        "inconclusive"
                        if len(self.derived_checks) != 10
                        else all(self.derived_checks),
                        obtained=self.derived_checks,
                        reason="Unavailable secondary readings do not block independent extraction",
                    )
                    await self.extract_auxiliary(session)
                if phase == "G":
                    self.assess_radiation()
                    self.assess_power()
                    self.compare_touchstone_reference()
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
        if tool not in {
            "cst_read_saved_result",
            "cst_get_s_parameters",
            "cst_get_s_parameter_phase",
            "cst_get_vswr",
        }:
            return
        if tool == "cst_read_saved_result" and arguments.get("format") == "real_imag":
            x, values = complex_curve(payload)
            self.require(
                "S11 native source identity",
                payload.get("source") == "cst.results"
                and Path(payload.get("project_path", "")).resolve() == self.project.resolve()
                and payload.get("tree_path") == self.bindings.get("TREE_S11")
                and payload.get("run_id") == self.bindings.get("RUN_ID"),
                evidence=evidence,
            )
            if self.baseline is None:
                self.require(
                    "full S11",
                    self.idle and self.project.is_file(),
                    obtained={"samples": len(x), "source": payload.get("source")},
                    evidence=evidence,
                )
                self.baseline, self.baseline_source = (x, values), payload
                increasing = all(b > a for a, b in itertools.pairwise(x))
                self.check(
                    "S11 frequency order and duplicates",
                    increasing,
                    obtained={
                        "strictly_increasing": increasing,
                        "duplicates": len(x) - len(set(x)),
                    },
                    evidence=evidence,
                    mandatory=True,
                )
                self.check(
                    "S11 actual frequency range",
                    compare([min(x), max(x)], FREQUENCY_RANGE, 1e-6),
                    expected=FREQUENCY_RANGE,
                    obtained=[min(x), max(x)],
                    tolerance=1e-6,
                    evidence=evidence,
                    mandatory=True,
                )
                write_json(
                    self.results / "exports/independent_modal_s11.json",
                    {
                        "x_ghz": x,
                        "derived": modal_derived(values),
                        "normalization": "Calculated modal reference",
                    },
                )
                reflection = self.interpolate_complex(x, values, 10)
                db = 20 * math.log10(abs(reflection)) if reflection else None
                self.check(
                    "modal S11 reference diagnostic",
                    db is not None
                    and abs(db - REFERENCE_VALUES["modal_s11_db_inferred"])
                    <= CRITERIA["modal_s11_db"],
                    expected=REFERENCE_VALUES["modal_s11_db_inferred"],
                    obtained=db,
                    tolerance=CRITERIA["modal_s11_db"],
                    evidence=evidence,
                    reason=REFERENCE_VALUES["s11_note"],
                )
            elif self.phase == "I":
                self.require(
                    "project closure before persistence", self.closed_verified, evidence=evidence
                )
                passed = compare(x, self.baseline[0], 1e-10) and compare(
                    values, self.baseline[1], 1e-10
                )
                self.persistence_reads.append(
                    {
                        "project_open": not self.project_closed,
                        "passed": passed,
                        "evidence": evidence,
                    }
                )
                self.check(
                    "closed S11 comparison" if self.project_closed else "reopened S11 comparison",
                    passed,
                    tolerance=1e-10,
                    obtained=len(values),
                    evidence=evidence,
                    mandatory=True,
                )
                if not self.project_closed:
                    self.check(
                        "S11 persistence",
                        len(self.persistence_reads) == 2
                        and all(p["passed"] for p in self.persistence_reads),
                        obtained=self.persistence_reads,
                    )
            return
        if self.baseline is None:
            raise Incomplete("Full saved modal S11 is unavailable")
        x, values = self.baseline
        if arguments.get("format") == "real_imag":
            rx, rv = complex_curve(payload)
            passed = compare(rx, x, 1e-10) and compare(rv, values, 1e-5)
        else:
            fmt = (
                ("unwrapped" if arguments.get("unwrap") else "phase")
                if tool == "cst_get_s_parameter_phase"
                else ("vswr" if tool == "cst_get_vswr" else arguments["format"])
            )
            passed = compare(payload.get("x", []), x, 1e-10) and compare(
                payload.get("y", []), modal_derived(values)[fmt], wrapped=fmt == "phase"
            )
        self.derived_checks.append(passed)
        self.check(
            f"independent full modal curve {self.seq}",
            passed,
            evidence=evidence,
            tolerance=1e-5,
            reason="Same native modal reference, full curve comparison",
        )

    async def extract_auxiliary(self, session):
        """Use only discovered native identifiers and existing generic tools."""
        self.active_manifest_index = None
        modal_entries = [
            e
            for e in self.entries
            if re.search(
                r"port.?modes?|port.?1|port information|impedance|propagation|gamma|cutoff|fcutoff|zwave|zline",
                e.get("tree_path", ""),
                re.IGNORECASE,
            )
        ]
        power_entries = [
            e for e in self.entries if "\\power\\" in e.get("tree_path", "").casefold()
        ]
        selected = {e["tree_path"]: e for e in modal_entries + power_entries}
        if len(selected) > 64:
            self.check(
                "auxiliary extraction bound",
                "inconclusive",
                obtained=len(selected),
                reason="At most 64 discovered supplementary curves are read",
            )
        for number, entry in enumerate(list(selected.values())[:64], 1):
            tree, runs = entry["tree_path"], entry.get("run_ids", [])
            if len(runs) != 1 or type(runs[0]) is not int:
                self.check("auxiliary result run " + tree, "inconclusive", obtained=runs)
                continue
            args = {
                "project_path": str(self.project),
                "tree_path": tree,
                "run_id": runs[0],
                "format": "real_imag",
                "max_points": 0,
            }
            try:
                payload = await self.request(session, "cst_read_saved_result", args)
                self.auxiliary.append(
                    {"tree": tree, "payload": payload, "evidence": self.responses[-1]["evidence"]}
                )
                self.capture_power_curve(tree, payload)
                self.capture_reference_curve(tree, payload)
            except Stop as exc:
                self.check("auxiliary curve " + tree, "inconclusive", reason=str(exc))
            if entry in modal_entries:
                try:
                    args = {
                        "result_path": tree,
                        "output_file": str(self.results / f"exports/modal_{number:02d}.txt"),
                        "format": "txt",
                    }
                    await self.request(session, "cst_export_result", args)
                    self.artifact_contract("cst_export_result", args, self.responses[-1])
                except Stop as exc:
                    self.check("modal export " + tree, "inconclusive", reason=str(exc))
        # 3D port mode fields may be absent from the cst.results 1D inventory.
        paths = sorted(set(self.tree_items + [e.get("tree_path", "") for e in self.entries]))
        fields = [
            p
            for p in paths
            if re.search(r"port.?modes?", p, re.IGNORECASE)
            and re.search(
                r"e[- ]?field|h[- ]?field|[\\/][eh]\d+(?:\s*\[[^\]]+\])?$", p, re.IGNORECASE
            )
        ]
        roots = [p for p in fields if not any(p.startswith(q + "\\") for q in fields if q != p)]
        for number, tree in enumerate(roots[:8], 1):
            args = {
                "result_path": tree,
                "output_file": str(self.results / f"exports/modal_field_{number:02d}.txt"),
                "format": "txt",
            }
            try:
                await self.request(session, "cst_export_result", args)
                self.artifact_contract("cst_export_result", args, self.responses[-1])
                response = self.responses[-1]
                self.inspect_numeric_export(args, response)
                if re.search(r"[\\/]e\d+(?:\s*\[[^\]]+\])?$", tree, re.IGNORECASE):
                    for artifact in response["artifacts"]:
                        if (
                            Path(artifact["original"]).resolve()
                            == Path(args["output_file"]).resolve()
                        ):
                            table = ascii_table(Path(artifact["archived"]))
                            self.assess_te10_field(table, artifact["archived"], port_plane=True)
            except Stop as exc:
                self.check("modal field export " + tree, "inconclusive", reason=str(exc))
            except Incomplete as exc:
                if self.unknown:
                    raise
                self.check("modal field assessment " + tree, "inconclusive", reason=str(exc))
        modal_sources = [
            a["tree"]
            for a in self.auxiliary
            if a["tree"] in {e["tree_path"] for e in modal_entries}
        ]
        self.check(
            "native modal extraction",
            "passed" if modal_sources else "inconclusive",
            obtained=modal_sources,
            reason="Generic reads and exports only. No dedicated modal API or extra raw VBA fallback",
        )
        self.assess_modal_cutoff()
        if all(k in self.power_curves for k in ("P_stim", "P_acc", "P_rad")):
            self.bindings["POWER_CURVES"] = {
                k: v for k, v in self.power_curves.items() if k != "P_ref"
            }
        write_json(self.results / "exports/native_auxiliary.json", self.auxiliary)

    def capture_power_curve(self, tree, payload):
        key = POWER_ITEMS.get(tree.split("\\")[-1].casefold())
        if key is None:
            return
        x, real, imag = payload.get("x", []), payload.get("real", []), payload.get("imag", [])
        valid = (
            payload.get("source") == "cst.results" and len(x) == len(real) == len(imag) and bool(x)
        )
        valid = valid and re.search(r"\bGHz\b", payload.get("xlabel", ""), re.IGNORECASE)
        valid = valid and re.search(r"\bW\b|\bwatt", payload.get("ylabel", ""), re.IGNORECASE)
        valid = (
            valid
            and all(math.isfinite(v) for v in [*x, *real, *imag])
            and all(abs(v) < 1e-10 for v in imag)
        )
        if valid and key not in self.power_curves and key not in self.ambiguous_power:
            self.power_curves[key] = {"x": x, "y": real}
        else:
            self.check(
                "native power units or ambiguity " + key,
                "inconclusive",
                obtained=payload,
                reason="Power must have verified GHz and W units and a unique real native source",
            )
            if key in self.power_curves:
                self.power_curves.pop(key)
                self.ambiguous_power.add(key)

    def capture_reference_curve(self, tree, payload):
        # ZRef is explicitly native reference data. Zwave and Zline alone are not ZRef.
        convention = payload.get("normalization_convention")
        identifies_s_reference = payload.get("is_s_parameter_reference") is True
        documented = (
            self.metadata.get("installation", {})
            .get("normalization_documentation", {})
            .get("non_tem_wave_reference_documented", False)
        )
        native_zref = (
            payload.get("source") == "cst.results"
            and documented
            and "\\reference impedance\\" in tree.casefold()
            and re.fullmatch(r"ZRef\s+1(?:\s*\(?1\)?)?", tree.split("\\")[-1], re.IGNORECASE)
            and payload.get("run_id") == self.bindings.get("RUN_ID")
            and Path(payload.get("project_path", "")).resolve() == self.project.resolve()
        )
        explicit = identifies_s_reference and convention == "voltage_reflection_real_reference"
        if not (native_zref or explicit):
            return
        x, values = complex_curve(payload)
        if not re.search(r"ohm|Ω", payload.get("ylabel", ""), re.IGNORECASE):
            return
        if not all(v.real > 0 and abs(v.imag) <= 1e-10 for v in values):
            return
        if explicit and payload.get("s_parameter_tree_path") != self.bindings.get("TREE_S11"):
            return
        if explicit and payload.get("s_parameter_run_id") != self.bindings.get("RUN_ID"):
            return
        if self.ambiguous_reference:
            return
        if self.reference_curve is not None:
            self.reference_curve = None
            self.ambiguous_reference = True
            self.metadata.pop("native_modal_reference", None)
            self.check(
                "native modal reference uniqueness",
                "inconclusive",
                reason="Multiple native reference curves",
            )
            return
        self.reference_curve = {
            "x": x,
            "values": [v.real for v in values],
            "tree": tree,
            "convention": convention
            or "Documented travelling-wave amplitudes with real positive native ZRef",
            "evidence": self.responses[-1]["evidence"],
        }
        self.metadata["native_modal_reference"] = self.reference_curve

    def assess_modal_cutoff(self):
        candidates = [
            a
            for a in self.auxiliary
            if re.search(r"cutoff|fcutoff", a["tree"], re.IGNORECASE)
            and (
                re.search(r"port\s*1|port1", a["tree"], re.IGNORECASE)
                or a["tree"].casefold().startswith("1d results\\port information\\")
                and a["tree"].split("\\")[-1] == "1(1)"
            )
        ]
        supported = []
        for source in candidates:
            payload = source["payload"]
            label = payload.get("ylabel", "")
            match = re.search(r"\b(GHz|MHz|Hz)\b", label, re.IGNORECASE)
            real, imag = payload.get("real", []), payload.get("imag", [])
            if not match or not real or len(real) != len(imag) or any(abs(v) > 1e-10 for v in imag):
                continue
            scale = {"ghz": 1, "mhz": 1e-3, "hz": 1e-9}[match.group(1).casefold()]
            # Multiple modes or varying values cannot be reduced to a fabricated scalar.
            values = [v * scale for v in real]
            if max(values) - min(values) <= 1e-4 and all(math.isfinite(v) for v in values):
                supported.append((values[0], source["evidence"]))
        self.check(
            "native fundamental cutoff",
            "inconclusive"
            if len(supported) != 1
            else abs(supported[0][0] / THEORY["te10_cutoff_ghz"] - 1)
            <= CRITERIA["cutoff_relative"],
            expected=THEORY["te10_cutoff_ghz"],
            obtained=supported,
            tolerance=CRITERIA["cutoff_relative"],
            reason="Theoretical TE10 prediction is separate from measured cutoff and field identification",
        )

    def artifact_contract(self, tool, arguments, response):
        super().artifact_contract(tool, arguments, response)
        if tool in {"cst_export_result", "cst_export_farfield", "cst_export_touchstone"}:
            requested = Path(arguments.get("output_file", arguments.get("file_path"))).resolve()
            produced = {
                str(Path(a["original"]).resolve()).casefold(): a for a in response["artifacts"]
            }
            artifact = produced.get(str(requested).casefold())
            self.check(
                "requested export file " + str(response["id"]),
                bool(artifact)
                and requested.is_file()
                and artifact["fresh"]
                and artifact["bytes"] > 0,
                expected=str(requested),
                obtained=artifact,
                evidence=response["evidence"],
            )

    def validate_touchstone(self, response):
        candidates = {
            str(Path(a["archived"]).resolve()).casefold(): a
            for a in response["artifacts"]
            if Path(a["archived"]).suffix.casefold() == ".s1p"
        }
        if len(candidates) != 1:
            raise Incomplete("Unique Touchstone artifact missing")
        artifact = next(iter(candidates.values()))
        ts = touchstone(Path(artifact["archived"]))
        self.touchstone_data = ts
        artifact.update(
            readable=True,
            units=ts["frequency_unit"],
            sample_count=ts["sample_count"],
            inspection={k: v for k, v in ts.items() if k not in {"x", "values"}},
        )
        self.require(
            "Touchstone syntax and reference",
            artifact["fresh"]
            and ts["ports"] == 1
            and ts["reference_ohm"] == 50
            and ts["representation"] == "RI"
            and all(b > a for a, b in zip(ts["x"], ts["x"][1:])),
            expected="Full one-port RI file explicitly renormalized to 50 ohms",
            obtained=artifact["inspection"],
            evidence=artifact["archived"],
        )
        if self.baseline:
            self.check(
                "Touchstone full frequency grid",
                compare(ts["x"], self.baseline[0], CRITERIA["frequency_ghz"]),
                obtained={
                    "native_samples": len(self.baseline[0]),
                    "export_samples": len(ts["x"]),
                    "max_frequency_difference_ghz": max(
                        (abs(a - b) for a, b in zip(ts["x"], self.baseline[0])), default=None
                    ),
                },
                tolerance=CRITERIA["frequency_ghz"],
                evidence=artifact["archived"],
                mandatory=True,
            )

    def compare_touchstone_reference(self):
        if not self.touchstone_data or not self.baseline or not self.reference_curve:
            self.check(
                "Touchstone cross-normalization comparison",
                "inconclusive",
                reason="Actual native S-parameter reference and understood voltage-reflection convention are required. Theoretical wave impedance is not substituted",
            )
            return
        x, values = self.baseline
        reference = self.reference_curve
        if (
            not all(b > a for a, b in zip(reference["x"], reference["x"][1:]))
            or not reference["x"][0] - CRITERIA["frequency_ghz"]
            <= x[0]
            <= x[-1]
            <= reference["x"][-1] + CRITERIA["frequency_ghz"]
            or any(abs(1 - s) < 1e-15 for s in values)
        ):
            self.check(
                "Touchstone cross-normalization comparison",
                "inconclusive",
                reason="Reference grid differs or conversion is singular",
            )
            return
        aligned_frequencies = [min(max(f, reference["x"][0]), reference["x"][-1]) for f in x]
        native_reference = [
            self.interpolate_complex(reference["x"], reference["values"], f)
            for f in aligned_frequencies
        ]
        impedance = [z0 * (1 + s) / (1 - s) for z0, s in zip(native_reference, values)]
        if any(abs(z + 50) < 1e-15 for z in impedance):
            self.check(
                "Touchstone cross-normalization comparison",
                "inconclusive",
                reason="50-ohm conversion is singular",
            )
            return
        converted = [(z - 50) / (z + 50) for z in impedance]
        ts = self.touchstone_data
        passed = compare(ts["x"], x, CRITERIA["frequency_ghz"]) and compare(
            ts["values"], converted, 1e-5
        )
        self.check(
            "Touchstone cross-normalization comparison",
            passed,
            expected="Z=Zref*(1+S)/(1-S), S50=(Z-50)/(Z+50)",
            obtained={
                "native_reference": reference,
                "max_complex_difference": max(
                    (abs(a - b) for a, b in zip(ts["values"], converted)), default=None
                ),
            },
            tolerance={"frequency_ghz": CRITERIA["frequency_ghz"], "complex_s11": 1e-5},
            mandatory=True,
        )
        write_json(
            self.results / "exports/common_reference_comparison.json",
            {
                "formula": "Z=Zref*(1+S)/(1-S), S50=(Z-50)/(Z+50)",
                "x_ghz": x,
                "native_reference_ohm": native_reference,
                "s11_50ohm_from_modal": converted,
                "native_reference_source": reference,
                "reference_sampling": "Linear interpolation of measured native ZRef on the modal S11 grid",
                "endpoint_alignment": "Only endpoint roundoff within the recorded frequency tolerance is clamped to the native reference range",
                "max_endpoint_alignment_ghz": max(
                    abs(a - b) for a, b in zip(x, aligned_frequencies)
                ),
            },
        )

    def grid_from_source(self, response, artifact, quantity):
        grid = farfield_grid(Path(artifact["archived"]), quantity)
        payload = response["payload"]
        tree = payload.get("tree_path") or payload.get("source", {}).get("tree_path")
        self.require(
            "native radiation frequency " + str(response["id"]),
            self.path_frequency(tree) == 10,
            expected=10,
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
            f = float(declared[1]) * {"ghz": 1, "mhz": 1e-3, "hz": 1e-9}[declared[2].casefold()]
            self.require("radiation header frequency", abs(f - 10) <= 1e-9, obtained=f, expected=10)
        artifact.update(
            readable=True,
            units=grid["unit"],
            sample_count=grid["sample_count"],
            inspection={k: v for k, v in grid.items() if k not in {"grid", "theta", "phi"}},
        )
        return grid

    def assess_farfield_export(self, response):
        seen = set()
        for artifact in response["artifacts"]:
            path = Path(artifact["archived"])
            if str(path).casefold() in seen or not artifact["fresh"]:
                continue
            seen.add(str(path).casefold())
            if path.suffix.casefold() == ".ffs":
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
                    compare(info["frequency_ghz"], [10], 1e-9),
                    obtained=info["frequency_ghz"],
                    expected=10,
                )
            elif path.suffix.casefold() in {".txt", ".csv"}:
                self.realized = self.grid_from_source(response, artifact, "realized_gain")

    def inspect_numeric_export(self, arguments, response):
        requested = Path(arguments["output_file"]).resolve()
        artifacts = {
            str(Path(a["archived"]).resolve()).casefold(): a
            for a in response["artifacts"]
            if Path(a["original"]).resolve() == requested
        }
        for artifact in artifacts.values():
            table = ascii_table(Path(artifact["archived"]))
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
                    evidence=artifact["archived"],
                    reason="The csv extension contains native ASCII with a different delimiter",
                )
            if requested.name.startswith("s11_modal"):
                self.validate_native_ascii(artifact, table)
            elif requested.name.startswith(("efield_", "hfield_", "surface_current_")):
                header = table["header"]
                unit = "V/m" if requested.name.startswith("efield") else "A/m"
                self.check(
                    "numeric vector field " + requested.name,
                    "passed"
                    if unit in header
                    and table["columns"] >= 6
                    and re.search(r"[EHJK][xyz](?:Re|Im)?\b", header, re.IGNORECASE)
                    else "inconclusive",
                    obtained={
                        "tree": arguments["result_path"],
                        "header": header,
                        "columns": table["columns"],
                    },
                    evidence=artifact["archived"],
                )
                if requested.name.startswith("efield"):
                    self.assess_te10_field(table, artifact["archived"])

    def validate_native_ascii(self, artifact, table):
        header, rows = table["header"], table["rows"]
        if (
            not self.baseline
            or not re.search(r"\bGHz\b", header, re.IGNORECASE)
            or not re.search(r"S1[, _]?1|S-Parameter", header, re.IGNORECASE)
        ):
            self.check(
                "modal S11 ASCII quantity",
                "inconclusive",
                obtained=header,
                evidence=artifact["archived"],
            )
            return
        x, values = self.baseline
        quantity = (
            "db"
            if re.search(r"\bdB\b", header)
            else "mag"
            if re.search(r"magnitude|abs", header, re.IGNORECASE)
            else None
        )
        if (
            table["columns"] == 3
            and re.search(r"real", header, re.IGNORECASE)
            and re.search(r"imag", header, re.IGNORECASE)
        ):
            expected, actual = values, [complex(row[1], row[2]) for row in rows]
        elif table["columns"] == 2 and quantity:
            expected, actual = modal_derived(values)[quantity], [row[1] for row in rows]
        else:
            self.check(
                "modal S11 ASCII quantity",
                "inconclusive",
                obtained=header,
                reason="Header does not establish complex RI or a supported scalar representation",
                evidence=artifact["archived"],
            )
            return
        self.check(
            "modal full-curve ASCII comparison",
            compare([row[0] for row in rows], x, CRITERIA["frequency_ghz"])
            and compare(actual, expected, 1e-5),
            obtained={
                "native_samples": len(x),
                "export_samples": len(rows),
                "max_frequency_difference_ghz": max(
                    (abs(row[0] - f) for row, f in zip(rows, x)), default=None
                ),
                "max_value_difference": max(
                    (
                        abs(a - b)
                        for a, b in zip(actual, expected)
                        if a is not None and b is not None
                    ),
                    default=None,
                ),
            },
            tolerance={"frequency_ghz": CRITERIA["frequency_ghz"], "value": 1e-5},
            evidence=artifact["archived"],
            mandatory=True,
            reason="Discovered native S11 branch export retains the modal reference",
        )

    def assess_te10_field(self, table, evidence, port_plane=False):
        check_name = (
            "TE10 native port-plane profile" if port_plane else "TE10 uniform-guide profile"
        )
        columns = []
        for line in table["header"].splitlines():
            candidate = re.findall(r"([A-Za-z][A-Za-z0-9]*)\s*\[([^\]]+)\]", line)
            if len(candidate) == table["columns"]:
                columns = candidate
                break
        names = {name.casefold(): i for i, (name, unit) in enumerate(columns)}
        needed = ["x", "y", "z", "exre", "exim", "eyre", "eyim", "ezre", "ezim"]
        if not all(name in names for name in needed) or any(
            columns[names[n]][1].casefold() != "mm" for n in ("x", "y", "z")
        ):
            self.check(
                check_name,
                "inconclusive",
                evidence=evidence,
                reason="Requires identified complex Cartesian E components and coordinate units in mm",
            )
            return
        lines = {}
        for row in table["rows"]:
            x, y, z = (row[names[n]] for n in ("x", "y", "z"))
            normal_ok = abs(z + 30) <= 1e-5 if port_plane else -27 <= z <= -15
            if normal_ok and abs(y) <= 2 and abs(x) < 11.43:
                fields = [
                    complex(row[names[n + "re"]], row[names[n + "im"]]) for n in ("ex", "ey", "ez")
                ]
                lines.setdefault((y, z), []).append((x, *fields))
        candidates = [
            sorted(line)
            for line in lines.values()
            if len(line) >= 7
            and len({p[0] for p in line}) == len(line)
            and min(p[0] for p in line) < -8
            and max(p[0] for p in line) > 8
        ]
        if not candidates:
            self.check(
                check_name,
                "inconclusive",
                evidence=evidence,
                reason="No adequately sampled transverse line at the intended port or uniform-guide location",
            )
            return
        diagnostics = []
        for line in candidates:
            profile = [math.cos(math.pi * p[0] / 22.86) for p in line]
            ey = [p[2] for p in line]
            energy = sum(abs(e) ** 2 for e in ey)
            total = sum(sum(abs(e) ** 2 for e in p[1:]) for p in line)
            if not energy or not total:
                continue
            scale = sum(v * e for v, e in zip(profile, ey)) / sum(v * v for v in profile)
            error = math.sqrt(sum(abs(e - scale * v) ** 2 for v, e in zip(profile, ey)) / energy)
            diagnostics.append(
                {"relative_rms": error, "ey_fraction": energy / total, "samples": len(line)}
            )
        self.check(
            check_name,
            "inconclusive"
            if not diagnostics
            else all(
                d["relative_rms"] <= CRITERIA["te10_profile_relative_rms"]
                and d["ey_fraction"] >= CRITERIA["te10_ey_energy_fraction"]
                for d in diagnostics
            ),
            expected=THEORY["te10_electric_field"],
            obtained=diagnostics,
            evidence=evidence,
            reason="Complex profile fit removes overall phase and amplitude inside the uniform guide, away from the aperture",
        )

    @staticmethod
    def signed_profile(grid, phi):
        opposite = (phi + 180) % 360
        if phi not in grid["phi"] or opposite not in grid["phi"]:
            raise Incomplete("Opposite azimuth rows missing from full angular grid")
        return sorted(
            [(-t, grid["grid"][(t, opposite)]) for t in grid["theta"] if t > 0]
            + [(t, grid["grid"][(t, phi)]) for t in grid["theta"]]
        )

    @staticmethod
    def beamwidth(profile):
        angles, values = zip(*profile)
        peak = max(range(len(values)), key=values.__getitem__)
        level = values[peak] - 3
        crossings = []
        for direction in (-1, 1):
            i = peak
            while 0 <= i + direction < len(values):
                j = i + direction
                if values[j] <= level < values[i]:
                    crossing = angles[i] + (angles[j] - angles[i]) * (level - values[i]) / (
                        values[j] - values[i]
                    )
                    crossings.append(crossing)
                    break
                i = j
        if len(crossings) != 2:
            raise Incomplete("Principal-plane main lobe lacks two half-power crossings")
        return crossings[1] - crossings[0]

    def assess_radiation(self):
        metrics = self.native_metrics_10()
        directivity, realized = self.directivity, self.realized
        dpeak = (
            max(directivity["grid"].values()) if directivity else metrics.get("max_directivity_dbi")
        )
        gpeak = max(realized["grid"].values()) if realized else metrics.get("max_realized_gain_dbi")
        self.check(
            "WR90 directivity",
            "inconclusive" if dpeak is None else abs(dpeak - 8.13) <= CRITERIA["directivity_db"],
            expected=8.13,
            obtained=dpeak,
            tolerance=CRITERIA["directivity_db"],
        )
        self.check(
            "WR90 realized gain",
            "inconclusive" if gpeak is None else abs(gpeak - 7.94) <= CRITERIA["realized_gain_db"],
            expected=7.94,
            obtained=gpeak,
            tolerance=CRITERIA["realized_gain_db"],
        )
        angular = directivity or realized
        try:
            if angular is None:
                raise Incomplete("Proven full native angular grid is unavailable")
            peak = max(angular["grid"].values())
            peak_directions = [p for p, v in angular["grid"].items() if abs(v - peak) < 1e-6]
            self.check(
                "main beam towards +Z",
                all(t <= angular["step_theta"] for t, p in peak_directions),
                expected="theta within one actual angular step of zero",
                obtained=peak_directions,
                tolerance=angular["step_theta"],
            )
            forward = max(v for (t, p), v in angular["grid"].items() if t == 0)
            backward = max(v for (t, p), v in angular["grid"].items() if t == 180)
            fbr = forward - backward
            rear_peak = max(v for (t, p), v in angular["grid"].items() if t >= 90)
            self.check(
                "WR90 front-to-back",
                abs(fbr - 12) <= CRITERIA["front_to_back_db"],
                expected=12,
                obtained={
                    "quantity": angular["quantity"],
                    "axial_front_to_back_db": fbr,
                    "peak_to_rear_hemisphere_db": peak - rear_peak,
                },
                tolerance=CRITERIA["front_to_back_db"],
                reason="Axial +Z/-Z ratio. Rear-hemisphere maximum retained separately",
            )
            profiles = {
                name: self.signed_profile(angular, phi)
                for name, phi in (("E_YZ_phi90", 90), ("H_XZ_phi0", 0))
            }
            widths = {name: self.beamwidth(profile) for name, profile in profiles.items()}
            self.check(
                "H-plane narrower than E-plane",
                widths["H_XZ_phi0"] < widths["E_YZ_phi90"],
                obtained=widths,
            )
            write_json(
                self.results / "exports/signed_principal_planes.json",
                {
                    "quantity": angular["quantity"],
                    "unit": "dBi",
                    "profiles": profiles,
                    "beamwidth_deg": widths,
                },
            )
        except Incomplete as exc:
            for name in (
                "main beam towards +Z",
                "WR90 front-to-back",
                "H-plane narrower than E-plane",
            ):
                if not any(c["name"] == name for c in self.checks):
                    self.check(name, "inconclusive", reason=str(exc))
        self.assess_cut_consistency()
        rad, total, gain = (
            metrics.get(k)
            for k in ("radiation_efficiency", "total_efficiency", "verified_max_gain_dbi")
        )
        self.check(
            "radiation efficiency",
            "inconclusive" if rad is None else abs(rad - 1) <= 0.03,
            expected=1,
            obtained=rad,
            tolerance=0.03,
            reason="PEC/vacuum physical target is unity. Overshoot is retained, not a physical target",
        )
        self.check(
            "total efficiency reference diagnostic",
            "inconclusive" if total is None else abs(total - 0.956) <= 0.05,
            expected=0.956,
            obtained=total,
            tolerance=0.05,
        )
        if self.baseline and all(v is not None for v in (rad, total, dpeak, gpeak)):
            s = self.interpolate_complex(*self.baseline, 10)
            identities = compare(
                [total, 10 ** ((gpeak - dpeak) / 10)], [rad * (1 - abs(s) ** 2), total], 0.03
            )
            verified_gain = metrics.get("verified_max_gain_dbi")
            self.check(
                "IEEE gain identity",
                "inconclusive"
                if verified_gain is None
                else abs(10 ** ((verified_gain - dpeak) / 10) - rad) <= 0.03,
                expected="G/D=eta_rad",
                obtained=verified_gain,
                tolerance=0.03,
                reason="Independent native GetMaxGain is required because the metric parser can fall back to realized gain",
            )
            self.check(
                "efficiency identities",
                identities,
                expected="Grealized=D*eta_total, eta_total=eta_rad*(1-|Smodal|^2)",
                obtained={
                    "directivity_dbi": dpeak,
                    "realized_gain_dbi": gpeak,
                    "radiation_efficiency": rad,
                    "total_efficiency": total,
                    "native_gain_dbi": gain,
                },
                tolerance=0.03,
            )
        else:
            self.check(
                "efficiency identities",
                "inconclusive",
                reason="Native efficiency metrics or modal S11 unavailable",
            )
        sphere_efficiency = sphere_mean(realized) if realized else None
        self.check(
            "realized-gain sphere integral",
            "inconclusive"
            if total is None or sphere_efficiency is None
            else abs(sphere_efficiency - total) <= 0.03,
            expected=total,
            obtained=sphere_efficiency,
            tolerance=0.03,
        )

    def native_metrics_10(self):
        metrics = {}
        for response in self.responses:
            if response["rejected"] or response["tool"] not in {
                "cst_get_farfield_metrics",
                "cst_get_gain",
                "cst_get_efficiency",
                "cst_get_efficiency_breakdown",
            }:
                continue
            args, payload = response["arguments"], response["payload"]
            if args.get("frequency_ghz", args.get("frequency")) != 10:
                continue
            tree = payload.get("tree_path") or payload.get("sources", {}).get(
                "farfield_plot", {}
            ).get("tree_path")
            if self.path_frequency(tree) != 10:
                self.check(
                    "native radiation metric frequency",
                    "inconclusive",
                    obtained=payload,
                    evidence=response["evidence"],
                )
                continue
            for key, value in payload.get("metrics", {}).items():
                if key in {
                    "radiation_efficiency",
                    "total_efficiency",
                    "radiation_efficiency_db",
                    "total_efficiency_db",
                    "max_gain_dbi",
                }:
                    continue
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                ):
                    metrics.setdefault(key, value)
            # Raw native keys prove which values the plot actually measured instead of API fallbacks.
            source = payload.get("path")
            for artifact in response["artifacts"]:
                if (
                    not source
                    or Path(artifact["original"]).resolve() != Path(source).resolve()
                    or not artifact["fresh"]
                ):
                    continue
                raw = read_text(Path(artifact["archived"]))
                for native_key, key in (
                    ("GetRadiationEfficiency", "radiation_efficiency"),
                    ("GetTotalEfficiency", "total_efficiency"),
                    ("GetMaxGain", "verified_max_gain_dbi"),
                ):
                    match = re.search(rf"(?m)^{native_key}\s*=\s*([-+\d.eE]+)\s*$", raw)
                    if match:
                        value = float(match[1])
                        if math.isfinite(value) and value > -199:
                            metrics[key] = 10 ** (value / 10) if "efficiency" in key else value
                            self.metadata.setdefault("radiation_metric_sources", {})[key] = (
                                artifact["archived"]
                            )
        # Verified power curves provide an independent route if plot efficiency extraction is absent.
        try:
            pa = self.power_at_10(self.power_curves["P_acc"])
            ps = self.power_at_10(self.power_curves["P_stim"])
            pr = self.power_at_10(self.power_curves["P_rad"])
            if pa > 0 and ps > 0:
                metrics.setdefault("radiation_efficiency", pr / pa)
                metrics.setdefault("total_efficiency", pr / ps)
                self.metadata["independent_power_efficiencies"] = {
                    "radiation": pr / pa,
                    "total": pr / ps,
                    "source": "Verified native power curves",
                }
        except (KeyError, Incomplete):
            pass
        self.metadata["radiation_metrics_10GHz"] = metrics
        return metrics

    def assess_cut_consistency(self):
        for response in self.responses:
            if (
                response["tool"] != "cst_get_pattern_cut"
                or response["rejected"]
                or not self.realized
            ):
                continue
            payload = response["payload"]
            phi = response["arguments"]["phi_cut"]
            profile = dict(self.signed_profile(self.realized, phi))
            angles, values = payload.get("angle_deg", []), payload.get("value", [])
            paired = [(a, v) for a, v in zip(angles, values) if a in profile]
            coverage = (
                len(angles) == len(values) == payload.get("n_points")
                and len({a for a, v in paired}) == len(profile)
                and self.path_frequency(payload.get("tree_path")) == 10
                and payload.get("angle_convention")
                == "signed theta: +theta at phi, -theta at phi+180"
            )
            self.check(
                "signed principal-plane full-grid comparison " + str(phi),
                "inconclusive"
                if not paired or not coverage
                else compare([v for a, v in paired], [profile[a] for a, v in paired], 0.05),
                obtained={
                    "matched_samples": len(paired),
                    "angle_convention": payload.get("angle_convention"),
                    "full_grid_cut_coverage": coverage,
                },
                tolerance=0.05,
                evidence=response["evidence"],
            )

    def power_at_10(self, curve):
        x, y = curve["x"], curve["y"]
        if not all(b > a for a, b in itertools.pairwise(x)):
            raise Incomplete("Power curve axis has duplicates or is unordered")
        indices = [i for i, f in enumerate(x) if abs(f - 10) <= 1e-6]
        if len(indices) == 1:
            return y[indices[0]]
        if len(x) > 10 and min(x) <= 10 <= max(x):
            return self.interpolate_complex(x, y, 10).real
        raise Incomplete("Sparse power curve lacks a 10 GHz sample")

    def assess_power(self):
        values = {}
        for key, curve in self.power_curves.items():
            try:
                values[key] = self.power_at_10(curve)
            except Incomplete as exc:
                self.check("power sample " + key, "inconclusive", reason=str(exc))
        ps, pa, pr = (values.get(key) for key in ("P_stim", "P_acc", "P_rad"))
        if ps is None or ps <= 0 or pa is None or pr is None or not self.baseline:
            self.check(
                "incident reflected accepted power",
                "inconclusive",
                obtained=values,
                reason="Verified incident, accepted, radiated native power and modal S11 required",
            )
            return
        reflected_from_s = ps * abs(self.interpolate_complex(*self.baseline, 10)) ** 2
        reflected_native = values.get("P_ref")
        consistent = abs((ps - reflected_from_s - pa) / ps) <= 0.03
        if reflected_native is not None:
            consistent = consistent and abs((reflected_native - reflected_from_s) / ps) <= 0.03
        self.check(
            "incident reflected accepted power",
            consistent,
            expected="Paccepted=Pincident-Preflected, Preflected=Pincident*|Smodal|^2",
            obtained=dict(**values, reflected_from_modal_s11=reflected_from_s),
            tolerance=0.03,
            reason="Reflected power from S11 is identified as an inference when a native reflected curve is absent",
        )
        loss_keys = ("P_loss_diel", "P_loss_metal")
        if all(k in values for k in loss_keys) and pa > 0:
            loss = sum(values[k] for k in loss_keys)
            self.check(
                "accepted radiated loss power",
                abs((pa - pr - loss) / pa) <= 0.03,
                expected="(Paccepted-Pradiated-Ploss)/Paccepted near zero",
                obtained=values,
                tolerance=0.03,
            )
        else:
            self.check(
                "accepted radiated loss power",
                "inconclusive",
                obtained=values,
                reason="Unavailable loss curves are not measured zero loss",
            )
        self.check(
            "native power radiation efficiency",
            "inconclusive" if pa <= 0 else abs(pr / pa - 1) <= 0.03,
            expected=1,
            obtained=pr / pa if pa > 0 else None,
            tolerance=0.03,
        )
        self.check(
            "radiated over incident reference diagnostic",
            abs(pr / ps - 0.956) <= 0.05,
            expected=0.956,
            obtained=pr / ps,
            tolerance=0.05,
        )

    def report(self):
        def cell(value):
            return (
                str(safe_json(value)).replace("|", "\\|").replace("\n", " ")
                if value is not None
                else "pending"
            )

        installation = self.metadata.get("installation", {}).get(
            "path", self.args.cst_path or DEFAULT_CST_PATH
        )
        command = "uv run capabilities_test\\04_antenna_simulation\\run_wr90.py"
        command += (
            " --preflight"
            if self.args.preflight
            else f" --solver-timeout-s {self.args.solver_timeout_s:g}"
        )
        command += f' --cst-path "{installation}" --cst-version {self.args.cst_version}'

        lines = [
            "# WR-90 open-ended waveguide validation",
            "",
            f"Outcome: {self.reason}. Exit code {self.exit_code}.",
            "",
            f"Mode: {self.metadata['mode']}. Started UTC: {self.metadata['started_utc']}.",
            "",
            f"Owned project: {self.project}",
            "",
            f"Interpreter: {sys.executable}",
            "",
            f"Selected installation: {cell(self.metadata.get('installation', {}).get('path'))}",
            "",
            "## Fixed model and implementation choices",
            "",
            "Vacuum aperture 22.86 by 10.16 mm in X and Y, PEC wall 2.5 mm, finite flange 40 by 20 mm. Aperture Z=0 radiates towards +Z. Guide length 30 mm and flange thickness 0.10 mm are implementation choices. The reference describes a PEC sheet, so this is not an exact reproduction.",
            "",
            "Library PEC is loaded as WR90PEC. Three bricks, one add and one subtraction must leave WR90:Body alone. The bore overshoots both guide ends and is removed. Background vacuum fills the opening.",
            "",
            "Port 1 uses Free coordinates at Z=-30 mm, zmin orientation and PortOnBound False. mode_number=1 requests one calculated mode, with fundamental TE10 intended. Listing does not independently prove its position or field profile.",
            "",
            f"Configuration requested: {cell(self.metadata['configuration'])}",
            "",
            f"[Finite-flange Ansys reference]({REFERENCE}). Targets at 10 GHz are directivity 8.13 dBi, realized gain 7.94 dBi, axial front-to-back approximately 12 dB and radiated/incident power 0.956. The physical radiation-efficiency target is unity. The published 1.006 value is numerical error, not a physical target.",
            "",
            "Approximately -13 dB modal S11 is inferred from the published incident and accepted powers. It is not a 50-ohm matching requirement. Guide theory predicts cutoffs near 6.557, 13.114 and 14.754 GHz for TE10, TE20 and TE01. Theory is kept separate from native measurements.",
            "",
            "## Normalization and capability coverage",
            "",
            "Simulation requests calculated modal normalization. wr90_50ohm.s1p uses the existing exporter with explicit 50-ohm renormalization. Its complex values are not directly compared with modal S11. A common-reference comparison requires native S-reference data with an understood convention. Neither analytical wave impedance nor unlabeled line impedance supplies that evidence.",
            "",
            "Directivity, IEEE gain, realized gain, radiation efficiency and total efficiency remain separate quantities. E-plane is YZ at phi=90 degrees and H-plane is XZ at phi=0 degrees. Signed profiles use the opposite azimuth and preserve rear radiation. No Lumerical source-window setting is fabricated.",
            "",
            f"Diagnostic criteria: {cell(CRITERIA)}",
            "",
            "## Checks",
            "",
            "| Check | Outcome | Expected | Obtained | Tolerance | Reason and evidence |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {cell(c['name'])} | {c['verdict']} | {cell(c['expected'])} | {cell(c['obtained'])} | {cell(c['tolerance'])} | {cell(c['reason'])}. {cell(c['evidence'])} |"
            for c in self.checks
        ]
        lines += [
            "",
            "## Phase outcomes",
            "",
            "| Phase | UTC start | Duration (s) | Outcome |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {p['phase']} | {p['started_utc']} | {p['elapsed_s']:.3f} | {p['outcome']} |"
            for p in self.phases
        ]
        lines += [
            "",
            "## Original CST messages",
            "",
            "Native snapshots retain partial or unknown coverage. Protocol errors and server stderr are separate evidence.",
            "",
        ]
        for log in self.logs:
            lines += [
                f"### {log['phase']} at {log['captured_utc']}",
                "",
                f"Snapshot: {log['json']}",
                "",
                "```text",
                log["text"].replace("```", "'''"),
                "```",
                "",
            ]
        if not self.logs:
            lines += ["No original CST messages captured.", ""]
        lines += [
            "## Artifacts",
            "",
            "| File | Bytes | Fresh | Inspection | SHA-256 |",
            "| --- | --- | --- | --- | --- |",
        ]
        lines += [
            f"| {cell(a['archived'])} | {a['bytes']} | {a['fresh']} | {cell(a['inspection'])} | {a['sha256']} |"
            for a in self.inventory
        ]
        lines += ["", "## Limitations", ""] + [
            f"- {value}" for value in self.metadata["limitations"]
        ]
        lines += [
            "",
            "## Execution state",
            "",
            f"Native state unknown: {self.unknown}. Solve pending: {self.pending_solve}.",
            "",
            "If a timeout or unknown state occurred, CST may still be running. No replacement session, solve retry or stop command is issued. On successful persistence, the owned project is reopened for inspection.",
            "",
            "Exactly two primary reports are emitted. calls.jsonl records actual tool requests in sequence. Full requests, responses, schemas, logs, metadata and validation JSON are supporting evidence under results/.",
            "",
            "## Command",
            "",
            "```powershell",
            command,
            "```",
            "",
        ]
        atomic_text(self.reports / "validation.md", "\n".join(lines))

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
        for name in sorted(
            MANDATORY
            | {
                "TE10 uniform-guide profile",
                "native fundamental cutoff",
                "Touchstone cross-normalization comparison",
            }
        ):
            if not any(c["name"] == name for c in self.checks):
                self.check(
                    name,
                    "not_executed" if self.args.preflight else "inconclusive",
                    reason=self.reason,
                )
        if not self.args.preflight:
            self.inspect_exports()
            if self.exit_code == 1 or any(
                c["mandatory"] and c["verdict"] == "failed" for c in self.checks
            ):
                self.exit_code = 1
                self.reason = "Native failure or diagnostic criterion failed. " + self.reason
            elif not self.unknown and all(
                any(c["name"] == name and c["verdict"] == "passed" for c in self.checks)
                for name in MANDATORY
            ):
                self.exit_code, self.reason = (
                    0,
                    "Required native and WR-90 diagnostics passed. Capability limitations remain separate",
                )
            else:
                self.exit_code, self.reason = 2, "Incomplete WR-90 assessment. " + self.reason
        self.metadata.update(
            solve_pending=self.pending_solve,
            cst_may_still_be_running=self.pending_solve or self.unknown,
            persistence_reads=self.persistence_reads,
        )
        self.persist()

    def inspect_exports(self):
        """Inspect artifact structure without claiming visual verification of runtime figures."""
        import struct

        for artifact in self.inventory:
            path = Path(artifact["archived"])
            try:
                if path.suffix.casefold() == ".png":
                    raw = path.read_bytes()
                    signature = (
                        raw.startswith(b"\x89PNG\r\n\x1a\n")
                        and raw[12:16] == b"IHDR"
                        and len(raw) >= 24
                    )
                    dimensions = list(struct.unpack(">II", raw[16:24])) if signature else []
                    artifact.update(
                        readable=None,
                        inspection={
                            "format": "PNG",
                            "signature": signature,
                            "dimensions": dimensions,
                            "visually_reviewed": False,
                        },
                    )
                    self.check(
                        "PNG structure " + path.name,
                        signature and all(v > 0 for v in dimensions),
                        evidence=str(path),
                    )
                elif path.suffix.casefold() == ".pdf":
                    raw = path.read_bytes()
                    artifact.update(
                        readable=None,
                        inspection={
                            "format": "PDF",
                            "signature": raw.startswith(b"%PDF"),
                            "rendered": False,
                        },
                    )
                    self.check(
                        "PDF rendering " + path.name,
                        "inconclusive",
                        reason="PDF signature alone does not prove rendered content. User inspection required",
                        evidence=str(path),
                    )
                elif (
                    path.suffix.casefold() in {".txt", ".csv"} and artifact.get("readable") is None
                ):
                    table = ascii_table(path)
                    artifact.update(
                        readable=True,
                        units=table["header"],
                        sample_count=table["sample_count"],
                        inspection={k: v for k, v in table.items() if k != "rows"},
                    )
            except (OSError, ValueError, Stop, Incomplete) as exc:
                artifact.update(readable=False, inspection={"exception": str(exc)})
                self.check(
                    "artifact inspection " + path.name,
                    "inconclusive",
                    reason=str(exc),
                    evidence=str(path),
                )

    async def run(self):
        with workspace_lock(self.parent, self.args.preflight, scenario="wr90"):
            prepare_outputs(self.base, self.parent, self.args.preflight, scenario="wr90")
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
                    (self.results / "protocol/server_stderr.log").open(
                        "w", encoding="utf-8"
                    ) as stderr,
                    preserve_cst_processes(),
                ):
                    async with stdio_client(params, errlog=stderr) as (read, write):  # noqa: SIM117 - explicit SDK cleanup scopes
                        async with ClientSession(read, write) as session:
                            try:
                                await self.protocol(session)
                                if not self.args.preflight:
                                    await self.live(session)
                            except Stop:
                                await self.diagnose(session)
                                raise
                self.exit_code, self.reason = (
                    (0, "Disabled-server preflight passed. All live phases not_executed")
                    if self.args.preflight
                    else (2, "Live manifest completed. Required diagnostic assessment pending")
                )
                self.check(
                    "preserving transport shutdown",
                    True,
                    reason="Existing server-only lifecycle hooks completed. Native process liveness after exit requires user inspection",
                )
            except BaseException as exc:  # noqa: BLE001 - finalize evidence on cancellation too
                leaves = list(exception_leaves(exc))
                cause = next((e for e in leaves if isinstance(e, (Stop, Incomplete))), leaves[0])
                if any(isinstance(e, (asyncio.CancelledError, KeyboardInterrupt)) for e in leaves):
                    self.unknown = True
                self.exit_code = 2 if isinstance(cause, Incomplete) or self.unknown else 1
                self.reason = f"{self.phase}: {type(cause).__name__}: {cause}"
                write_json(
                    self.results / "protocol/client_exception.json",
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
    runner = WR90(options(argv))
    try:
        code = asyncio.run(runner.run())
    except Exception as exc:  # noqa: BLE001 - report preparation faults without replacing evidence
        print(
            f"Preparation refused: {exc}. Close the owned project before live overwrite.",
            file=sys.stderr,
        )
        return 1
    print(f"{runner.reason}\nReport: {runner.reports / 'validation.md'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
