
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from client.conduit.sdk import Conduit

class InMemorySessionTests(unittest.TestCase):
    def test_constructor_does_not_create_session_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"HOME": td}, clear=False):
            client = Conduit("https://host")
            try:
                self.assertFalse((Path(td) / ".conduit_state.json").exists())
            finally:
                asyncio.run(client.close())

    def test_owned_session_terminates_on_close(self) -> None:
        client = Conduit("https://host")
        terminate = AsyncMock()
        close = AsyncMock()
        with patch.object(client.transport, "terminate", terminate), patch.object(client.transport, "close", close):
            asyncio.run(client.close())
        terminate.assert_awaited_once()
        close.assert_awaited_once()

    def test_workspace_constructor_binds_after_initialization(self) -> None:
        client = Conduit("https://host", workspace="bar-coop-sys")
        ensure_initialized = AsyncMock()
        call = AsyncMock(return_value={"structuredContent": {"success": True, "workspace": {"id": "bar-coop-sys"}}})
        try:
            with patch.object(client.transport, "ensure_initialized", ensure_initialized), patch.object(client.transport, "call", call):
                result = asyncio.run(client.__aenter__())
            self.assertIs(result, client)
            ensure_initialized.assert_awaited_once()
            call.assert_awaited_once_with("workspace.bind", {"id": "bar-coop-sys"})
        finally:
            asyncio.run(client.close(terminate_session=False))

    def test_terminal_wait_attaches_until_process_completes(self) -> None:
        client = Conduit("https://host")
        snapshots = [{"complete": False}, {"complete": True, "exitCode": 0}]
        attach = AsyncMock(side_effect=snapshots)
        sleep = AsyncMock()
        try:
            with patch.object(client.terminal, "attach", attach), patch("client.conduit.sdk.asyncio.sleep", sleep):
                result = asyncio.run(client.terminal.wait("proc-1", timeout_s=1, poll_s=0, tail_bytes=123))
            self.assertEqual(result, {"complete": True, "exitCode": 0})
        finally:
            asyncio.run(client.close())
