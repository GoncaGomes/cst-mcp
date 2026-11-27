"""cst_delete_port: Port.Delete with the port number, existence check and readback (MCP 20)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

from cst_mcp.config import CSTConfig
from cst_mcp.cst_client import CSTClient
from cst_mcp.tools import ports


class FakeModel:
    """model3d fake: queries list the current ports; ``Port.Delete <n>`` history removes one."""

    def __init__(self, numbers: list[int], *, honour_delete: bool = True) -> None:
        self.numbers = list(numbers)
        self.honour_delete = honour_delete
        self.history: list[str] = []

    def is_solver_running(self, *, timeout=None):
        return False

    def add_to_history(self, title, code, *args, **kwargs):
        self.history.append(code)
        deleted = re.fullmatch(r"Port\.Delete (\d+)", code.strip())
        if deleted and self.honour_delete and int(deleted[1]) in self.numbers:
            self.numbers.remove(int(deleted[1]))

    def _execute_vba_code(self, code: str, timeout=None) -> None:
        opened = re.search(r'Open "([^"]+)" For Output', code)
        if opened:
            lines = [f"count\t{len(self.numbers)}", *(f"port\t{n}" for n in self.numbers)]
            Path(opened[1]).write_text("\n".join(lines) + "\n", encoding="ascii")


def _client(tmp_path, model: FakeModel) -> CSTClient:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    client.config.work_dir = tmp_path
    client._de = object()
    client._project = SimpleNamespace(model3d=model)
    return client


async def _delete(client, port_number) -> dict:
    content = await ports.handle("cst_delete_port", {"port_number": port_number}, client)
    return json.loads(content[0].text)


async def test_delete_passes_port_number_to_delete(tmp_path) -> None:
    model = FakeModel([1, 2])
    result = await _delete(_client(tmp_path, model), 1)
    assert model.history == ["Port.Delete 1"]
    assert result["status"] == "executed"
    assert result["action"] == "deleted"
    assert result["remaining_ports"] == [2]


async def test_missing_port_is_rejected_without_history(tmp_path) -> None:
    model = FakeModel([1, 3])
    result = await _delete(_client(tmp_path, model), 2)
    assert model.history == []
    assert result["status"] == "error"
    assert "action" not in result
    assert result["existing_ports"] == [1, 3]
    assert "defined ports: 1, 3" in result["message"]


async def test_no_ports_message_says_none(tmp_path) -> None:
    result = await _delete(_client(tmp_path, FakeModel([])), 1)
    assert result["status"] == "error"
    assert "defined ports: none" in result["message"]


async def test_port_still_listed_after_delete_is_an_error(tmp_path) -> None:
    model = FakeModel([1], honour_delete=False)
    result = await _delete(_client(tmp_path, model), 1)
    assert model.history == ["Port.Delete 1"]
    assert result["status"] == "error"
    assert "action" not in result
    assert result["remaining_ports"] == [1]


async def test_failed_execution_does_not_claim_deleted(tmp_path) -> None:
    class FailingModel(FakeModel):
        def add_to_history(self, title, code, *args, **kwargs):
            raise RuntimeError("This method call requires a different number of arguments")

    result = await _delete(_client(tmp_path, FailingModel([1])), 1)
    assert result["status"] == "error"
    assert "action" not in result


async def test_offline_returns_vba_without_claiming_deleted() -> None:
    client = CSTClient(CSTConfig(connect_mode="disabled"))
    result = await _delete(client, 4)
    assert result["status"] == "offline"
    assert result["vba"] == "Port.Delete 4"
    assert "action" not in result


async def test_invalid_port_number_is_rejected(tmp_path) -> None:
    model = FakeModel([1])
    result = await _delete(_client(tmp_path, model), 0)
    assert result["status"] == "error"
    assert model.history == []


def test_port_numbers_query_has_no_history_or_delete() -> None:
    assert "Port.StartPortNumberIteration" in ports._PORT_NUMBERS_VBA
    assert "Delete" not in ports._PORT_NUMBERS_VBA
