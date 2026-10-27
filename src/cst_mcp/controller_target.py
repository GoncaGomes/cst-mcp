"""Opt-in Controller protocol, on the CST worker, not model tool arguments.

This is a single-instance destination guard, not complete process accounting.
Legacy clients retain their existing schemas. Missing support is detectable by
the read-only probe before a Controller can authorize a launch.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.types import TextContent


@dataclass(frozen=True)
class ExpectedTarget:
    stage: str
    path: str | None
    connection: str | None
    project: str | None

    @classmethod
    def parse(cls, value: Any) -> ExpectedTarget:
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value["version"] != 1
        ):
            raise ValueError("Unsupported Controller target protocol.")
        stage = value.get("stage")
        if stage not in {"probe", "launch", "select", "bound"}:
            raise ValueError("Unknown Controller stage.")
        path, connection, project = value.get("path"), value.get("connection"), value.get("project")
        if stage != "probe" and (not isinstance(path, str) or not path):
            raise ValueError("Expected destination is required.")
        if stage in {"select", "bound"} and (not isinstance(connection, str) or not connection):
            raise ValueError("Expected connection is required.")
        if stage == "bound" and (not isinstance(project, str) or not project):
            raise ValueError("Expected live project is required.")
        return cls(stage, path, connection, project)


def destination(value: str, *, exists: bool = False) -> str:
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".cst":
        raise ValueError("An absolute .cst destination is required.")
    if path.name.rstrip(" .") != path.name or ":" in path.name or path.is_reserved():
        raise ValueError("Ambiguous Windows destination names are refused.")
    parent = path.parent.resolve(strict=True)
    resolved = path.resolve(strict=exists)
    if resolved.parent != parent or (resolved.exists() and not resolved.is_file()):
        raise ValueError("Destination alias or directory conflict.")
    if resolved.exists() and resolved.stat().st_nlink != 1:
        raise ValueError("Hard-link project aliases are refused.")
    for entry in parent.iterdir():
        if entry.suffix.lower() == ".cst" and os.path.normcase(str(entry)) != os.path.normcase(
            str(path)
        ):
            raise ValueError("Each project requires its own dedicated directory.")
    return os.path.normcase(str(resolved))


@dataclass
class ControllerBinding:
    """Session-owned live object identity and acquisition order; never persisted."""

    connection: str
    environment: Any
    pid: int
    instances: Callable[[], list[int] | None]
    project: Any = None
    project_id: str | None = None
    sequence: int = 0

    def check_connection(self, client: Any) -> None:
        if (
            not client.connected
            or client._de is not self.environment
            or client._de_pid(client._de) != self.pid
        ):
            raise ValueError("Controller connection changed.")
        observed = self.instances()
        if observed is None or set(observed) != {self.pid}:
            raise ValueError("Conflicting CST instance detected, or instance check unavailable.")

    def readback(self, client: Any) -> dict[str, Any]:
        self.check_connection(client)
        if not client.has_project or self.project is None or client._project is not self.project:
            raise ValueError("Held live project is unavailable or changed.")
        filename = self.project.filename()
        if not isinstance(filename, str) or not filename:
            raise ValueError("Live project filename support is unavailable.")
        return {
            "version": 1,
            "connection": self.connection,
            "project": self.project_id,
            "path": destination(filename, exists=True),
            "source": "held-project.filename",
        }


async def invoke(
    handler: Any, name: str, args: dict, client: Any, context: dict
) -> list[TextContent]:
    """Validate immediately before the actual handler and read back after it."""
    from cst_mcp.tools.registry import err

    try:
        expected = ExpectedTarget.parse(context)
        stage = expected.stage
        if stage == "probe":
            if name != "cst_connection_status":
                raise ValueError("Probe must be read-only connection status.")
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "status": "ok",
                            "controller": {"version": 1, "stage": "probe"},
                        }
                    ),
                )
            ]
        if expected.path is None:
            raise ValueError("Expected destination is required.")
        target = destination(expected.path)
        binding = client._controller_binding
        instances = None
        if stage == "launch":
            if name != "cst_connect" or args != {"mode": "new"} or client.connected:
                raise ValueError("Explicit disconnected new-instance launch required.")
            import cst.interface  # type: ignore[import-not-found] - vendor library is optional offline

            before = client._running_de_pids(cst.interface)
            if before != []:
                raise ValueError(
                    "CST must be closed; conflicting instance or unknown instance check."
                )
            instances = lambda: client._running_de_pids(cst.interface)
        else:
            if binding is None or expected.connection != binding.connection:
                raise ValueError("Expected Controller connection does not match.")
            if stage == "select":
                binding.check_connection(client)
                if name not in {"cst_create_project", "cst_open_project"} or client.has_project:
                    raise ValueError("Select requires an empty Controller-owned session.")
                if destination(args["path"], exists=name == "cst_open_project") != target:
                    raise ValueError("Selection destination does not match.")
                if name == "cst_create_project" and Path(target).exists():
                    raise ValueError("Creation destination already exists.")
            elif stage == "bound":
                observed = binding.readback(client)
                if observed["path"] != target or observed["project"] != expected.project:
                    raise ValueError("Expected actual target does not match live project.")
                if name in {
                    "cst_connect",
                    "cst_open_project",
                    "cst_create_project",
                    "cst_export_cad",
                    "cst_export_structure_views",
                } or name.startswith("cst_workflow"):
                    raise ValueError(
                        "Lifecycle/workflow operation requires a separate reviewed route."
                    )
                if (
                    name == "cst_save_project"
                    and args.get("path") is not None
                    and destination(args["path"]) != target
                ):
                    raise ValueError("Save destination does not match selected project.")
                if name == "cst_save_project":
                    # Never use the legacy cached requested destination for a
                    # no-argument save; dispatch the checked actual destination.
                    args = {**args, "path": target}
            else:
                raise ValueError("Unknown Controller stage.")
        content = await handler(name, args, client)
        payload = json.loads(content[0].text)
        if payload.get("status") in {"error", "offline", "busy", "timeout"}:
            return content
        if stage == "launch":
            pid = client._de_pid(client._de)
            if (
                pid is None
                or client.has_project
                or payload.get("open_projects") != 0
                or instances is None
                or instances() != [pid]
            ):
                raise ValueError(
                    "New instance has unexpected project/instance state; inspect locally."
                )
            binding = ControllerBinding(str(uuid.uuid4()), client._de, pid, instances)
            client._controller_binding = binding
            evidence = {
                "version": 1,
                "connection": binding.connection,
                "stage": "launch",
            }
        elif name in {"cst_close_project", "cst_disconnect"}:
            client._controller_binding = None
            evidence = {"version": 1, "stage": "invalidated"}
        else:
            if stage == "select":
                binding.project = client._project
                binding.project_id = str(uuid.uuid4())
            evidence = binding.readback(client)
            if evidence["path"] != target:
                raise ValueError(
                    "Actual destination differs from selected target; inspect locally."
                )
        binding.sequence += 1
        evidence["sequence"] = binding.sequence
        payload["controller"] = evidence
        return [TextContent(type="text", text=json.dumps(payload))]
    except Exception:  # noqa: BLE001 - sanitize arbitrary vendor errors at the protocol boundary
        # Native errors can contain arbitrary filenames/content. Recovery owns
        # the incident; never retry, close another instance or claim rollback.
        return err("Controller live target check failed; inspect instance/destination locally.")
