"""The Markdown tool catalog must match the complete registered tool set."""

import re
from pathlib import Path

from mcp.server import Server

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import register_all_tools


def test_documented_tools_match_registered_tools():
    path = Path(__file__).resolve().parents[1] / "docs/TOOLS.md"
    catalog = re.findall(
        r"^\|[ \t]*`([^`\r\n]+)`[ \t]*\|", path.read_text(encoding="utf-8"), re.MULTILINE
    )
    registry = register_all_tools(
        Server("catalog-test"), CSTClient(CSTConfig(connect_mode="disabled", toolsets=None))
    )
    assert catalog, "The Markdown tool catalog is empty"
    duplicates = sorted({name for name in catalog if catalog.count(name) > 1})
    assert not duplicates, f"Duplicate documented tool names: {duplicates}"
    documented = set(catalog)
    registered = set(registry.tool_names)
    missing = sorted(registered - documented)
    additional = sorted(documented - registered)
    assert documented == registered, (
        f"Missing tool names: {missing}. Additional tool names: {additional}"
    )
    assert len(catalog) == len(registry)
