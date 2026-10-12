# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["mcp>=1.29,<3", "jsonschema>=4.20"]
# ///
"""Local response/preservation tests. No CST instance, server or handlers."""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

import run_batch as batch_module
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from preserving_stdio import preserve_cst_processes
from run_batch import Batch, Case, UnknownState, classify, interpret, parse_shapes, reported_timeout


class ResponseTests(unittest.TestCase):
    def test_multiblock_error_without_structured_content_is_preserved(self):
        raw = {
            "isError": True,
            "content": [
                {"type": "text", "text": "Output schema failed"},
                {"type": "image", "data": "unchanged", "mimeType": "image/png"},
                {
                    "type": "text",
                    "text": json.dumps(
                        {"status": "error", "message": "bad object", "vba": "With Brick\nEnd With"}
                    ),
                },
            ],
        }
        saved = json.loads(json.dumps(raw))
        decoded = interpret(raw)
        result = classify(Case("error", "cst_create_brick"), raw, decoded, False, True)
        self.assertEqual(decoded["payload"]["message"], "bad object")
        self.assertEqual(result["classification"], "failure")
        self.assertFalse(result["executed_in_real_cst"])
        self.assertEqual(raw, saved)
        self.assertEqual(len(raw["content"]), 3)

    def test_offline_and_wrapped_timeouts_never_count_as_execution(self):
        for payload, expected in [
            ({"status": "offline", "vba": "generated"}, "offline_only"),
            ({"status": "timeout", "execution_state": "unknown"}, "indeterminate_state"),
            (
                {
                    "status": "error",
                    "connection": {"status": "offline", "message": "native call timed out"},
                },
                "indeterminate_state",
            ),
            ({"status": "busy", "running": None}, "indeterminate_state"),
            (
                {"mode": "connected", "project_open": True, "solver_running": None},
                "indeterminate_state",
            ),
            ({"status": "error", "connection": {"status": "offline"}}, "failure"),
        ]:
            with self.subTest(payload=payload):
                raw = {"content": [{"type": "text", "text": json.dumps(payload)}]}
                result = classify(
                    Case("probe", "cst_create_brick"), raw, interpret(raw), False, True
                )
                self.assertEqual(result["classification"], expected)
                self.assertFalse(result["executed_in_real_cst"])
                if payload.get("status") == "busy":
                    self.assertFalse(reported_timeout(raw, interpret(raw)))

    def test_statusless_database_response_and_incomplete_shapes(self):
        raw = {"content": [{"type": "text", "text": '{"material":{"name":"Copper"}}'}]}
        result = classify(
            Case("database", "cst_get_material_info", kind="database"),
            raw,
            interpret(raw),
            False,
            True,
        )
        self.assertEqual(result["classification"], "database_query")
        self.assertFalse(result["executed_in_real_cst"])
        with self.assertRaises(ValueError):
            parse_shapes("COUNT\t2\nSOLID\t0\tBatch:Brick\tPEC\nDONE\n")
        self.assertEqual(parse_shapes("COUNT\t0\nDONE\n"), {})


class PreservationTests(unittest.IsolatedAsyncioTestCase):
    def options(self):
        return argparse.Namespace(
            preflight=True,
            cst_path=batch_module.DEFAULT_CST_PATH,
            call_timeout=0.01,
            connection_timeout=0.01,
        )

    async def test_client_timeout_stops_calls_and_keeps_terminal_log(self):
        class SlowSession:
            calls = 0

            async def call_tool(self, name, args):
                self.calls += 1
                await asyncio.sleep(1)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(batch_module, "BATCH_DIR", Path(temporary)),
        ):
            runner = Batch(self.options())
            runner.catalog = {"probe": {"inputSchema": {"type": "object"}}}
            session = SlowSession()
            with self.assertRaises(UnknownState):
                await runner.request(session, Case("slow", "probe"))
            with self.assertRaises(UnknownState):
                await runner.request(session, Case("forbidden", "probe"))
            self.assertEqual(session.calls, 1)
            runner.exit_code = 2
            runner.reason = "Client timeout"
            runner.finalize()
            events = [
                json.loads(line)
                for line in (runner.work / "mcp_calls.jsonl").read_text().splitlines()
            ]
            terminal = [e for e in events if e["event"] == "error"]
            self.assertEqual(len(terminal), 1)
            self.assertTrue(terminal[0]["timeout"])
            summary = json.loads((runner.work / "summary.json").read_text())
            self.assertEqual(summary["state"], "indeterminate")
            self.assertEqual(summary["exit_code"], 2)

    async def test_interrupt_finalizes_summary_without_cleanup_calls(self):
        class InterruptedSession:
            methods: ClassVar[list[str]] = []

            def __init__(self, *args):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def initialize(self):
                self.methods.append("initialize")
                raise asyncio.CancelledError()

            async def call_tool(self, name, args):
                self.methods.append(name)
                raise AssertionError("No calls may follow an interrupted request")

        @asynccontextmanager
        async def transport(*args, **kwargs):
            yield None, None

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(batch_module, "BATCH_DIR", Path(temporary)),
            patch.object(batch_module, "ClientSession", InterruptedSession),
            patch.object(batch_module, "stdio_client", transport),
            patch.object(Batch, "probe_import"),
        ):
            runner = Batch(self.options())
            self.assertEqual(await runner.run(), 130)
            self.assertEqual(InterruptedSession.methods, ["initialize"])
            summary = json.loads((runner.work / "summary.json").read_text())
            self.assertEqual(summary["state"], "indeterminate")
            self.assertFalse(summary["completed"])
            self.assertIn("Interrupted", (runner.work / "summary.md").read_text(encoding="utf-8"))
            self.assertTrue(
                all(
                    (runner.work / file).exists()
                    for file in [
                        "metadata.json",
                        "tool_catalog.json",
                        "mcp_calls.jsonl",
                        "server_stderr.log",
                        "cst_messages.jsonl",
                    ]
                )
            )

    async def test_real_sdk_shutdown_escalation_preserves_descendant(self):
        # A finite-lived Python child represents CST process ownership, with no
        # vendor imports. The parent deliberately ignores EOF to force escalation.
        script = """import pathlib, subprocess, sys, time
folder = pathlib.Path(sys.argv[1])
child_code = "import pathlib, sys, time; time.sleep(4); pathlib.Path(sys.argv[1]).write_text('survived')"
child = subprocess.Popen([sys.executable, "-c", child_code, str(folder / "survived.txt")],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=folder.parent)
(folder / "ready.txt").write_text(str(child.pid))
sys.stdin.buffer.read()
time.sleep(30)
"""
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            params = StdioServerParameters(
                command=sys.executable,
                args=["-c", script, temporary],
                cwd=temporary,
                env=os.environ.copy(),
            )
            with (folder / "stderr.log").open("w") as stderr, preserve_cst_processes():
                async with stdio_client(params, errlog=stderr):
                    deadline = asyncio.get_running_loop().time() + 10
                    while not (folder / "ready.txt").exists():
                        self.assertLess(asyncio.get_running_loop().time(), deadline)
                        await asyncio.sleep(0.02)
            deadline = asyncio.get_running_loop().time() + 10
            while not (folder / "survived.txt").exists():
                self.assertLess(
                    asyncio.get_running_loop().time(),
                    deadline,
                    "SDK shutdown killed the descendant or it did not finish",
                )
                await asyncio.sleep(0.02)
            self.assertEqual((folder / "survived.txt").read_text(), "survived")

    async def test_complete_multiblock_response_and_vba_reach_disk(self):
        raw = {
            "isError": True,
            "content": [
                {"type": "text", "text": "Output schema mismatch"},
                {"type": "image", "data": "preserved", "mimeType": "image/png"},
                {
                    "type": "text",
                    "text": json.dumps(
                        {"status": "error", "message": "failed", "vba": "With Brick\nEnd With"}
                    ),
                },
            ],
        }

        class Session:
            async def call_tool(self, name, args):
                return raw

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.object(batch_module, "BATCH_DIR", Path(temporary)),
        ):
            runner = Batch(self.options())
            runner.catalog = {"probe": {"inputSchema": {"type": "object"}}}
            await runner.request(Session(), Case("multiblock", "probe"))
            runner.reason = "Expected local error fixture"
            runner.finalize()
            events = [
                json.loads(line)
                for line in (runner.work / "mcp_calls.jsonl").read_text().splitlines()
            ]
            completion = next(event for event in events if event["event"] == "completion")
            self.assertEqual(completion["response"], raw)
            self.assertTrue(completion["isError"])
            sources = [event["source"] for event in events if event["event"] == "vba_saved"]
            self.assertIn("content[2].text", sources[0])
            self.assertEqual(
                next((runner.work / "vba").glob("*.bas")).read_text(), "With Brick\nEnd With"
            )


if __name__ == "__main__":
    unittest.main()
