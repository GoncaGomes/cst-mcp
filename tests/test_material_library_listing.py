"""cst_list_library_materials and cst_load_material name suggestions (temporary .mtd files)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.material_library import (
    load_material_vba,
    read_library_material,
    similar_library_names,
)
from cst_mcp.tools import materials
from cst_mcp.tools.annotations import annotations_for

FR4_LOSSY = """[Definition]
     .FrqType "all"
     .Type "Normal"
     .SetMaterialUnit "GHz", "mm"
     .Epsilon "4.3"
     .Mu "1.0"
     .Kappa "0.0"
     .TanD "0.025"
     .TanDFreq "10.0"
     .TanDGiven "True"
     .TanDModel "ConstTanD"
     .KappaM "0.0"
     .DispModelEps "None"
     .UseGeneralDispersionEps "False"
     .Colour "0.94", "0.82", "0.76"
     .Create

[Attributes]
Epsilon         = 4.3

[Description]
EM(HF) properties measured @ 10GHz
"""

FR4_LOSS_FREE = FR4_LOSSY.replace('.TanDGiven "True"', '.TanDGiven "False"')

# Static block first, then the lossy-metal block HF solvers use.
COPPER = """[Definition]
     .FrqType "static"
     .Type "Normal"
     .SetMaterialUnit "Hz", "mm"
     .Epsilon "1"
     .Mu "1.0"
     .Kappa "5.8e+007"
     .TanDGiven "False"
     .FrqType "all"
     .Type "Lossy metal"
     .SetMaterialUnit "GHz", "mm"
     .Mu "1.0"
     .Kappa "5.8e+007"
     .Create
"""

# An hf block overrides all for high-frequency solvers.
IRON = """[Definition]
     .FrqType "static"
     .Type "Normal"
     .Mu "1000"
     .FrqType "all"
     .Type "Normal"
     .Epsilon "1"
     .Mu "1000"
     .FrqType "hf"
     .Type "Lossy metal"
     .Mu "1"
     .Sigma "1.04e7"
     .Create
"""

PEC = """[Definition]
     .FrqType "all"
     .Type "PEC"
     .Create
"""

# Identity lines before the first FrqType, MaterialUnit spelling, tabulated dispersion in MHz.
ABSORBER = """[Definition]
.Reset
.Name "Absorber"
.FrqType "all"
.Type "Normal"
.MaterialUnit "Frequency", "MHz"
.MaterialUnit "Geometry", "mm"
.Epsilon "1"
.Mu "1.0"
.Sigma "0.0"
.TanD "0.0"
.TanDFreq "0.0"
.TanDGiven "False"
.DispModelEps "None"
.UseGeneralDispersionEps "True"
.AddDispersionFittingValueEps "100", "12.5", "3.1", "1.0"
.AddDispersionFittingValueEps "200", "11.0", "2.9", "1.0"
.Create
"""

SWEPT = FR4_LOSSY.replace('.SetMaterialUnit "GHz", "mm"', '.SetMaterialUnit "MHz", "mm"').replace(
    '.Epsilon "4.3"', '.Epsilon "eps_sub"'
)

ROGERS = FR4_LOSSY.replace('.Epsilon "4.3"', '.Epsilon "2.33"').replace(
    '.TanD "0.025"', '.TanD "0.0012"'
)

LIBRARY = {
    "FR-4 (lossy)": FR4_LOSSY,
    "FR-4 (loss free)": FR4_LOSS_FREE,
    "Copper (annealed)": COPPER,
    "Copper (pure)": COPPER,
    "Iron": IRON,
    "PEC": PEC,
    "Absorber": ABSORBER,
    "Swept": SWEPT,
    "Rogers RT-duroid 5870 (lossy)": ROGERS,
    "Rogers RT-duroid 5880 (lossy)": ROGERS,
}


def _library(tmp_path: Path, extra: dict[str, str] | None = None) -> Path:
    directory = tmp_path / "CST Studio Suite 2025" / "Library" / "Materials"
    directory.mkdir(parents=True)
    for name, text in {**LIBRARY, **(extra or {})}.items():
        (directory / f"{name}.mtd").write_text(text, encoding="utf-8")
    (directory / "readme.txt").write_text("not a material", encoding="utf-8")
    return directory


def _client(directory: Path, *, detected: bool = False, version: str = "2025") -> CSTClient:
    install = directory.parent.parent
    return CSTClient(
        CSTConfig(
            connect_mode="disabled",
            cst_path=install,
            cst_path_detected=detected,
            version=version,
        )
    )


async def _call(name: str, arguments: dict, client) -> dict:
    content = await materials.handle(name, arguments, client)
    return json.loads(content[0].text)


def _by_name(data: dict) -> dict:
    return {m["library_name"]: m for m in data["materials"]}


@pytest.mark.asyncio
async def test_listing_reads_properties_from_native_files(tmp_path) -> None:
    directory = _library(tmp_path)
    data = await _call("cst_list_library_materials", {}, _client(directory))

    assert data["status"] == "ok"
    assert data["library_directory"] == str(directory.resolve())
    assert data["cst_version"] == "2025"
    assert "version_warning" not in data
    assert data["match_count"] == len(LIBRARY) and data["truncated"] is False
    assert "readme" not in json.dumps(data["materials"])
    entries = _by_name(data)

    fr4 = entries["FR-4 (lossy)"]
    assert fr4["type"] == "normal" and fr4["raw_type"] == "Normal"
    assert fr4["epsilon_r"] == 4.3 and fr4["mu_r"] == 1.0 and fr4["conductivity"] == 0.0
    assert fr4["tan_d_given"] is True and fr4["tan_d"] == 0.025
    assert fr4["tan_d_freq"] == 1e10 and fr4["tan_d_model"] == "ConstTanD"
    assert fr4["dispersion_model"] == "none"
    assert fr4["description"] == "EM(HF) properties measured @ 10GHz"
    assert fr4["unavailable"] == {}

    loss_free = entries["FR-4 (loss free)"]
    assert loss_free["tan_d_given"] is False and loss_free["tan_d"] is None
    assert "conductivity" in loss_free["unavailable"]["tan_d"]

    copper = entries["Copper (annealed)"]
    assert copper["type"] == "lossy_metal" and copper["raw_type"] == "Lossy metal"
    assert copper["conductivity"] == 5.8e7 and copper["epsilon_r"] is None
    assert copper["frequency_block"] == "all"
    assert copper["frequency_blocks"] == ["static", "all"]
    assert "Not applicable" in copper["unavailable"]["tan_d"]

    iron = entries["Iron"]
    assert iron["frequency_block"] == "hf" and iron["type"] == "lossy_metal"
    assert iron["conductivity"] == 1.04e7 and iron["mu_r"] == 1.0

    pec = entries["PEC"]
    assert pec["type"] == "pec" and pec["epsilon_r"] is None and pec["conductivity"] is None
    assert "PEC" in pec["unavailable"]["conductivity"]

    absorber = entries["Absorber"]
    assert absorber["frequency_block"] == "all" and "frequency_blocks" not in absorber
    assert absorber["dispersion_model"] == "general"
    assert absorber["dispersion_fit_points"] == 2
    assert "dispersion model" in absorber["unavailable"]["tan_d"]

    swept = entries["Swept"]
    assert swept["epsilon_r"] is None
    assert swept["unavailable"]["epsilon_r"] == "Not a number in the file: 'eps_sub'"
    assert swept["tan_d_freq"] == 1e7  # 10 MHz


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("FR4", ["FR-4 (loss free)", "FR-4 (lossy)"]),
        ("fr-4 lossy", ["FR-4 (lossy)"]),
        ("RT/duroid 5870", ["Rogers RT-duroid 5870 (lossy)"]),
        ("COPPER", ["Copper (annealed)", "Copper (pure)"]),
    ],
)
async def test_filter_matches_every_word_ignoring_case_and_punctuation(
    tmp_path, query, expected
) -> None:
    data = await _call("cst_list_library_materials", {"filter": query}, _client(_library(tmp_path)))

    assert [m["library_name"] for m in data["materials"]] == expected
    assert data["filter"] == query


@pytest.mark.asyncio
async def test_every_listed_entry_loads_unchanged(tmp_path) -> None:
    directory = _library(tmp_path)
    data = await _call("cst_list_library_materials", {}, _client(directory))

    for entry in data["materials"]:
        vba = load_material_vba(
            "M", entry["library_name"], cst_path=None, library_path=str(directory)
        )
        assert vba.rstrip().endswith("End With")


@pytest.mark.asyncio
async def test_no_match_offers_similar_names(tmp_path) -> None:
    data = await _call(
        "cst_list_library_materials", {"filter": "Coper"}, _client(_library(tmp_path))
    )

    assert data["status"] == "ok" and data["materials"] == []
    assert data["message"] == "No library entry matches 'Coper'."
    assert data["similar"][0] == "Copper (pure)"


@pytest.mark.asyncio
async def test_truncation_and_names_only(tmp_path) -> None:
    client = _client(_library(tmp_path))
    data = await _call("cst_list_library_materials", {"max_results": 3}, client)
    assert data["returned"] == 3 and data["truncated"] is True
    assert data["match_count"] == len(LIBRARY)
    assert "Showing 3 of" in data["message"]

    names = await _call(
        "cst_list_library_materials", {"include_properties": False, "filter": "copper"}, client
    )
    assert names["materials"] == [
        {"library_name": "Copper (annealed)"},
        {"library_name": "Copper (pure)"},
    ]


@pytest.mark.asyncio
async def test_unloadable_and_unreadable_files_are_skipped(tmp_path) -> None:
    directory = _library(
        tmp_path,
        {
            # The loader rejects names ending in a dot or with outer spaces.
            "Odd.": FR4_LOSSY,
            " Leading": FR4_LOSSY,
            "No definition": "[Attributes]\nEpsilon = 1\n",
        },
    )
    data = await _call("cst_list_library_materials", {}, _client(directory))

    listed = {m["library_name"] for m in data["materials"]}
    assert "Odd." not in listed and " Leading" not in listed and "No definition" not in listed
    skipped = {s["file"]: s["reason"] for s in data["skipped"]}
    assert "not a name cst_load_material accepts" in skipped["Odd..mtd"]
    assert "not a name cst_load_material accepts" in skipped[" Leading.mtd"]
    assert "Missing [Definition]" in skipped["No definition.mtd"]


@pytest.mark.asyncio
async def test_version_warning_only_for_a_discovered_install(tmp_path) -> None:
    directory = _library(tmp_path)

    found = await _call(
        "cst_list_library_materials", {}, _client(directory, detected=True, version="2026")
    )
    assert "CST_VERSION is 2026" in found["version_warning"]
    assert "CST 2025" in found["version_warning"]

    configured = await _call(
        "cst_list_library_materials", {}, _client(directory, detected=False, version="2026")
    )
    assert "version_warning" not in configured

    explicit = await _call(
        "cst_list_library_materials",
        {"library_path": str(directory)},
        _client(directory, detected=True, version="2026"),
    )
    assert "version_warning" not in explicit


@pytest.mark.asyncio
async def test_custom_directory_has_no_version(tmp_path) -> None:
    custom = tmp_path / "my_materials"
    custom.mkdir()
    (custom / "Sub.mtd").write_text(FR4_LOSSY, encoding="utf-8")
    data = await _call(
        "cst_list_library_materials",
        {"library_path": str(custom)},
        CSTClient(CSTConfig(connect_mode="disabled")),
    )

    assert data["cst_version"] is None
    assert "CST Studio Suite" in data["unavailable"]["cst_version"]
    assert [m["library_name"] for m in data["materials"]] == ["Sub"]


@pytest.mark.asyncio
async def test_missing_library_is_an_error(tmp_path) -> None:
    data = await _call(
        "cst_list_library_materials",
        {"library_path": str(tmp_path / "nowhere")},
        CSTClient(CSTConfig(connect_mode="disabled")),
    )
    assert data["status"] == "error" and "does not exist" in data["message"]

    no_install = await _call(
        "cst_list_library_materials", {}, CSTClient(CSTConfig(connect_mode="disabled"))
    )
    assert no_install["status"] == "error" and "CST_PATH" in no_install["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"filter": ""},
        {"filter": "   "},
        {"filter": "/-()"},
        {"filter": "x" * 101},
        {"filter": 4003},
        {"max_results": 0},
        {"max_results": 1001},
        {"max_results": True},
        {"max_results": "10"},
        {"include_properties": "yes"},
    ],
)
async def test_invalid_listing_arguments(tmp_path, arguments) -> None:
    data = await _call("cst_list_library_materials", arguments, _client(_library(tmp_path)))
    assert data["status"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("library_name", "first"),
    [
        ("Rogers RT/duroid 5870", "Rogers RT-duroid 5870 (lossy)"),  # display name, has "/"
        ("FR-4", "FR-4 (lossy)"),
        ("copper annealed", "Copper (annealed)"),
    ],
)
async def test_load_of_unknown_name_returns_close_matches(tmp_path, library_name, first) -> None:
    directory = _library(tmp_path)
    data = await _call(
        "cst_load_material", {"name": "Sub", "library_name": library_name}, _client(directory)
    )

    assert data["status"] == "error"
    assert data["similar"][0] == first and len(data["similar"]) <= 5
    assert data["library_directory"] == str(directory.resolve())
    assert first in data["message"]


@pytest.mark.asyncio
async def test_load_reports_library_directory_and_version(tmp_path) -> None:
    directory = _library(tmp_path)
    data = await _call(
        "cst_load_material", {"name": "Sub", "library_name": "FR-4 (lossy)"}, _client(directory)
    )

    assert data["status"] == "offline"
    assert data["library_directory"] == str(directory.resolve())
    assert data["library_cst_version"] == "2025"
    assert '.Epsilon "4.3"' in data["vba"]


def test_similar_names_limit_and_empty_query() -> None:
    names = [f"Material {i}" for i in range(20)]
    assert len(similar_library_names("material", names)) == 5
    assert similar_library_names("---", names) == []


def test_reader_never_uses_bundled_values(tmp_path) -> None:
    path = tmp_path / "FR-4.mtd"
    path.write_text(FR4_LOSSY.replace('"4.3"', '"4.25"'), encoding="utf-8")
    assert read_library_material(path)["epsilon_r"] == 4.25


def test_listing_is_read_only() -> None:
    tool = next(t for t in materials.TOOLS if t.name == "cst_list_library_materials")
    ann = annotations_for(tool.name)
    # MCP 1.x uses camelCase attributes; 2.x uses snake_case.
    assert getattr(ann, "readOnlyHint", getattr(ann, "read_only_hint", None)) is True
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    assert schema["additionalProperties"] is False
