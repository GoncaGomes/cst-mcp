"""Port and excitation tools for CST Studio Suite."""

from __future__ import annotations

import json
import logging
import math

from mcp.types import TextContent, Tool

from cst_mcp.cst_client import CSTClient
from cst_mcp.validators import (
    validate_name,
    validate_port_number,
    validate_positive,
    validate_range,
)
from cst_mcp.vba_builder import VBABuilder
from cst_mcp.vba_safety import vba_int as _vba_int

_ORIENTATION_ENUM = ["xmin", "xmax", "ymin", "ymax", "zmin", "zmax"]
_FLOQUET_ORIENTATION_ENUM = ["zmin", "zmax"]
_DISCRETE_PORT_TYPE_ENUM = ["SParameter", "Voltage", "Current"]
_POLARIZATION_ENUM = ["linear", "circular"]
_ELEMENT_TYPE_ENUM = ["R", "L", "C", "RLC_serial", "RLC_parallel"]
_MAX_READBACK_PORTS = 100
_MAX_READBACK_MODES = 20

TOOLS: list[Tool] = [
    Tool(
        name="cst_add_waveguide_port",
        description=(
            "Add a waveguide port for S-parameter excitation. Defines a port face on "
            "the boundary of the simulation domain by default. Set port_on_bound=False "
            "to preserve the supplied normal coordinate for an internal port. "
            "IMPORTANT: The port plane should be at or near the edge of the model "
            "geometry. Ground planes and substrates must NOT extend past the port "
            "plane in the port's orientation direction, or VBA execution may hang. "
            "For microstrip feeds: place the port at the end of the feed line where "
            "the ground/substrate terminates. Use Coordinates='Free' for ports not "
            "aligned to the bounding box. Valid orientations: xmin/xmax/ymin/ymax/zmin/zmax."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port_number": {
                    "type": "integer",
                    "description": "Port number (1-999)",
                },
                "orientation": {
                    "type": "string",
                    "enum": _ORIENTATION_ENUM,
                    "description": "Port orientation and excitation direction, also valid for internal ports",
                },
                "x_min": {
                    "type": "number",
                    "description": "X-axis minimum coordinate of the port aperture (mm)",
                },
                "x_max": {
                    "type": "number",
                    "description": "X-axis maximum coordinate of the port aperture (mm)",
                },
                "y_min": {
                    "type": "number",
                    "description": "Y-axis minimum coordinate of the port aperture (mm)",
                },
                "y_max": {
                    "type": "number",
                    "description": "Y-axis maximum coordinate of the port aperture (mm)",
                },
                "z_min": {
                    "type": "number",
                    "description": "Z-axis minimum coordinate of the port aperture (mm)",
                },
                "z_max": {
                    "type": "number",
                    "description": "Z-axis maximum coordinate of the port aperture (mm)",
                },
                "mode_number": {
                    "type": "integer",
                    "description": "Number of calculated modes (default 1), not a mode-index selector",
                    "default": 1,
                },
                "coordinates": {
                    "type": "string",
                    "enum": ["Free", "Full", "Picks"],
                    "description": (
                        "Coordinate mode. 'Free' allows arbitrary placement using "
                        "the specified ranges (required for microstrip/coplanar ports). "
                        "'Full' maps the port to the full bounding-box face. "
                        "'Picks' uses previously picked geometry faces. Default: 'Free'."
                    ),
                    "default": "Free",
                },
                "port_on_bound": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Place the port on the domain boundary by default. False preserves "
                        "the explicitly supplied normal coordinate inside the domain. "
                        "CST ignores this setting for picked ports."
                    ),
                },
            },
            "required": [
                "port_number",
                "orientation",
                "x_min",
                "x_max",
                "y_min",
                "y_max",
                "z_min",
                "z_max",
            ],
        },
    ),
    Tool(
        name="cst_add_discrete_port",
        description=(
            "Add a discrete (lumped) port between two points. Used for circuit-level "
            "excitation with a defined impedance."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port_number": {
                    "type": "integer",
                    "description": "Port number (1-999)",
                },
                "impedance": {
                    "type": "number",
                    "description": "Port impedance in ohms (default 50)",
                    "default": 50.0,
                },
                "x1": {
                    "type": "number",
                    "description": "X coordinate of the start point (mm)",
                },
                "y1": {
                    "type": "number",
                    "description": "Y coordinate of the start point (mm)",
                },
                "z1": {
                    "type": "number",
                    "description": "Z coordinate of the start point (mm)",
                },
                "x2": {
                    "type": "number",
                    "description": "X coordinate of the end point (mm)",
                },
                "y2": {
                    "type": "number",
                    "description": "Y coordinate of the end point (mm)",
                },
                "z2": {
                    "type": "number",
                    "description": "Z coordinate of the end point (mm)",
                },
                "port_type": {
                    "type": "string",
                    "enum": _DISCRETE_PORT_TYPE_ENUM,
                    "description": "Excitation type (default SParameter)",
                    "default": "SParameter",
                },
            },
            "required": ["port_number", "x1", "y1", "z1", "x2", "y2", "z2"],
        },
    ),
    Tool(
        name="cst_add_lumped_element",
        description=(
            "Add a lumped R, L, C, or RLC element between two points. "
            "Value is in ohms for R, henries for L, farads for C."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Unique name for the lumped element",
                },
                "element_type": {
                    "type": "string",
                    "enum": _ELEMENT_TYPE_ENUM,
                    "description": "Type of lumped element",
                },
                "value": {
                    "type": "number",
                    "description": "Element value (ohms for R, henries for L, farads for C)",
                },
                "x1": {
                    "type": "number",
                    "description": "X coordinate of the first terminal (mm)",
                },
                "y1": {
                    "type": "number",
                    "description": "Y coordinate of the first terminal (mm)",
                },
                "z1": {
                    "type": "number",
                    "description": "Z coordinate of the first terminal (mm)",
                },
                "x2": {
                    "type": "number",
                    "description": "X coordinate of the second terminal (mm)",
                },
                "y2": {
                    "type": "number",
                    "description": "Y coordinate of the second terminal (mm)",
                },
                "z2": {
                    "type": "number",
                    "description": "Z coordinate of the second terminal (mm)",
                },
            },
            "required": [
                "name",
                "element_type",
                "value",
                "x1",
                "y1",
                "z1",
                "x2",
                "y2",
                "z2",
            ],
        },
    ),
    Tool(
        name="cst_add_plane_wave",
        description=(
            "Add a plane wave excitation source. Defines an incident plane wave "
            "with given direction and polarization for scattering / RCS analysis."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "polarization": {
                    "type": "string",
                    "enum": _POLARIZATION_ENUM,
                    "description": "Polarization type",
                },
                "theta": {
                    "type": "number",
                    "description": "Elevation angle of incidence in degrees (0-180)",
                },
                "phi": {
                    "type": "number",
                    "description": "Azimuth angle of incidence in degrees (0-360)",
                },
                "e_theta": {
                    "type": "number",
                    "description": "Theta component of the polarization vector (default 1)",
                    "default": 1.0,
                },
                "e_phi": {
                    "type": "number",
                    "description": "Phi component of the polarization vector (default 0)",
                    "default": 0.0,
                },
            },
            "required": ["polarization", "theta", "phi"],
        },
    ),
    Tool(
        name="cst_add_floquet_port",
        description=(
            "Add a Floquet port for periodic structures such as frequency selective "
            "surfaces, metamaterials, and phased arrays."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port_number": {
                    "type": "integer",
                    "description": "Port number (1-999)",
                },
                "orientation": {
                    "type": "string",
                    "enum": _FLOQUET_ORIENTATION_ENUM,
                    "description": "Orientation of the Floquet port (zmin or zmax)",
                },
                "modes": {
                    "type": "integer",
                    "description": "Number of Floquet modes to include (default 2)",
                    "default": 2,
                },
            },
            "required": ["port_number", "orientation"],
        },
    ),
    Tool(
        name="cst_list_ports",
        description=(
            "List the ports in the current CST project and read back how each one is "
            "defined, as CST reports it (not from the creation arguments): number, type "
            "(waveguide/discrete/other), label and centre; for waveguide ports the "
            "orientation, the port window snapped to mesh lines, the number of modes and, "
            "once port modes are computed, each mode's type and line impedance; for "
            "discrete ports the edge end points or face shape, excitation type and "
            "impedance. Values CST does not expose are null and listed in the port's "
            "'unavailable' map with the reason; whether a waveguide port lies on the "
            "domain boundary is never exposed by CST. Read-only: a VBA query through output "
            "capture that adds no model-history entry. Refused while a solver is running "
            "or its state is unknown. Offline mode returns the query VBA only."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port_numbers": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 999},
                    "minItems": 1,
                    "maxItems": _MAX_READBACK_PORTS,
                    "description": (
                        "Only read these ports back (default: all). Every port number "
                        "in the project is still listed in port_numbers."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="cst_delete_port",
        description=(
            "Delete a port of any type (waveguide, discrete, discrete face) by its port "
            "number. A missing port is rejected before CST is changed; the response lists "
            "the ports that remain."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port_number": {
                    "type": "integer",
                    "description": "Port number to delete (1-999)",
                },
            },
            "required": ["port_number"],
        },
    ),
    Tool(
        name="cst_add_multipin_port",
        description=(
            "Add a waveguide port with multiple mode monitoring for higher-order mode "
            "analysis. Used for multimode waveguides, mode converters, and structures "
            "where higher-order propagating modes need to be captured."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "port_number": {
                    "type": "integer",
                    "description": "Port number (1-999)",
                },
                "orientation": {
                    "type": "string",
                    "enum": _ORIENTATION_ENUM,
                    "description": "Face of the bounding box where the port is placed",
                },
                "x_min": {
                    "type": "number",
                    "description": "X-axis minimum coordinate of the port aperture (mm)",
                },
                "x_max": {
                    "type": "number",
                    "description": "X-axis maximum coordinate of the port aperture (mm)",
                },
                "y_min": {
                    "type": "number",
                    "description": "Y-axis minimum coordinate of the port aperture (mm)",
                },
                "y_max": {
                    "type": "number",
                    "description": "Y-axis maximum coordinate of the port aperture (mm)",
                },
                "z_min": {
                    "type": "number",
                    "description": "Z-axis minimum coordinate of the port aperture (mm)",
                },
                "z_max": {
                    "type": "number",
                    "description": "Z-axis maximum coordinate of the port aperture (mm)",
                },
                "num_modes": {
                    "type": "integer",
                    "description": "Number of modes to monitor (1-10, default 1)",
                    "default": 1,
                },
            },
            "required": [
                "port_number",
                "orientation",
                "x_min",
                "x_max",
                "y_min",
                "y_max",
                "z_min",
                "z_max",
            ],
        },
    ),
]


async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Handle a port/excitation tool call."""
    try:
        if name == "cst_add_waveguide_port":
            return await _handle_waveguide_port(arguments, client)
        if name == "cst_add_discrete_port":
            return await _handle_discrete_port(arguments, client)
        if name == "cst_add_lumped_element":
            return await _handle_lumped_element(arguments, client)
        if name == "cst_add_plane_wave":
            return await _handle_plane_wave(arguments, client)
        if name == "cst_add_floquet_port":
            return await _handle_floquet_port(arguments, client)
        if name == "cst_list_ports":
            return await _handle_list_ports(arguments, client)
        if name == "cst_delete_port":
            return await _handle_delete_port(arguments, client)
        if name == "cst_add_multipin_port":
            return await _handle_multipin_port(arguments, client)

        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {"tool": name, "status": "error", "message": f"Unknown port tool: {name}"},
                    indent=2,
                ),
            )
        ]
    except Exception as e:
        logging.getLogger(__name__).debug("Handled error in ports.handle", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps({"tool": name, "status": "error", "message": str(e)}, indent=2),
            )
        ]


async def _handle_waveguide_port(arguments: dict, client: CSTClient) -> list[TextContent]:
    port_number = validate_port_number(arguments["port_number"])
    orientation = arguments["orientation"]
    if orientation not in _ORIENTATION_ENUM:
        raise ValueError(
            f"Invalid orientation '{orientation}'. Must be one of: {_ORIENTATION_ENUM}"
        )
    x_min = float(arguments["x_min"])
    x_max = float(arguments["x_max"])
    y_min = float(arguments["y_min"])
    y_max = float(arguments["y_max"])
    z_min = float(arguments["z_min"])
    z_max = float(arguments["z_max"])
    mode_number = int(arguments.get("mode_number", 1))
    if mode_number < 1:
        raise ValueError(f"mode_number must be >= 1, got {mode_number}")

    coordinates = arguments.get("coordinates", "Free")
    if coordinates not in ("Free", "Full", "Picks"):
        raise ValueError(f"Invalid coordinates '{coordinates}'. Must be Free, Full, or Picks.")
    port_on_bound = arguments.get("port_on_bound", True)
    if not isinstance(port_on_bound, bool):
        raise TypeError("port_on_bound must be a boolean")

    vba = (
        VBABuilder("Port")
        .call("Reset")
        .set_number("PortNumber", port_number)
        .set("Label", "")
        .set("Coordinates", coordinates)
        .set("Orientation", orientation)
        .set_bool("PortOnBound", port_on_bound)
        .set_double("Xrange", x_min, x_max)
        .set_double("Yrange", y_min, y_max)
        .set_double("Zrange", z_min, z_max)
        .set_number("NumberOfModes", mode_number)
        .call("Create")
    )
    script = vba.build()
    result = client.execute_vba(script)
    result["port_type"] = "waveguide"
    result["port_number"] = port_number
    result["orientation"] = orientation
    result["coordinates"] = coordinates
    result["port_on_bound"] = port_on_bound

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_discrete_port(arguments: dict, client: CSTClient) -> list[TextContent]:
    port_number = validate_port_number(arguments["port_number"])
    impedance = float(arguments.get("impedance", 50.0))
    validate_positive(impedance, "impedance")
    x1 = float(arguments["x1"])
    y1 = float(arguments["y1"])
    z1 = float(arguments["z1"])
    x2 = float(arguments["x2"])
    y2 = float(arguments["y2"])
    z2 = float(arguments["z2"])
    port_type = arguments.get("port_type", "SParameter")
    if port_type not in _DISCRETE_PORT_TYPE_ENUM:
        raise ValueError(
            f"Invalid port_type '{port_type}'. Must be one of: {_DISCRETE_PORT_TYPE_ENUM}"
        )

    # Official DiscretePort: SetP1/SetP2 (picked, x, y, z); Type "Sparameter"
    type_map = {
        "SParameter": "Sparameter",
        "Sparameter": "Sparameter",
        "Voltage": "Voltage",
        "Current": "Current",
    }
    cst_type = type_map.get(port_type, port_type)
    from cst_mcp.vba_builder import _format_number as _fn

    vba = (
        VBABuilder("DiscretePort")
        .call("Reset")
        .set_number("PortNumber", port_number)
        .set("Type", cst_type)
        .set_number("Impedance", impedance)
        .set_number("Voltage", 1.0)
        .set_number("Current", 1.0)
        .set_raw(
            "SetP1",
            f'"False", "{_fn(x1)}", "{_fn(y1)}", "{_fn(z1)}"',
        )
        .set_raw(
            "SetP2",
            f'"False", "{_fn(x2)}", "{_fn(y2)}", "{_fn(z2)}"',
        )
        .set("InvertDirection", "False")
        .set("LocalCoordinates", "False")
        .set_bool("Monitor", True)
        .set_number("Radius", 0.01)
        .call("Create")
    )
    script = vba.build()
    result = client.execute_vba(script)
    result["port_type"] = "discrete"
    result["port_number"] = port_number
    result["impedance"] = impedance

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_lumped_element(arguments: dict, client: CSTClient) -> list[TextContent]:
    elem_name = validate_name(arguments["name"], "element name")
    element_type = arguments["element_type"]
    if element_type not in _ELEMENT_TYPE_ENUM:
        raise ValueError(
            f"Invalid element_type '{element_type}'. Must be one of: {_ELEMENT_TYPE_ENUM}"
        )
    value = float(arguments["value"])
    x1 = float(arguments["x1"])
    y1 = float(arguments["y1"])
    z1 = float(arguments["z1"])
    x2 = float(arguments["x2"])
    y2 = float(arguments["y2"])
    z2 = float(arguments["z2"])

    # Map element_type to CST VBA LumpedElement type string
    cst_type_map = {
        "R": "rlcserial",
        "L": "rlcserial",
        "C": "rlcserial",
        "RLC_serial": "rlcserial",
        "RLC_parallel": "rlcparallel",
    }
    cst_type = cst_type_map[element_type]

    vba = (
        VBABuilder("LumpedElement").call("Reset").set("SetName", elem_name).set("SetType", cst_type)
    )

    # Set R, L, C values depending on element_type
    if element_type == "R":
        vba.set_number("SetR", value)
        vba.set_number("SetL", 0)
        vba.set_number("SetC", 0)
    elif element_type == "L":
        vba.set_number("SetR", 0)
        vba.set_number("SetL", value)
        vba.set_number("SetC", 0)
    elif element_type == "C":
        vba.set_number("SetR", 0)
        vba.set_number("SetL", 0)
        vba.set_number("SetC", value)
    else:
        # For RLC_serial / RLC_parallel, value is used as R and user should
        # set L/C separately; here we set R=value with L=0 and C=0 as defaults
        vba.set_number("SetR", value)
        vba.set_number("SetL", 0)
        vba.set_number("SetC", 0)

    vba = vba.set_point("SetP1", x1, y1, z1).set_point("SetP2", x2, y2, z2).call("Create")
    script = vba.build()
    result = client.execute_vba(script)
    result["element_name"] = elem_name
    result["element_type"] = element_type
    result["value"] = value

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_plane_wave(arguments: dict, client: CSTClient) -> list[TextContent]:
    polarization = arguments["polarization"]
    if polarization not in _POLARIZATION_ENUM:
        raise ValueError(
            f"Invalid polarization '{polarization}'. Must be one of: {_POLARIZATION_ENUM}"
        )
    theta = float(arguments["theta"])
    phi = float(arguments["phi"])
    e_theta = float(arguments.get("e_theta", 1.0))
    e_phi = float(arguments.get("e_phi", 0.0))

    vba = VBABuilder("PlaneWave").call("Reset")

    # Propagation direction defaults to z
    vba.set("Normal", "z")

    # Set polarization type and polarization-specific parameters
    vba.set("Polarization", polarization)
    if polarization == "circular":
        vba.set("CircularPolarization", "Left")
    else:
        vba.set_number("EFieldVector", e_theta)

    vba.set_number("Theta", theta)
    vba.set_number("Phi", phi)
    vba.set_number("PolarizationAngle", 0)
    vba.set_number("EFieldAmplitudeTheta", e_theta)
    vba.set_number("EFieldAmplitudePhi", e_phi)
    vba.call("Store")
    script = vba.build()
    result = client.execute_vba(script)
    result["excitation"] = "plane_wave"
    result["polarization"] = polarization
    result["theta"] = theta
    result["phi"] = phi

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_floquet_port(arguments: dict, client: CSTClient) -> list[TextContent]:
    port_number = validate_port_number(arguments["port_number"])
    orientation = arguments["orientation"]
    if orientation not in _FLOQUET_ORIENTATION_ENUM:
        raise ValueError(
            f"Invalid orientation '{orientation}'. Must be one of: {_FLOQUET_ORIENTATION_ENUM}"
        )
    modes = int(arguments.get("modes", 2))
    if modes < 1:
        raise ValueError(f"modes must be >= 1, got {modes}")

    vba = (
        VBABuilder("FloquetPort")
        .call("Reset")
        .set("Port", orientation)
        .set_number("SetNumberOfModesConsidered", modes)
    )
    script = vba.build()
    result = client.execute_vba(script)
    result["port_type"] = "floquet"
    result["note"] = (
        "Floquet ports are selected by zmin/zmax, not a freely assigned port number; unit-cell/open boundaries must already be configured."
    )
    result["port_number"] = port_number
    result["modes"] = modes

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_list_ports(arguments: dict, client: CSTClient) -> list[TextContent]:
    try:
        result = _list_ports(arguments, client)
    except ValueError as e:
        result = {"status": "error", "message": str(e)}
    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Port readback (cst_list_ports)
# ---------------------------------------------------------------------------

# Port.GetPortMeshCoordinates reports the orientation as 0..5 (CST 2025 help, Port object).
_ORIENTATION_BY_CODE = dict(enumerate(_ORIENTATION_ENUM))
# Port.GetFacePortTypeAndSize face types and the meaning of its two size values.
_FACE_SHAPES = {
    1: ("rectangular", ("width", "length")),
    2: ("cylindrical", ("radius", "length")),
    3: ("coaxial", ("excited_edge_radius", "ground_edge_radius")),
}
_PORT_NOTES = [
    "Every value is read from CST; none is taken from the arguments that created the port.",
    (
        "window is the port window snapped to mesh lines (Port.GetPortMeshCoordinates), not "
        "the coordinates as entered; CST has no getter for the entered window."
    ),
    (
        "Coordinates are in project length units (the CST help states 'local units' for the "
        "window and gives no unit for the centre or discrete end points)."
    ),
]
# A Debug.Print-safe error text: no line breaks or tabs to break the line protocol.
_ERR = 'Replace(Replace(Replace(Err.Description, vbCr, " "), vbLf, " "), vbTab, " ")'
_SIX = ("mcpA", "mcpB", "mcpC", "mcpD", "mcpE", "mcpF")


def _readback_port_numbers(arguments: dict) -> list[int] | None:
    raw = arguments.get("port_numbers")
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise ValueError("port_numbers must be a non-empty list of port numbers")
    if len(raw) > _MAX_READBACK_PORTS:
        raise ValueError(f"port_numbers accepts at most {_MAX_READBACK_PORTS} numbers")
    numbers: list[int] = []
    for i, value in enumerate(raw):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"port_numbers[{i}] must be an integer, got {value!r}")  # noqa: TRY004
        validate_port_number(value)
        if value not in numbers:
            numbers.append(value)
    return numbers


def _vba_print(key: str, value: str) -> str:
    """One Debug.Print at line start (output capture only redirects those)."""
    return f'Debug.Print mcpKey & "{key}" & vbTab & {value}'


def _vba_report(call: str, key: str, value: str, indent: str = "    ") -> list[str]:
    """Run ``call`` and print ``key`` with ``value``, or ``key_error`` with CST's message."""
    return [
        f"{indent}Err.Clear",
        f"{indent}{call}",
        f"{indent}If Err.Number = 0 Then",
        f"{indent}  {_vba_print(key, value)}",
        f"{indent}Else",
        f"{indent}  {_vba_print(key + '_error', _ERR)}",
        f"{indent}End If",
    ]


def _vba_report_bool(call: str, key: str, value: str, indent: str = "    ") -> list[str]:
    """Like _vba_report for getters returning a Boolean: False prints ``key_none``."""
    return [
        f"{indent}Err.Clear",
        f"{indent}mcpOk = False",
        f"{indent}mcpOk = {call}",
        f"{indent}If Err.Number <> 0 Then",
        f"{indent}  {_vba_print(key + '_error', _ERR)}",
        f"{indent}ElseIf mcpOk Then",
        f"{indent}  {_vba_print(key, value)}",
        f"{indent}Else",
        f'{indent}  Debug.Print mcpKey & "{key}_none"',
        f"{indent}End If",
    ]


def _cstr_join(names: tuple[str, ...] | list[str]) -> str:
    return " & vbTab & ".join(f"CStr({n})" for n in names)


def _port_readback_vba(port_numbers: list[int] | None) -> str:
    """VBA printing tab-separated ``<port><TAB><key><TAB>...`` lines.

    ``On Error Resume Next`` keeps one failing getter from hiding the others; every getter's
    error is printed as ``<key>_error`` so the parser can say why a value is unavailable.
    """
    if port_numbers is None:
        want = "True"
    else:
        want = "(" + " Or ".join(f"mcpPn = {_vba_int(n)}" for n in port_numbers) + ")"
    six = ", ".join(_SIX)
    lines = [
        "Dim mcpN As Long, mcpI As Long, mcpPn As Long, mcpM As Long, mcpModes As Long",
        "Dim mcpOrient As Long",
        "Dim mcpA As Double, mcpB As Double, mcpC As Double",
        "Dim mcpD As Double, mcpE As Double, mcpF As Double",
        "Dim mcpText As String, mcpType As String, mcpKey As String",
        "Dim mcpOk As Boolean, mcpMonitor As Boolean",
        "Dim mcpPorts() As Long",
        "On Error Resume Next",
        "Err.Clear",
        "mcpText = CStr(Units.GetGeometryUnitToSI())",
        "If Err.Number = 0 Then",
        '  Debug.Print "unit" & vbTab & mcpText',
        "End If",
        "Err.Clear",
        "mcpN = Port.StartPortNumberIteration()",
        "If Err.Number <> 0 Then",
        f'  Debug.Print "count_error" & vbTab & {_ERR}',
        "  mcpN = 0",
        "Else",
        '  Debug.Print "count" & vbTab & CStr(mcpN)',
        "End If",
        "ReDim mcpPorts(mcpN)",
        # Collect the numbers first: other Port getters might disturb the iteration counter.
        "For mcpI = 1 To mcpN",
        "  Err.Clear",
        "  mcpPorts(mcpI) = Port.GetNextPortNumber()",
        "  If Err.Number = 0 Then",
        '    Debug.Print "port" & vbTab & CStr(mcpPorts(mcpI))',
        "  Else",
        "    mcpPorts(mcpI) = 0",
        f'    Debug.Print "port_error" & vbTab & {_ERR}',
        "  End If",
        "Next mcpI",
        "For mcpI = 1 To mcpN",
        "  mcpPn = mcpPorts(mcpI)",
        "  If mcpPn > 0 Then",
        f"  If {want} Then",
        "    mcpKey = CStr(mcpPn) & vbTab",
        '    mcpType = ""',
        *_vba_report("mcpType = Port.GetType(mcpPn)", "type", "mcpType"),
        *_vba_report(
            "mcpText = Port.GetLabel(mcpPn)",
            "label",
            'Replace(Replace(Replace(mcpText, vbCr, " "), vbLf, " "), vbTab, " ")',
        ),
        *_vba_report(
            "Port.GetPortCenterCoordinates mcpPn, mcpA, mcpB, mcpC",
            "center",
            _cstr_join(_SIX[:3]),
        ),
        '    If LCase(mcpType) <> "discrete" Then',
        *_vba_report(
            f"Port.GetPortMeshCoordinates mcpPn, mcpOrient, {six}",
            "window",
            _cstr_join(("mcpOrient", *_SIX)),
            indent="      ",
        ),
        "      Err.Clear",
        "      mcpModes = 0",
        "      mcpModes = Port.GetNumberOfModes(mcpPn)",
        "      If Err.Number = 0 Then",
        f"        {_vba_print('modes', 'CStr(mcpModes)')}",
        "      Else",
        f"        {_vba_print('modes_error', _ERR)}",
        "        mcpModes = 0",
        "      End If",
        f"      If mcpModes > {_MAX_READBACK_MODES} Then mcpModes = {_MAX_READBACK_MODES}",
        "      For mcpM = 1 To mcpModes",
        *_vba_report(
            "mcpText = Port.GetModeType(mcpPn, mcpM)",
            "mode_type",
            "CStr(mcpM) & vbTab & mcpText",
            indent="        ",
        ),
        *_vba_report(
            "mcpA = Port.GetLineImpedance(mcpPn, mcpM)",
            "mode_z",
            "CStr(mcpM) & vbTab & CStr(mcpA)",
            indent="        ",
        ),
        "      Next mcpM",
        "    End If",
        '    If LCase(mcpType) <> "waveguide" Then',
        *_vba_report_bool(
            f"DiscretePort.GetCoordinates(mcpPn, {six})",
            "edge_coords",
            _cstr_join(_SIX),
            indent="      ",
        ),
        *_vba_report_bool(
            "DiscretePort.GetProperties(mcpPn, mcpText, mcpA, mcpB, mcpC, mcpD, mcpE, mcpMonitor)",
            "edge_props",
            "mcpText & vbTab & CStr(mcpA)",
            indent="      ",
        ),
        *_vba_report_bool(
            "DiscreteFacePort.GetProperties(mcpPn, mcpText, mcpA, mcpB, mcpC, mcpD, mcpMonitor)",
            "face_props",
            "mcpText & vbTab & CStr(mcpA)",
            indent="      ",
        ),
        *_vba_report(
            "Port.GetFacePortTypeAndSize mcpPn, mcpOrient, mcpA, mcpB",
            "face_shape",
            _cstr_join(("mcpOrient", "mcpA", "mcpB")),
            indent="      ",
        ),
        "    End If",
        "  End If",
        "  End If",
        "Next mcpI",
    ]
    return "\n".join(lines)


def _port_float(text: str) -> float:
    # CStr follows the Windows locale, which may use a decimal comma.
    value = float(text.strip().replace(",", "."))
    if not math.isfinite(value):
        raise ValueError(f"non-finite value {text!r}")
    return value


def _cst_said(row: dict[str, list[str]], key: str, fallback: str) -> str:
    detail = "\t".join(row.get(f"{key}_error", [])).strip()
    return f"CST reported: {detail}" if detail else fallback


def _floats(values: list[str], count: int) -> list[float] | None:
    try:
        parsed = [_port_float(v) for v in values[:count]]
    except ValueError:
        return None
    return parsed if len(parsed) == count else None


def _parse_waveguide(entry: dict, row: dict, modes: dict, unavailable: dict) -> None:
    entry.update(orientation=None, window=None, window_kind="mesh_snapped", on_boundary=None)
    window = row.get("window")
    values = _floats(window[1:], 6) if window else None
    if window and values is not None and window[0].strip().isdigit():
        orientation = _ORIENTATION_BY_CODE.get(int(window[0].strip()))
        if orientation is None:
            unavailable["orientation"] = f"CST returned unknown orientation code {window[0]!r}"
        entry["orientation"] = orientation
        x1, x2, y1, y2, z1, z2 = values
        entry["window"] = {"x": [x1, x2], "y": [y1, y2], "z": [z1, z2]}
    else:
        reason = _cst_said(
            row,
            "window",
            "Unreadable port window from CST" if window else "CST returned no port window",
        )
        reason += " The window is read from mesh lines, so CST may need a mesh first."
        unavailable["window"] = reason
        unavailable["orientation"] = "Read together with the window, which is unavailable."
    unavailable["on_boundary"] = (
        "CST has no getter for whether a waveguide port lies on the domain boundary or "
        "inside it; compare the window with the domain extent instead."
    )
    entry["reference_impedance"] = None
    unavailable["reference_impedance"] = (
        "CST has no getter for a waveguide port's reference impedance; modes[].line_impedance "
        "is the computed line impedance of each mode."
    )

    entry["num_modes"] = None
    try:
        entry["num_modes"] = int(row["modes"][0].strip())
    except (KeyError, IndexError, ValueError):
        unavailable["num_modes"] = _cst_said(row, "modes", "CST returned no number of modes")
    mode_list = []
    for m in sorted(modes):
        data = modes[m]
        mode: dict = {"mode": m, "type": None, "line_impedance": None}
        mode_unavailable: dict[str, str] = {}
        if "mode_type" in data and data["mode_type"].strip():
            mode["type"] = data["mode_type"].strip()
        else:
            mode_unavailable["type"] = (
                f"CST reported: {data['mode_type_error']}"
                if data.get("mode_type_error")
                else "CST returned no mode type; the port modes may not be computed yet."
            )
        try:
            z = _port_float(data["mode_z"])
        except (KeyError, ValueError):
            mode_unavailable["line_impedance"] = (
                f"CST reported: {data['mode_z_error']}"
                if data.get("mode_z_error")
                else "CST returned no line impedance"
            )
        else:
            if z > 0:
                mode["line_impedance"] = z
            else:
                mode_unavailable["line_impedance"] = (
                    "CST returned 0: only TEM and quasi-TEM modes have a line impedance, and "
                    "only after the port modes are computed."
                )
        if mode_unavailable:
            mode["unavailable"] = mode_unavailable
        mode_list.append(mode)
    entry["modes"] = mode_list
    if entry["num_modes"] and entry["num_modes"] > _MAX_READBACK_MODES:
        entry["modes_truncated_to"] = _MAX_READBACK_MODES


def _parse_discrete(entry: dict, row: dict, unavailable: dict) -> None:
    edge_coords = _floats(row["edge_coords"], 6) if "edge_coords" in row else None
    edge_props = row.get("edge_props")
    face_props = row.get("face_props")
    if edge_coords is not None or (edge_props and not face_props):
        kind, props = "edge", edge_props
    elif face_props:
        kind, props = "face", face_props
    else:
        kind, props = None, None
    entry.update(discrete_kind=kind, endpoints=None, excitation_type=None, impedance=None)
    if kind is None:
        unavailable["discrete_kind"] = (
            "Neither DiscretePort nor DiscreteFacePort reported properties for this port. "
            + _cst_said(row, "edge_props", "")
        ).strip()

    if edge_coords is not None:
        x1, y1, z1, x2, y2, z2 = edge_coords
        entry["endpoints"] = {"start": [x1, y1, z1], "end": [x2, y2, z2]}
    elif kind == "face":
        unavailable["endpoints"] = (
            "CST has no end point getter for discrete face ports; see center and face_shape."
        )
    else:
        unavailable["endpoints"] = _cst_said(
            row, "edge_coords", "DiscretePort.GetCoordinates returned no end points"
        )

    if props:
        entry["excitation_type"] = props[0].strip() or None
        try:
            entry["impedance"] = _port_float(props[1])
        except (IndexError, ValueError):
            unavailable["impedance"] = "Unreadable impedance from CST"
    else:
        reason = _cst_said(row, f"{kind or 'edge'}_props", "CST reported no port properties")
        unavailable["excitation_type"] = reason
        unavailable["impedance"] = reason

    if kind == "face":
        entry["face_shape"] = None
        shape = _floats(row["face_shape"], 3) if "face_shape" in row else None
        known = _FACE_SHAPES.get(int(shape[0])) if shape else None
        if known:
            name, labels = known
            entry["face_shape"] = {"kind": name, labels[0]: shape[1], labels[1]: shape[2]}
        else:
            unavailable["face_shape"] = _cst_said(
                row, "face_shape", "CST reports no special face shape for this port"
            )


def _parse_port_readback(output: str, requested: list[int] | None) -> dict:
    from cst_mcp.tools.drawing import _unit_from_factor

    unit: str | None = None
    unit_to_si: float | None = None
    count: int | None = None
    count_error: str | None = None
    listed: list[int] = []
    rows: dict[int, dict[str, list[str]]] = {}
    modes: dict[int, dict[int, dict[str, str]]] = {}
    for line in output.splitlines():
        parts = line.rstrip("\r").split("\t")
        head, rest = parts[0].strip(), parts[1:]
        if head == "unit" and rest:
            try:
                unit_to_si = _port_float(rest[0])
                unit = _unit_from_factor(unit_to_si)
            except ValueError:
                pass
        elif head == "count" and rest:
            try:
                count = int(rest[0].strip())
            except ValueError:
                count_error = f"Unreadable port count from CST: {rest[0]!r}"
        elif head == "count_error":
            count_error = "\t".join(rest).strip() or "CST could not count the ports"
        elif head == "port" and rest and rest[0].strip().isdigit():
            number = int(rest[0].strip())
            if number not in listed:
                listed.append(number)
        elif head.isdigit() and rest:
            number, key, values = int(head), rest[0], rest[1:]
            if key.startswith("mode_") and values and values[0].strip().isdigit():
                text = "\t".join(values[1:]).strip()
                modes.setdefault(number, {}).setdefault(int(values[0]), {})[key] = text
            else:
                rows.setdefault(number, {})[key] = values

    if count_error is not None:
        return {
            "status": "error",
            "message": f"Could not list the ports: {count_error}",
            "source": "VBA query via output capture (no model history)",
        }

    wanted = listed if requested is None else [n for n in requested if n in listed]
    ports: list[dict] = []
    for number in wanted:
        row = rows.get(number, {})
        unavailable: dict[str, str] = {}
        raw_type = "\t".join(row.get("type", [])).strip() or None
        kind = (raw_type or "").lower()
        entry: dict = {
            "number": number,
            "type": kind if kind in {"waveguide", "discrete"} else ("other" if raw_type else None),
            "raw_type": raw_type,
            "label": "\t".join(row["label"]).strip() if "label" in row else None,
            "center": None,
        }
        if raw_type is None:
            unavailable["type"] = _cst_said(row, "type", "CST returned no port type")
        if "label" not in row:
            unavailable["label"] = _cst_said(row, "label", "CST returned no label")
        center = _floats(row["center"], 3) if "center" in row else None
        if center is not None:
            entry["center"] = center
        else:
            unavailable["center"] = _cst_said(row, "center", "CST returned no port centre")

        if entry["type"] == "waveguide":
            _parse_waveguide(entry, row, modes.get(number, {}), unavailable)
        elif entry["type"] == "discrete":
            _parse_discrete(entry, row, unavailable)
        else:
            unavailable["details"] = (
                "Type-specific values are only read for waveguide and discrete ports."
            )
        entry["unavailable"] = unavailable
        ports.append(entry)

    result: dict = {
        "status": "ok",
        "port_count": count if count is not None else len(listed),
        "port_numbers": listed,
        "unit": unit,
        "unit_to_si": unit_to_si,
        "ports": ports,
        "notes": _PORT_NOTES,
        "source": "VBA query via output capture (no model history)",
    }
    missing = [] if requested is None else [n for n in requested if n not in listed]
    if missing:
        result["status"] = "error"
        result["missing"] = missing
        result["message"] = f"No such port: {', '.join(str(n) for n in missing)}"
    elif not listed:
        result["message"] = "The project has no ports."
    return result


def _list_ports(arguments: dict, client: CSTClient) -> dict:
    requested = _readback_port_numbers(arguments)
    script = _port_readback_vba(requested)
    if not client.connected:
        return {
            "status": "offline",
            "vba": script,
            "message": "Not connected; ports are only read back from a live CST project.",
        }
    if not client.has_project:
        return {"status": "error", "message": "No project open"}
    captured = client.capture_vba_output(script)
    if captured.get("status") != "ok":
        return captured
    return _parse_port_readback(str(captured.get("output", "")), requested)


_PORT_NUMBERS_LINES = (
    "Dim mcpN As Long, mcpI As Long",
    "On Error Resume Next",
    "Err.Clear",
    "mcpN = Port.StartPortNumberIteration()",
    "If Err.Number <> 0 Then",
    '  Debug.Print "count_error"',
    "Else",
    '  Debug.Print "count" & vbTab & CStr(mcpN)',
    "  For mcpI = 1 To mcpN",
    "    Err.Clear",
    '    Debug.Print "port" & vbTab & CStr(Port.GetNextPortNumber())',
    "  Next mcpI",
    "End If",
)
_PORT_NUMBERS_VBA = "\n".join(_PORT_NUMBERS_LINES)


def _existing_port_numbers(client: CSTClient) -> list[int] | None:
    """Defined port numbers (every port type), or None when they cannot be read."""
    if not getattr(client, "connected", False) or not getattr(client, "has_project", False):
        return None
    captured = client.capture_vba_output(_PORT_NUMBERS_VBA)
    if captured.get("status") != "ok":
        return None
    count: int | None = None
    numbers: list[int] = []
    for line in str(captured.get("output", "")).splitlines():
        parts = [p.strip() for p in line.rstrip("\r").split("\t")]
        if parts[0] == "count_error":
            return None
        if parts[0] == "count" and len(parts) > 1 and parts[1].isdigit():
            count = int(parts[1])
        elif parts[0] == "port" and len(parts) > 1 and parts[1].isdigit():
            number = int(parts[1])
            if number > 0 and number not in numbers:
                numbers.append(number)
    if count is None or len(numbers) != count:
        return None
    return sorted(numbers)


async def _handle_delete_port(arguments: dict, client: CSTClient) -> list[TextContent]:
    port_number = validate_port_number(arguments["port_number"])

    # CST 2025 help (special_vbaports_port_object.htm): "Delete ( int portnumber )".
    # It is the only port delete: DiscretePort documents none, and the Port number
    # iteration covers every port type.
    script = f"Port.Delete {_vba_int(port_number)}"

    existing = _existing_port_numbers(client)
    if existing is not None and port_number not in existing:
        listed = ", ".join(str(n) for n in existing) or "none"
        result = {
            "status": "error",
            "port_number": port_number,
            "existing_ports": existing,
            "message": (
                f"Port {port_number} does not exist (defined ports: {listed}). "
                "Nothing was changed in CST."
            ),
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    result = client.execute_vba(script)
    result["port_number"] = port_number
    if result.get("status") != "executed":
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    remaining = _existing_port_numbers(client)
    if remaining is None:
        result["action"] = "deleted"
        if existing is None:
            result["warning"] = (
                "The port list could not be read before or after the delete; "
                "check it with cst_list_ports."
            )
    elif port_number in remaining:
        result["status"] = "error"
        result["remaining_ports"] = remaining
        result["message"] = (
            f"CST ran the delete, but port {port_number} is still listed; "
            "check it with cst_list_ports."
        )
    else:
        result["action"] = "deleted"
        result["remaining_ports"] = remaining

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_multipin_port(arguments: dict, client: CSTClient) -> list[TextContent]:
    port_number = validate_port_number(arguments["port_number"])
    orientation = arguments["orientation"]
    if orientation not in _ORIENTATION_ENUM:
        raise ValueError(
            f"Invalid orientation '{orientation}'. Must be one of: {_ORIENTATION_ENUM}"
        )
    x_min = float(arguments["x_min"])
    x_max = float(arguments["x_max"])
    y_min = float(arguments["y_min"])
    y_max = float(arguments["y_max"])
    z_min = float(arguments["z_min"])
    z_max = float(arguments["z_max"])
    num_modes = int(arguments.get("num_modes", 1))
    validate_range(num_modes, 1, 10, "num_modes")

    vba = (
        VBABuilder("Port")
        .call("Reset")
        .set_number("PortNumber", port_number)
        .set("Label", "")
        .set("Orientation", orientation)
        .set_double("Xrange", x_min, x_max)
        .set_double("Yrange", y_min, y_max)
        .set_double("Zrange", z_min, z_max)
        .set_number("NumberOfModes", num_modes)
        .call("Create")
    )
    script = vba.build()
    result = client.execute_vba(script)
    result["port_type"] = "multipin"
    result["port_number"] = port_number
    result["orientation"] = orientation
    result["num_modes"] = num_modes

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


# Reject line breaks and non-numeric values in numeric slots before any VBA
# is generated from the arguments (generated VBA bypasses CST_ALLOW_RAW_VBA).
from cst_mcp.vba_safety import guard_handler as _guard_handler

handle = _guard_handler(TOOLS, handle)
