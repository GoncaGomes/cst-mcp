"""Material management tools for CST Studio MCP server."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

from mcp.types import TextContent, Tool

from cst_mcp.config import DEFAULT_VERSION
from cst_mcp.cst_client import CSTClient
from cst_mcp.material_library import (
    LibraryEntryNotFound,
    is_loadable_library_name,
    library_cst_version,
    library_directory,
    library_entry_names,
    library_name_matches,
    load_material_vba,
    read_library_material,
    similar_library_names,
)
from cst_mcp.validators import (
    ValidationError,
    default_material_name,
    validate_name,
    validate_non_negative,
    validate_positive,
    validate_range,
)
from cst_mcp.vba_builder import VBABuilder, VBAScript
from cst_mcp.vba_safety import vba_string_literal

DATA_DIR = Path(__file__).parent.parent / "data" / "materials"

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="cst_create_material",
        description=(
            "Create a new material with electromagnetic properties in CST Studio. "
            "Specify relative permittivity (epsilon), relative permeability (mu), "
            "electric and magnetic loss tangents, and conductivity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'MySubstrate')",
                },
                "epsilon": {
                    "type": "number",
                    "description": "Relative permittivity (epsilon_r)",
                    "default": 1.0,
                },
                "mu": {
                    "type": "number",
                    "description": "Relative permeability (mu_r)",
                    "default": 1.0,
                },
                "tan_d_e": {
                    "type": "number",
                    "description": "Electric loss tangent (tan delta_e)",
                    "default": 0.0,
                },
                "tan_d_m": {
                    "type": "number",
                    "description": "Magnetic loss tangent (tan delta_m)",
                    "default": 0.0,
                },
                "conductivity": {
                    "type": "number",
                    "description": "Electric conductivity in S/m",
                    "default": 0.0,
                },
                "color_r": {
                    "type": "number",
                    "description": "Red colour component (0-1)",
                    "default": 0.6,
                },
                "color_g": {
                    "type": "number",
                    "description": "Green colour component (0-1)",
                    "default": 0.6,
                },
                "color_b": {
                    "type": "number",
                    "description": "Blue colour component (0-1)",
                    "default": 0.6,
                },
                "transparency": {
                    "type": "number",
                    "description": "Transparency (0 = opaque, 1 = fully transparent)",
                    "default": 0.0,
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="cst_create_lossy_metal",
        description=(
            "Create a lossy metal material in CST Studio. Lossy metals model "
            "finite conductivity skin-effect losses, essential for accurate loss "
            "calculations in connectors, waveguides, and PCB traces."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Lossy Copper')",
                },
                "conductivity": {
                    "type": "number",
                    "description": "Electric conductivity in S/m (e.g. 5.8e7 for copper)",
                },
                "mu": {
                    "type": "number",
                    "description": "Relative permeability (mu_r)",
                    "default": 1.0,
                },
            },
            "required": ["name", "conductivity"],
        },
    ),
    Tool(
        name="cst_create_anisotropic_material",
        description=(
            "Create an anisotropic material with per-axis permittivity, "
            "permeability, and loss tangent values. Used for crystals, "
            "metamaterials, and composite substrates."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Material name"},
                "epsilon_x": {"type": "number", "description": "Relative permittivity along X"},
                "epsilon_y": {"type": "number", "description": "Relative permittivity along Y"},
                "epsilon_z": {"type": "number", "description": "Relative permittivity along Z"},
                "mu_x": {"type": "number", "description": "Relative permeability along X"},
                "mu_y": {"type": "number", "description": "Relative permeability along Y"},
                "mu_z": {"type": "number", "description": "Relative permeability along Z"},
                "tan_d_x": {
                    "type": "number",
                    "description": "Electric loss tangent along X",
                    "default": 0.0,
                },
                "tan_d_y": {
                    "type": "number",
                    "description": "Electric loss tangent along Y",
                    "default": 0.0,
                },
                "tan_d_z": {
                    "type": "number",
                    "description": "Electric loss tangent along Z",
                    "default": 0.0,
                },
            },
            "required": [
                "name",
                "epsilon_x",
                "epsilon_y",
                "epsilon_z",
                "mu_x",
                "mu_y",
                "mu_z",
            ],
        },
    ),
    Tool(
        name="cst_load_material",
        description=(
            "Load a native CST .mtd material definition and all its properties under "
            "a project name ('name', derived from library_name when omitted; the "
            "response returns the name used). Uses CST_PATH/Library/Materials by default, "
            "or the directory supplied as library_path. library_name is the .mtd file name "
            "without extension, as cst_list_library_materials returns it (e.g. "
            "'Rogers RT-duroid 5870 (lossy)', not 'Rogers RT/duroid 5870'); an unknown name "
            "returns up to five close library names in 'similar'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": (
                        "Optional name for the material in the project. Letters, digits, "
                        "space, underscore, dot and hyphen, starting with a letter or "
                        "underscore; default: the library name with other characters "
                        "replaced by '_' (e.g. 'Copper (annealed)' -> 'Copper_annealed')"
                    ),
                },
                "library_name": {
                    "type": "string",
                    "description": (
                        "Native .mtd file name without extension (e.g. 'Copper (annealed)')"
                    ),
                },
                "library_path": {
                    "type": "string",
                    "description": (
                        "Directory containing native .mtd material files. "
                        "Overrides the configured CST installation's Library/Materials directory."
                    ),
                },
            },
            "required": ["library_name"],
        },
    ),
    Tool(
        name="cst_list_library_materials",
        description=(
            "List the native CST material library entries that cst_load_material loads, "
            "read from the .mtd files on disk (no CST connection, no model history). Each "
            "entry gives library_name exactly as cst_load_material accepts it, and the "
            "file's type, epsilon_r, mu_r, conductivity (S/m), tan_d with tan_d_freq (Hz) "
            "and tan_d_model, and dispersion_model, with MCP 12's field names; a value the "
            "file does not give is null with the reason in 'unavailable'. The result names "
            "the library directory and the CST version it belongs to. filter matches every "
            "word case-insensitively, ignoring punctuation ('FR4', 'RT/duroid 5870', 'copper')."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "filter": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "description": "Words that must all occur in the entry name",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 1000,
                    "description": "Most entries to return, in name order (default 50)",
                },
                "include_properties": {
                    "type": "boolean",
                    "description": "Read each entry's properties (default true); false lists names",
                },
                "library_path": {
                    "type": "string",
                    "description": (
                        "Directory containing native .mtd material files. "
                        "Overrides the configured CST installation's Library/Materials directory."
                    ),
                },
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="cst_list_materials",
        description=(
            "List materials from the reference database bundled with this MCP server. "
            "These are not CST library entries: names are not .mtd names and values can "
            "differ from CST's (use cst_list_library_materials for the CST library and "
            "cst_get_project_materials for the open project). "
            "Optionally filter by category: 'metals', 'dielectrics', or 'substrates'. "
            "Returns name, key EM properties, and usage notes for each material."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Filter by category: 'metals', 'dielectrics', or 'substrates'",
                    "enum": ["metals", "dielectrics", "substrates"],
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="cst_assign_material",
        description=(
            "Assign a material to an existing solid in CST Studio. "
            "The solid is specified as 'Component:SolidName'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "solid": {
                    "type": "string",
                    "description": "Solid reference as 'Component:SolidName' (e.g. 'Antenna:Patch')",
                },
                "material": {
                    "type": "string",
                    "description": "Material name to assign",
                },
            },
            "required": ["solid", "material"],
        },
    ),
    Tool(
        name="cst_get_material_info",
        description=(
            "Get electromagnetic properties of a material from the reference database "
            "bundled with this MCP server, not the CST library or the project; values can "
            "differ from CST's (use cst_list_library_materials for CST library values). "
            "Returns epsilon_r, mu_r, conductivity, loss tangent, and usage notes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Copper', 'FR-4', 'Rogers RO4003C')",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="cst_get_project_materials",
        description=(
            "Read back the materials defined in the open CST project, as CST reports them "
            "(not the bundled database). For each material: found, type (normal, lossy_metal, "
            "pec, anisotropic, other with CST's raw type), whether it is the background "
            "material, epsilon_r, mu_r and electric conductivity (S/m) per axis, and the "
            "magnetic conductivity. CST has no getter for the loss tangent, its frequency or "
            "the dispersion model, so those are always null with the reason in 'unavailable'. "
            "Read-only: adds no history entry; refused while a solver runs. Optional "
            "'names' limits the readback to those materials (exact, case-sensitive)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "description": (
                        "Material names to read back, exactly as in the project (e.g. "
                        "'Copper (annealed)', 'FR-4 (lossy)'). Default: every material."
                    ),
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                    "minItems": 1,
                    "maxItems": 100,
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="cst_delete_material",
        description="Delete a material from the current CST project.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the material to delete",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="cst_create_debye_material",
        description=(
            "Create a frequency-dependent dielectric material using the Debye "
            "relaxation model. Models polar dielectrics where permittivity "
            "decreases with frequency: eps(w) = eps_inf + delta_eps/(1 + jw*tau). "
            "Used for biological tissues, water, polymers, and soil."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Wet Soil')",
                },
                "epsilon_inf": {
                    "type": "number",
                    "description": "High-frequency (optical) permittivity limit",
                },
                "delta_epsilon": {
                    "type": "number",
                    "description": "Static permittivity increment (eps_s - eps_inf)",
                },
                "relaxation_time_ps": {
                    "type": "number",
                    "description": "Relaxation time in picoseconds",
                },
                "order": {
                    "type": "integer",
                    "description": "Debye model order (1 or 2)",
                    "default": 1,
                    "enum": [1, 2],
                },
                "tan_d": {
                    "type": "number",
                    "description": "Optional static loss tangent",
                },
            },
            "required": ["name", "epsilon_inf", "delta_epsilon", "relaxation_time_ps"],
        },
    ),
    Tool(
        name="cst_create_lorentz_material",
        description=(
            "Create a Lorentz oscillator dispersive material. Models resonant "
            "dielectric behaviour near absorption bands: eps(w) = eps_inf + "
            "delta_eps * w0^2 / (w0^2 - w^2 + j*gamma*w). Used for glass, "
            "crystals, and optical materials."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Optical Glass')",
                },
                "epsilon_inf": {
                    "type": "number",
                    "description": "High-frequency permittivity limit",
                },
                "delta_epsilon": {
                    "type": "number",
                    "description": "Oscillator strength (permittivity increment)",
                },
                "resonant_freq_ghz": {
                    "type": "number",
                    "description": "Resonant frequency in GHz",
                },
                "damping_freq_ghz": {
                    "type": "number",
                    "description": "Damping (collision) frequency in GHz",
                },
            },
            "required": [
                "name",
                "epsilon_inf",
                "delta_epsilon",
                "resonant_freq_ghz",
                "damping_freq_ghz",
            ],
        },
    ),
    Tool(
        name="cst_create_drude_material",
        description=(
            "Create a Drude metal model material for plasmonic and metamaterial "
            "simulations. Models free-electron metals: eps(w) = 1 - wp^2 / "
            "(w^2 + j*gamma*w). Used for gold, silver, aluminium in optical/THz "
            "frequency ranges."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Drude Gold')",
                },
                "plasma_freq_ghz": {
                    "type": "number",
                    "description": "Plasma frequency in GHz",
                },
                "collision_freq_ghz": {
                    "type": "number",
                    "description": "Collision (damping) frequency in GHz",
                },
            },
            "required": ["name", "plasma_freq_ghz", "collision_freq_ghz"],
        },
    ),
    Tool(
        name="cst_create_ferrite_material",
        description=(
            "Create a ferrite material with gyrotropic permeability tensor "
            "(Polder model). Essential for circulators, isolators, and phase "
            "shifters. The Polder tensor describes the anisotropic magnetic "
            "response of a magnetised ferrite (Pozar Ch. 9.1)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'YIG Ferrite')",
                },
                "epsilon_r": {
                    "type": "number",
                    "description": "Relative permittivity of the ferrite",
                },
                "saturation_magnetization_ka_m": {
                    "type": "number",
                    "description": "Saturation magnetisation in kA/m (4piMs)",
                },
                "linewidth_oe": {
                    "type": "number",
                    "description": "Ferromagnetic resonance linewidth in Oersted",
                },
                "applied_field_ka_m": {
                    "type": "number",
                    "description": "Applied DC bias magnetic field in kA/m",
                    "default": 0,
                },
                "field_direction": {
                    "type": "string",
                    "description": "Direction of the applied bias field",
                    "enum": ["x", "y", "z"],
                    "default": "z",
                },
            },
            "required": [
                "name",
                "epsilon_r",
                "saturation_magnetization_ka_m",
                "linewidth_oe",
            ],
        },
    ),
    Tool(
        name="cst_create_temperature_dependent_material",
        description=(
            "Create a material with temperature-dependent electromagnetic "
            "properties. Specify base properties and temperature coefficients "
            "for thermal-electromagnetic co-simulation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Thermal FR-4')",
                },
                "epsilon_r": {
                    "type": "number",
                    "description": "Relative permittivity at reference temperature",
                },
                "conductivity": {
                    "type": "number",
                    "description": "Electric conductivity in S/m at reference temperature",
                },
                "temp_coeff_epsilon_ppm_k": {
                    "type": "number",
                    "description": "Temperature coefficient of permittivity in ppm/K",
                    "default": 0,
                },
                "temp_coeff_conductivity": {
                    "type": "number",
                    "description": "Temperature coefficient of conductivity (fractional per K)",
                    "default": 0,
                },
                "reference_temp_c": {
                    "type": "number",
                    "description": "Reference temperature in degrees Celsius",
                    "default": 25,
                },
            },
            "required": ["name", "epsilon_r", "conductivity"],
        },
    ),
    Tool(
        name="cst_create_cole_cole_material",
        description=(
            "Create a Cole-Cole dispersive material. Generalisation of the Debye "
            "model with a distribution parameter alpha (0-1) that broadens the "
            "relaxation spectrum. Used for biological tissues, soil, and "
            "broadband absorbers."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Material name (e.g. 'Muscle Tissue')",
                },
                "epsilon_inf": {
                    "type": "number",
                    "description": "High-frequency (optical) permittivity limit",
                },
                "delta_epsilon": {
                    "type": "number",
                    "description": "Static permittivity increment (eps_s - eps_inf)",
                },
                "relaxation_time_ps": {
                    "type": "number",
                    "description": "Relaxation time in picoseconds",
                },
                "alpha": {
                    "type": "number",
                    "description": "Distribution parameter (0 = Debye, 1 = max broadening)",
                },
            },
            "required": [
                "name",
                "epsilon_inf",
                "delta_epsilon",
                "relaxation_time_ps",
                "alpha",
            ],
        },
    ),
    Tool(
        name="cst_list_ferrite_materials",
        description=(
            "List available ferrite materials from the bundled database. "
            "Returns name, permittivity, saturation magnetisation, linewidth, "
            "loss tangent, maximum frequency, and usage notes for each ferrite."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]

# ---------------------------------------------------------------------------
# Bundled material database helpers
# ---------------------------------------------------------------------------

_material_db_cache: dict[str, list[dict]] | None = None


def _load_material_db() -> dict[str, list[dict]]:
    """Load and cache the bundled JSON material databases."""
    global _material_db_cache
    if _material_db_cache is not None:
        return _material_db_cache

    db: dict[str, list[dict]] = {"metals": [], "dielectrics": [], "substrates": []}

    metals_path = DATA_DIR / "common_metals.json"
    if metals_path.exists():
        with metals_path.open() as f:
            data = json.load(f)
        db["metals"] = data.get("metals", [])

    dielectrics_path = DATA_DIR / "common_dielectrics.json"
    if dielectrics_path.exists():
        with dielectrics_path.open() as f:
            data = json.load(f)
        db["dielectrics"] = data.get("dielectrics", [])

    substrates_path = DATA_DIR / "substrates.json"
    if substrates_path.exists():
        with substrates_path.open() as f:
            data = json.load(f)
        db["substrates"] = data.get("substrates", [])

    _material_db_cache = db
    return db


def _find_material(name: str) -> dict | None:
    """Find a material by name in the bundled database (case-insensitive)."""
    db = _load_material_db()
    name_lower = name.lower()
    for category in db.values():
        for mat in category:
            if mat["name"].lower() == name_lower:
                return mat
    return None


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Dispatch a material tool call and return results."""
    try:
        if name == "cst_create_material":
            return _handle_create_material(arguments, client)

        if name == "cst_create_lossy_metal":
            return _handle_create_lossy_metal(arguments, client)

        if name == "cst_create_anisotropic_material":
            return _handle_create_anisotropic_material(arguments, client)

        if name == "cst_load_material":
            return _handle_load_material(arguments, client)

        if name == "cst_list_library_materials":
            return _handle_list_library_materials(arguments, client)

        if name == "cst_list_materials":
            return _handle_list_materials(arguments)

        if name == "cst_assign_material":
            return _handle_assign_material(arguments, client)

        if name == "cst_get_material_info":
            return _handle_get_material_info(arguments)

        if name == "cst_get_project_materials":
            return _handle_get_project_materials(arguments, client)

        if name == "cst_delete_material":
            return _handle_delete_material(arguments, client)

        if name == "cst_create_debye_material":
            return _handle_create_debye_material(arguments, client)

        if name == "cst_create_lorentz_material":
            return _handle_create_lorentz_material(arguments, client)

        if name == "cst_create_drude_material":
            return _handle_create_drude_material(arguments, client)

        if name == "cst_create_ferrite_material":
            return _handle_create_ferrite_material(arguments, client)

        if name == "cst_create_temperature_dependent_material":
            return _handle_create_temperature_dependent_material(arguments, client)

        if name == "cst_create_cole_cole_material":
            return _handle_create_cole_cole_material(arguments, client)

        if name == "cst_list_ferrite_materials":
            return _handle_list_ferrite_materials(arguments)

        raise ValueError(f"Unknown material tool: {name}")
    except Exception as e:
        logging.getLogger(__name__).debug("Handled error in materials.handle", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps({"tool": name, "status": "error", "message": str(e)}, indent=2),
            )
        ]


# ---------------------------------------------------------------------------
# Individual handlers
# ---------------------------------------------------------------------------


def _handle_create_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    epsilon = float(args.get("epsilon", 1.0))
    mu = float(args.get("mu", 1.0))
    tan_d_e = float(args.get("tan_d_e", 0.0))
    tan_d_m = float(args.get("tan_d_m", 0.0))
    conductivity = float(args.get("conductivity", 0.0))
    color_r = validate_range(float(args.get("color_r", 0.6)), 0.0, 1.0, "color_r")
    color_g = validate_range(float(args.get("color_g", 0.6)), 0.0, 1.0, "color_g")
    color_b = validate_range(float(args.get("color_b", 0.6)), 0.0, 1.0, "color_b")
    transparency = validate_range(float(args.get("transparency", 0.0)), 0.0, 1.0, "transparency")
    validate_non_negative(conductivity, "conductivity")
    validate_non_negative(tan_d_e, "tan_d_e")
    validate_non_negative(tan_d_m, "tan_d_m")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Normal")
        .set_triple("Colour", color_r, color_g, color_b)
        .set_number("Transparency", transparency)
        .set_number("Epsilon", epsilon)
        .set_number("Mu", mu)
        .set_number("Sigma", conductivity)
        # TanD = numeric loss tangent (not a boolean); no TanDValue property
        .set_number("TanD", tan_d_e)
        .set_number("TanDFreq", 0.0)
        .set("TanDGiven", "True" if tan_d_e > 0 else "False")
        .set("TanDModel", "ConstTanD")
        .set_number("TanDM", tan_d_m)
        .set_number("TanDMFreq", 0.0)
        .set("TanDMGiven", "True" if tan_d_m > 0 else "False")
        .call("Create")
        .build()
    )

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_material",
                    "material": mat_name,
                    "properties": {
                        "epsilon_r": epsilon,
                        "mu_r": mu,
                        "tan_d_e": tan_d_e,
                        "tan_d_m": tan_d_m,
                        "conductivity_S_m": conductivity,
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_create_lossy_metal(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    conductivity = float(args["conductivity"])
    mu = float(args.get("mu", 1.0))
    validate_non_negative(conductivity, "conductivity")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Lossy metal")
        .set_number("Sigma", conductivity)
        .set_number("Mu", mu)
        .call("Create")
        .build()
    )

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_lossy_metal",
                    "material": mat_name,
                    "properties": {
                        "type": "Lossy metal",
                        "conductivity_S_m": conductivity,
                        "mu_r": mu,
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_create_anisotropic_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    eps_x = float(args["epsilon_x"])
    eps_y = float(args["epsilon_y"])
    eps_z = float(args["epsilon_z"])
    mu_x = float(args["mu_x"])
    mu_y = float(args["mu_y"])
    mu_z = float(args["mu_z"])
    td_x = float(args.get("tan_d_x", 0.0))
    td_y = float(args.get("tan_d_y", 0.0))
    td_z = float(args.get("tan_d_z", 0.0))
    validate_non_negative(td_x, "tan_d_x")
    validate_non_negative(td_y, "tan_d_y")
    validate_non_negative(td_z, "tan_d_z")

    script = VBAScript()
    script.add_comment(f"Create anisotropic material: {mat_name}")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Anisotropic")
        .set_number("EpsilonX", eps_x)
        .set_number("EpsilonY", eps_y)
        .set_number("EpsilonZ", eps_z)
        .set_number("MuX", mu_x)
        .set_number("MuY", mu_y)
        .set_number("MuZ", mu_z)
        .set_number("TanDX", td_x)
        .set_number("TanDY", td_y)
        .set_number("TanDZ", td_z)
        .call("Create")
    )
    script.add_block(vba)
    code = script.build()

    result = client.execute_vba(code)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_anisotropic_material",
                    "material": mat_name,
                    "properties": {
                        "type": "Anisotropic",
                        "epsilon": {"x": eps_x, "y": eps_y, "z": eps_z},
                        "mu": {"x": mu_x, "y": mu_y, "z": mu_z},
                        "tan_d": {"x": td_x, "y": td_y, "z": td_z},
                    },
                    "vba": code,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_load_material(args: dict, client: CSTClient) -> list[TextContent]:
    library_name = args["library_name"]
    # An explicit name is validated as given: renaming a caller's choice would hide a
    # mistake. Only an omitted name is derived from the library name.
    explicit = args.get("name") is not None
    if explicit:
        mat_name = validate_name(args["name"], "material name")
    else:
        mat_name = default_material_name(library_name)

    library_path = args.get("library_path")
    try:
        vba = load_material_vba(
            mat_name,
            library_name,
            cst_path=client.config.cst_path,
            library_path=library_path,
        )
    except LibraryEntryNotFound as exc:
        payload: dict = {
            "tool": "cst_load_material",
            "status": "error",
            "message": str(exc),
            "library_name": library_name,
            "similar": exc.similar,
        }
        if exc.directory is not None:
            payload["library_directory"] = str(exc.directory)
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]

    directory = library_directory(client.config.cst_path, library_path)
    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_load_material",
                    "material": mat_name,
                    "name": mat_name,
                    "name_source": "argument" if explicit else "derived from library_name",
                    "library_name": library_name,
                    "library_directory": str(directory),
                    "library_cst_version": library_cst_version(directory),
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Native library listing (cst_list_library_materials)
# ---------------------------------------------------------------------------

_DEFAULT_LIBRARY_RESULTS = 50
_MAX_LIBRARY_RESULTS = 1000
_MAX_LIBRARY_FILTER = 100
_LIBRARY_SOURCE = "Native .mtd files read from disk (no CST connection, no model history)"
_LIBRARY_NOTES = [
    "Values are the file's own [Definition] settings, never the bundled database.",
    (
        "Where a file has several .FrqType blocks, the hf block is reported, then all; "
        "frequency_blocks lists them all."
    ),
    (
        "conductivity is in S/m and tan_d_freq in Hz. With dispersion_model 'general' "
        "(fitted to tabulated data) or a named model, CST uses that model and epsilon_r "
        "is only the file's constant value."
    ),
    (
        "Every listed library_name passes the loader's name check; cst_load_material "
        "validates the full definition when it loads the entry."
    ),
]


def _library_listing_args(arguments: dict) -> tuple[str | None, int, bool]:
    query = arguments.get("filter")
    if query is not None:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("filter must be a non-empty string")
        if len(query) > _MAX_LIBRARY_FILTER:
            raise ValueError(f"filter is longer than {_MAX_LIBRARY_FILTER} characters")
        if not any(c.isalnum() for c in query):
            raise ValueError("filter must contain at least one letter or digit")
    limit = arguments.get("max_results", _DEFAULT_LIBRARY_RESULTS)
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("max_results must be an integer")
    if not 1 <= limit <= _MAX_LIBRARY_RESULTS:
        raise ValueError(f"max_results must be between 1 and {_MAX_LIBRARY_RESULTS}")
    properties = arguments.get("include_properties", True)
    if not isinstance(properties, bool):
        raise TypeError("include_properties must be true or false")
    return query, limit, properties


def _list_library_materials(arguments: dict, client: CSTClient) -> dict:
    query, limit, include_properties = _library_listing_args(arguments)
    library_path = arguments.get("library_path")
    directory = library_directory(client.config.cst_path, library_path)
    version = library_cst_version(directory)

    names = library_entry_names(directory)
    if query is not None:
        names = [n for n in names if library_name_matches(query, n)]
    skipped = [
        {
            "file": f"{n}.mtd",
            "reason": "The file name is not a name cst_load_material accepts.",
        }
        for n in names
        if not is_loadable_library_name(n)
    ]
    matched = [n for n in names if is_loadable_library_name(n)]

    materials: list[dict] = []
    for library_name in matched[:limit]:
        if not include_properties:
            materials.append({"library_name": library_name})
            continue
        try:
            properties = read_library_material(directory / f"{library_name}.mtd")
        except ValidationError as exc:
            skipped.append({"file": f"{library_name}.mtd", "reason": str(exc)})
            continue
        materials.append({"library_name": library_name, **properties})

    result: dict = {
        "status": "ok",
        "library_directory": str(directory),
        "cst_version": version,
        "filter": query,
        "match_count": len(matched),
        "returned": len(materials),
        "truncated": len(matched) > limit,
        "materials": materials,
    }
    if version is None:
        result["unavailable"] = {
            "cst_version": "The directory path names no 'CST Studio Suite <year>' install."
        }
    elif (
        library_path is None
        and client.config.cst_path_detected
        and version != client.config.version
    ):
        result["version_warning"] = (
            f"CST_VERSION is {client.config.version} (default {DEFAULT_VERSION}), but no "
            f"install of that version was found, so the nearest install was used: CST "
            f"{version}. The CST Python API is loaded from that install too. Set CST_VERSION "
            "or CST_PATH "
            "to the CST you want."
        )
    if skipped:
        result["skipped"] = skipped
    if result["truncated"]:
        result["message"] = (
            f"Showing {len(materials)} of {len(matched)} entries; narrow the filter or "
            "raise max_results."
        )
    if query is not None and not matched:
        loadable = [n for n in library_entry_names(directory) if is_loadable_library_name(n)]
        result["similar"] = similar_library_names(query, loadable)
        result["message"] = f"No library entry matches {query!r}."
    result["notes"] = _LIBRARY_NOTES
    result["source"] = _LIBRARY_SOURCE
    return result


def _handle_list_library_materials(arguments: dict, client: CSTClient) -> list[TextContent]:
    payload = {"tool": "cst_list_library_materials", **_list_library_materials(arguments, client)}
    return [TextContent(type="text", text=json.dumps(payload, indent=2))]


def _handle_list_materials(args: dict) -> list[TextContent]:
    db = _load_material_db()
    category = args.get("category")

    if category in ("metals", "dielectrics", "substrates"):
        materials = db.get(category, [])
    else:
        # Return all categories
        materials = []
        for cat_name, cat_list in db.items():
            for mat in cat_list:
                materials.append({**mat, "category": cat_name})

    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_list_materials",
                    "category": category or "all",
                    "count": len(materials),
                    "materials": materials,
                },
                indent=2,
            ),
        )
    ]


def _handle_assign_material(args: dict, client: CSTClient) -> list[TextContent]:
    solid = args["solid"]
    material = validate_name(args["material"], "material name")

    # Validate the solid reference format (Component:SolidName)
    if ":" not in solid:
        raise ValidationError(
            f"Invalid solid reference '{solid}': expected 'Component:SolidName' format"
        )
    component, solid_name = solid.split(":", 1)
    validate_name(component, "component")
    validate_name(solid_name, "solid")

    vba = (
        VBABuilder("Solid")
        .call_with_args("ChangeMaterial", f"{component}:{solid_name}", material)
        .build()
    )

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_assign_material",
                    "solid": f"{component}:{solid_name}",
                    "material": material,
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_get_material_info(args: dict) -> list[TextContent]:
    name = args["name"]
    mat = _find_material(name)

    if mat is None:
        # List available names to help the user
        db = _load_material_db()
        available: list[str] = []
        for cat_list in db.values():
            available.extend(m["name"] for m in cat_list)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "tool": "cst_get_material_info",
                        "status": "error",
                        "message": f"Material '{name}' not found in bundled database",
                        "available_materials": sorted(available),
                    },
                    indent=2,
                ),
            )
        ]

    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_get_material_info",
                    "material": mat,
                },
                indent=2,
            ),
        )
    ]


def _handle_delete_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")

    vba = VBABuilder("Material").call_with_args("Delete", mat_name).build()

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_delete_material",
                    "material": mat_name,
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Advanced / dispersive material handlers
# ---------------------------------------------------------------------------


def _handle_create_debye_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    epsilon_inf = float(args["epsilon_inf"])
    delta_epsilon = float(args["delta_epsilon"])
    relaxation_time_ps = float(args["relaxation_time_ps"])
    order = int(args.get("order", 1))
    tan_d = float(args["tan_d"]) if "tan_d" in args else None

    validate_positive(epsilon_inf, "epsilon_inf")
    validate_positive(delta_epsilon, "delta_epsilon")
    validate_positive(relaxation_time_ps, "relaxation_time_ps")
    if order not in (1, 2):
        raise ValueError("order must be 1 or 2")
    if tan_d is not None:
        validate_non_negative(tan_d, "tan_d")

    model_name = f"Debye {order}st Order" if order == 1 else "Debye 2nd Order"

    builder = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Normal")
        .set("SetDispersionModelEps", model_name)
        .set_number("EpsilonInfinity", epsilon_inf)
        .set_number("DispEps", delta_epsilon)
        .set_number("DispCoeff0Eps", relaxation_time_ps)
    )
    if tan_d is not None:
        builder.set_number("TanDe", tan_d)
    builder.call("Create")
    vba = builder.build()

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_debye_material",
                    "material": mat_name,
                    "properties": {
                        "model": model_name,
                        "epsilon_inf": epsilon_inf,
                        "delta_epsilon": delta_epsilon,
                        "relaxation_time_ps": relaxation_time_ps,
                        "order": order,
                        **({"tan_d": tan_d} if tan_d is not None else {}),
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_create_lorentz_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    epsilon_inf = float(args["epsilon_inf"])
    delta_epsilon = float(args["delta_epsilon"])
    resonant_freq_ghz = float(args["resonant_freq_ghz"])
    damping_freq_ghz = float(args["damping_freq_ghz"])

    validate_positive(epsilon_inf, "epsilon_inf")
    validate_positive(delta_epsilon, "delta_epsilon")
    validate_positive(resonant_freq_ghz, "resonant_freq_ghz")
    validate_positive(damping_freq_ghz, "damping_freq_ghz")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Normal")
        .set("SetDispersionModelEps", "Lorentz")
        .set_number("LorentzEpsInf", epsilon_inf)
        .set_number("LorentzDispEps", delta_epsilon)
        .set_number("LorentzFreqEps", resonant_freq_ghz)
        .set_number("LorentzGamma0Eps", damping_freq_ghz)
        .call("Create")
        .build()
    )

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_lorentz_material",
                    "material": mat_name,
                    "properties": {
                        "model": "Lorentz",
                        "epsilon_inf": epsilon_inf,
                        "delta_epsilon": delta_epsilon,
                        "resonant_freq_ghz": resonant_freq_ghz,
                        "damping_freq_ghz": damping_freq_ghz,
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_create_drude_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    plasma_freq_ghz = float(args["plasma_freq_ghz"])
    collision_freq_ghz = float(args["collision_freq_ghz"])

    validate_positive(plasma_freq_ghz, "plasma_freq_ghz")
    validate_positive(collision_freq_ghz, "collision_freq_ghz")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Normal")
        .set("SetDispersionModelEps", "Drude")
        .set_number("DrudeFreqEps", plasma_freq_ghz)
        .set_number("DrudeGammaEps", collision_freq_ghz)
        .call("Create")
        .build()
    )

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_drude_material",
                    "material": mat_name,
                    "properties": {
                        "model": "Drude",
                        "plasma_freq_ghz": plasma_freq_ghz,
                        "collision_freq_ghz": collision_freq_ghz,
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_create_ferrite_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    epsilon_r = float(args["epsilon_r"])
    sat_mag = float(args["saturation_magnetization_ka_m"])
    linewidth = float(args["linewidth_oe"])
    applied_field = float(args.get("applied_field_ka_m", 0))
    field_direction = args.get("field_direction", "z")

    validate_positive(epsilon_r, "epsilon_r")
    validate_positive(sat_mag, "saturation_magnetization_ka_m")
    validate_positive(linewidth, "linewidth_oe")
    validate_non_negative(applied_field, "applied_field_ka_m")
    if field_direction not in ("x", "y", "z"):
        raise ValueError("field_direction must be 'x', 'y', or 'z'")

    script = VBAScript()
    script.add_comment(f"Create ferrite material: {mat_name} (Polder tensor model)")

    builder = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Normal")
        .set_number("Epsilon", epsilon_r)
        .set("SetGyroMagneticModel", "Saturation Magnetization")
        .set_number("GyroMagneticSaturation", sat_mag)
        .set_number("GyroMagneticLineWidth", linewidth)
        .set_number("GyroMagneticAppliedField", applied_field)
        .set("GyroMagneticFieldDirection", field_direction)
        .call("Create")
    )
    script.add_block(builder)
    vba = script.build()

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_ferrite_material",
                    "material": mat_name,
                    "properties": {
                        "model": "Ferrite (Polder tensor)",
                        "epsilon_r": epsilon_r,
                        "saturation_magnetization_ka_m": sat_mag,
                        "linewidth_oe": linewidth,
                        "applied_field_ka_m": applied_field,
                        "field_direction": field_direction,
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_create_temperature_dependent_material(
    args: dict,
    client: CSTClient,
) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    epsilon_r = float(args["epsilon_r"])
    conductivity = float(args["conductivity"])
    tc_epsilon = float(args.get("temp_coeff_epsilon_ppm_k", 0))
    tc_cond = float(args.get("temp_coeff_conductivity", 0))
    ref_temp = float(args.get("reference_temp_c", 25))

    validate_positive(epsilon_r, "epsilon_r")
    validate_non_negative(conductivity, "conductivity")

    script = VBAScript()
    script.add_comment(f"Create temperature-dependent material: {mat_name}")
    script.add_comment(
        f"Temp coefficients: eps {tc_epsilon} ppm/K, sigma {tc_cond} /K, ref {ref_temp} C"
    )

    builder = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Normal")
        .set_number("Epsilon", epsilon_r)
        .set_number("Sigma", conductivity)
        .set_number("ReferenceTemperature", ref_temp)
        .set_number("TempCoeffEpsilon", tc_epsilon)
        .set_number("TempCoeffConductivity", tc_cond)
        .call("Create")
    )
    script.add_block(builder)
    vba = script.build()

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_temperature_dependent_material",
                    "material": mat_name,
                    "properties": {
                        "epsilon_r": epsilon_r,
                        "conductivity_S_m": conductivity,
                        "temp_coeff_epsilon_ppm_k": tc_epsilon,
                        "temp_coeff_conductivity": tc_cond,
                        "reference_temp_c": ref_temp,
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_create_cole_cole_material(args: dict, client: CSTClient) -> list[TextContent]:
    mat_name = validate_name(args["name"], "material name")
    epsilon_inf = float(args["epsilon_inf"])
    delta_epsilon = float(args["delta_epsilon"])
    relaxation_time_ps = float(args["relaxation_time_ps"])
    alpha = float(args["alpha"])

    validate_positive(epsilon_inf, "epsilon_inf")
    validate_positive(delta_epsilon, "delta_epsilon")
    validate_positive(relaxation_time_ps, "relaxation_time_ps")
    validate_range(alpha, 0.0, 1.0, "alpha")

    vba = (
        VBABuilder("Material")
        .call("Reset")
        .set("Name", mat_name)
        .set("Type", "Normal")
        .set("SetDispersionModelEps", "Cole Cole 1st Order")
        .set_number("EpsilonInfinity", epsilon_inf)
        .set_number("DispEps", delta_epsilon)
        .set_number("DispCoeff0Eps", relaxation_time_ps)
        .set_number("Alpha", alpha)
        .call("Create")
        .build()
    )

    result = client.execute_vba(vba)
    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_create_cole_cole_material",
                    "material": mat_name,
                    "properties": {
                        "model": "Cole-Cole 1st Order",
                        "epsilon_inf": epsilon_inf,
                        "delta_epsilon": delta_epsilon,
                        "relaxation_time_ps": relaxation_time_ps,
                        "alpha": alpha,
                    },
                    "vba": vba,
                    **result,
                },
                indent=2,
            ),
        )
    ]


def _handle_list_ferrite_materials(args: dict) -> list[TextContent]:
    ferrites_path = DATA_DIR / "ferrites.json"
    if not ferrites_path.exists():
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "tool": "cst_list_ferrite_materials",
                        "status": "error",
                        "message": "Ferrite database not found",
                    },
                    indent=2,
                ),
            )
        ]

    with ferrites_path.open() as f:
        data = json.load(f)
    ferrites = data.get("ferrites", [])

    return [
        TextContent(
            type="text",
            text=json.dumps(
                {
                    "tool": "cst_list_ferrite_materials",
                    "count": len(ferrites),
                    "ferrites": ferrites,
                },
                indent=2,
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Project material readback (cst_get_project_materials)
# ---------------------------------------------------------------------------

_MAX_READBACK_MATERIALS = 100
_MAX_MATERIAL_NAME = 200
# Material.GetTypeOfMaterial values (CST 2025 help, Material object) with a stable key.
_MATERIAL_TYPES = {
    "normal": "normal",
    "lossy metal": "lossy_metal",
    "pec": "pec",
    "anisotropic": "anisotropic",
}
_SOURCE = "VBA query via output capture (no model history)"
_MATERIAL_NOTES = [
    "Every value is read from CST; none is taken from the call that created the material.",
    (
        "conductivity is the electric conductivity in S/m (Material.GetSigma); "
        "magnetic_conductivity is CST's SigmaM in Ohm/m."
    ),
    (
        "CST's Material object has setters but no getters for TanD, TanDGiven, TanDFreq, "
        "TanDModel or the dispersion model, so tan_d, tan_d_freq and dispersion_model are "
        "always null. A dielectric whose losses are given as tan(delta) may report "
        "conductivity 0; that does not mean it is lossless."
    ),
]
# A Debug.Print-safe text: no line breaks or tabs to break the line protocol.
_ERR = 'Replace(Replace(Replace(Err.Description, vbCr, " "), vbLf, " "), vbTab, " ")'
_SAFE_NAME = 'Replace(Replace(Replace(mcpName, vbCr, " "), vbLf, " "), vbTab, " ")'
_AXES = ("x", "y", "z")


def _readback_material_names(arguments: dict) -> list[str] | None:
    raw = arguments.get("names")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError("names must be a non-empty list of material names")
    if len(raw) > _MAX_READBACK_MATERIALS:
        raise ValueError(f"names accepts at most {_MAX_READBACK_MATERIALS} material names")
    names: list[str] = []
    for i, value in enumerate(raw):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"names[{i}] must be a non-empty string")
        if len(value) > _MAX_MATERIAL_NAME:
            raise ValueError(f"names[{i}] is longer than {_MAX_MATERIAL_NAME} characters")
        if "\t" in value:
            raise ValueError(f"names[{i}] must not contain a tab character")
        vba_string_literal(value, f"names[{i}]")  # rejects line breaks before any VBA
        if value not in names:
            names.append(value)
    return names


def _material_print(key: str, value: str) -> str:
    """One Debug.Print at line start (output capture only redirects those)."""
    return f'Debug.Print mcpKey & "{key}" & vbTab & {value}'


def _material_report(call: str, key: str, value: str) -> list[str]:
    """Run ``call`` and print ``key`` with ``value``, or ``key_error`` with CST's message."""
    indent = "    "
    return [
        f"{indent}Err.Clear",
        f"{indent}{call}",
        f"{indent}If Err.Number = 0 Then",
        f"{indent}  {_material_print(key, value)}",
        f"{indent}Else",
        f"{indent}  {_material_print(key + '_error', _ERR)}",
        f"{indent}End If",
    ]


def _material_readback_vba(names: list[str] | None) -> str:
    """VBA printing tab-separated ``<index><TAB><key><TAB>...`` lines for each material.

    Materials are keyed by their zero-based CST index, so a name with unusual characters
    cannot break the protocol. ``On Error Resume Next`` keeps one failing getter from
    hiding the others; each failure is printed as ``<key>_error`` with CST's message.
    """
    if names is None:
        want = "True"
    else:
        want = "(" + " Or ".join(f"mcpName = {vba_string_literal(n)}" for n in names) + ")"
    xyz = "CStr(mcpA) & vbTab & CStr(mcpB) & vbTab & CStr(mcpC)"
    lines = [
        "Dim mcpN As Long, mcpI As Long",
        "Dim mcpA As Double, mcpB As Double, mcpC As Double",
        "Dim mcpName As String, mcpText As String, mcpKey As String",
        "Dim mcpBg As Boolean",
        "On Error Resume Next",
        "Err.Clear",
        "mcpN = Material.GetNumberOfMaterials()",
        "If Err.Number <> 0 Then",
        f'  Debug.Print "count_error" & vbTab & {_ERR}',
        "  mcpN = 0",
        "Else",
        '  Debug.Print "count" & vbTab & CStr(mcpN)',
        "End If",
        "For mcpI = 0 To mcpN - 1",
        "  Err.Clear",
        '  mcpName = ""',
        "  mcpName = Material.GetNameOfMaterialFromIndex(mcpI)",
        "  If Err.Number <> 0 Then",
        f'    Debug.Print "material_error" & vbTab & CStr(mcpI) & vbTab & {_ERR}',
        "  Else",
        f'    Debug.Print "material" & vbTab & CStr(mcpI) & vbTab & {_SAFE_NAME}',
        f"  If {want} Then",
        "    mcpKey = CStr(mcpI) & vbTab",
        *_material_report("mcpText = Material.GetTypeOfMaterial(mcpName)", "type", "mcpText"),
        *_material_report(
            "mcpBg = Material.IsBackgroundMaterial(mcpName)", "background", "CStr(mcpBg)"
        ),
        *_material_report("Material.GetEpsilon mcpName, mcpA, mcpB, mcpC", "epsilon", xyz),
        *_material_report("Material.GetMu mcpName, mcpA, mcpB, mcpC", "mu", xyz),
        *_material_report("Material.GetSigma mcpName, mcpA, mcpB, mcpC", "sigma", xyz),
        *_material_report("Material.GetSigmaM mcpName, mcpA, mcpB, mcpC", "sigma_m", xyz),
        "  End If",
        "  End If",
        "Next mcpI",
    ]
    return "\n".join(lines)


def _material_float(text: str) -> float:
    # CStr follows the Windows locale, which may use a decimal comma.
    value = float(text.strip().replace(",", "."))
    if not math.isfinite(value):
        raise ValueError(f"non-finite value {text!r}")
    return value


def _material_said(row: dict[str, list[str]], key: str, fallback: str) -> str:
    detail = "\t".join(row.get(f"{key}_error", [])).strip()
    return f"CST reported: {detail}" if detail else fallback


def _material_axes(
    entry: dict, row: dict, unavailable: dict, cst_key: str, field: str, getter: str
) -> None:
    """Set ``field`` (scalar when isotropic) and ``field_axes`` from a 3-value getter."""
    entry[field] = None
    entry[f"{field}_axes"] = None
    values = row.get(cst_key)
    try:
        parsed = [_material_float(v) for v in (values or [])[:3]]
    except ValueError:
        parsed = []
    if len(parsed) != 3:
        unavailable[field] = _material_said(
            row, cst_key, f"Unreadable {getter} result" if values else f"{getter} returned nothing"
        )
        return
    entry[f"{field}_axes"] = dict(zip(_AXES, parsed))
    if parsed[0] == parsed[1] == parsed[2]:
        entry[field] = parsed[0]
    else:
        unavailable[field] = f"Anisotropic: the axes differ; see {field}_axes."


def _parse_material(name: str, row: dict[str, list[str]]) -> dict:
    unavailable: dict[str, str] = {}
    raw_type = "\t".join(row.get("type", [])).strip() or None
    kind = _MATERIAL_TYPES.get((raw_type or "").lower(), "other" if raw_type else None)
    entry: dict = {"name": name, "found": True, "type": kind, "raw_type": raw_type}
    if raw_type is None:
        unavailable["type"] = _material_said(row, "type", "CST returned no material type")

    background = "\t".join(row.get("background", [])).strip().lower()
    entry["background"] = {"true": True, "false": False}.get(background)
    if entry["background"] is None:
        unavailable["background"] = _material_said(
            row, "background", "CST returned no background flag"
        )

    if kind == "pec":
        for field in ("epsilon_r", "mu_r", "conductivity", "magnetic_conductivity"):
            entry[field] = None
            entry[f"{field}_axes"] = None
            unavailable[field] = "Not defined for PEC (a perfect electric conductor)."
    else:
        _material_axes(entry, row, unavailable, "epsilon", "epsilon_r", "Material.GetEpsilon")
        _material_axes(entry, row, unavailable, "mu", "mu_r", "Material.GetMu")
        _material_axes(entry, row, unavailable, "sigma", "conductivity", "Material.GetSigma")
        _material_axes(
            entry, row, unavailable, "sigma_m", "magnetic_conductivity", "Material.GetSigmaM"
        )

    entry.update(tan_d=None, tan_d_freq=None, dispersion_model=None)
    if kind in {"pec", "lossy_metal"}:
        reason = f"Not applicable to {raw_type} materials."
        for field in ("tan_d", "tan_d_freq", "dispersion_model"):
            unavailable[field] = reason
    else:
        unavailable["tan_d"] = "CST has no getter for a material's loss tangent (TanD)."
        unavailable["tan_d_freq"] = (
            "CST has no getter for the frequency the loss tangent is given at (TanDFreq)."
        )
        unavailable["dispersion_model"] = (
            "CST has no getter for whether a material is dispersive or for its model."
        )
    entry["unavailable"] = unavailable
    return entry


def _parse_material_readback(output: str, requested: list[str] | None) -> dict:
    count: int | None = None
    count_error: str | None = None
    listed: dict[int, str] = {}
    index_errors: list[str] = []
    rows: dict[int, dict[str, list[str]]] = {}
    for line in output.splitlines():
        parts = line.rstrip("\r").split("\t")
        head, rest = parts[0].strip(), parts[1:]
        if head == "count" and rest:
            try:
                count = int(rest[0].strip())
            except ValueError:
                count_error = f"Unreadable material count from CST: {rest[0]!r}"
        elif head == "count_error":
            count_error = "\t".join(rest).strip() or "CST could not count the materials"
        elif head == "material" and len(rest) >= 2 and rest[0].strip().isdigit():
            listed[int(rest[0].strip())] = "\t".join(rest[1:])
        elif head == "material_error" and rest:
            index_errors.append("\t".join(rest[1:]).strip() or f"material index {rest[0]}")
        elif head.isdigit() and rest:
            rows.setdefault(int(head), {})[rest[0]] = rest[1:]

    if count_error is not None:
        return {
            "status": "error",
            "message": f"Could not list the project materials: {count_error}",
            "source": _SOURCE,
        }

    names = [listed[i] for i in sorted(listed)]
    index_of = {listed[i]: i for i in sorted(listed, reverse=True)}
    materials: list[dict] = []
    missing: list[str] = []
    for name in names if requested is None else requested:
        if name in index_of:
            materials.append(_parse_material(name, rows.get(index_of[name], {})))
            continue
        missing.append(name)
        similar = [n for n in names if n.lower() == name.lower() or name.lower() in n.lower()]
        entry: dict = {"name": name, "found": False}
        if similar:
            entry["similar"] = similar[:5]
        materials.append(entry)

    result: dict = {
        "status": "ok",
        "material_count": count if count is not None else len(names),
        "material_names": names,
        "materials": materials,
        "notes": _MATERIAL_NOTES,
        "source": _SOURCE,
    }
    if index_errors:
        result["listing_errors"] = index_errors
    if missing:
        result["missing"] = missing
        result["message"] = f"Not in the project: {', '.join(missing)}"
    elif not names:
        result["message"] = "The project has no materials."
    return result


def _get_project_materials(arguments: dict, client: CSTClient) -> dict:
    requested = _readback_material_names(arguments)
    script = _material_readback_vba(requested)
    if not client.connected:
        return {
            "status": "offline",
            "vba": script,
            "message": "Not connected; materials are only read back from a live CST project.",
        }
    if not client.has_project:
        return {"status": "error", "message": "No project open"}
    captured = client.capture_vba_output(script)
    if captured.get("status") != "ok":
        return captured
    return _parse_material_readback(str(captured.get("output", "")), requested)


def _handle_get_project_materials(args: dict, client: CSTClient) -> list[TextContent]:
    try:
        result = _get_project_materials(args, client)
    except ValueError as e:
        result = {"status": "error", "message": str(e)}
    return [
        TextContent(
            type="text",
            text=json.dumps({"tool": "cst_get_project_materials", **result}, indent=2),
        )
    ]


# ---------------------------------------------------------------------------
# Registration helper (used by tools/__init__.py)
# ---------------------------------------------------------------------------

# Reject line breaks and non-numeric values in numeric slots before any VBA
# is generated from the arguments (generated VBA bypasses CST_ALLOW_RAW_VBA).
from cst_mcp.vba_safety import guard_handler as _guard_handler

handle = _guard_handler(TOOLS, handle)
