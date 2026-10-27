"""Actual server operation boundary with controlled live API objects; no CST launch."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import connection


def is_error(result):
    """Assert the actual wire field across MCP SDK 1.x/2.x Python naming."""
    return result.model_dump(by_alias=True)["isError"]


@pytest.mark.asyncio
async def test_new_request_cannot_be_satisfied_by_existing_connection():
    client = SimpleNamespace(config=SimpleNamespace(connect_mode="manual"), connected=True)
    result = await connection.handle("cst_connect", {"mode": "new"}, client)
    assert '"status": "error"' in result[0].text


def test_create_refuses_existing_destination_without_factory_or_suffix(tmp_path):
    path = tmp_path / "exact.cst"
    path.write_text("original")
    client = CSTClient(CSTConfig())
    client._de = SimpleNamespace(new_mws=lambda: pytest.fail("must not create"))
    result = client.new_project(str(path))
    assert result["status"] == "error"
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "open"])
async def test_registry_select_reads_live_filename_not_cached_request(tmp_path, action):
    import json

    from mcp.types import CallToolRequestParams

    from cst_mcp.tools import project
    from cst_mcp.tools.registry import ToolRegistry

    callbacks = {}

    class Server:
        def add_request_handler(self, name, params, callback):
            callbacks[name] = callback

    path = tmp_path / "exact.cst"
    if action == "open":
        path.write_text("controlled existing file")
    saved = []

    def save(p):
        saved.append(p)
        path.write_text("controlled saved file")

    live = SimpleNamespace(filename=lambda: str(path), save=save)
    client = CSTClient(CSTConfig())
    client._de = SimpleNamespace(pid=lambda: 42, open_project=lambda p: live, new_mws=lambda: live)
    from cst_mcp.controller_target import ControllerBinding

    client._controller_binding = ControllerBinding(
        "controlled-connection", client._de, 42, lambda: [42]
    )
    registry = ToolRegistry()
    registry.add_module(project.TOOLS, project.handle)
    registry.bind(Server(), client)
    request = CallToolRequestParams(
        name="cst_open_project" if action == "open" else "cst_create_project",
        arguments={"path": str(path)},
        _meta={
            "cst_controller": {
                "version": 1,
                "stage": "select",
                "path": str(path),
                "connection": "controlled-connection",
            }
        },
    )
    result = await callbacks["tools/call"](None, request)
    data = json.loads(result.content[0].text)
    assert data["controller"]["path"] == os.path.normcase(str(path.resolve()))
    assert data["controller"]["source"] == "held-project.filename"
    assert data["controller"]["connection"] == "controlled-connection"
    if action == "create":
        assert saved == [str(path)]
        saved.clear()
    # A changed API filename must fail before the next operation, even though
    # the session's cached requested path still contains the authorized target.
    live.filename = lambda: str(tmp_path / "wrong.cst")
    request.name = "cst_save_project"
    request.arguments = {}
    request.meta = {
        "cst_controller": {
            "version": 1,
            "stage": "bound",
            "path": str(path),
            "connection": "controlled-connection",
            "project": data["controller"]["project"],
        }
    }
    result = await callbacks["tools/call"](None, request)
    assert is_error(result)
    assert saved == []
    live.filename = lambda: str(path)
    client._de = SimpleNamespace(pid=lambda: 42)
    result = await callbacks["tools/call"](None, request)
    assert is_error(result)
    assert saved == []


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [[], [99], None])
async def test_actual_new_launch_refuses_conflicts_without_closing_them(
    tmp_path, monkeypatch, existing
):
    import sys

    launched = []
    running = existing
    de = SimpleNamespace(pid=lambda: 42, get_open_projects=list)

    def new():
        nonlocal running
        launched.append(True)
        running = [42]
        return de

    interface = SimpleNamespace(
        DesignEnvironment=SimpleNamespace(new=new),
        running_design_environments=lambda: running,
    )
    monkeypatch.setitem(sys.modules, "cst", SimpleNamespace(interface=interface))
    monkeypatch.setitem(sys.modules, "cst.interface", interface)
    client = CSTClient(CSTConfig())
    monkeypatch.setattr(CSTConfig, "cst_available", property(lambda self: True))
    call = registry_boundary(client)
    result, data = await call(
        "cst_connect",
        {"mode": "new"},
        {
            "version": 1,
            "stage": "launch",
        },
    )
    if existing == []:
        assert data["controller"]["stage"] == "launch"
        assert launched == [True]
        assert client._project is None
        result, status = await call(
            "cst_connection_status",
            {},
            {"version": 1, "stage": "connection", "connection": data["controller"]["connection"]},
        )
        assert not is_error(result)
        assert status["controller"]["project"] is None
    else:
        assert data["status"] == "error"
        assert launched == []


def registry_boundary(client):
    from mcp.types import CallToolRequestParams

    from cst_mcp.tools import geometry, import_export, project, workflows
    from cst_mcp.tools.registry import ToolRegistry

    callbacks = {}

    class Server:
        def add_request_handler(self, name, params, callback):
            callbacks[name] = callback

    registry = ToolRegistry()
    for module in (connection, project, geometry, import_export, workflows):
        registry.add_module(module.TOOLS, module.handle)
    registry.bind(Server(), client)

    async def call(name, args, metadata):
        result = await callbacks["tools/call"](
            None,
            CallToolRequestParams(name=name, arguments=args, _meta={"cst_controller": metadata}),
        )
        return result, json.loads(result.content[0].text)

    return call


def controlled_client(tmp_path):
    from cst_mcp.controller_target import ControllerBinding

    path = tmp_path / "exact.cst"
    saved, history = [], []

    def save(value):
        saved.append(value)
        Path(value).write_text("controlled project")

    live = SimpleNamespace(
        filename=lambda: str(path),
        save=save,
        model3d=SimpleNamespace(add_to_history=lambda *a, **kw: history.append(a)),
    )
    client = CSTClient(CSTConfig())
    client._de = SimpleNamespace(pid=lambda: 42, new_mws=lambda: live, open_project=lambda p: live)
    client._controller_binding = ControllerBinding("connection", client._de, 42, lambda: [42])
    client.is_solver_running = lambda **kw: False
    client.results_present = lambda: False
    return client, live, path, saved, history


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["create", "open"])
async def test_separate_selection_successive_operations_and_save(tmp_path, monkeypatch, action):
    import sys

    client, _live, path, saved, history = controlled_client(tmp_path)
    environment = client._de
    environment.get_open_projects = list
    running = []

    def new():
        running.append(42)
        return environment

    interface = SimpleNamespace(
        DesignEnvironment=SimpleNamespace(new=new), running_design_environments=lambda: running
    )
    monkeypatch.setitem(sys.modules, "cst", SimpleNamespace(interface=interface))
    monkeypatch.setitem(sys.modules, "cst.interface", interface)
    monkeypatch.setattr(CSTConfig, "cst_available", property(lambda self: True))
    client._de = None
    client._controller_binding = None
    call = registry_boundary(client)
    result, data = await call("cst_connect", {"mode": "new"}, {"version": 1, "stage": "launch"})
    assert not is_error(result)
    assert data["controller"]["sequence"] == 1
    assert client._project is None
    context = {"version": 1, "stage": "connection", "connection": data["controller"]["connection"]}
    result, data = await call("cst_connection_status", {}, context)
    assert not is_error(result)
    assert data["controller"]["project"] is None
    assert data["controller"]["sequence"] == 2
    result, data = await call("cst_project_info", {}, context)
    assert is_error(result)
    assert "Select and verify" in data["message"]
    assert client._project is None
    # A bound operation cannot turn an unselected instance into an active project.
    result, _ = await call(
        "cst_save_project",
        {},
        {**context, "stage": "bound", "path": str(path), "project": "invented"},
    )
    assert is_error(result)
    assert not saved
    if action == "open":
        path.write_text("existing project")
    context = {**context, "stage": "select", "path": str(path)}
    result, data = await call(f"cst_{action}_project", {"path": str(path)}, context)
    assert not is_error(result)
    assert data["controller"]["sequence"] == 3
    context = {**context, "stage": "bound", "project": data["controller"]["project"]}
    saved.clear()
    for index in range(2):
        result, data = await call(
            "cst_create_brick",
            {
                "name": f"brick{index}",
                "component": "component",
                "material": "PEC",
                "x_min": 0,
                "x_max": 1,
                "y_min": 0,
                "y_max": 1,
                "z_min": 0,
                "z_max": 1,
            },
            context,
        )
        assert not is_error(result)
        assert data["controller"]["sequence"] == 4 + index
        assert data["controller"]["project"] == context["project"]
    assert len(history) == 2
    assert saved == []  # selection never grants an automatic later save
    result, data = await call("cst_save_project", {}, context)
    assert not is_error(result)
    assert saved == [os.path.normcase(str(path.resolve()))]
    assert data["controller"]["sequence"] == 6


async def select_controlled(tmp_path):
    client, _live, path, _saved, history = controlled_client(tmp_path)
    call = registry_boundary(client)
    context = {"version": 1, "stage": "select", "connection": "connection", "path": str(path)}
    result, data = await call("cst_create_project", {"path": str(path)}, context)
    assert not is_error(result)
    context.update(stage="bound", project=data["controller"]["project"])
    return client, call, context, history


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,key", [("cst_export_cad", "file_path"), ("cst_export_project", "path")]
)
async def test_cad_actual_handler_safe_conflict_and_replacement(tmp_path, tool, key):
    _client, call, context, history = await select_controlled(tmp_path)
    output = tmp_path / "model.stl"
    args = {key: str(output), "format": "stl"}
    result, data = await call(tool, args, context)
    assert not is_error(result)
    assert str(output).replace("\\", "/").lower() in history[-1][1].replace("\\", "/").lower()
    output.write_text("keep this")
    before = len(history)
    result, data = await call(tool, args, context)
    assert is_error(result)
    assert "replacement authorization" in data["message"]
    assert len(history) == before
    assert output.read_text() == "keep this"
    # A model-visible argument cannot authorize replacement.
    result, _ = await call(tool, {**args, "replace_outputs": [str(output)]}, context)
    assert is_error(result)
    assert len(history) == before
    result, _ = await call(tool, args, {**context, "replace_outputs": [str(output)]})
    assert not is_error(result)
    assert len(history) == before + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["relative.stl", "exact.cst", "missing/model.stl", "model.sat"])
async def test_export_refuses_unknown_unsafe_or_wrong_format(tmp_path, bad):
    _, call, context, history = await select_controlled(tmp_path)
    output = bad if bad == "relative.stl" else str(tmp_path / bad)
    result, _ = await call("cst_export_cad", {"file_path": output, "format": "stl"}, context)
    assert is_error(result)
    assert history == []


@pytest.mark.asyncio
async def test_structure_views_precheck_all_outputs_no_unlink_and_no_retry(tmp_path):
    client, call, context, _ = await select_controlled(tmp_path)
    out = tmp_path / "images"
    out.mkdir()
    image = out / "view_front.png"
    image.write_bytes(b"existing evidence")
    codes = []

    def execute(code, *, timeout):
        codes.append(code)
        assert timeout == 30
        # A failure can still have written output. It must not delete or retry.
        assert image.read_bytes() == b"existing evidence"
        raise RuntimeError("controlled partial failure")

    client._project.schematic = SimpleNamespace(execute_vba_code=execute)
    args = {"out_dir": str(out), "views": ["front", "top"]}
    result, _ = await call("cst_export_structure_views", args, context)
    assert is_error(result)
    assert codes == []
    result, data = await call(
        "cst_export_structure_views", args, {**context, "replace_outputs": [str(image)]}
    )
    assert is_error(result)
    assert len(codes) == 1
    assert image.read_bytes() == b"existing evidence"
    assert client._controller_binding.project is client._project
    assert data["images"][0]["exists"]


@pytest.mark.asyncio
async def test_structure_views_healthy_actual_paths(tmp_path):
    import re

    client, call, context, _ = await select_controlled(tmp_path)
    written = []

    def execute(code, *, timeout):
        path = Path(re.search(r'Plot.ExportImage "([^"]+)"', code)[1])
        path.write_bytes(b"controlled image")
        written.append(path)

    client._project.schematic = SimpleNamespace(execute_vba_code=execute)
    result, data = await call(
        "cst_export_structure_views", {"out_dir": str(tmp_path), "views": ["xy", "xz"]}, context
    )
    assert not is_error(result)
    assert written == [tmp_path / "view_xy.png", tmp_path / "view_xz.png"]
    assert data["controller"]["project"] == context["project"]


@pytest.mark.asyncio
async def test_connection_only_route_cannot_bypass_selected_project_guard(tmp_path):
    client, call, context, _ = await select_controlled(tmp_path)
    client.status = lambda: pytest.fail("must refuse before status inspects a project")
    result, _ = await call(
        "cst_connection_status",
        {},
        {"version": 1, "stage": "connection", "connection": context["connection"]},
    )
    assert is_error(result)


@pytest.mark.asyncio
async def test_view_export_without_nonhistory_support_refuses_without_fallback(tmp_path):
    _, call, context, history = await select_controlled(tmp_path)
    result, _ = await call(
        "cst_export_structure_views", {"out_dir": str(tmp_path), "views": ["front"]}, context
    )
    assert is_error(result)
    assert history == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["connection", "project", "instance"])
async def test_export_rechecks_actual_target_before_handler(tmp_path, change):
    client, call, context, history = await select_controlled(tmp_path)
    if change == "connection":
        client._de = SimpleNamespace(pid=lambda: 42)
    elif change == "project":
        client._project.filename = lambda: str(tmp_path / "wrong.cst")
    else:
        client._controller_binding.instances = lambda: [42, 99]
    result, _ = await call(
        "cst_export_cad", {"file_path": str(tmp_path / "model.stl"), "format": "stl"}, context
    )
    assert is_error(result)
    assert history == []


@pytest.mark.asyncio
async def test_wrong_selection_readback_retains_live_project_and_destination(tmp_path):
    client, live, path, saved, _ = controlled_client(tmp_path)
    live.filename = lambda: str(tmp_path / "wrong.cst")
    call = registry_boundary(client)
    result, data = await call(
        "cst_create_project",
        {"path": str(path)},
        {"version": 1, "stage": "select", "connection": "connection", "path": str(path)},
    )
    assert is_error(result)
    assert client._project is live
    assert client._controller_binding.project is live
    assert saved == [str(path)]
    assert path.exists()
    assert data["operation_response"]["status"] == "created"
    assert data["execution_state"] == "unknown"


@pytest.mark.asyncio
async def test_export_conflicts_aliases_and_unknown_views_before_handler(tmp_path):
    _, call, context, history = await select_controlled(tmp_path)
    directory = tmp_path / "directory.stl"
    directory.mkdir()
    original = tmp_path / "original.stl"
    original.write_text("evidence")
    alias = tmp_path / "alias.stl"
    os.link(original, alias)
    for output in (
        directory,
        alias,
        tmp_path / "unsafe:stream.stl",
        tmp_path / "trailing. " / "x.stl",
    ):
        result, _ = await call(
            "cst_export_cad",
            {"file_path": str(output), "format": "stl"},
            {**context, "replace_outputs": [str(output)]},
        )
        assert is_error(result)
    for args in (
        {},
        {"out_dir": str(tmp_path), "views": ["../escape"]},
        {"out_dir": str(tmp_path), "views": ["front", "front"]},
    ):
        result, _ = await call("cst_export_structure_views", args, context)
        assert is_error(result)
    assert history == []
    assert original.read_text() == "evidence"
