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
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.types import TextContent


class ControllerRefusal(ValueError):
    """Safe, fixed diagnostic; vendor exception text must never be exposed."""


guarded_view_export: ContextVar[bool] = ContextVar("guarded_view_export", default=False)


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
            raise ControllerRefusal("Unsupported Controller target protocol.")
        stage = value.get("stage")
        if stage not in {"probe", "launch", "connection", "select", "bound"}:
            raise ControllerRefusal("Unknown Controller stage.")
        path, connection, project = value.get("path"), value.get("connection"), value.get("project")
        if stage in {"select", "bound"} and (not isinstance(path, str) or not path):
            raise ControllerRefusal("Expected destination is required at project selection.")
        if stage in {"connection", "select", "bound"} and (
            not isinstance(connection, str) or not connection
        ):
            raise ControllerRefusal("Expected connection is required.")
        if stage == "bound" and (not isinstance(project, str) or not project):
            raise ControllerRefusal(
                "Expected live project is required; select and verify a project."
            )
        return cls(stage, path, connection, project)


def destination(value: str, *, exists: bool = False) -> str:
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".cst":
        raise ControllerRefusal("An absolute .cst destination is required.")
    if path.name.rstrip(" .") != path.name or ":" in path.name or path.is_reserved():
        raise ControllerRefusal("Ambiguous Windows destination names are refused.")
    parent = path.parent.resolve(strict=True)
    resolved = path.resolve(strict=exists)
    if resolved.parent != parent or (resolved.exists() and not resolved.is_file()):
        raise ControllerRefusal("Destination alias or directory conflict.")
    if resolved.exists() and resolved.stat().st_nlink != 1:
        raise ControllerRefusal("Hard-link project aliases are refused.")
    for entry in parent.iterdir():
        if entry.suffix.lower() == ".cst" and os.path.normcase(str(entry)) != os.path.normcase(
            str(path)
        ):
            raise ControllerRefusal("Each project requires its own dedicated directory.")
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
            raise ControllerRefusal("Controller connection changed; re-establish prerequisites.")
        observed = self.instances()
        if observed is None or set(observed) != {self.pid}:
            raise ControllerRefusal(
                "Conflicting CST instance detected, or instance check unavailable."
            )

    def readback(self, client: Any) -> dict[str, Any]:
        self.check_connection(client)
        if not client.has_project or self.project is None or client._project is not self.project:
            raise ControllerRefusal(
                "Held live project is unavailable or changed; select and verify a project."
            )
        reader = getattr(self.project, "filename", None)
        if not callable(reader):
            raise ControllerRefusal("Live project filename support is unavailable.")
        filename = reader()
        if not isinstance(filename, str) or not filename:
            raise ControllerRefusal("Live project filename support is unavailable.")
        return {
            "version": 1,
            "connection": self.connection,
            "project": self.project_id,
            "path": destination(filename, exists=True),
            "source": "held-project.filename",
        }


def output_destination(value: Any) -> str:
    """Require a known existing parent and a plain, non-project output file."""
    if not isinstance(value, str) or not value:
        raise ControllerRefusal("Export requires an explicit absolute output destination.")
    path = Path(value)
    if not path.is_absolute() or not path.suffix or path.suffix.lower() == ".cst":
        raise ControllerRefusal("Export requires an absolute non-project output file.")
    for part in (path, *path.parents):
        if part == part.parent:
            continue
        if (
            part.name.rstrip(" .") != part.name
            or ":" in part.name
            or part.is_reserved()
            or part.is_symlink()
            or getattr(part, "is_junction", lambda: False)()
        ):
            raise ControllerRefusal("Ambiguous or linked export destinations are refused.")
    if not path.parent.is_dir():
        raise ControllerRefusal("Export parent directory must already exist; choose a destination.")
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise ControllerRefusal("Export destination has a directory or hard-link conflict.")
    return os.path.normcase(str(path.resolve()))


def export_arguments(name: str, args: dict, context: dict) -> dict:
    """Guard actual handler filenames; replacement grants come only from the app."""
    if name in {"cst_export_cad", "cst_export_project"}:
        extensions = {
            "stl": {".stl"},
            "sat": {".sat"},
            "stp": {".stp", ".step"},
            "igs": {".igs", ".iges"},
            "obj": {".obj"},
            "nas": {".nas"},
        }
        key = "path" if name == "cst_export_project" else "file_path"
        path = output_destination(args.get(key))
        if Path(path).suffix.lower() not in extensions.get(args.get("format"), set()):
            raise ControllerRefusal("CAD output extension must match the requested format.")
        outputs = [path]
        args = {**args, key: path}
    else:
        out = args.get("out_dir")
        if not isinstance(out, str) or not out or not Path(out).is_absolute():
            raise ControllerRefusal(
                "Structure views require an explicit absolute output directory."
            )
        views = args.get("views") or ["perspective", "front", "top", "left", "right"]
        supported = {
            "perspective",
            "front",
            "back",
            "top",
            "bottom",
            "left",
            "right",
            "xy",
            "xz",
            "yz",
        }
        if any(view not in supported for view in views) or len(set(views)) != len(views):
            raise ControllerRefusal("Unsupported or duplicate structure view; choose named views.")
        outputs = [output_destination(str(Path(out) / f"view_{view}.png")) for view in views]
        args = {**args, "out_dir": str(Path(outputs[0]).parent), "views": views}
    replacements = context.get("replace_outputs", [])
    if not isinstance(replacements, list):
        raise ControllerRefusal("Replacement authorization must list exact output paths.")
    authorized = {output_destination(value) for value in replacements}
    if not authorized.issubset(outputs):
        raise ControllerRefusal("Replacement authorization does not match actual export outputs.")
    if any(Path(path).exists() and path not in authorized for path in outputs):
        raise ControllerRefusal(
            "Export output already exists; explicit replacement authorization required."
        )
    return args


async def invoke(
    handler: Any, name: str, args: dict, client: Any, context: dict
) -> list[TextContent]:
    """Validate immediately before the actual handler and read back after it."""
    from cst_mcp.tools.registry import err

    export_token = guarded_view_export.set(False)
    payload = None
    try:
        expected = ExpectedTarget.parse(context)
        stage = expected.stage
        if stage == "probe":
            if name != "cst_connection_status":
                raise ControllerRefusal("Probe must be read-only connection status.")
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
        target = destination(expected.path) if stage in {"select", "bound"} else None
        binding = client._controller_binding
        instances = None
        if stage == "launch":
            if name != "cst_connect" or args != {"mode": "new"} or client.connected:
                raise ControllerRefusal("Explicit disconnected new-instance launch required.")
            import cst.interface  # type: ignore[import-not-found] - vendor library is optional offline

            before = client._running_de_pids(cst.interface)
            if before != []:
                raise ControllerRefusal(
                    "CST must be closed; conflicting instance or unknown instance check."
                )
            instances = lambda: client._running_de_pids(cst.interface)
        else:
            if binding is None or expected.connection != binding.connection:
                raise ControllerRefusal("Expected Controller connection does not match.")
            if stage == "connection":
                binding.check_connection(client)
                if name != "cst_connection_status":
                    raise ControllerRefusal(
                        "Select and verify an exact project before project operations."
                    )
                if binding.project is not None or client.has_project:
                    raise ControllerRefusal(
                        "Connection-only status requires an unselected instance; "
                        "use the expected bound project for selected-project status."
                    )
            elif stage == "select":
                binding.check_connection(client)
                if name not in {"cst_create_project", "cst_open_project"} or client.has_project:
                    raise ControllerRefusal("Select requires an empty Controller-owned session.")
                if destination(args["path"], exists=name == "cst_open_project") != target:
                    raise ControllerRefusal("Selection destination does not match.")
                if name == "cst_create_project" and Path(target).exists():
                    raise ControllerRefusal("Creation destination already exists.")
            elif stage == "bound":
                observed = binding.readback(client)
                if observed["path"] != target or observed["project"] != expected.project:
                    raise ControllerRefusal("Expected actual target does not match live project.")
                if name in {
                    "cst_connect",
                    "cst_open_project",
                    "cst_create_project",
                } or name.startswith("cst_workflow"):
                    raise ControllerRefusal(
                        "Lifecycle/workflow operation requires a separate reviewed route."
                    )
                if (
                    name == "cst_save_project"
                    and args.get("path") is not None
                    and destination(args["path"]) != target
                ):
                    raise ControllerRefusal("Save destination does not match selected project.")
                if name == "cst_save_project":
                    # Never use the legacy cached requested destination for a
                    # no-argument save; dispatch the checked actual destination.
                    args = {**args, "path": target}
                if name in {"cst_export_cad", "cst_export_project", "cst_export_structure_views"}:
                    args = export_arguments(name, args, context)
                    guarded_view_export.set(name == "cst_export_structure_views")
            else:
                raise ControllerRefusal("Unknown Controller stage.")
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
                raise ControllerRefusal(
                    "New instance has unexpected project/instance state; inspect locally."
                )
            binding = ControllerBinding(str(uuid.uuid4()), client._de, pid, instances)
            client._controller_binding = binding
            evidence = {
                "version": 1,
                "connection": binding.connection,
                "stage": "launch",
            }
        elif stage == "connection":
            binding.check_connection(client)
            if binding.project is None and client.has_project:
                raise ControllerRefusal("Unexpected held project; no project was adopted.")
            evidence = {
                "version": 1,
                "connection": binding.connection,
                "stage": "connection",
                "project": None,
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
                raise ControllerRefusal(
                    "Actual destination differs from selected target; inspect locally."
                )
        binding.sequence += 1
        evidence["sequence"] = binding.sequence
        payload["controller"] = evidence
        return [TextContent(type="text", text=json.dumps(payload))]
    except ControllerRefusal as exc:
        return err(
            str(exc),
            **({"operation_response": payload, "execution_state": "unknown"} if payload else {}),
        )
    except Exception:  # noqa: BLE001 - sanitize arbitrary vendor errors at the protocol boundary
        # Native errors can contain arbitrary filenames/content. Recovery owns
        # the incident; never retry, close another instance or claim rollback.
        return err(
            "Controller live target check failed; inspect instance/destination locally.",
            **({"operation_response": payload, "execution_state": "unknown"} if payload else {}),
        )
    finally:
        guarded_view_export.reset(export_token)
