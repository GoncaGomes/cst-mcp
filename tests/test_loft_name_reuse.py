"""cst_create_loft reuses a name whose solid is gone; an existing solid still fails (MCP 21)."""

from __future__ import annotations

import json

from cst_mcp.tools import geometry

PROFILES = [[[0, 0, 0], [1, 0, 0], [1, 1, 0]], [[0, 0, 5], [1, 0, 5], [1, 1, 5]]]


def _loft(component: str = "horn", name: str = "flare") -> str:
    return geometry._build_loft({"component": component, "name": name, "profiles": PROFILES})


def test_existing_solid_is_refused_with_its_name_before_any_curve_change() -> None:
    code = _loft()
    check = next(line for line in code.splitlines() if "GetNameOfShapeFromIndex" in line)
    assert '"horn:flare", vbTextCompare) = 0 Then Err.Raise 513' in check
    assert "Solid horn:flare already exists" in check
    assert code.index(check) < code.index("Curve.DeleteCurve") < code.index("NewCurve")


def test_orphaned_groups_of_this_loft_are_deleted_before_profiles() -> None:
    code = _loft()
    prefix = geometry._loft_group_prefix("horn", "flare")
    lines = [line.strip() for line in code.splitlines()]
    match = f'If StrComp(Left(loftCurveName, {len(prefix)}), "{prefix}", vbTextCompare) = 0 Then'
    assert match in lines
    assert f"loftSuffix = Mid(loftCurveName, {len(prefix) + 1})" in lines
    # Only <prefix><digits>, the same rule cst_delete_solid uses for loft groups.
    assert 'If loftSuffix <> "" And Not loftSuffix Like "*[!0-9]*" Then' in lines
    assert "Curve.DeleteCurve loftStale(loftCurveIndex)" in lines
    assert code.index("Curve.DeleteCurve") < code.index("With Curve")


def test_cleanup_prefix_matches_delete_solid_rule() -> None:
    prefix = geometry._loft_group_prefix("horn", "flare")
    curves = [f"{prefix}0", f"{prefix}3", f"{prefix}x", geometry._loft_group_prefix("horn", "f2")]
    assert geometry._loft_groups_of(["horn:flare"], curves) == [f"{prefix}0", f"{prefix}3"]


def test_long_name_prefix_length_matches_vba() -> None:
    name = "L" * 100
    code = _loft(name=name)
    prefix = geometry._loft_group_prefix("horn", name)
    assert len(prefix) == 40 + len("_loft_") + 16 + len("_p")
    assert f"Left(loftCurveName, {len(prefix)})" in code


def test_collision_check_still_runs_after_cleanup() -> None:
    code = _loft()
    groups = [f"{geometry._loft_group_prefix('horn', 'flare')}{i}" for i in range(2)]
    for group in groups:
        assert f'If StrComp(loftCurveName, "{group}", vbTextCompare) = 0 Then' in code
    assert code.index("Curve.DeleteCurve") < code.index("Auxiliary loft curve group already exists")
    assert code.index("Auxiliary loft curve group already exists") < code.index("With Curve")


async def test_handler_sends_one_history_entry_with_cleanup() -> None:
    sent: list[str] = []

    class Client:
        def execute_vba(self, code):
            sent.append(code)
            return {"status": "executed"}

    content = await geometry.handle(
        "cst_create_loft", {"component": "horn", "name": "flare", "profiles": PROFILES}, Client()
    )
    assert json.loads(content[0].text)["status"] == "executed"
    assert len(sent) == 1 and "Curve.DeleteCurve" in sent[0]


def test_description_states_reuse_and_existing_solid_failure() -> None:
    tool = next(t for t in geometry.TOOLS if t.name == "cst_create_loft")
    assert "already exists" in tool.description
    assert "reused" in tool.description
