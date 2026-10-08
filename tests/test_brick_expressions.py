"""Brick expression serialization and public-handler checks; no CST contacted."""

import asyncio
import json

import pytest
from test_vba_injection import assert_payload_contained

from cst_mcp.tools import geometry
from cst_mcp.vba_builder import VBABuilder, _format_expression
from cst_mcp.vba_safety import check_arguments

BOUNDS = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
ARGS = {
    "component": "ParameterTest",
    "name": "ParametricBrick",
    "material": "PEC",
    "x_min": 200,
    "x_max": 210,
    "y_min": 40,
    "y_max": 46,
    "z_min": 0,
    "z_max": 2,
}


class RecordingClient:
    def __init__(self):
        self.codes = []

    def execute_vba(self, code):
        self.codes.append(code)
        return {"status": "executed"}


def call(args, client, tool="cst_create_brick"):
    return json.loads(asyncio.run(geometry.handle(tool, args, client))[0].text)


def test_all_numeric_brick_preserves_previous_format():
    client = RecordingClient()
    assert call(ARGS, client)["status"] == "executed"
    assert client.codes == [
        (
            'With Brick\n  .Reset\n  .Name "ParametricBrick"\n'
            '  .Component "ParameterTest"\n  .Material "PEC"\n'
            '  .Xrange "200", "210"\n  .Yrange "40", "46"\n'
            '  .Zrange "0", "2"\n  .Create\nEnd With'
        )
    ]


@pytest.mark.parametrize("bound", BOUNDS)
@pytest.mark.parametrize(
    "expression",
    ["PBrick_L", "200+PBrick_L", "PBrick_H/2", " (PBrick_L + 2)*3 ", "Unknown_Parameter + ("],
)
def test_every_bound_preserves_expression_text(bound, expression):
    client = RecordingClient()
    assert call({**ARGS, bound: expression}, client)["status"] == "executed"
    assert f'"{expression}"' in client.codes[0]
    # Semantic validation belongs to CST, including undefined/malformed expressions.
    assert_payload_contained(client.codes[0])


def test_mixed_bounds_use_same_public_handler():
    client = RecordingClient()
    call({**ARGS, "x_max": "200+PBrick_L", "z_max": "PBrick_H/2"}, client)
    assert '.Xrange "200", "200+PBrick_L"' in client.codes[0]
    assert '.Yrange "40", "46"' in client.codes[0]
    assert '.Zrange "0", "PBrick_H/2"' in client.codes[0]


@pytest.mark.parametrize("bound", BOUNDS)
@pytest.mark.parametrize(
    "bad",
    [
        True,
        False,
        None,
        [],
        {},
        1j,
        float("nan"),
        float("inf"),
        -float("inf"),
        "",
        " \t ",
        "a\nb",
        "a\rb",
        "a\x00b",
        "a\u2028b",
        "a\u2029b",
        "a\x85b",
        "a\vb",
        "a\fb",
    ],
)
def test_invalid_bounds_rejected_by_handler_and_helper(bound, bad):
    client = RecordingClient()
    assert call({**ARGS, bound: bad}, client)["status"] == "error"
    assert client.codes == []
    with pytest.raises(ValueError):
        geometry._build_brick({**ARGS, bound: bad})
    with pytest.raises(ValueError):
        _format_expression(bad)


def test_quotes_and_statement_payloads_cannot_escape_literal():
    expression = 'PBrick_L" : RunAndWait "calc.exe" : \' '
    client = RecordingClient()
    assert call({**ARGS, "x_max": expression}, client)["status"] == "executed"
    assert f'"{expression.replace(chr(34), chr(34) * 2)}"' in client.codes[0]
    assert_payload_contained(client.codes[0])
    assert _format_expression('a"b') == '"a""b"'


def test_existing_builder_dangerous_pattern_check_still_applies():
    with pytest.raises(ValueError):
        _format_expression('p" & Shell')


def test_expression_numbers_match_number_only_builder():
    for value in [0, -2, 1.234567890123, 1e-11, 1e20]:
        assert (
            VBABuilder("Brick").set_expression_pair("Xrange", value, value).build()
            == VBABuilder("Brick").set_double("Xrange", value, value).build()
        )
    with pytest.raises(ValueError):
        _format_expression(10**1000)
    with pytest.raises((TypeError, ValueError)):
        VBABuilder("Cylinder").set_double("Xrange", "PBrick_L", 1)


@pytest.mark.parametrize("bad", ["PBrick_L", "1", True, float("inf")])
def test_unrelated_numeric_only_tool_still_rejects_expressions(bad):
    client = RecordingClient()
    args = {"component": "c", "name": "s", "radius": bad}
    assert call(args, client, "cst_create_sphere")["status"] == "error"
    assert client.codes == []


def test_only_brick_bound_schemas_change():
    tools = {tool.name: tool for tool in geometry.TOOLS}
    for bound in BOUNDS:
        spec = tools["cst_create_brick"].model_dump(by_alias=True)["inputSchema"]["properties"][
            bound
        ]
        assert spec["type"] == ["number", "string"]
        assert "history" in spec["description"]
    schema = tools["cst_create_sphere"].model_dump(by_alias=True)["inputSchema"]
    assert schema["properties"]["radius"]["type"] == "number"


@pytest.mark.parametrize("attribute", ["inputSchema", "input_schema"])
def test_guard_schema_lookup_supports_both_mcp_sdk_attribute_names(attribute):
    from types import SimpleNamespace

    tool = SimpleNamespace(
        name="numeric_only",
        **{attribute: {"type": "object", "properties": {"value": {"type": "number"}}}},
    )
    check_arguments([tool], "numeric_only", {"value": 3.5})
    for bad in (True, "PBrick_L", float("inf")):
        with pytest.raises(ValueError):
            check_arguments([tool], "numeric_only", {"value": bad})
