"""Parameter names CST refuses are rejected before any VBA runs (docs/tasks/mcp/05)."""

from __future__ import annotations

import json

import pytest

from cst_mcp.session import CSTSession
from cst_mcp.tools import parameters
from cst_mcp.tools.registry import _tool_schema
from cst_mcp.validators import VBA_CONSTANT_NAMES, validate_parameter_name


class ParameterClient:
    """Connected fake that records every call reaching CST."""

    connected = True
    has_project = True

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.vba: list[str] = []

    def solver_status(self, timeout_s: float = 30.0) -> dict:
        self.calls.append("solver_status")
        return {"status": "ok", "running": False}

    def results_present(self) -> bool:
        self.calls.append("results_present")
        return False

    def _run_model3d_vba(self, code: str) -> dict:
        self.calls.append("vba")
        self.vba.append(code)
        return {"status": "executed", "entrypoint": "fake"}

    def rebuild(self) -> dict:
        self.calls.append("rebuild")
        return {"status": "ok"}


async def _call(name: str, arguments: dict, client) -> dict:
    content = await parameters.handle(name, arguments, client)
    return json.loads(content[0].text)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Pi", "pi", "PI", "True", "Nothing", "vbCrLf", "VBTAB"])
async def test_set_parameter_rejects_vba_constant_names_without_running_vba(name) -> None:
    client = ParameterClient()
    data = await _call("cst_set_parameter", {"name": name, "value": 3.14159}, client)
    assert data["status"] == "error"
    assert "VBA constant name" in data["message"]
    assert f"'{name}_c'" in data["message"]
    assert len(data["message"]) < 200
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["1width", "patch length", "patch.length", "w-1", "", "a\nb"])
async def test_set_parameter_rejects_invalid_identifiers(name) -> None:
    client = ParameterClient()
    data = await _call("cst_set_parameter", {"name": name, "value": 1}, client)
    assert data["status"] == "error"
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["patch_length", "pi_c", "PiC", "Pi2", "_h", "vbias", "nothing1"])
async def test_set_parameter_accepts_valid_names(name) -> None:
    client = ParameterClient()
    data = await _call("cst_set_parameter", {"name": name, "value": 2.5}, client)
    assert data["status"] == "ok"
    assert client.vba == [f'StoreParameter "{name}", "2.5"']


@pytest.mark.asyncio
async def test_other_parameter_tools_share_the_rule() -> None:
    client = ParameterClient()
    data = await _call("cst_delete_parameter", {"name": "Pi"}, client)
    assert data["status"] == "error" and "VBA constant name" in data["message"]
    assert client.calls == []


def test_session_parameter_vba_shares_the_rule() -> None:
    with pytest.raises(ValueError, match="VBA constant name"):
        CSTSession._parameter_vba({"w": 1.0, "Pi": 3.14})
    assert CSTSession._parameter_vba({"pi_c": 3.14}) == 'StoreParameter "pi_c", "3.14"'


def test_validator_folds_case_and_keeps_the_confirmed_name() -> None:
    assert "pi" in VBA_CONSTANT_NAMES  # confirmed by CST's own error in a live run
    assert all(name == name.lower() for name in VBA_CONSTANT_NAMES)
    assert validate_parameter_name("substrate_h") == "substrate_h"
    for bad in (None, 3, "Empty", "vbnullstring"):
        with pytest.raises(ValueError):
            validate_parameter_name(bad)


def test_set_parameter_schema_states_the_rule() -> None:
    tool = next(t for t in parameters.TOOLS if t.name == "cst_set_parameter")
    description = _tool_schema(tool, "inputSchema", "input_schema")["properties"]["name"][
        "description"
    ]
    assert "VBA constant" in description and "Pi" in description
