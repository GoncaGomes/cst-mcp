"""Batch-local process policy for the real MCP SDK stdio transport.

The SDK's Windows Job Object normally kills descendants at shutdown, even
after a graceful server exit. CST must survive. Reuse the SDK's framing,
streams, stderr handling and shutdown, but launch without its kill scope and
restrict escalation to the Python server PID. No installed package is edited.
"""

import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import patch

import anyio
import mcp.client.stdio as sdk_stdio


async def _spawn_server_only(command, args, env=None, errlog=None, cwd=None):
    options = {"env": env, "stderr": errlog, "cwd": cwd}
    if sys.platform == "win32":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    # No Job Object, process group, shell, watcher or additional launcher.
    return await anyio.open_process([command, *args], **options)


async def _terminate_server_only(process):
    # Windows terminate() addresses only this process, not descendants.
    try:
        process.terminate()
    except ProcessLookupError:
        return
    with anyio.move_on_after(2):
        while process.returncode is None:
            await anyio.sleep(0.01)
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass


@contextmanager
def preserve_cst_processes():
    """Scope two client hooks to this batch; refuse an unrecognized SDK layout.

    These hooks exist in the inspected SDK 1.29.0 and 2.3.0. They are private:
    missing hooks fail before any server or CST instance can be started.
    """
    for name in ["_create_platform_compatible_process", "_terminate_process_tree"]:
        if not callable(getattr(sdk_stdio, name, None)):
            raise TypeError(f"MCP SDK lacks preservation hook {name}; refuse transport startup")
    with (
        patch.object(sdk_stdio, "_create_platform_compatible_process", _spawn_server_only),
        patch.object(sdk_stdio, "_terminate_process_tree", _terminate_server_only),
    ):
        yield
