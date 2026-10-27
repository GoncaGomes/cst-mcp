"""Actual server operation boundary with controlled live API objects; no CST launch."""

from types import SimpleNamespace

import pytest

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import connection


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

    from mcp.types import CallToolRequestParams, RequestParamsMeta

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
        meta=RequestParamsMeta(
            cst_controller={
                "version": 1,
                "stage": "select",
                "path": str(path),
                "connection": "controlled-connection",
            }
        ),
    )
    result = await callbacks["tools/call"](None, request)
    data = json.loads(result.content[0].text)
    assert data["controller"]["path"] == str(path.resolve()).lower()
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
    request.meta = RequestParamsMeta(
        cst_controller={
            "version": 1,
            "stage": "bound",
            "path": str(path),
            "connection": "controlled-connection",
            "project": data["controller"]["project"],
        }
    )
    result = await callbacks["tools/call"](None, request)
    assert result.is_error
    assert saved == []
    live.filename = lambda: str(path)
    client._de = SimpleNamespace(pid=lambda: 42)
    result = await callbacks["tools/call"](None, request)
    assert result.is_error
    assert saved == []


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [[], [99], None])
async def test_actual_new_launch_refuses_conflicts_without_closing_them(
    tmp_path, monkeypatch, existing
):
    import json
    import sys

    from cst_mcp.controller_target import invoke

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
    client = CSTClient(
        SimpleNamespace(
            cst_available=True,
            cst_path=None,
            python_lib_path=None,
            connect_mode="manual",
        )
    )
    result = await invoke(
        connection.handle,
        "cst_connect",
        {"mode": "new"},
        client,
        {
            "version": 1,
            "stage": "launch",
            "path": str(tmp_path / "exact.cst"),
        },
    )
    data = json.loads(result[0].text)
    if existing == []:
        assert data["controller"]["stage"] == "launch"
        assert launched == [True]
        assert client._project is None
    else:
        assert data["status"] == "error"
        assert launched == []
