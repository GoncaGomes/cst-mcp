"""cst_delete_solid / cst_delete_component: existence check, loft curve cleanup, readback (MCP 19)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import geometry


def _loft_group(component: str, name: str, index: int) -> str:
    digest = hashlib.sha256(f"{component}:{name}".encode()).hexdigest()[:16]
    return f"{name[:40]}_loft_{digest}_p{index}"


class FakeModel:
    """model3d fake: queries list shapes and curve groups; history applies the deletes."""

    def __init__(self, solids, curves=(), *, honour_delete=True, fail_curve_read=False):
        self.solids = list(solids)
        self.curves = list(curves)
        self.honour_delete = honour_delete
        self.fail_curve_read = fail_curve_read
        self.history: list[str] = []

    def is_solver_running(self, *, timeout=None):
        return False

    def add_to_history(self, title, code, *args, **kwargs):
        self.history.append(code)
        if not self.honour_delete:
            return
        for line in code.splitlines():
            m = re.fullmatch(r'(Solid|Component|Curve)\.(Delete|DeleteCurve) "(.+)"', line.strip())
            if not m:
                continue
            kind, _, target = m.groups()
            if kind == "Solid":
                self.solids = [s for s in self.solids if s != target]
            elif kind == "Component":
                self.solids = [
                    s
                    for s in self.solids
                    if not (
                        s.rpartition(":")[0] == target
                        or s.rpartition(":")[0].startswith(target + "/")
                    )
                ]
            else:
                self.curves = [c for c in self.curves if c != target]

    def _execute_vba_code(self, code: str, timeout=None) -> None:
        opened = re.search(r'Open "([^"]+)" For Output', code)
        if not opened:
            return
        lines = [f"solids\t{len(self.solids)}", *(f"solid\t{s}" for s in self.solids)]
        if self.fail_curve_read:
            lines.append("curves_error\tno curves")
        else:
            lines += [f"curves\t{len(self.curves)}", *(f"curve\t{c}" for c in self.curves)]
        Path(opened[1]).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _client(tmp_path, model: FakeModel) -> CSTClient:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client.config.work_dir = tmp_path
    client._de = object()
    client._project = SimpleNamespace(model3d=model)
    return client


async def _call(client, name, arguments) -> dict:
    content = await geometry.handle(name, arguments, client)
    return json.loads(content[0].text)


async def test_delete_solid_runs_solid_delete_and_lists_remaining(tmp_path) -> None:
    model = FakeModel(["antenna:patch", "antenna:feed", "substrate:board"])
    result = await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": "antenna:patch"})
    assert model.history == ['Solid.Delete "antenna:patch"']
    assert result["status"] == "executed"
    assert result["action"] == "deleted"
    assert result["remaining_solids"] == ["antenna:feed"]
    assert result["curve_groups_deleted"] == []


async def test_missing_solid_is_rejected_without_history(tmp_path) -> None:
    model = FakeModel(["antenna:Patch", "antenna:feed", "other:patch"])
    result = await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": "antenna:patch"})
    assert model.history == []
    assert result["status"] == "error"
    assert result["code"] == "solid_not_found"
    assert "action" not in result
    assert set(result["similar"]) >= {"antenna:Patch", "other:patch"}
    assert result["component_solids"] == ["antenna:Patch", "antenna:feed"]
    assert "Nothing was changed" in result["message"]


async def test_loft_curve_groups_are_deleted_with_the_solid(tmp_path) -> None:
    groups = [_loft_group("horn", "flare", i) for i in range(3)]
    other = [_loft_group("horn", "flare2", 0), _loft_group("other", "flare", 0), "Curves"]
    model = FakeModel(["horn:flare", "horn:flare2"], [*groups, *other])
    result = await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": "horn:flare"})
    assert len(model.history) == 1
    lines = model.history[0].splitlines()
    assert lines[0] == 'Solid.Delete "horn:flare"'
    assert lines[1:] == [f'Curve.DeleteCurve "{g}"' for g in groups]
    assert result["curve_groups_deleted"] == groups
    assert model.curves == other
    assert "warning" not in result


async def test_loft_group_match_ignores_case(tmp_path) -> None:
    group = _loft_group("horn", "flare", 0)
    model = FakeModel(["horn:flare"], [group.upper()])
    result = await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": "horn:flare"})
    assert result["curve_groups_deleted"] == [group.upper()]


async def test_loft_name_reusable_after_delete(tmp_path) -> None:
    profile = [[0, 0, 0], [1, 0, 0], [1, 1, 0]]
    loft = geometry._build_loft(
        {
            "component": "horn",
            "name": "flare",
            "profiles": [profile, [[0, 0, 5], [1, 0, 5], [1, 1, 5]]],
        }
    )
    groups = [_loft_group("horn", "flare", i) for i in range(2)]
    for group in groups:  # The loft still names its groups the same way.
        assert f'"{group}"' in loft
    model = FakeModel(["horn:flare"], groups)
    await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": "horn:flare"})
    # Nothing is left for the loft's collision check to find.
    assert not any(g in model.curves for g in groups)


async def test_solid_still_listed_after_delete_is_an_error(tmp_path) -> None:
    model = FakeModel(["a:b"], honour_delete=False)
    result = await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": "a:b"})
    assert model.history == ['Solid.Delete "a:b"']
    assert result["status"] == "error"
    assert result["still_present"] == ["a:b"]
    assert "action" not in result
    assert "curve_groups_deleted" not in result


async def test_failed_execution_does_not_claim_deleted(tmp_path) -> None:
    class FailingModel(FakeModel):
        def add_to_history(self, title, code, *args, **kwargs):
            raise RuntimeError("Solid not found")

    result = await _call(
        _client(tmp_path, FailingModel(["a:b"])), "cst_delete_solid", {"solid": "a:b"}
    )
    assert result["status"] == "error"
    assert "action" not in result


async def test_unreadable_curves_still_deletes_the_solid(tmp_path) -> None:
    model = FakeModel(["a:b"], fail_curve_read=True)
    result = await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": "a:b"})
    assert model.history == ['Solid.Delete "a:b"']
    assert result["action"] == "deleted"
    assert "loft curve groups were not checked" in result["note"]


async def test_results_present_refuses_the_delete(tmp_path, monkeypatch) -> None:
    model = FakeModel(["a:b"])
    client = _client(tmp_path, model)
    monkeypatch.setattr(client, "results_present", lambda: True)
    result = await _call(client, "cst_delete_solid", {"solid": "a:b"})
    assert result["code"] == "results_exist"
    assert model.history == []
    assert "action" not in result


async def test_offline_returns_vba_without_claiming_deleted() -> None:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    result = await _call(client, "cst_delete_solid", {"solid": "antenna/feed:pin"})
    assert result["status"] == "offline"
    assert result["vba"] == 'Solid.Delete "antenna/feed:pin"'
    assert "action" not in result


async def test_invalid_solid_name_is_rejected(tmp_path) -> None:
    model = FakeModel(["a:b"])
    for bad in ("nocolon", 'a:b"c', "a:b:c"):
        result = await _call(_client(tmp_path, model), "cst_delete_solid", {"solid": bad})
        assert result["status"] == "error"
    assert model.history == []


async def test_delete_component_includes_sub_components_and_lofts(tmp_path) -> None:
    groups = [_loft_group("ant/feed", "taper", i) for i in range(2)]
    model = FakeModel(
        ["ant:patch", "ant/feed:taper", "antenna:keep", "board:sub"], [*groups, "Curves"]
    )
    result = await _call(_client(tmp_path, model), "cst_delete_component", {"component": "ant"})
    lines = model.history[0].splitlines()
    assert lines[0] == 'Component.Delete "ant"'
    assert lines[1:] == [f'Curve.DeleteCurve "{g}"' for g in groups]
    assert result["action"] == "deleted"
    assert result["deleted_solids"] == ["ant:patch", "ant/feed:taper"]
    assert result["curve_groups_deleted"] == groups
    assert model.solids == ["antenna:keep", "board:sub"]


async def test_delete_missing_component_lists_existing(tmp_path) -> None:
    model = FakeModel(["antenna:patch", "board:sub"])
    result = await _call(_client(tmp_path, model), "cst_delete_component", {"component": "ant"})
    assert model.history == []
    assert result["code"] == "component_not_found"
    assert result["existing_components"] == ["antenna", "board"]
    assert "action" not in result


async def test_component_still_holding_solids_is_an_error(tmp_path) -> None:
    model = FakeModel(["ant:patch"], honour_delete=False)
    result = await _call(_client(tmp_path, model), "cst_delete_component", {"component": "ant"})
    assert result["status"] == "error"
    assert result["still_present"] == ["ant:patch"]
    assert "deleted_solids" not in result


def test_inventory_query_is_read_only() -> None:
    assert "Solid.GetNumberOfShapes" in geometry._INVENTORY_VBA
    assert "Curve.StartCurveNameIteration" in geometry._INVENTORY_VBA
    assert "Delete" not in geometry._INVENTORY_VBA


def test_delete_tools_are_marked_destructive() -> None:
    from cst_mcp.tools.annotations import is_destructive

    assert is_destructive("cst_delete_solid")
    assert is_destructive("cst_delete_component")
