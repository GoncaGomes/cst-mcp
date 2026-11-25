"""Units read/set tools and tool references in responses (docs/tasks/mcp/04), fakes only."""

from __future__ import annotations

import io
import json
import re
import tokenize
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate

from cst_mcp.tools import _MODULES, project, solvers
from cst_mcp.tools.annotations import is_read_only
from cst_mcp.tools.registry import _tool_schema

NEW_PROJECT_UNITS = {
    "length": "mm",
    "frequency": "GHz",
    "time": "ns",
    "temperature": "K",
    "length_to_si": "0,001",  # CStr follows the Windows locale
    "frequency_to_si": "1000000000",
    "time_to_si": "1E-09",
}


class UnitsClient:
    """Connected fake: a units table that SetUnit history VBA updates."""

    connected = True
    has_project = True

    def __init__(self, values: dict | None = None, errors: dict | None = None) -> None:
        self.values = dict(NEW_PROJECT_UNITS if values is None else values)
        self.errors = dict(errors or {})
        self.history: list[tuple[str, str | None]] = []
        self.queries: list[list[tuple[str, str]]] = []
        self.history_result: dict = {"status": "executed"}
        self.apply = True

    def query_values(self, fields):
        self.queries.append(fields)
        keys = {key for key, _ in fields}
        return {
            "status": "ok",
            "values": {k: v for k, v in self.values.items() if k in keys},
            "errors": {k: v for k, v in self.errors.items() if k in keys},
        }

    def execute_vba(self, code: str, history_label: str | None = None) -> dict:
        self.history.append((code, history_label))
        if self.apply:
            for dimension, unit in re.findall(r'\.SetUnit "(\w+)", "(\w+)"', code):
                self.values[dimension.lower()] = unit
        return dict(self.history_result)


class OfflineClient:
    connected = False
    has_project = False

    def query_values(self, fields):
        from cst_mcp.cst_client import CSTClient

        return {"status": "offline", "vba": CSTClient.build_query_vba(fields)}

    def execute_vba(self, code: str, history_label: str | None = None) -> dict:
        return {"status": "offline", "vba": code}


async def _call(name: str, arguments: dict, client) -> dict:
    module = solvers if name == "cst_get_solver_info" else project
    content = await module.handle(name, arguments, client)
    return json.loads(content[0].text)


def _schema(name: str) -> dict:
    tool = next(t for t in project.TOOLS if t.name == name)
    return _tool_schema(tool, "inputSchema", "input_schema")


# --- cst_get_units -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_units_reads_units_and_si_factors() -> None:
    data = await _call("cst_get_units", {}, UnitsClient())
    assert data["status"] == "ok"
    assert data["units"] == {"length": "mm", "frequency": "GHz", "time": "ns", "temperature": "K"}
    assert data["to_si"] == {"length": 1e-3, "frequency": 1e9, "time": 1e-9}
    assert "unavailable" not in data


@pytest.mark.asyncio
async def test_get_units_query_is_read_only_vba() -> None:
    client = UnitsClient()
    await _call("cst_get_units", {}, client)
    expressions = [expr for _, expr in client.queries[0]]
    assert 'Units.GetUnit("Length")' in expressions
    assert "Units.GetFrequencyUnitToSI()" in expressions
    assert client.history == []
    assert is_read_only("cst_get_units") and not is_read_only("cst_set_units")


@pytest.mark.asyncio
async def test_get_units_falls_back_to_the_si_factor() -> None:
    values = {k: v for k, v in NEW_PROJECT_UNITS.items() if k not in {"length", "frequency"}}
    values["length_to_si"] = "0.0254"
    client = UnitsClient(values, errors={"length": "Unknown method", "frequency": "Unknown"})
    data = await _call("cst_get_units", {}, client)
    assert data["status"] == "ok"
    assert data["units"]["length"] == "in"
    assert data["units"]["frequency"] == "GHz"
    assert "unavailable" not in data


@pytest.mark.asyncio
async def test_get_units_reports_what_could_not_be_read() -> None:
    values = {"length": "mm", "length_to_si": "0.001"}
    errors = {"temperature": "Unknown dimension", "time": "x", "time_to_si": "x"}
    data = await _call("cst_get_units", {}, UnitsClient(values, errors))
    assert data["units"] == {"length": "mm"}
    assert set(data["unavailable"]) == {"temperature", "time", "time_to_si"}


@pytest.mark.asyncio
async def test_get_units_offline_returns_the_query() -> None:
    data = await _call("cst_get_units", {}, OfflineClient())
    assert data["status"] == "offline"
    assert 'Units.GetUnit("Frequency")' in data["vba"]


@pytest.mark.asyncio
async def test_get_units_needs_a_project() -> None:
    client = UnitsClient()
    client.has_project = False
    data = await _call("cst_get_units", {}, client)
    assert data == {"status": "error", "message": "No project open"}


# --- cst_set_units -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_units_writes_history_and_reads_back() -> None:
    client = UnitsClient()
    data = await _call("cst_set_units", {"length": "in", "frequency": "MHz"}, client)
    code, label = client.history[0]
    assert label == "define units"
    assert code == 'With Units\n  .SetUnit "Length", "in"\n  .SetUnit "Frequency", "MHz"\nEnd With'
    assert data["status"] == "executed"
    assert data["requested"] == {"length": "in", "frequency": "MHz"}
    assert data["units"]["length"] == "in" and data["units"]["frequency"] == "MHz"
    assert data["units"]["time"] == "ns"  # untouched


@pytest.mark.asyncio
async def test_set_units_reports_a_readback_mismatch() -> None:
    client = UnitsClient()
    client.apply = False
    data = await _call("cst_set_units", {"length": "um"}, client)
    assert data["status"] == "error"
    assert data["mismatched"] == {"length": {"requested": "um", "read_back": "mm"}}


@pytest.mark.asyncio
async def test_set_units_does_not_read_back_after_an_unknown_outcome() -> None:
    client = UnitsClient()
    client.history_result = {"status": "timeout", "execution_state": "unknown"}
    data = await _call("cst_set_units", {"time": "ps"}, client)
    assert data["status"] == "timeout"
    assert client.queries == []
    assert len(client.history) == 1  # not replayed


@pytest.mark.asyncio
async def test_set_units_offline_returns_vba() -> None:
    data = await _call("cst_set_units", {"temperature": "degC"}, OfflineClient())
    assert data["status"] == "offline"
    assert '.SetUnit "Temperature", "degC"' in data["vba"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [{}, {"length": "MM"}, {"length": 'mm"\nRunScript "x'}, {"frequency": "GHz", "voltage": "V"}],
)
async def test_set_units_rejects_bad_requests_before_cst(arguments) -> None:
    client = UnitsClient()
    with pytest.raises(ValidationError):
        validate(arguments, _schema("cst_set_units"))
    data = await _call("cst_set_units", arguments, client)
    assert data["status"] == "error"
    assert client.history == []


def test_set_units_schema_matches_the_cst_help_lists() -> None:
    props = _schema("cst_set_units")["properties"]
    assert props["length"]["enum"] == ["nm", "um", "mm", "cm", "m", "mil", "in", "ft"]
    assert props["frequency"]["enum"] == ["Hz", "kHz", "MHz", "GHz", "THz", "PHz"]
    assert props["time"]["enum"] == ["fs", "ps", "ns", "us", "ms", "s"]
    assert props["temperature"]["enum"] == ["degC", "K", "degF"]


# --- cst_get_solver_info -------------------------------------------------------------------


class SolverInfoClient:
    connected = True
    has_project = True

    def __init__(self, frequency_unit: str | None) -> None:
        self.frequency_unit = frequency_unit

    def build_query_vba(self, fields):
        return ""

    def query_values(self, fields):
        values = {"solver_type": "HF Time Domain", "fmin": "0.8", "fmax": "1.2"}
        errors = {}
        if self.frequency_unit is None:
            errors["frequency_unit"] = "Unknown method"
        else:
            values["frequency_unit"] = self.frequency_unit
        return {"status": "ok", "values": values, "errors": errors}


@pytest.mark.asyncio
async def test_solver_info_reports_the_project_frequency_unit() -> None:
    data = await _call("cst_get_solver_info", {}, SolverInfoClient("MHz"))
    assert data["fmin"] == 0.8 and data["frequency_unit"] == "MHz"


@pytest.mark.asyncio
async def test_solver_info_points_to_cst_get_units_when_unit_is_unreadable() -> None:
    data = await _call("cst_get_solver_info", {}, SolverInfoClient(None))
    assert "cst_get_units" in data["frequency_unit"]


# --- No response names a tool that is not registered ---------------------------------------

# cst_* strings in server code that are keys, folders or fields, not tool names.
_NOT_TOOL_NAMES = {
    "cst_ascii_export",
    "cst_available",
    "cst_client",
    "cst_controller",
    "cst_dialogs",
    "cst_export",
    "cst_mcp",
    "cst_mcp_cache",
    "cst_message_file",
    "cst_messages_tail",
    "cst_path",
    "cst_process",
    "cst_projects",
    "cst_version",
    "cst_workflow",
}


def test_server_strings_only_name_registered_tools() -> None:
    registered = {tool.name for module in _MODULES for tool in module.TOOLS}
    kinds = {tokenize.STRING} | {getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
    source = Path(project.__file__).resolve().parents[1]
    unknown: dict[str, str] = {}
    for path in sorted(source.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in kinds:
                continue
            # A trailing "_" marks a name prefix (annotation patterns), not a tool.
            for ref in re.findall(r"\bcst_[a-z0-9_]*[a-z0-9]\b", tok.string):
                if ref not in registered and ref not in _NOT_TOOL_NAMES:
                    unknown.setdefault(ref, f"{path.name}:{tok.start[0]}")
    assert unknown == {}
