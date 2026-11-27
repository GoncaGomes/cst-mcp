"""Geometry creation tools for CST Studio Suite.

Provides MCP tools for creating 3D shapes, curves, extruded profiles and solids
along curves in CST Studio by generating VBA scripts via VBABuilder, plus a
read-only measured-bounds query (``cst_get_solid_bounds``) and deletes for solids
and components (``cst_delete_solid``, ``cst_delete_component``).
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable

from mcp.types import TextContent, Tool

from cst_mcp.cst_client import CSTClient
from cst_mcp.validators import (
    ValidationError,
    validate_name,
    validate_non_negative,
    validate_positive,
)
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


# 'Component:Solid', nested components joined by '/'. Quotes and further colons are
# never part of a CST shape name; line breaks are rejected separately.
_SOLID_NAME_PATTERN = r'^[^:"]+:[^:"]+$'
_SOLID_NAME_RE = re.compile(_SOLID_NAME_PATTERN)
_COMPONENT_NAME_PATTERN = r'^[^:"]+$'
_COMPONENT_NAME_RE = re.compile(_COMPONENT_NAME_PATTERN)
_MAX_BOUNDS_SOLIDS = 100


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
        description=(
            "Create a capped solid with LoftCurves between two or more explicit 3D polygon "
            "profiles. Coordinates accept finite JSON numbers or nonempty single-line CST "
            "expressions, preserved for parameter rebuilds. Legacy [x, y] points are rejected; "
            "supply [x, y, z] without inferred spacing. CST validates expressions and geometry."
        ),
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
                            "items": _expression_field("Profile coordinate"),
                            "minItems": 3,
                            "maxItems": 3,
                        },
                        "minItems": 3,
                    },
                    "minItems": 2,
                    "description": (
                        "At least two profiles of explicit [x, y, z] vertices, each coordinate "
                        "a finite JSON number or nonempty single-line CST expression. "
                        "Vertex order is preserved; an optional repeated endpoint is removed "
                        "before closing exactly once. At least three vertices must remain. "
                        "Separate auxiliary curve groups are created; existing groups are "
                        "not adopted or deleted. No path or inferred z coordinates."
                    ),
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
        description=(
            "Create a parametric analytical curve in CST Studio using expressions of parameter t. "
            "t_min and t_max accept finite numbers or CST expressions preserved in model history."
        ),
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
                "t_min": _expression_field("Parameter t minimum value"),
                "t_max": _expression_field("Parameter t maximum value"),
            },
            "required": ["name", "x_expr", "y_expr", "z_expr", "t_min", "t_max"],
        },
    ),
    # 11. Face from curves
    Tool(
        name="cst_create_face_from_curves",
        description=(
            "Create a planar sheet consisting of one face from exactly one qualified closed "
            "planar curve reference (curvegroup:curveitem) in CST Studio. CST reports native "
            "geometric errors. Connected curve items may be included by CST."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "component": {"type": "string", "description": "Component name"},
                "name": {"type": "string", "description": "Sheet/face name"},
                "curve_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 1,
                    "description": (
                        "Exactly one qualified curve reference, e.g. 'Curves:Rectangle'. "
                        "Independent profile lists are unsupported; the curve must be closed and planar."
                    ),
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
    # 14. Measured bounds readback (read-only)
    Tool(
        name="cst_get_solid_bounds",
        description=(
            "Read back where existing solids actually are: for each named solid, the "
            "axis-aligned bounding box in global coordinates and project length units, its "
            "material and (optionally) its volume. Use it after building to check offsets, "
            "contact between parts and layer placement instead of re-deriving coordinates. "
            "Read-only: a VBA query through output capture that adds no model-history entry. "
            "The box comes from CST's Solid.GetLooseBoundingBoxOfShape, which guarantees the "
            "whole solid lies inside it but may be larger than the solid (for example around "
            "curved faces); treat it as an outer bound. Unknown names are reported per solid. "
            "Refused while a solver is running or its state is unknown."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "solids": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 3,
                        "maxLength": 300,
                        "pattern": _SOLID_NAME_PATTERN,
                    },
                    "minItems": 1,
                    "maxItems": _MAX_BOUNDS_SOLIDS,
                    "description": (
                        "Full solid names as 'Component:Solid', e.g. 'substrate:board'. "
                        "Nested components use '/', e.g. 'antenna/feed:pin'."
                    ),
                },
                "include_volume": {
                    "type": "boolean",
                    "default": True,
                    "description": "Also return Solid.GetVolume for each solid.",
                },
            },
            "required": ["solids"],
            "additionalProperties": False,
        },
    ),
    # 15. Round wire along a curve
    Tool(
        name="cst_create_curve_wire",
        description=(
            "Make a round solid wire of given radius that follows an existing curve: helix, "
            "loop, wire spiral, bent feed or any other curved conductor. Build the curve first "
            "(cst_create_analytical_curve or cst_create_polygon3d, group 'Curves'), then pass "
            "it as 'Curves:<item>'. Replaces chains of straight cst_create_wire segments. "
            "The curve may be open or closed but must be one connected path. "
            "CST uses up the curve item: make the curve again to reuse it. Two history "
            "entries: a solid-model Wire (Curvewire), then Wire.ConvertToSolidShape with "
            "KeepWire False, so only the solid Component:name remains. If the conversion "
            "fails, the tool deletes the wire again. A missing curve is refused before any "
            "change. Any material. The response includes the new solid's measured bounds "
            "and volume (same fields as cst_get_solid_bounds)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "curve": {
                    "type": "string",
                    "pattern": _SOLID_NAME_PATTERN,
                    "description": "Curve item as 'curvegroup:curveitem', e.g. 'Curves:helix'",
                },
                "component": {"type": "string", "description": "Component of the new solid"},
                "name": {"type": "string", "description": "Name of the new solid"},
                "radius": _expression_field("Wire radius (> 0)"),
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "termination": {
                    "type": "string",
                    "enum": ["natural", "rounded", "extended"],
                    "default": "natural",
                    "description": "Shape of the wire ends (Wire.Termination)",
                },
            },
            "required": ["curve", "component", "name", "radius"],
            "additionalProperties": False,
        },
    ),
    # 16. Flat trace along a curve
    Tool(
        name="cst_create_trace_from_curve",
        description=(
            "Make a flat conductor trace of given width and thickness along an existing curve: "
            "printed spiral, meander line, curved microstrip or feed line. Build the curve "
            "first (cst_create_analytical_curve or cst_create_polygon3d, group 'Curves'), "
            "then pass it as 'Curves:<item>'. The trace is centred on the curve in width and "
            "grows by 'thickness' along +Z (local W) from the curve, so put the curve at the "
            "substrate's top Z. CST uses up the curve item: make the curve again to reuse "
            "it. One history entry (TraceFromCurve). A missing curve is refused before any "
            "change. The response includes the new solid's measured bounds and volume (same "
            "fields as cst_get_solid_bounds)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "curve": {
                    "type": "string",
                    "pattern": _SOLID_NAME_PATTERN,
                    "description": "Curve item as 'curvegroup:curveitem', e.g. 'Curves:spiral'",
                },
                "component": {"type": "string", "description": "Component of the new solid"},
                "name": {"type": "string", "description": "Name of the new solid"},
                "width": _expression_field("Trace width (> 0)"),
                "thickness": _expression_field("Trace thickness along +Z (> 0)"),
                "material": {"type": "string", "description": "Material name", "default": "PEC"},
                "round_start": {
                    "type": "boolean",
                    "default": False,
                    "description": "Round the first end of the trace",
                },
                "round_end": {
                    "type": "boolean",
                    "default": False,
                    "description": "Round the last end of the trace",
                },
                "gap_type": {
                    "type": "string",
                    "enum": ["rounded", "extended", "natural"],
                    "default": "natural",
                    "description": (
                        "Shape at the curve's corners (TraceFromCurve.GapType 0/1/2): rounded "
                        "like arcs, extended like lines, or natural like curve extensions"
                    ),
                },
            },
            "required": ["curve", "component", "name", "width", "thickness"],
            "additionalProperties": False,
        },
    ),
    # 17. Delete a solid
    Tool(
        name="cst_delete_solid",
        description=(
            "Delete one solid by its full name 'Component:Solid'. Use it to remove a wrong or "
            "temporary solid instead of subtracting it into another one (a subtract of solids "
            "that do not overlap changes nothing). A missing solid is rejected before CST is "
            "changed, with similar names. One history entry (Solid.Delete). For a solid made by "
            "cst_create_loft, the same entry also deletes the loft's profile curve groups, so "
            "the name can be used for a new loft. The response lists the solids that remain "
            "in the component. Refused while the project has results (call cst_delete_results "
            "first) or a solver is running."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "solid": {
                    "type": "string",
                    "minLength": 3,
                    "maxLength": 300,
                    "pattern": _SOLID_NAME_PATTERN,
                    "description": (
                        "Full solid name as 'Component:Solid', e.g. 'antenna:patch'. "
                        "Nested components use '/', e.g. 'antenna/feed:pin'."
                    ),
                },
            },
            "required": ["solid"],
            "additionalProperties": False,
        },
    ),
    # 18. Delete a component
    Tool(
        name="cst_delete_component",
        description=(
            "Delete a component with all its solids and sub-components (Component.Delete). "
            "A component without solids is rejected before CST is changed, with the existing "
            "components. One history entry, which also deletes the profile curve groups of "
            "lofts in the component. The response lists the deleted solids. Refused while the "
            "project has results (call cst_delete_results first) or a solver is running."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "component": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 300,
                    "pattern": _COMPONENT_NAME_PATTERN,
                    "description": (
                        "Component name, e.g. 'antenna'. Nested components use '/', "
                        "e.g. 'antenna/feed'."
                    ),
                },
            },
            "required": ["component"],
            "additionalProperties": False,
        },
    ),
]

# ---------------------------------------------------------------------------
# Solid bounds readback
# ---------------------------------------------------------------------------

_BOUNDS_NOTE = (
    "Loose bounding box from Solid.GetLooseBoundingBoxOfShape: the solid is guaranteed to lie "
    "inside it, but it may be larger than the solid, e.g. around curved faces."
)


def _solid_bounds_names(arguments: dict) -> list[str]:
    raw = arguments.get("solids")
    if not isinstance(raw, list) or not raw:
        raise ValueError("solids must be a non-empty list of 'Component:Solid' names")
    if len(raw) > _MAX_BOUNDS_SOLIDS:
        raise ValueError(f"solids accepts at most {_MAX_BOUNDS_SOLIDS} names")
    names: list[str] = []
    for i, name in enumerate(raw):
        if not isinstance(name, str) or not _SOLID_NAME_RE.fullmatch(name):
            raise ValueError(
                f"solids[{i}] must be a full solid name 'Component:Solid' "
                f"(nested components use '/'), got {name!r}"
            )
        _q(name, f"solids[{i}]")  # Rejects line breaks and NUL.
        if name not in names:
            names.append(name)
    return names


_BOX_VARS = ("mcpX1", "mcpX2", "mcpY1", "mcpY2", "mcpZ1", "mcpZ2")


def _solid_bounds_vba(names: list[str], include_volume: bool) -> str:
    """VBA printing ``<index><TAB><kind><TAB>...`` lines; one failure never aborts the rest.

    Every ``Debug.Print`` starts its own line so output capture redirects it to the file.
    """
    lines = [
        "Dim mcpFound As Boolean",
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
    for i, name in enumerate(names):
        literal = _vba_string_literal(name, f"solids[{i}]")
        key = f'"{i}" & vbTab & '
        box = " & vbTab & ".join(f"CStr({v})" for v in _BOX_VARS)
        lines += [
            "Err.Clear",
            "mcpFound = False",
            f"mcpFound = Solid.GetLooseBoundingBoxOfShape({literal}, {', '.join(_BOX_VARS)})",
            "If Err.Number <> 0 Then",
            f'  Debug.Print {key}"error" & vbTab & Err.Description',
            "ElseIf Not mcpFound Then",
            f'  Debug.Print {key}"missing"',
            "Else",
            f'  Debug.Print {key}"box" & vbTab & {box}',
            "  Err.Clear",
            f"  mcpText = Solid.GetMaterialNameForShape({literal})",
            "  If Err.Number = 0 Then",
            f'    Debug.Print {key}"material" & vbTab & mcpText',
            "  End If",
        ]
        if include_volume:
            lines += [
                "  Err.Clear",
                f"  mcpText = CStr(Solid.GetVolume({literal}))",
                "  If Err.Number = 0 Then",
                f'    Debug.Print {key}"volume" & vbTab & mcpText',
                "  Else",
                f'    Debug.Print {key}"volume_error" & vbTab & Err.Description',
                "  End If",
            ]
        lines.append("End If")
    return "\n".join(lines)


def _vba_float(text: str) -> float:
    # CStr follows the Windows locale, which may use a decimal comma.
    value = float(text.strip().replace(",", "."))
    if not math.isfinite(value):
        raise ValueError(f"non-finite value {text!r}")
    return value


def _parse_solid_bounds(output: str, names: list[str], include_volume: bool) -> dict:
    from cst_mcp.tools.drawing import _unit_from_factor

    unit: str | None = None
    unit_to_si: float | None = None
    rows: dict[int, dict[str, list[str]]] = {}
    for line in output.splitlines():
        parts = line.rstrip("\r").split("\t")
        if parts[0] == "unit" and len(parts) > 1:
            try:
                unit_to_si = _vba_float(parts[1])
                unit = _unit_from_factor(unit_to_si)
            except ValueError:
                pass
        elif parts[0].isdigit() and len(parts) >= 2:
            rows.setdefault(int(parts[0]), {})[parts[1]] = parts[2:]

    solids: list[dict] = []
    for i, name in enumerate(names):
        row = rows.get(i, {})
        entry: dict = {"name": name}
        if "box" in row:
            try:
                x1, x2, y1, y2, z1, z2 = (_vba_float(v) for v in row["box"][:6])
            except ValueError as exc:
                entry.update(found=True, error=f"Unreadable bounding box from CST: {exc}")
                solids.append(entry)
                continue
            entry.update(
                found=True,
                bounds={"x": [x1, x2], "y": [y1, y2], "z": [z1, z2]},
                size=[x2 - x1, y2 - y1, z2 - z1],
            )
            if "material" in row:
                entry["material"] = "\t".join(row["material"]).strip()
            if include_volume:
                try:
                    entry["volume"] = _vba_float(row["volume"][0])
                except (KeyError, IndexError, ValueError):
                    detail = "\t".join(row.get("volume_error", [])).strip()
                    entry["volume_error"] = detail or "CST returned no volume"
        elif "missing" in row:
            entry.update(
                found=False,
                error="Solid not found. Use the full 'Component:Solid' name "
                "(nested components use '/').",
            )
        else:
            detail = "\t".join(row.get("error", [])).strip()
            entry.update(found=False, error=detail or "CST returned no result for this solid")
        solids.append(entry)

    failed = [s["name"] for s in solids if "bounds" not in s]
    result: dict = {
        "status": "error" if failed else "ok",
        "unit": unit,
        "unit_to_si": unit_to_si,
        "bounds_kind": "loose",
        "note": _BOUNDS_NOTE,
        "solids": solids,
        "source": "VBA query via output capture (no model history)",
    }
    if include_volume:
        result["volume_unit"] = f"{unit}^3" if unit else None
    if failed:
        result["message"] = f"No bounds for: {', '.join(failed)}"
    return result


def _get_solid_bounds(arguments: dict, client: CSTClient) -> dict:
    names = _solid_bounds_names(arguments)
    include_volume = arguments.get("include_volume", True)
    if not isinstance(include_volume, bool):
        raise ValueError("include_volume must be a boolean")  # noqa: TRY004 - handler catches ValueError
    script = _solid_bounds_vba(names, include_volume)
    if not client.connected:
        return {
            "status": "offline",
            "vba": script,
            "message": "Not connected; bounds are only measured on a live CST project.",
        }
    if not client.has_project:
        return {"status": "error", "message": "No project open"}
    captured = client.capture_vba_output(script)
    if captured.get("status") != "ok":
        return captured
    return _parse_solid_bounds(str(captured.get("output", "")), names, include_volume)


# ---------------------------------------------------------------------------
# Solids along a curve (curve wire, trace)
# ---------------------------------------------------------------------------

_GAP_TYPES = {"rounded": 0, "extended": 1, "natural": 2}
_TERMINATIONS = ("natural", "rounded", "extended")
_CURVE_CONSUMED_NOTE = (
    "CST used up the curve item; create the curve again before building another solid on it."
)


def _curve_reference(value) -> str:
    if not isinstance(value, str) or value.count(":") != 1:
        raise ValueError("curve must be 'curvegroup:curveitem', e.g. 'Curves:helix'")
    group, item = value.split(":")
    validate_name(group, "curve group")
    validate_name(item, "curve item")
    return value


def _positive_expression(value, field: str) -> float | str:
    if value is None:
        raise ValueError(f"{field} is required")
    return _radius_expression(value, field)


def _curve_wire_blocks(args: dict) -> dict:
    """The wire and conversion blocks, plus the rollback that deletes the wire."""
    curve = _curve_reference(args.get("curve"))
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    radius = _positive_expression(args.get("radius"), "radius")
    material = args.get("material", "PEC")
    termination = args.get("termination", "natural")
    if termination not in _TERMINATIONS:
        raise ValueError(f"termination must be one of {', '.join(_TERMINATIONS)}")
    wire = (
        VBABuilder("Wire")
        .call("Reset")
        .set("Name", name)
        .set("Type", "Curvewire")
        .set("Curve", curve)
        .set_expression("Radius", radius)
        # CST 2025 refuses to convert a thin wire; the solid wire model converts exactly.
        .set_bool("SolidWireModel", True)
        .set("Material", material)
        .set("Termination", termination)
        .set_bool("AdvancedChainSelection", True)
        .call("Add")
        .build()
    )
    convert = (
        VBABuilder("Wire")
        .call("Reset")
        .set("SolidName", f"{component}:{name}")
        .set("Name", name)
        .set("Material", material)
        .set_bool("KeepWire", False)
        .call("ConvertToSolidShape")
        .build()
    )
    rollback = f"Wire.Delete {_vba_string_literal(name, 'name')}"
    return {
        "curve": curve,
        "solid": f"{component}:{name}",
        "blocks": [wire, convert],
        "rollback": rollback,
    }


def _trace_blocks(args: dict) -> dict:
    curve = _curve_reference(args.get("curve"))
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    width = _positive_expression(args.get("width"), "width")
    thickness = _positive_expression(args.get("thickness"), "thickness")
    gap_type = args.get("gap_type", "natural")
    if gap_type not in _GAP_TYPES:
        raise ValueError(f"gap_type must be one of {', '.join(_GAP_TYPES)}")
    flags = {}
    for field in ("round_start", "round_end"):
        flags[field] = args.get(field, False)
        if not isinstance(flags[field], bool):
            raise ValueError(f"{field} must be a boolean")  # noqa: TRY004 - handler catches ValueError
    trace = (
        VBABuilder("TraceFromCurve")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", args.get("material", "PEC"))
        .set("Curve", curve)
        .set_expression("Thickness", thickness)
        .set_expression("Width", width)
        .set_bool("RoundStart", flags["round_start"])
        .set_bool("RoundEnd", flags["round_end"])
        .set_number("GapType", _GAP_TYPES[gap_type])
        .call("Create")
        .build()
    )
    return {"curve": curve, "solid": f"{component}:{name}", "blocks": [trace]}


def _curve_check_vba(curve: str) -> str:
    literal = _vba_string_literal(curve, "curve")
    return "\n".join(
        [
            "Dim mcpPoints As Long",
            "On Error Resume Next",
            "Err.Clear",
            f"mcpPoints = Curve.GetNumberOfPoints({literal})",
            "If Err.Number <> 0 Then",
            '  Debug.Print "error" & vbTab & Err.Description',
            "Else",
            '  Debug.Print "points" & vbTab & CStr(mcpPoints)',
            "End If",
        ]
    )


def _check_curve(client: CSTClient, curve: str) -> dict | None:
    """None when the curve item exists; otherwise the refusal (nothing was changed)."""
    captured = client.capture_vba_output(_curve_check_vba(curve))
    if captured.get("status") != "ok":
        return {**captured, "stage": "curve_check", "project_changed": False}
    for line in str(captured.get("output", "")).splitlines():
        kind, _, detail = line.rstrip("\r").partition("\t")
        if kind == "points":
            return None
        if kind == "error":
            return {
                "status": "error",
                "code": "curve_not_found",
                "message": (
                    f"Curve item {curve!r} not found ({detail.strip() or 'no detail'}). "
                    "Pass 'curvegroup:curveitem'; curves from cst_create_analytical_curve and "
                    "cst_create_polygon3d are in group 'Curves'. A curve that was already "
                    "used for a solid is gone."
                ),
                "project_changed": False,
            }
    return {
        "status": "error",
        "message": "CST returned no result for the curve check",
        "stage": "curve_check",
        "project_changed": False,
    }


def _measure(client: CSTClient, solid: str) -> dict:
    captured = client.capture_vba_output(_solid_bounds_vba([solid], True))
    if captured.get("status") != "ok":
        return {"status": "error", "message": captured.get("message") or "Bounds query failed"}
    return _parse_solid_bounds(str(captured.get("output", "")), [solid], True)


def _build_along_curve(name: str, arguments: dict, client: CSTClient) -> dict:
    plan = (
        _curve_wire_blocks(arguments)
        if name == "cst_create_curve_wire"
        else _trace_blocks(arguments)
    )
    blocks = plan["blocks"]
    if not client.connected or not client.has_project:
        return {
            "status": "offline",
            "vba": "\n".join(blocks),
            "history_blocks": blocks,
            "solid": plan["solid"],
            "message": "VBA generated; run each block as its own history entry, in order.",
        }
    refused = _check_curve(client, plan["curve"])
    if refused is not None:
        return refused

    labels: list[str] = []
    for stage, block in zip(("create", "convert"), blocks):
        result = client.execute_vba(block)
        if result.get("status") == "executed":
            labels.append(result.get("label"))
            continue
        result = {**result, "stage": stage, "solid": plan["solid"], "history_labels": labels}
        if result.get("status") == "timeout" or result.get("execution_state") == "unknown":
            return result  # No further calls while CST's state is uncertain.
        if stage == "create":
            # A failed history command adds no entry and leaves the curve in place.
            return {**result, "project_changed": False}
        # The wire exists and has used up the curve; delete it so no stray wire remains.
        cleanup = client.execute_vba(plan["rollback"])
        removed = cleanup.get("status") == "executed"
        return {
            **result,
            "wire_removed": removed,
            "rollback": {k: cleanup.get(k) for k in ("status", "label", "message") if k in cleanup},
            "curve_consumed": True,
            "note": (
                "The wire was created but its conversion failed. "
                + (
                    "The tool deleted the wire; the history keeps the wire and its deletion. "
                    if removed
                    else "Deleting the wire also failed; it is still in the project. "
                )
                + _CURVE_CONSUMED_NOTE
            ),
        }
    return {
        "status": "executed",
        "solid": plan["solid"],
        "curve": plan["curve"],
        "curve_consumed": True,
        "history_labels": labels,
        "note": _CURVE_CONSUMED_NOTE,
        "measured": _measure(client, plan["solid"]),
    }


# ---------------------------------------------------------------------------
# Deleting solids and components
# ---------------------------------------------------------------------------

# Read-only: every shape name, then every curve group name. A failed name read
# skips its Debug.Print, so a count mismatch marks that list as unreadable.
_INVENTORY_LINES = (
    "Dim mcpN As Long, mcpI As Long",
    "On Error Resume Next",
    "Err.Clear",
    "mcpN = Solid.GetNumberOfShapes()",
    "If Err.Number <> 0 Then",
    '  Debug.Print "solids_error" & vbTab & Err.Description',
    "Else",
    '  Debug.Print "solids" & vbTab & CStr(mcpN)',
    "  For mcpI = 0 To mcpN - 1",
    "    Err.Clear",
    '    Debug.Print "solid" & vbTab & Solid.GetNameOfShapeFromIndex(mcpI)',
    "  Next mcpI",
    "End If",
    "Err.Clear",
    'mcpN = Curve.StartCurveNameIteration("all")',
    "If Err.Number <> 0 Then",
    '  Debug.Print "curves_error" & vbTab & Err.Description',
    "Else",
    '  Debug.Print "curves" & vbTab & CStr(mcpN)',
    "  For mcpI = 1 To mcpN",
    "    Err.Clear",
    '    Debug.Print "curve" & vbTab & Curve.GetNextCurveName()',
    "  Next mcpI",
    "End If",
)
_INVENTORY_VBA = "\n".join(_INVENTORY_LINES)


def _read_inventory(client: CSTClient) -> dict:
    """``{"status": "ok", "solids": [...] | None, "curves": [...] | None}`` or the failure.

    A list is None when CST could not report it completely.
    """
    captured = client.capture_vba_output(_INVENTORY_VBA)
    if captured.get("status") != "ok":
        return captured
    counts: dict[str, int | None] = {"solid": None, "curve": None}
    names: dict[str, list[str]] = {"solid": [], "curve": []}
    for line in str(captured.get("output", "")).splitlines():
        kind, _, value = line.rstrip("\r").partition("\t")
        if kind in ("solids", "curves") and value.strip().isdigit():
            counts[kind[:-1]] = int(value.strip())
        elif kind in ("solid", "curve") and value.strip():
            names[kind].append(value.strip())
    result: dict = {"status": "ok"}
    for kind in ("solid", "curve"):
        complete = counts[kind] is not None and counts[kind] == len(names[kind])
        result[f"{kind}s"] = names[kind] if complete else None
    return result


def _split_solid(solid: str) -> tuple[str, str]:
    component, _, name = solid.rpartition(":")
    return component, name


def _in_component(solid: str, component: str) -> bool:
    """True for solids of the component or of one of its sub-components."""
    owner = _split_solid(solid)[0]
    return owner == component or owner.startswith(component + "/")


def _loft_groups_of(solids: list[str], curves: list[str]) -> list[str]:
    """Curve groups that ``cst_create_loft`` made for any of ``solids``."""
    patterns = [
        re.compile(re.escape(_loft_group_prefix(*_split_solid(s))) + r"\d+", re.IGNORECASE)
        for s in solids
    ]
    return [c for c in curves if any(p.fullmatch(c) for p in patterns)]


def _similar_solids(solid: str, solids: list[str]) -> list[str]:
    import difflib

    short = _split_solid(solid)[1].lower()
    similar = [
        s
        for s in solids
        if s.lower() == solid.lower()
        or _split_solid(s)[1].lower() == short
        or short in _split_solid(s)[1].lower()
    ]
    for s in difflib.get_close_matches(solid, solids, n=5, cutoff=0.6):
        if s not in similar:
            similar.append(s)
    return similar[:5]


def _delete_script(head: str, groups: list[str]) -> str:
    lines = [head]
    lines += [f"Curve.DeleteCurve {_vba_string_literal(g, 'curve group')}" for g in groups]
    return "\n".join(lines)


def _not_live(client: CSTClient, script: str) -> dict | None:
    if not client.connected:
        return {
            "status": "offline",
            "vba": script,
            "message": (
                "Not connected; nothing was deleted. Loft curve groups are only found on a "
                "live project, so this VBA deletes the solids only."
            ),
        }
    if not client.has_project:
        return {"status": "error", "message": "No project open"}
    return None


def _delete_and_verify(
    client: CSTClient, script: str, targets: list[str], groups: list[str], result: dict
) -> dict:
    """Run the delete entry, then read back that ``targets`` and ``groups`` are gone."""
    executed = client.execute_vba(script)
    result = {**executed, **result}
    if executed.get("status") != "executed":
        return result  # No follow-up calls after a failed or uncertain mutation.
    after = _read_inventory(client)
    solids_after = after.get("solids") if after.get("status") == "ok" else None
    if solids_after is None:
        result["action"] = "deleted"
        result["warning"] = (
            "CST ran the delete, but the solids could not be read back; "
            "check them with cst_project_tree."
        )
        return result
    left = [s for s in targets if s in solids_after]
    if left:
        result["status"] = "error"
        result["still_present"] = left
        result["message"] = (
            f"CST ran the delete, but {', '.join(left)} still exist; "
            "check them with cst_project_tree."
        )
        return result
    result["action"] = "deleted"
    curves_after = after.get("curves")
    if groups and curves_after is not None:
        stale = [g for g in groups if g in curves_after]
        if stale:
            result["warning"] = f"Loft curve groups still present: {', '.join(stale)}."
    result["_solids_after"] = solids_after
    return result


def _delete_solid(arguments: dict, client: CSTClient) -> dict:
    solid = arguments.get("solid")
    if not isinstance(solid, str) or not _SOLID_NAME_RE.fullmatch(solid):
        raise ValueError(
            "solid must be a full solid name 'Component:Solid' (nested components use '/'), "
            f"got {solid!r}"
        )
    head = f"Solid.Delete {_vba_string_literal(solid, 'solid')}"
    refused = _not_live(client, head)
    if refused is not None:
        return {**refused, "solid": solid}

    before = _read_inventory(client)
    if before.get("status") != "ok":
        return {**before, "stage": "solid_check", "project_changed": False}
    solids = before["solids"]
    if solids is None:
        return {
            "status": "error",
            "solid": solid,
            "message": "CST did not report the solid list; nothing was changed.",
            "project_changed": False,
        }
    component = _split_solid(solid)[0]
    if solid not in solids:
        similar = _similar_solids(solid, solids)
        hint = f" Similar: {', '.join(similar)}." if similar else ""
        return {
            "status": "error",
            "code": "solid_not_found",
            "solid": solid,
            "similar": similar,
            "component_solids": [s for s in solids if _split_solid(s)[0] == component],
            "message": f"Solid {solid!r} does not exist.{hint} Nothing was changed in CST.",
            "project_changed": False,
        }

    curves = before["curves"]
    groups = _loft_groups_of([solid], curves or [])
    result = _delete_and_verify(
        client,
        _delete_script(head, groups),
        [solid],
        groups,
        {"solid": solid, "curve_groups_deleted": groups},
    )
    solids_after = result.pop("_solids_after", None)
    if result.get("action") != "deleted":
        result.pop("curve_groups_deleted", None)
        return result
    if solids_after is not None:
        result["remaining_solids"] = [s for s in solids_after if _split_solid(s)[0] == component]
    if curves is None:
        result["note"] = "The curve list could not be read, so loft curve groups were not checked."
    return result


def _delete_component(arguments: dict, client: CSTClient) -> dict:
    component = arguments.get("component")
    if not isinstance(component, str) or not _COMPONENT_NAME_RE.fullmatch(component):
        raise ValueError(f"component must be a component name without ':', got {component!r}")
    head = f"Component.Delete {_vba_string_literal(component, 'component')}"
    refused = _not_live(client, head)
    if refused is not None:
        return {**refused, "component": component}

    before = _read_inventory(client)
    if before.get("status") != "ok":
        return {**before, "stage": "component_check", "project_changed": False}
    solids = before["solids"]
    if solids is None:
        return {
            "status": "error",
            "component": component,
            "message": "CST did not report the solid list; nothing was changed.",
            "project_changed": False,
        }
    targets = [s for s in solids if _in_component(s, component)]
    if not targets:
        existing = sorted({_split_solid(s)[0] for s in solids})
        listed = ", ".join(existing) or "none"
        return {
            "status": "error",
            "code": "component_not_found",
            "component": component,
            "existing_components": existing,
            "message": (
                f"Component {component!r} has no solids (components with solids: {listed}). "
                "Nothing was changed in CST."
            ),
            "project_changed": False,
        }

    curves = before["curves"]
    groups = _loft_groups_of(targets, curves or [])
    result = _delete_and_verify(
        client,
        _delete_script(head, groups),
        targets,
        groups,
        {"component": component, "deleted_solids": targets, "curve_groups_deleted": groups},
    )
    result.pop("_solids_after", None)
    if result.get("action") != "deleted":
        result.pop("deleted_solids", None)
        result.pop("curve_groups_deleted", None)
    elif curves is None:
        result["note"] = "The curve list could not be read, so loft curve groups were not checked."
    return result


_DELETE_HANDLERS: dict[str, Callable[[dict, CSTClient], dict]] = {
    "cst_delete_solid": _delete_solid,
    "cst_delete_component": _delete_component,
}


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


def _loft_group_prefix(component: str, name: str) -> str:
    """Start of every profile curve group name of the loft ``component:name``.

    The groups are ``<prefix><i>`` for profile index ``i``; the digest of the full
    solid name keeps lofts with the same leading 40 characters apart.
    """
    import hashlib

    digest = hashlib.sha256(f"{component}:{name}".encode()).hexdigest()[:16]
    return f"{name[:40]}_loft_{digest}_p"


def _build_loft(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    material = validate_name(args.get("material", "PEC"), "material")
    profiles = args["profiles"]
    if not isinstance(profiles, list) or len(profiles) < 2:
        raise ValueError("profiles must contain at least two explicit 3D profiles")

    group_prefix = _loft_group_prefix(component, name)
    prepared = []
    for i, profile in enumerate(profiles):
        if not isinstance(profile, list) or len(profile) < 3:
            raise ValueError(f"profiles[{i}] must contain at least three [x, y, z] vertices")
        for j, point in enumerate(profile):
            if isinstance(point, list) and len(point) == 2:
                raise ValueError(
                    f"profiles[{i}][{j}]: legacy [x, y] points are unsupported; "
                    "explicit 3D coordinates [x, y, z] are required"
                )
            if not isinstance(point, list) or len(point) != 3:
                raise ValueError(f"profiles[{i}][{j}] must contain exactly three coordinates")
            for coordinate in point:
                _format_expression(coordinate)
        points = list(profile)
        while len(points) > 1 and points[-1] == points[0]:
            points.pop()
        if len(points) < 3:
            raise ValueError(f"profiles[{i}] requires at least three vertices without the endpoint")
        group = validate_name(f"{group_prefix}{i}", "loft curve group")
        item = validate_name(f"{name[:40]}_profile{i}", "loft curve item")
        prepared.append((group, item, points))

    script = VBAScript()
    script.add_comment(f"Loft: {component}:{name}")
    # Reject collisions before creating any profile, including empty existing groups.
    script.add_raw(
        "Dim loftCurveCount As Long, loftCurveIndex As Long, loftCurveName As String\n"
        'loftCurveCount = Curve.StartCurveNameIteration("all")\n'
        "For loftCurveIndex = 1 To loftCurveCount\n"
        "  loftCurveName = Curve.GetNextCurveName()\n"
        + "\n".join(
            f"  If StrComp(loftCurveName, {_vba_string_literal(group)}, vbTextCompare) = 0 Then "
            'Err.Raise 513, "cst_create_loft", "Auxiliary loft curve group already exists"'
            for group, _, _ in prepared
        )
        + "\nNext loftCurveIndex"
    )

    for group, item, points in prepared:
        script.add_block(VBABuilder("Curve").call_with_args("NewCurve", group))
        curve_vba = VBABuilder("Polygon3D").call("Reset").set("Name", item).set("Curve", group)
        for point in [*points, points[0]]:
            curve_vba.set_expression_triple("Point", *point)
        curve_vba.call("Create")
        script.add_block(curve_vba)

    loft_vba = (
        VBABuilder("LoftCurves")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Material", material)
        .set_bool("Solid", True)
    )
    for group, item, _ in prepared:
        loft_vba.call_with_args("AddCurve", f"{group}:{item}")
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
        .set_expression_pair("ParameterRange", args["t_min"], args["t_max"])
        .call("Create")
    )
    return vba.build()


def _build_face_from_curves(args: dict) -> str:
    component = validate_name(args["component"], "component")
    name = validate_name(args["name"], "name")
    curve_names = args["curve_names"]
    if not isinstance(curve_names, list) or len(curve_names) != 1:
        raise ValueError("curve_names must contain exactly one qualified curve reference")
    reference = curve_names[0]
    if not isinstance(reference, str) or reference.count(":") != 1:
        raise ValueError("Curve reference must use 'curvegroup:curveitem' format")
    group, item = reference.split(":")
    validate_name(group, "curve group")
    validate_name(item, "curve item")

    return (
        VBABuilder("CoverCurve")
        .call("Reset")
        .set("Name", name)
        .set("Component", component)
        .set("Curve", reference)
        .call("Create")
        .build()
    )


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
    if name == "cst_get_solid_bounds":
        try:
            result = _get_solid_bounds(arguments, client)
        except ValueError as e:
            result = {"status": "error", "message": str(e)}
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if name in _DELETE_HANDLERS:
        try:
            result = _DELETE_HANDLERS[name](arguments, client)
        except (ValueError, ValidationError) as e:
            result = {"status": "error", "message": str(e)}
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    if name in {"cst_create_curve_wire", "cst_create_trace_from_curve"}:
        try:
            result = _build_along_curve(name, arguments, client)
        except (ValueError, ValidationError) as e:
            result = {"status": "error", "message": str(e)}
        return [TextContent(type="text", text=json.dumps(result))]

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
