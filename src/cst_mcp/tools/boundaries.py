"""Boundary condition and simulation domain tools for CST Studio Suite."""

from __future__ import annotations

import json
import logging

from mcp.types import TextContent, Tool

from cst_mcp.cst_client import CSTClient
from cst_mcp.validators import validate_frequency, validate_non_negative, validate_range
from cst_mcp.vba_builder import VBABuilder

_BOUNDARY_TYPES = [
    "open",
    "electric",
    "magnetic",
    "periodic",
    "conducting wall",
    "expanded open",
]

_BACKGROUND_MATERIALS = ["Normal", "PEC", "PMC"]

_SYMMETRY_OPTIONS = ["none", "electric", "magnetic"]

_BOUNDARY_PROPERTY = {
    "type": "string",
    "enum": _BOUNDARY_TYPES,
}

_SYMMETRY_PROPERTY = {
    "type": "string",
    "enum": _SYMMETRY_OPTIONS,
}

TOOLS: list[Tool] = [
    Tool(
        name="cst_set_boundary",
        description=(
            "Set boundary conditions for the simulation domain. Each face of "
            "the bounding box can be assigned an independent boundary type "
            "(open, electric, magnetic, periodic, etc.). 'expanded open' is an open "
            "boundary with space added around the structure. Plain 'open' adds no "
            "space: the domain ends at the structure's bounding box, so CST extends "
            "the structure into the PML on that face (efficiency/gain unreliable); "
            "the response warns about such faces. For a feed at the board edge "
            "(coax/SMA), also use an internal waveguide port (PortOnBound False) on "
            "the closed feed instead of a port on the boundary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "x_min": {
                    **_BOUNDARY_PROPERTY,
                    "description": "Boundary condition on the -X face",
                },
                "x_max": {
                    **_BOUNDARY_PROPERTY,
                    "description": "Boundary condition on the +X face",
                },
                "y_min": {
                    **_BOUNDARY_PROPERTY,
                    "description": "Boundary condition on the -Y face",
                },
                "y_max": {
                    **_BOUNDARY_PROPERTY,
                    "description": "Boundary condition on the +Y face",
                },
                "z_min": {
                    **_BOUNDARY_PROPERTY,
                    "description": "Boundary condition on the -Z face",
                },
                "z_max": {
                    **_BOUNDARY_PROPERTY,
                    "description": "Boundary condition on the +Z face",
                },
                "waveguide_port_faces": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"],
                    },
                    "maxItems": 6,
                    "default": [],
                    "description": (
                        "Optional: faces that carry a waveguide port with structure touching them. "
                        "A port on a plain 'open' face is reported as an error-level warning."
                    ),
                },
            },
            "required": ["x_min", "x_max", "y_min", "y_max", "z_min", "z_max"],
        },
    ),
    Tool(
        name="cst_get_boundaries",
        description=(
            "Read back the simulation domain as CST applied it: the boundary type on each "
            "face, the symmetry on each plane, the calculation box and structure box, and "
            "the space between them on each face (what 'expanded open' added). Values CST "
            "does not expose (background material, open-boundary distance settings) are "
            "listed in 'unavailable' with the reason. Read-only query through output "
            "capture (no history)."
        ),
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="cst_set_background",
        description=(
            "Set the background material properties of the simulation domain. "
            "The background fills all space not occupied by defined solids."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "material": {
                    "type": "string",
                    "enum": _BACKGROUND_MATERIALS,
                    "description": "Background material type (default Normal)",
                    "default": "Normal",
                },
                "epsilon": {
                    "type": "number",
                    "description": "Relative permittivity (default 1.0, used when material is Normal)",
                    "default": 1.0,
                },
                "mu": {
                    "type": "number",
                    "description": "Relative permeability (default 1.0, used when material is Normal)",
                    "default": 1.0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="cst_set_symmetry",
        description=(
            "Set symmetry planes to reduce computation time. Each axis can be "
            "assigned electric or magnetic symmetry, or none. Requires the "
            "model geometry and excitation to be compatible with the chosen symmetry."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "x_plane": {
                    **_SYMMETRY_PROPERTY,
                    "description": "Symmetry condition on the YZ plane (default none)",
                    "default": "none",
                },
                "y_plane": {
                    **_SYMMETRY_PROPERTY,
                    "description": "Symmetry condition on the XZ plane (default none)",
                    "default": "none",
                },
                "z_plane": {
                    **_SYMMETRY_PROPERTY,
                    "description": "Symmetry condition on the XY plane (default none)",
                    "default": "none",
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="cst_set_frequency_range",
        description=(
            "Set the simulation frequency range in GHz. This determines the "
            "bandwidth over which the solver computes results."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "f_min": {
                    "type": "number",
                    "description": "Minimum frequency in GHz",
                },
                "f_max": {
                    "type": "number",
                    "description": "Maximum frequency in GHz",
                },
            },
            "required": ["f_min", "f_max"],
        },
    ),
    Tool(
        name="cst_set_periodic_boundary",
        description=(
            "Configure periodic boundary conditions with optional phase shift for "
            "unit cell simulation. Sets X and Y boundaries to periodic and configures "
            "the phase shift for infinite array, FSS, and metasurface analysis."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "phase_x_deg": {
                    "type": "number",
                    "description": "Phase shift in X direction in degrees (default 0)",
                    "default": 0,
                },
                "phase_y_deg": {
                    "type": "number",
                    "description": "Phase shift in Y direction in degrees (default 0)",
                    "default": 0,
                },
            },
            "required": [],
        },
    ),
    Tool(
        name="cst_set_floquet_port_advanced",
        description=(
            "Configure advanced Floquet port settings for periodic structures. "
            "Controls the number of Floquet modes and scan angle for phased array "
            "element simulation and oblique incidence analysis."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "num_modes": {
                    "type": "integer",
                    "description": "Number of Floquet modes (2-20, default 2)",
                    "default": 2,
                },
                "scan_theta_deg": {
                    "type": "number",
                    "description": "Scan elevation angle in degrees (default 0)",
                    "default": 0,
                },
                "scan_phi_deg": {
                    "type": "number",
                    "description": "Scan azimuth angle in degrees (default 0)",
                    "default": 0,
                },
            },
            "required": [],
        },
    ),
]


async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Handle a boundary/domain tool call."""
    try:
        if name == "cst_set_boundary":
            return await _handle_set_boundary(arguments, client)
        if name == "cst_get_boundaries":
            result = _get_boundaries(client)
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        if name == "cst_set_background":
            return await _handle_set_background(arguments, client)
        if name == "cst_set_symmetry":
            return await _handle_set_symmetry(arguments, client)
        if name == "cst_set_frequency_range":
            return await _handle_set_frequency_range(arguments, client)
        if name == "cst_set_periodic_boundary":
            return await _handle_set_periodic_boundary(arguments, client)
        if name == "cst_set_floquet_port_advanced":
            return await _handle_set_floquet_port_advanced(arguments, client)

        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"tool": name, "status": "error", "message": f"Unknown boundary tool: {name}"},
                    indent=2,
                ),
            )
        ]
    except Exception as e:
        logging.getLogger(__name__).debug("Handled error in boundaries.handle", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps({"tool": name, "status": "error", "message": str(e)}, indent=2),
            )
        ]


async def _handle_set_boundary(arguments: dict, client: CSTClient) -> list[TextContent]:
    faces = {}
    for face_key in ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max"):
        value = arguments[face_key]
        if value not in _BOUNDARY_TYPES:
            raise ValueError(
                f"Invalid boundary type '{value}' for {face_key}. Must be one of: {_BOUNDARY_TYPES}"
            )
        faces[face_key] = value

    vba = (
        VBABuilder("Boundary")
        .set("Xmin", faces["x_min"])
        .set("Xmax", faces["x_max"])
        .set("Ymin", faces["y_min"])
        .set("Ymax", faces["y_max"])
        .set("Zmin", faces["z_min"])
        .set("Zmax", faces["z_max"])
    )
    from cst_mcp.execution.model_checks import check_setup

    port_faces = arguments.get("waveguide_port_faces") or []
    check = check_setup(
        faces,
        [
            {"port_number": i + 1, "orientation": f.replace("_", ""), "type": "waveguide"}
            for i, f in enumerate(port_faces)
        ],
    )

    script = vba.build()
    result = client.execute_vba(script)
    result["boundaries"] = faces
    if check["issues"]:
        result["warnings"] = check["issues"]

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Boundary readback (cst_get_boundaries)
# ---------------------------------------------------------------------------

_FACE_GETTERS = {
    "x_min": "GetXmin",
    "x_max": "GetXmax",
    "y_min": "GetYmin",
    "y_max": "GetYmax",
    "z_min": "GetZmin",
    "z_max": "GetZmax",
}
# cst_set_symmetry writes x_plane/y_plane/z_plane to Xsymmetry/Ysymmetry/Zsymmetry.
_SYMMETRY_GETTERS = {
    "x_plane": "GetXSymmetry",
    "y_plane": "GetYSymmetry",
    "z_plane": "GetZSymmetry",
}
_BOX_GETTERS = {
    "calculation_box": "GetCalculationBox",
    "structure_box": "GetStructureBox",
}
_BOX_VARS = ("mcpX1", "mcpX2", "mcpY1", "mcpY2", "mcpZ1", "mcpZ2")
# Checked in the CST 2025 help (special_vbasolver_background_object.htm and
# special_vbasolver_boundary_object.htm): these objects document setters only.
_NOT_EXPOSED = {
    "background": "CST documents no getter for the Background object (type, epsilon, mu).",
    "open_boundary_distance": (
        "CST documents no getter for MinimumDistanceType, MinimumDistancePerWavelength, "
        "SetAbsoluteDistance or the reference frequency; see added_space for the space "
        "CST actually added."
    ),
}


def _boundaries_vba() -> str:
    """VBA printing ``<kind><TAB><key><TAB>...`` lines; one failed getter never aborts the rest.

    Every ``Debug.Print`` starts its own line so output capture redirects it to the file.
    """
    lines = [
        "Dim mcpX1 As Double, mcpX2 As Double, mcpY1 As Double, mcpY2 As Double",
        "Dim mcpZ1 As Double, mcpZ2 As Double",
        "Dim mcpText As String",
        "On Error Resume Next",
        "Err.Clear",
        "mcpText = CStr(Units.GetGeometryUnitToSI())",
        "If Err.Number = 0 Then",
        '  Debug.Print "unit" & vbTab & mcpText',
        "End If",
    ]
    for kind, getters in (("face", _FACE_GETTERS), ("symmetry", _SYMMETRY_GETTERS)):
        for key, getter in getters.items():
            lines += [
                "Err.Clear",
                'mcpText = ""',
                f"mcpText = CStr(Boundary.{getter}())",
                "If Err.Number = 0 Then",
                f'  Debug.Print "{kind}" & vbTab & "{key}" & vbTab & mcpText',
                "Else",
                f'  Debug.Print "error" & vbTab & "{kind}.{key}" & vbTab & Err.Description',
                "End If",
            ]
    box = " & vbTab & ".join(f"CStr({v})" for v in _BOX_VARS)
    for key, getter in _BOX_GETTERS.items():
        lines += [
            "Err.Clear",
            *(f"{v} = 0" for v in _BOX_VARS),
            f"Boundary.{getter} {', '.join(_BOX_VARS)}",
            "If Err.Number = 0 Then",
            f'  Debug.Print "box" & vbTab & "{key}" & vbTab & {box}',
            "Else",
            f'  Debug.Print "error" & vbTab & "{key}" & vbTab & Err.Description',
            "End If",
        ]
    return "\n".join(lines)


def _parse_boundaries(output: str) -> dict:
    from cst_mcp.tools.drawing import _unit_from_factor
    from cst_mcp.tools.geometry import _vba_float

    unit: str | None = None
    unit_to_si: float | None = None
    values: dict[str, dict[str, str]] = {"face": {}, "symmetry": {}}
    boxes: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.rstrip("\r").split("\t")
        if parts[0] == "unit" and len(parts) > 1:
            try:
                unit_to_si = _vba_float(parts[1])
                unit = _unit_from_factor(unit_to_si)
            except ValueError:
                pass
        elif parts[0] in values and len(parts) >= 3:
            values[parts[0]][parts[1]] = "\t".join(parts[2:]).strip()
        elif parts[0] == "box" and len(parts) >= 8:
            boxes[parts[1]] = parts[2:8]
        elif parts[0] == "error" and len(parts) >= 2:
            errors[parts[1]] = "\t".join(parts[2:]).strip()

    unavailable: dict[str, str] = {}

    def _read(kind: str, keys) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for key in keys:
            text = values[kind].get(key)
            if text:
                out[key] = text
            else:
                out[key] = None
                detail = errors.get(f"{kind}.{key}")
                unavailable[f"{'boundaries' if kind == 'face' else 'symmetry'}.{key}"] = (
                    f"CST error: {detail}" if detail else "CST returned no value"
                )
        return out

    boundaries = _read("face", _FACE_GETTERS)
    symmetry = _read("symmetry", _SYMMETRY_GETTERS)

    parsed_boxes: dict[str, dict[str, list[float]] | None] = {}
    for key in _BOX_GETTERS:
        raw = boxes.get(key)
        if raw is None:
            detail = errors.get(key)
            unavailable[key] = f"CST error: {detail}" if detail else "CST returned no value"
            parsed_boxes[key] = None
            continue
        try:
            x1, x2, y1, y2, z1, z2 = (_vba_float(v) for v in raw)
        except ValueError as exc:
            unavailable[key] = f"Unreadable box from CST: {exc}"
            parsed_boxes[key] = None
            continue
        if x1 == x2 and y1 == y2 and z1 == z2:
            # A point box: nothing to enclose, or CST has not computed the box yet.
            unavailable[key] = (
                "CST returned a zero-size box (empty model, or the box is not computed yet)"
            )
            parsed_boxes[key] = None
            continue
        parsed_boxes[key] = {"x": [x1, x2], "y": [y1, y2], "z": [z1, z2]}

    calc, struct = parsed_boxes["calculation_box"], parsed_boxes["structure_box"]
    added_space: dict[str, float] | None = None
    if calc and struct:
        added_space = {}
        for axis in ("x", "y", "z"):
            added_space[f"{axis}_min"] = struct[axis][0] - calc[axis][0]
            added_space[f"{axis}_max"] = calc[axis][1] - struct[axis][1]
    else:
        unavailable["added_space"] = "Needs both calculation_box and structure_box."

    unavailable.update(_NOT_EXPOSED)
    read_any = (
        any(v is not None for v in boundaries.values())
        or any(v is not None for v in symmetry.values())
        or any(parsed_boxes.values())
    )
    result: dict = {
        "status": "ok" if read_any else "error",
        "unit": unit,
        "unit_to_si": unit_to_si,
        "boundaries": boundaries,
        "symmetry": symmetry,
        "calculation_box": calc,
        "structure_box": struct,
        "added_space": added_space,
        "background": None,
        "open_boundary_distance": None,
        "unavailable": unavailable,
        "source": "VBA query via output capture (no model history)",
    }
    if not read_any:
        result["message"] = "CST returned no boundary values."
    return result


def _get_boundaries(client: CSTClient) -> dict:
    script = _boundaries_vba()
    if not client.connected:
        return {
            "status": "offline",
            "vba": script,
            "message": "Not connected; boundaries are only read from a live CST project.",
        }
    if not client.has_project:
        return {"status": "error", "message": "No project open"}
    captured = client.capture_vba_output(script)
    if captured.get("status") != "ok":
        return captured
    return _parse_boundaries(str(captured.get("output", "")))


async def _handle_set_background(arguments: dict, client: CSTClient) -> list[TextContent]:
    material = arguments.get("material", "Normal")
    if material not in _BACKGROUND_MATERIALS:
        raise ValueError(
            f"Invalid background material '{material}'. Must be one of: {_BACKGROUND_MATERIALS}"
        )
    epsilon = float(arguments.get("epsilon", 1.0))
    mu = float(arguments.get("mu", 1.0))

    validate_non_negative(epsilon, "epsilon")
    validate_non_negative(mu, "mu")

    vba = VBABuilder("Background").call("Reset").set("Type", material)

    if material == "Normal":
        vba.set_number("Epsilon", epsilon)
        vba.set_number("Mu", mu)

    script = vba.build()
    result = client.execute_vba(script)
    result["background"] = {
        "material": material,
        "epsilon": epsilon,
        "mu": mu,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_set_symmetry(arguments: dict, client: CSTClient) -> list[TextContent]:
    x_plane = arguments.get("x_plane", "none")
    y_plane = arguments.get("y_plane", "none")
    z_plane = arguments.get("z_plane", "none")

    for label, value in [("x_plane", x_plane), ("y_plane", y_plane), ("z_plane", z_plane)]:
        if value not in _SYMMETRY_OPTIONS:
            raise ValueError(
                f"Invalid symmetry '{value}' for {label}. Must be one of: {_SYMMETRY_OPTIONS}"
            )

    vba = (
        VBABuilder("Boundary")
        .set("Xsymmetry", x_plane)
        .set("Ysymmetry", y_plane)
        .set("Zsymmetry", z_plane)
    )
    script = vba.build()
    result = client.execute_vba(script)
    result["symmetry"] = {
        "x_plane": x_plane,
        "y_plane": y_plane,
        "z_plane": z_plane,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_set_frequency_range(arguments: dict, client: CSTClient) -> list[TextContent]:
    f_min = float(arguments["f_min"])
    f_max = float(arguments["f_max"])

    validate_frequency(f_min)
    validate_frequency(f_max)

    if f_min >= f_max:
        raise ValueError(f"f_min ({f_min} GHz) must be less than f_max ({f_max} GHz)")

    vba = VBABuilder("Solver").set_double("FrequencyRange", f_min, f_max)
    script = vba.build()
    result = client.execute_vba(script)
    result["frequency_range"] = {
        "f_min_ghz": f_min,
        "f_max_ghz": f_max,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_set_periodic_boundary(arguments: dict, client: CSTClient) -> list[TextContent]:
    phase_x = float(arguments.get("phase_x_deg", 0))
    phase_y = float(arguments.get("phase_y_deg", 0))

    vba = (
        VBABuilder("Boundary")
        .set("Xmin", "periodic")
        .set("Xmax", "periodic")
        .set("Ymin", "periodic")
        .set("Ymax", "periodic")
        .set_number("XPeriodicShift", phase_x)
        .set_number("YPeriodicShift", phase_y)
    )
    script = vba.build()
    result = client.execute_vba(script)
    result["boundary_type"] = "periodic"
    result["phase_x_deg"] = phase_x
    result["phase_y_deg"] = phase_y

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_set_floquet_port_advanced(
    arguments: dict, client: CSTClient
) -> list[TextContent]:
    num_modes = int(arguments.get("num_modes", 2))
    scan_theta = float(arguments.get("scan_theta_deg", 0))
    scan_phi = float(arguments.get("scan_phi_deg", 0))

    validate_range(num_modes, 2, 20, "num_modes")
    validate_range(scan_theta, -90, 90, "scan_theta_deg")
    validate_range(scan_phi, -360, 360, "scan_phi_deg")

    vba = (
        VBABuilder("FloquetPort")
        .call("Reset")
        .set_number("SetDialogTheta", scan_theta)
        .set_number("SetDialogPhi", scan_phi)
        .set("Port", "zmin")
        .set_number("SetNumberOfModesConsidered", num_modes)
        .set("Port", "zmax")
        .set_number("SetNumberOfModesConsidered", num_modes)
    )
    script = vba.build()
    result = client.execute_vba(script)
    result["floquet_port"] = "advanced"
    result["num_modes"] = num_modes
    result["scan_theta_deg"] = scan_theta
    result["scan_phi_deg"] = scan_phi

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# Reject line breaks and non-numeric values in numeric slots before any VBA
# is generated from the arguments (generated VBA bypasses CST_ALLOW_RAW_VBA).
from cst_mcp.vba_safety import guard_handler as _guard_handler

handle = _guard_handler(TOOLS, handle)
