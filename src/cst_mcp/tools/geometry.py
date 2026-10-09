"""Geometry creation tools for CST Studio Suite.

Provides 13 MCP tools for creating 3D shapes, curves, and extruded profiles
in CST Studio by generating VBA scripts via VBABuilder.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from mcp.types import TextContent, Tool

from cst_mcp.cst_client import CSTClient
from cst_mcp.validators import validate_name, validate_non_negative, validate_positive
from cst_mcp.vba_builder import VBABuilder, VBAScript, _format_expression
from cst_mcp.vba_safety import vba_escape as _q
from cst_mcp.vba_safety import vba_number as _vba_number
from cst_mcp.vba_safety import vba_string_literal as _vba_string_literal

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------


def _expression_field(description: str, **settings) -> dict:
    """Schema for the selected primitive dimensions, retaining numeric defaults."""
    return dict(
        type=["number", "string"],
        minLength=1,
        description=(
            description + ": finite JSON number or nonempty single-line CST expression; "
            "preserved in model history. CST reports semantic expression errors."
        ),
        **settings,
    )


TOOLS: list[Tool] = [
    # 1. Brick
    Tool(
        name="cst_create_brick",
        description=(
            "Create a rectangular brick (box) in CST Studio. Bounds accept JSON numbers "
            "or nonempty, single-line CST parameter names/arithmetic expressions, mixed "
            "in the same call. Expressions remain in model history for parameter rebuilds; "
            "CST validates their syntax and parameter references. Does not edit existing solids."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name (e.g. 'Antenna')"},
                "name": {"type": "string", "description": "Solid name (e.g. 'Substrate')"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                **{
                    bound: {
                        "type": ["number", "string"],
                        "minLength": 1,
                        "description": (
                            f"{bound[0].upper()} range {end}: finite JSON number or nonempty "
                            "single-line CST parameter/arithmetic expression (e.g. 'PBrick_L', "
                            "'200+PBrick_L', 'PBrick_H/2'); preserved in model history. "
                            "CST reports malformed expressions or undefined parameters."
                        ),
                    }
                    for bound, end in (
                        ("x_min", "minimum"),
                        ("x_max", "maximum"),
                        ("y_min", "minimum"),
                        ("y_max", "maximum"),
                        ("z_min", "minimum"),
                        ("z_max", "maximum"),
                    )
                },
            },
            "required": ["component", "name", "x_min", "x_max", "y_min", "y_max", "z_min", "z_max"],
        },
    ),
    # 2. Cylinder
    Tool(
        name="cst_create_cylinder",
        description="Create a cylinder in CST Studio. Use inner_radius=0 for a solid cylinder.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "axis": {"type": "string", "enum": ["x", "y", "z"], "description": "Cylinder axis"},
                "outer_radius": _expression_field("Outer radius"),
                "inner_radius": _expression_field("Inner radius (0 for solid)", default=0),
                "center_x": _expression_field("Center X coordinate", default=0),
                "center_y": _expression_field("Center Y coordinate", default=0),
                "center_z": _expression_field("Center Z coordinate", default=0),
                "range_min": _expression_field("Axis range minimum"),
                "range_max": _expression_field("Axis range maximum"),
            },
            "required": ["component", "name", "axis", "outer_radius", "range_min", "range_max"],
        },
    ),
    # 3. Cone
    Tool(
        name="cst_create_cone",
        description="Create a cone or truncated cone in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "axis": {"type": "string", "enum": ["x", "y", "z"], "description": "Cone axis"},
                "bottom_radius": _expression_field("Bottom radius"),
                "top_radius": _expression_field("Top radius (0 for pointed cone)"),
                "center_x": _expression_field("Center X coordinate", default=0),
                "center_y": _expression_field("Center Y coordinate", default=0),
                "center_z": _expression_field("Center Z coordinate", default=0),
                "range_min": _expression_field("Axis range minimum"),
                "range_max": _expression_field("Axis range maximum"),
            },
            "required": [
                "component",
                "name",
                "axis",
                "bottom_radius",
                "top_radius",
                "range_min",
                "range_max",
            ],
        },
    ),
    # 4. Sphere
    Tool(
        name="cst_create_sphere",
        description="Create a sphere in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "center_x": _expression_field("Center X coordinate", default=0),
                "center_y": _expression_field("Center Y coordinate", default=0),
                "center_z": _expression_field("Center Z coordinate", default=0),
                "radius": _expression_field("Sphere radius"),
                "segments": {
                    "type": "integer",
                    "description": "Number of segments (0=auto)",
                    "default": 0,
                },
            },
            "required": ["component", "name", "radius"],
        },
    ),
    # 5. Torus
    Tool(
        name="cst_create_torus",
        description="Create a torus in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "axis": {"type": "string", "enum": ["x", "y", "z"], "description": "Torus axis"},
                "center_x": _expression_field("Center X coordinate", default=0),
                "center_y": _expression_field("Center Y coordinate", default=0),
                "center_z": _expression_field("Center Z coordinate", default=0),
                "outer_radius": _expression_field(
                    "CST outer (large) radius, from axis to outer surface"
                ),
                "inner_radius": _expression_field(
                    "CST inner (small) radius, from axis to inner surface"
                ),
            },
            "required": ["component", "name", "axis", "outer_radius", "inner_radius"],
        },
    ),
    # 6. Extrude
    Tool(
        name="cst_create_extrude",
        description=(
            "Extrude a 2D polygon profile into a 3D solid in CST Studio (Extrude object, Mode "
            "'pointlist'). The profile lies in the plane normal to 'axis' (default z) at the given "
            "x/y/z_offset. Height, profile coordinates and the active offset accept numbers or "
            "single-line CST expressions preserved in history. Double arguments use native Evaluate. "
            "Only the selected-axis offset applies. Optional 'holes' are extruded the same way "
            "and subtracted (Solid.Subtract). Live CST validation is pending."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": _expression_field("Profile coordinate"),
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "minItems": 3,
                    "description": "List of [x, y] coordinate pairs forming the profile polygon",
                },
                "height": _expression_field("Extrusion height"),
                "axis": {
                    "type": "string",
                    "enum": ["x", "y", "z"],
                    "default": "z",
                    "description": "Extrusion axis (profile plane normal). z: (u,v)=(x,y); x: (u,v)=(y,z); y: (u,v)=(x,-z).",
                },
                "x_offset": _expression_field(
                    "Base-plane position on x; expressions allowed only with axis='x'",
                    default=0,
                ),
                "y_offset": _expression_field(
                    "Base-plane position on y; expressions allowed only with axis='y'",
                    default=0,
                ),
                "z_offset": _expression_field(
                    "Base-plane position on z; expressions allowed only with axis='z'",
                    default=0,
                ),
                "holes": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": _expression_field("Profile coordinate"),
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "minItems": 3,
                    },
                    "default": [],
                    "description": (
                        "Optional holes/slots: each a list of [x, y] pairs in the same profile plane. "
                        "Each hole is extruded with the same height and removed with Solid.Subtract."
                    ),
                },
                "extrude_direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "default": "up",
                    "description": (
                        "'up' (default, unchanged behaviour): solid spans offset..offset+height along "
                        "+axis (e.g. copper on top of a substrate whose top face is z=offset). 'down': "
                        "offset-height..offset. Pointlist down reverses the plane normal and remaps "
                        "local coordinates, correcting the previously ignored option."
                    ),
                },
            },
            "required": ["component", "name", "points", "height"],
        },
    ),
    # 7. Loft
    Tool(
        name="cst_create_loft",
        description="Create a lofted solid between two or more 2D profiles in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "profiles": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "minItems": 3,
                    },
                    "minItems": 2,
                    "description": "List of profiles, each a list of [x, y] coordinate pairs",
                },
            },
            "required": ["component", "name", "profiles"],
        },
    ),
    # 8. Wire
    Tool(
        name="cst_create_wire",
        description="Create a bondwire / wire between two points in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "start_x": {"type": "number", "description": "Start point X"},
                "start_y": {"type": "number", "description": "Start point Y"},
                "start_z": {"type": "number", "description": "Start point Z"},
                "end_x": {"type": "number", "description": "End point X"},
                "end_y": {"type": "number", "description": "End point Y"},
                "end_z": {"type": "number", "description": "End point Z"},
                "radius": {"type": "number", "description": "Wire radius"},
            },
            "required": [
                "component",
                "name",
                "start_x",
                "start_y",
                "start_z",
                "end_x",
                "end_y",
                "end_z",
                "radius",
            ],
        },
    ),
    # 9. Polygon3D
    Tool(
        name="cst_create_polygon3d",
        description="Create a 3D polygon curve in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Curve name"},
                "points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": _expression_field("Point coordinate"),
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "minItems": 2,
                    "description": "List of [x, y, z] coordinate triples",
                },
            },
            "required": ["name", "points"],
        },
    ),
    # 10. Analytical curve
    Tool(
        name="cst_create_analytical_curve",
        description="Create a parametric analytical curve in CST Studio using expressions of parameter t.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Curve name"},
                "x_expr": {
                    "type": "string",
                    "description": "X expression as function of t (e.g. 'cos(t)')",
                },
                "y_expr": {
                    "type": "string",
                    "description": "Y expression as function of t (e.g. 'sin(t)')",
                },
                "z_expr": {
                    "type": "string",
                    "description": "Z expression as function of t (e.g. 't')",
                },
                "t_min": {"type": "number", "description": "Parameter t minimum value"},
                "t_max": {"type": "number", "description": "Parameter t maximum value"},
            },
            "required": ["name", "x_expr", "y_expr", "z_expr", "t_min", "t_max"],
        },
    ),
    # 11. Face from curves
    Tool(
        name="cst_create_face_from_curves",
        description="Create a planar face from one or more closed curves in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Face/solid name"},
                "curve_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "List of curve names to form the face boundary",
                },
            },
            "required": ["component", "name", "curve_names"],
        },
    ),
    # 12. Elliptical cylinder
    Tool(
        name="cst_create_ecylinder",
        description="Create an elliptical cylinder in CST Studio.",
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "axis": {"type": "string", "enum": ["x", "y", "z"], "description": "Cylinder axis"},
                "x_radius": _expression_field("Radius in local X direction"),
                "y_radius": _expression_field("Radius in local Y direction"),
                "center_x": _expression_field("Center X coordinate", default=0),
                "center_y": _expression_field("Center Y coordinate", default=0),
                "center_z": _expression_field("Center Z coordinate", default=0),
                "range_min": _expression_field("Axis range minimum"),
                "range_max": _expression_field("Axis range maximum"),
            },
            "required": [
                "component",
                "name",
                "axis",
                "x_radius",
                "y_radius",
                "range_min",
                "range_max",
            ],
        },
    ),
    # 13. Polygon extrude (convenience)
    Tool(
        name="cst_create_polygon_extrude",
        description=(
            "Create a polygon and extrude it along an axis in CST Studio. "
            "Convenience tool combining polygon profile creation (Polygon3D curve) and extrusion "
            "(ExtrudeCurve). The profile lies at the base plane given by x/y/z_offset for the chosen "
            "axis; optional 'holes' (lists of [x, y]) are extruded the same way and removed with "
            "Solid.Subtract, e.g. for slotted/fractal patches. Z placement: CST's ExtrudeCurve "
            "extrudes along the closed curve's normal, which follows the point winding (a clockwise "
            "profile at z=0 with positive thickness lands at z=-t..0, e.g. copper embedded in the "
            "substrate). This tool normalises the winding, so extrude_direction='up' (default) gives "
            "offset..offset+height along +axis and 'down' gives offset-height..offset; the response "
            "reports input-contract endpoints, not measured bounds. Height, all profile coordinates "
            "and the active offset accept numbers or single-line CST expressions. Native Evaluate "
            "and winding selection remain in history for reconstruction. Live CST validation is pending; "
            "native dialogs may show evaluated values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Solid name"},
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "points": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": _expression_field("Profile coordinate"),
                        "minItems": 2,
                        "maxItems": 2,
                    },
                    "minItems": 3,
                    "description": "List of [x, y] coordinate pairs forming the polygon",
                },
                "height": _expression_field("Extrusion height"),
                "axis": {
                    "type": "string",
                    "enum": ["x", "y", "z"],
                    "description": "Extrusion axis",
                    "default": "z",
                },
                "x_offset": _expression_field(
                    "Base-plane position on x; expressions allowed only with axis='x'",
                    default=0,
                ),
                "y_offset": _expression_field(
                    "Base-plane position on y; expressions allowed only with axis='y'",
                    default=0,
                ),
                "z_offset": _expression_field(
                    "Base-plane position on z; expressions allowed only with axis='z'",
                    default=0,
                ),
                "holes": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "array",
                            "items": _expression_field("Profile coordinate"),
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "minItems": 3,
                    },
                    "default": [],
                    "description": (
                        "Optional holes/slots: each a list of [x, y] pairs in the same profile plane. "
                        "Each hole is extruded with the same height and removed with Solid.Subtract."
                    ),
                },
                "extrude_direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "default": "up",
                    "description": (
                        "'up' (default, unchanged behaviour): solid spans offset..offset+height along "
                        "+axis (e.g. copper on top of a substrate whose top face is z=offset). 'down': "
                        "offset-height..offset. Numeric winding is normalized before generation; "
                        "symbolic winding is evaluated independently for each profile on every rebuild."
                    ),
                },
            },
            "required": ["component", "name", "points", "height"],
        },
    ),
]

# ---------------------------------------------------------------------------
# VBA generation helpers
# ---------------------------------------------------------------------------


def _radius_expression(value: float | str, field: str, *, allow_zero=False) -> float | str:
    """Validate serialization first, then apply the existing numeric radius contract."""
    _format_expression(value)
    if not isinstance(value, str):
        validator = validate_non_negative if allow_zero else validate_positive
        validator(value, field)
    return value


def _build_brick(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")

    vba = (
        VBABuilder("Brick")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set_expression_pair("Xrange", args["x_min"], args["x_max"])
        .set_expression_pair("Yrange", args["y_min"], args["y_max"])
        .set_expression_pair("Zrange", args["z_min"], args["z_max"])
        .call("Create")
    )
    return vba.build()


def _build_cylinder(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    axis = args["axis"]
    outer_radius = _radius_expression(args["outer_radius"], "outer_radius")
    inner_radius = _radius_expression(args.get("inner_radius", 0), "inner_radius", allow_zero=True)
    cx = args.get("center_x", 0)
    cy = args.get("center_y", 0)
    cz = args.get("center_z", 0)

    # Map axis to the correct CST VBA property names
    range_prop = {"x": "Xrange", "y": "Yrange", "z": "Zrange"}[axis]

    vba = (
        VBABuilder("Cylinder")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set("Axis", axis)
        .set_expression("Outerradius", outer_radius)
        .set_expression("Innerradius", inner_radius)
        .set_expression("Xcenter", cx)
        .set_expression("Ycenter", cy)
        .set_expression("Zcenter", cz)
        .set_expression_pair(range_prop, args["range_min"], args["range_max"])
        .call("Create")
    )
    return vba.build()


def _build_cone(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    axis = args["axis"]
    bottom_radius = _radius_expression(args["bottom_radius"], "bottom_radius", allow_zero=True)
    top_radius = _radius_expression(args["top_radius"], "top_radius", allow_zero=True)
    cx = args.get("center_x", 0)
    cy = args.get("center_y", 0)
    cz = args.get("center_z", 0)

    # Map axis to the correct CST VBA property names
    range_prop = {"x": "Xrange", "y": "Yrange", "z": "Zrange"}[axis]

    vba = (
        VBABuilder("Cone")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set("Axis", axis)
        .set_expression("Bottomradius", bottom_radius)
        .set_expression("Topradius", top_radius)
        .set_expression("Xcenter", cx)
        .set_expression("Ycenter", cy)
        .set_expression("Zcenter", cz)
        .set_expression_pair(range_prop, args["range_min"], args["range_max"])
        .call("Create")
    )
    return vba.build()


def _build_sphere(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    radius = _radius_expression(args["radius"], "radius")
    cx = args.get("center_x", 0)
    cy = args.get("center_y", 0)
    cz = args.get("center_z", 0)
    segments = args.get("segments", 0)

    # Official CST Sphere API (see vba_cst / Online Help):
    #   .Axis, .CenterRadius, .TopRadius, .BottomRadius, .Center x,y,z, .Segments
    vba = (
        VBABuilder("Sphere")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set("Axis", "z")
        .set_expression("CenterRadius", radius)
        .set_number("TopRadius", 0)
        .set_number("BottomRadius", 0)
        .set_expression_triple("Center", cx, cy, cz)
        .set_number("Segments", segments)
        .call("Create")
    )
    return vba.build()


def _build_torus(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    axis = args["axis"]
    outer_radius = _radius_expression(args["outer_radius"], "outer_radius")
    inner_radius = _radius_expression(args["inner_radius"], "inner_radius")
    cx = args.get("center_x", 0)
    cy = args.get("center_y", 0)
    cz = args.get("center_z", 0)

    vba = (
        VBABuilder("Torus")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set("Axis", axis)
        .set_expression("OuterRadius", outer_radius)
        .set_expression("InnerRadius", inner_radius)
        .set_expression("Xcenter", cx)
        .set_expression("Ycenter", cy)
        .set_expression("Zcenter", cz)
        .call("Create")
    )
    return vba.build()


_OFFSET_KEYS = {"x": "x_offset", "y": "y_offset", "z": "z_offset"}
# Extrude object plane per axis: (Uvector, Vvector); the profile's (u, v)
# map to the same world axes as cst_create_polygon_extrude's Polygon3D points.
_EXTRUDE_PLANES = {
    "z": ((1, 0, 0), (0, 1, 0)),
    "x": ((0, 1, 0), (0, 0, 1)),
    "y": ((1, 0, 0), (0, 0, -1)),
}


def _num(value, field: str) -> float:
    """Coerce through vba_safety.vba_number (finite, non-bool) to a float."""
    return float(_vba_number(value, field))


def _profile_points(points, field: str) -> list[tuple[float | str, float | str]]:
    if not isinstance(points, (list, tuple)) or len(points) < 3:
        raise ValueError(f"{field} must contain at least 3 [x, y] points")
    out = []
    for i, pt in enumerate(points):
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError(f"{field}[{i}] must be an [x, y] pair")
        for value in pt:
            _format_expression(value)
        out.append(tuple(pt))
    return out


def _signed_area(points: list[tuple[float, float]]) -> float:
    return 0.5 * sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1])
    )


def _extrude_axis_offset_holes(args: dict):
    """Validate axis, base-plane offset and holes shared by both extrude tools."""
    axis = args.get("axis", "z")
    if axis not in _OFFSET_KEYS:
        raise ValueError("axis must be x, y, or z")
    for other_axis, key in _OFFSET_KEYS.items():
        value = args.get(key, 0)
        _format_expression(value)
        if other_axis != axis and (isinstance(value, str) or value != 0):
            raise ValueError(
                f"{key} only applies to axis='{other_axis}'; use {_OFFSET_KEYS[axis]} for axis='{axis}'"
            )
    offset = args.get(_OFFSET_KEYS[axis], 0)
    points = _normalise_numeric_profile(_profile_points(args["points"], "points"), "points")
    holes = []
    raw_holes = args.get("holes", [])
    match raw_holes:
        case list() | tuple():
            pass
        case _:
            raise ValueError("holes must be a list of point lists")
    for h, raw in enumerate(raw_holes):
        hole = _profile_points(raw, f"holes[{h}]")
        holes.append(_normalise_numeric_profile(hole, f"holes[{h}]"))
    return axis, offset, points, holes


def _symbolic_profile(points) -> bool:
    return any(isinstance(value, str) for pt in points for value in pt)


def _normalise_numeric_profile(points, field):
    if _symbolic_profile(points):
        return points
    # Retain the previous double arithmetic for orientation, without replacing
    # the original values that will be serialized into model history.
    area = _signed_area([(float(u), float(v)) for u, v in points])
    if area == 0:
        raise ValueError(f"{field} must enclose a non-zero area")
    return points[::-1] if area < 0 else points


def _extrude_direction(args):
    direction = args.get("extrude_direction", "up")
    if direction not in ("up", "down"):
        raise ValueError("extrude_direction must be 'up' or 'down'")
    return direction


def _negated_expression(value):
    _format_expression(value)
    return f"-({value})" if isinstance(value, str) else -value


def _evaluated_argument(value):
    """Only fixed syntax and shared serialized literals enter executable VBA."""
    literal = _format_expression(value)
    return f"Evaluate({literal})" if isinstance(value, str) else literal


def _set_evaluated(vba, prop, *values):
    """Explicit native evaluation for documented double arguments, in history."""
    if any(isinstance(value, str) for value in values):
        return vba.set_raw(prop, ", ".join(_evaluated_argument(value) for value in values))
    method = {1: vba.set_expression, 2: vba.set_expression_pair, 3: vba.set_expression_triple}
    return method[len(values)](prop, *values)


def _profile_area_checks(script, profiles):
    """Check every symbolic profile before geometry; return per-profile area names."""
    areas = []
    for index, points in enumerate(profiles):
        if not _symbolic_profile(points):
            areas.append(None)
            continue
        prefix = f"cstProfile{index}"
        area = prefix + "Area"
        last = len(points) - 1
        lines = [
            f"Dim {prefix}U({last}) As Double",
            f"Dim {prefix}V({last}) As Double",
            f"Dim {area} As Double",
            f"Dim {prefix}I As Long",
            f"Dim {prefix}J As Long",
        ]
        for i, (u, v) in enumerate(points):
            # Typed array assignments require doubles even for numeric literals.
            lines += [
                f"{prefix}U({i}) = Evaluate({_format_expression(u)})",
                f"{prefix}V({i}) = Evaluate({_format_expression(v)})",
            ]
        lines += [
            f"{area} = 0",
            f"For {prefix}I = 0 To {last}",
            f"  {prefix}J = ({prefix}I + 1) Mod {len(points)}",
            f"  {area} = {area} + {prefix}U({prefix}I) * {prefix}V({prefix}J) _",
            f"    - {prefix}U({prefix}J) * {prefix}V({prefix}I)",
            f"Next {prefix}I",
            f"{area} = {area} / 2",
            f'If {area} = 0 Then Err.Raise 5, "CST MCP extrusion", "Profile {index} has zero signed area"',
        ]
        script.add_raw("\n".join(lines))
        areas.append(area)
    return areas


def _subtract_block(component: str, name: str, tool_name: str) -> str:
    target = _vba_string_literal(f"{component}:{name}", "solid")
    tool = _vba_string_literal(f"{component}:{tool_name}", "solid")
    return f"Solid.Subtract {target}, {tool}"


def _extrude_block(
    name, component, material, height, axis, offset, points, direction
) -> VBABuilder:
    u_vec, v_vec = _EXTRUDE_PLANES[axis]
    if direction == "down":
        # Reverse U cross V, while preserving the world profile: (-V) * (-v).
        v_vec = tuple(-value for value in v_vec)
        points = [(u, _negated_expression(v)) for u, v in points]
    origin = {"z": (0, 0, offset), "x": (offset, 0, 0), "y": (0, offset, 0)}[axis]
    vba = (
        VBABuilder("Extrude")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set("Mode", "pointlist")
        .set_expression("Height", height)
    )
    _set_evaluated(vba, "Origin", *origin)
    vba.set_triple("Uvector", *u_vec).set_triple("Vvector", *v_vec)
    # First point, subsequent points as LineTo, closed back to the first point.
    _set_evaluated(vba, "Point", *points[0])
    for pt in points[1:]:
        _set_evaluated(vba, "LineTo", *pt)
    _set_evaluated(vba, "LineTo", *points[0])
    vba.call("Create")
    return vba


def _build_extrude(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    height = args["height"]
    _format_expression(height)
    axis, offset, points, holes = _extrude_axis_offset_holes(args)
    direction = _extrude_direction(args)
    script = VBAScript()
    _profile_area_checks(script, [points, *holes])
    if holes:
        script.add_comment(f"Extrude with {len(holes)} hole(s): {component}:{name}")
    script.add_block(
        _extrude_block(name, component, material, height, axis, offset, points, direction)
    )
    for h, hole in enumerate(holes):
        hole_name = validate_name(f"{name}_hole{h + 1}", "hole name")
        script.add_block(
            _extrude_block(hole_name, component, material, height, axis, offset, hole, direction)
        )
        script.add_raw(_subtract_block(component, name, hole_name))
    return script.build()


def _build_loft(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    profiles: list[list[list[float]]] = args["profiles"]

    script = VBAScript()
    script.add_comment(f"Loft: {component}:{name}")

    # Create each profile as a named curve
    for i, profile in enumerate(profiles):
        curve_name = f"{name}_profile{i}"
        curve_vba = (
            VBABuilder("Polygon")
            .call("Reset")
            .set("Name", curve_name)
            .set("Curve", f"{name}_curves")
        )
        for pt in profile:
            curve_vba.set_double("Point", pt[0], pt[1])
        # Close the polygon
        curve_vba.set_double("Point", profile[0][0], profile[0][1])
        curve_vba.call("Create")
        script.add_block(curve_vba)

    # Create the loft
    loft_vba = (
        VBABuilder("Loft")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
    )
    for i in range(len(profiles)):
        curve_name = f"{name}_profile{i}"
        loft_vba.set("AddCurve", f"{name}_curves:{curve_name}")
    loft_vba.call("Create")
    script.add_block(loft_vba)

    return script.build()


def _build_wire(args: dict) -> str:
    """Straight round solid conductor, using Cylinder and rigid transforms.

    Avoid the Wire-to-solid conversion, which stalled the CST 2026 live test.
    """
    import math

    from cst_mcp.tools.transforms import _build_rotate, _build_translate

    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    radius = validate_positive(args["radius"], "radius")
    start = [float(args[f"start_{axis}"]) for axis in "xyz"]
    delta = [float(args[f"end_{axis}"]) - start[i] for i, axis in enumerate("xyz")]
    length = math.hypot(*delta)
    if not math.isfinite(length) or length <= 0:
        raise ValueError("Wire endpoints must be finite and distinct")
    solid = f"{component}:{name}"
    code = [
        _build_cylinder(
            {
                "component": component,
                "name": name,
                "material": args.get("material", "PEC"),
                "axis": "z",
                "outer_radius": radius,
                "range_min": 0,
                "range_max": length,
            }
        )
    ]
    theta = math.degrees(math.acos(max(-1, min(1, delta[2] / length))))
    phi = math.degrees(math.atan2(delta[1], delta[0]))
    for axis, angle in (("y", theta), ("z", phi)):
        if abs(angle) > 1e-12:
            code.append(_build_rotate({"solid": solid, "axis": axis, "angle": angle}))
    code.append(_build_translate({"solid": solid, "dx": start[0], "dy": start[1], "dz": start[2]}))
    return "\n".join(code)


def _build_polygon3d(args: dict) -> str:
    name = validate_name(args["name"], "name")
    points: list[list[float | str]] = args["points"]

    vba = VBABuilder("Polygon3D").call("Reset").set("Name", name).set("Curve", "Curves")
    for pt in points:
        vba.set_expression_triple("Point", pt[0], pt[1], pt[2])
    vba.call("Create")
    return vba.build()


def _build_analytical_curve(args: dict) -> str:
    name = validate_name(args["name"], "name")

    vba = (
        VBABuilder("AnalyticalCurve")
        .call("Reset")
        .set("Name", name)
        .set("Curve", "Curves")
        .set("LawX", args["x_expr"])
        .set("LawY", args["y_expr"])
        .set("LawZ", args["z_expr"])
        .set_double("ParameterRange", args["t_min"], args["t_max"])
        .call("Create")
    )
    return vba.build()


def _build_face_from_curves(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    curve_names: list[str] = args["curve_names"]

    vba = VBABuilder("CoverCurve").call("Reset").set("Name", name).set("Component", component)
    for curve_name in curve_names:
        validate_name(curve_name, "curve_name")
        vba.set("AddCurve", curve_name)
    vba.call("Create")
    return vba.build()


def _build_ecylinder(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    axis = args["axis"]
    x_radius = _radius_expression(args["x_radius"], "x_radius")
    y_radius = _radius_expression(args["y_radius"], "y_radius")
    cx = args.get("center_x", 0)
    cy = args.get("center_y", 0)
    cz = args.get("center_z", 0)

    # Map axis to the correct CST VBA property names
    range_prop = {"x": "Xrange", "y": "Yrange", "z": "Zrange"}[axis]

    vba = (
        VBABuilder("ECylinder")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set("Axis", axis)
        .set_expression("XRadius", x_radius)
        .set_expression("YRadius", y_radius)
        .set_expression("Xcenter", cx)
        .set_expression("Ycenter", cy)
        .set_expression("Zcenter", cz)
        .set_expression_pair(range_prop, args["range_min"], args["range_max"])
        .call("Create")
    )
    return vba.build()


def _polygon_curve_extrude(
    script: VBAScript,
    name,
    curve,
    item,
    component,
    material,
    height,
    point_fn,
    points,
    direction,
    area,
) -> None:
    poly_vba = VBABuilder("Polygon3D").call("Reset").set("Name", item).set("Curve", curve)

    def polygon(ordered):
        vba = VBABuilder("Polygon3D")
        for pt in [*ordered, ordered[0]]:
            _set_evaluated(vba, "Point", *point_fn(pt))
        return vba.build()

    if area:
        # Reevaluate orientation at every reconstruction, independently for holes.
        comparison = "<" if direction == "up" else ">"
        script.add_raw(poly_vba.build())
        script.add_raw(
            f"If {area} {comparison} 0 Then\n{polygon(points[::-1])}\n"
            f"Else\n{polygon(points)}\nEnd If"
        )
        script.add_block(VBABuilder("Polygon3D").call("Create"))
    else:
        ordered = points if direction == "up" else points[::-1]
        for pt in [*ordered, ordered[0]]:
            _set_evaluated(poly_vba, "Point", *point_fn(pt))
        script.add_block(poly_vba.call("Create"))

    # Extrude the closed planar curve item into a solid (the curve item is consumed).
    extrude_vba = (
        VBABuilder("ExtrudeCurve")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
    )
    _set_evaluated(extrude_vba, "Thickness", height)
    extrude_vba = (
        extrude_vba.set_number("Twistangle", 0)
        .set_number("Taperangle", 0)
        .set("Curve", f"{curve}:{item}")
    )
    # The closed curve's plane sets the extrusion direction in CST.
    extrude_vba.call("Create")
    script.add_block(extrude_vba)


def _build_polygon_extrude(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = args.get("material", "PEC")
    height = args["height"]
    _format_expression(height)
    axis, offset, points, holes = _extrude_axis_offset_holes(args)

    def point(pt):
        if axis == "x":
            return (offset, pt[0], pt[1])
        if axis == "y":
            return (pt[0], offset, _negated_expression(pt[1]))
        return (pt[0], pt[1], offset)

    direction = _extrude_direction(args)

    curve = f"{name}_curves"
    script = VBAScript()
    areas = _profile_area_checks(script, [points, *holes])
    script.add_comment(f"Polygon extrude: {component}:{name} ({direction}, winding sets direction)")
    script.add_raw(f'Curve.NewCurve "{_q(curve, "name")}"')
    _polygon_curve_extrude(
        script,
        name,
        curve,
        f"{name}_profile",
        component,
        material,
        height,
        point,
        points,
        direction,
        areas[0],
    )
    for h, hole in enumerate(holes):
        hole_name = validate_name(f"{name}_hole{h + 1}", "hole name")
        _polygon_curve_extrude(
            script,
            hole_name,
            curve,
            f"{hole_name}_profile",
            component,
            material,
            height,
            point,
            hole,
            direction,
            areas[h + 1],
        )
        script.add_raw(_subtract_block(component, name, hole_name))
    return script.build()


def _polygon_extrusion_note(args: dict) -> dict:
    """Input-contract prediction only; never measured CST bounds."""
    axis = args.get("axis", "z")
    offset = args.get(_OFFSET_KEYS[axis], 0)
    height = args["height"]
    direction = _extrude_direction(args)
    offset_literal, height_literal = _format_expression(offset), _format_expression(height)
    note = {
        "axis": axis,
        "direction": direction,
        "source": "input contract prediction, not measured CST bounds",
        "note": "For positive height: up spans offset..offset+height; down spans offset-height..offset. Live CST validation is required.",
    }
    if not isinstance(offset, str) and not isinstance(height, str):
        import math

        end = float(offset) + (1 if direction == "up" else -1) * float(height)
        if math.isfinite(end):
            note.update(expected_range=sorted((float(offset), end)), numeric_range_evaluated=True)
            return note
    # Shared formatter validates numbers; retain caller strings exactly in metadata.
    offset_text = offset if isinstance(offset, str) else offset_literal[1:-1]
    height_text = height if isinstance(height, str) else height_literal[1:-1]
    operator = "+" if direction == "up" else "-"
    note.update(
        symbolic_endpoints={
            "base": offset_text,
            "end": f"({offset_text}) {operator} ({height_text})",
        },
        numeric_range_evaluated=False,
    )
    return note


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_HANDLERS: dict[str, Callable[[dict], str]] = {
    "cst_create_brick": _build_brick,
    "cst_create_cylinder": _build_cylinder,
    "cst_create_cone": _build_cone,
    "cst_create_sphere": _build_sphere,
    "cst_create_torus": _build_torus,
    "cst_create_extrude": _build_extrude,
    "cst_create_loft": _build_loft,
    "cst_create_wire": _build_wire,
    "cst_create_polygon3d": _build_polygon3d,
    "cst_create_analytical_curve": _build_analytical_curve,
    "cst_create_face_from_curves": _build_face_from_curves,
    "cst_create_ecylinder": _build_ecylinder,
    "cst_create_polygon_extrude": _build_polygon_extrude,
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


async def handle(name: str, arguments: dict, client: CSTClient) -> list[TextContent]:
    """Handle a geometry tool call.

    Generates VBA via VBABuilder, executes through the CSTClient, and
    returns the result wrapped in TextContent.
    """
    builder_fn = _HANDLERS.get(name)
    if builder_fn is None:
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": f"Unknown geometry tool: {name}",
                    }
                ),
            )
        ]

    try:
        vba_code = builder_fn(arguments)
        extrusion = None
        if name in {"cst_create_extrude", "cst_create_polygon_extrude"}:
            extrusion = _polygon_extrusion_note(arguments)
            json.dumps(extrusion, allow_nan=False)  # Prepare all metadata before mutation.
        result = client.execute_vba(vba_code)
        if (
            extrusion is not None
            and isinstance(result, dict)
            and result.get("status") in {"executed", "offline"}
            and result.get("execution_state") != "unknown"
        ):
            result = dict(result, extrusion=extrusion)
        return [TextContent(type="text", text=json.dumps(result))]
    except Exception as e:
        logging.getLogger(__name__).debug("Handled error in geometry.handle", exc_info=True)
        return [
            TextContent(
                type="text",
                text=json.dumps(
                    {
                        "status": "error",
                        "message": str(e),
                    }
                ),
            )
        ]


# Reject line breaks and non-numeric values in numeric slots before any VBA
# is generated from the arguments (generated VBA bypasses CST_ALLOW_RAW_VBA).
from cst_mcp.vba_safety import guard_handler as _guard_handler

handle = _guard_handler(TOOLS, handle)
