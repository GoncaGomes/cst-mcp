"""Selected primitive contracts through public handlers, without CST."""

from copy import deepcopy

import pytest
from test_brick_expressions import RecordingClient, call

from cst_mcp.tools import geometry
from cst_mcp.vba_builder import VBABuilder

COMMON = {"component": "c", "name": "s", "axis": "z", "range_min": 0, "range_max": 6}
CASES = {
    "cylinder": (
        {**COMMON, "outer_radius": 2},
        (
            "outer_radius",
            "inner_radius",
            "center_x",
            "center_y",
            "center_z",
            "range_min",
            "range_max",
        ),
    ),
    "cone": (
        {**COMMON, "bottom_radius": 2, "top_radius": 1},
        (
            "bottom_radius",
            "top_radius",
            "center_x",
            "center_y",
            "center_z",
            "range_min",
            "range_max",
        ),
    ),
    "sphere": (
        {"component": "c", "name": "s", "radius": 2},
        ("radius", "center_x", "center_y", "center_z"),
    ),
    "ecylinder": (
        {**COMMON, "x_radius": 2, "y_radius": 1},
        ("x_radius", "y_radius", "center_x", "center_y", "center_z", "range_min", "range_max"),
    ),
    "torus": (
        {"component": "c", "name": "s", "axis": "z", "outer_radius": 6, "inner_radius": 4},
        ("outer_radius", "inner_radius", "center_x", "center_y", "center_z"),
    ),
    "polygon3d": ({"name": "p", "points": [[0, 0, 0], [2, 3, 0], [0, 0, 0]]}, ()),
}


def replace_coordinate(shape, field, value):
    args = deepcopy(CASES[shape][0])
    if shape == "polygon3d":
        args["points"][1][field] = value
    else:
        args[field] = value
    return args


FIELDS = [
    (shape, field)
    for shape, (_, fields) in CASES.items()
    for field in (fields if fields else range(3))
]


@pytest.mark.parametrize("shape,field", FIELDS)
def test_each_expression_coordinate_preserved_in_mixed_public_call(shape, field):
    client = RecordingClient()
    expression = " (PGeom_R + 2)/3 "
    assert (
        call(replace_coordinate(shape, field, expression), client, f"cst_create_{shape}")["status"]
        == "executed"
    )
    assert f'"{expression}"' in client.codes[0]
    assert '"0"' in client.codes[0]  # Numeric values/defaults coexist with expressions.


@pytest.mark.parametrize("shape", CASES)
def test_numeric_calls_keep_defaults_and_formatting(shape):
    client = RecordingClient()
    args = CASES[shape][0]
    assert call(args, client, f"cst_create_{shape}")["status"] == "executed"
    code = client.codes[0]
    if shape == "polygon3d":
        assert '.Point "2", "3", "0"' in code
    elif shape == "sphere":
        assert '.CenterRadius "2"' in code and '.Center "0", "0", "0"' in code
        assert '.Segments "0"' in code
    elif shape == "torus":
        assert '.OuterRadius "6"' in code and '.InnerRadius "4"' in code
    else:
        assert '.Zrange "0", "6"' in code and '.Xcenter "0"' in code


@pytest.mark.parametrize("shape", CASES)
@pytest.mark.parametrize(
    "bad",
    [True, None, [], {}, float("inf"), float("nan"), "", " \t ", "x\u2028y", "x\x01y", "x\x7fy"],
)
def test_representative_invalid_coordinates_stop_before_execution(shape, bad):
    field = CASES[shape][1][0] if CASES[shape][1] else 0
    client = RecordingClient()
    assert (
        call(replace_coordinate(shape, field, bad), client, f"cst_create_{shape}")["status"]
        == "error"
    )
    assert client.codes == []


@pytest.mark.parametrize(
    "shape,field,zero_ok",
    [
        ("cylinder", "outer_radius", False),
        ("cylinder", "inner_radius", True),
        ("cone", "bottom_radius", True),
        ("cone", "top_radius", True),
        ("sphere", "radius", False),
        ("ecylinder", "x_radius", False),
        ("ecylinder", "y_radius", False),
        ("torus", "outer_radius", False),
        ("torus", "inner_radius", False),
    ],
)
def test_original_numeric_radius_sign_contracts(shape, field, zero_ok):
    for value, status in ((-1, "error"), (0, "executed" if zero_ok else "error")):
        assert (
            call(replace_coordinate(shape, field, value), RecordingClient(), f"cst_create_{shape}")[
                "status"
            ]
            == status
        )


def test_only_selected_dimension_schema_leaves_expand():
    tools = {t.name: t.model_dump(by_alias=True)["inputSchema"] for t in geometry.TOOLS}
    for shape, (_, fields) in CASES.items():
        props = tools[f"cst_create_{shape}"]["properties"]
        leaves = [props[f] for f in fields] if fields else [props["points"]["items"]["items"]]
        assert all(s["type"] == ["number", "string"] and s["minLength"] == 1 for s in leaves)
        assert props["name"]["type"] == "string"
        if "axis" in props:
            assert props["axis"]["enum"] == ["x", "y", "z"]
        if "material" in props:
            assert props["material"]["type"] == "string" and props["material"]["default"] == "PEC"
    assert tools["cst_create_sphere"]["properties"]["segments"]["type"] == "integer"
    for shape in ("extrude", "polygon_extrude"):
        props = tools[f"cst_create_{shape}"]["properties"]
        leaves = [
            props["height"],
            props["points"]["items"]["items"],
            props["holes"]["items"]["items"]["items"],
            *(props[f"{axis}_offset"] for axis in "xyz"),
        ]
        assert all(s["type"] == ["number", "string"] and s["minLength"] == 1 for s in leaves)
    props = tools["cst_create_analytical_curve"]["properties"]
    for field in ("t_min", "t_max"):
        assert props[field]["type"] == ["number", "string"] and props[field]["minLength"] == 1
    assert all(props[field]["type"] == "string" for field in ("x_expr", "y_expr", "z_expr"))
    assert tools["cst_create_wire"]["properties"]["radius"]["type"] == "number"


CURVE_ARGS = {"name": "Line", "x_expr": "t", "y_expr": "0", "z_expr": "0"}


@pytest.mark.parametrize(
    "lower,upper,literals",
    [
        (-1.25, 1e-11, '"-1.25", "1e-11"'),
        (
            " PCurve_Start ",
            "PCurve_Start+PCurve_Length",
            '" PCurve_Start ", "PCurve_Start+PCurve_Length"',
        ),
        (2, "PCurve_Start+PCurve_Length", '"2", "PCurve_Start+PCurve_Length"'),
        ("PCurve_Start", 7, '"PCurve_Start", "7"'),
        (7, -2, '"7", "-2"'),  # Native range semantics, without new ordering rules.
    ],
)
def test_analytical_curve_bound_serialization(lower, upper, literals):
    client = RecordingClient()
    args = dict(CURVE_ARGS, t_min=lower, t_max=upper)
    assert call(args, client, "cst_create_analytical_curve")["status"] == "executed"
    assert client.codes == [
        (
            'With AnalyticalCurve\n  .Reset\n  .Name "Line"\n  .Curve "Curves"\n'
            '  .LawX "t"\n  .LawY "0"\n  .LawZ "0"\n'
            f"  .ParameterRange {literals}\n  .Create\nEnd With"
        )
    ]


@pytest.mark.parametrize("bound", ["t_min", "t_max"])
@pytest.mark.parametrize("bad", [True, None, {}, float("inf"), " ", "x\n.Create", "x\x01y"])
def test_invalid_analytical_bounds_rejected_before_execution(bound, bad):
    client = RecordingClient()
    args = {**CURVE_ARGS, "t_min": "PCurve_Start", "t_max": 7, bound: bad}
    assert call(args, client, "cst_create_analytical_curve")["status"] == "error"
    assert client.codes == []


@pytest.mark.parametrize(
    "shape,field,bad",
    [
        ("sphere", "segments", "PGeom_R"),
        ("sphere", "segments", True),
        ("cylinder", "axis", "PGeom_R"),
        ("cone", "name", "bad:name"),
        ("torus", "material", "PEC\n.Create"),
    ],
)
def test_unchanged_field_restrictions(shape, field, bad):
    client = RecordingClient()
    assert call({**CASES[shape][0], field: bad}, client, f"cst_create_{shape}")["status"] == "error"
    assert not client.codes


@pytest.mark.parametrize("value", [0, -2, 1.234567890123, 1e-11, 1e20])
def test_new_expression_helpers_match_numeric_helpers(value):
    assert (
        VBABuilder("X").set_expression("P", value).build()
        == VBABuilder("X").set_number("P", value).build()
    )
    assert (
        VBABuilder("X").set_expression_triple("P", value, 0, value).build()
        == VBABuilder("X").set_triple("P", value, 0, value).build()
    )


def test_new_expression_helpers_quote_and_reject_invalid_values():
    assert '.P "a""b", "0", "R/2"' in (
        VBABuilder("X").set_expression_triple("P", 'a"b', 0, "R/2").build()
    )
    for bad in (True, None, "", "x\ny"):
        with pytest.raises(ValueError):
            VBABuilder("X").set_expression("P", bad)
        with pytest.raises(ValueError):
            VBABuilder("X").set_expression_triple("P", 0, bad, 0)
    with pytest.raises((TypeError, ValueError)):
        VBABuilder("X").set_triple("P", "R", 0, 0)
