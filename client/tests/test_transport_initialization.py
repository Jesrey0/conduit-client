import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from client.conduit.transport import MCPTransport


class InitializationConcurrencyTests(unittest.TestCase):
    def test_concurrent_first_calls_share_one_initialization(self):
        async def scenario():
            transport = MCPTransport("https://host")
            calls = 0

            async def initialize_once():
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.01)
                transport._session_id = "session-1"
                transport._protocol_version = "2025-11-25"
                return {"ok": True}

            async def fake_rpc(method, params=None, **_kwargs):
                return {"method": method, "name": (params or {}).get("name")}

            with patch.object(transport, "_initialize_unlocked", side_effect=initialize_once), patch.object(transport, "_rpc", side_effect=fake_rpc):
                results = await asyncio.gather(
                    transport.call("workspace.current"),
                    transport.call("git.status"),
                    transport.call("runtime.health"),
                )
            await transport.close()
            self.assertEqual(calls, 1)
            self.assertEqual(len(results), 3)

        asyncio.run(scenario())

    def test_failed_initialization_can_be_retried(self):
        async def scenario():
            transport = MCPTransport("https://host")
            calls = 0
            async def initialize():
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError("first failure")
                transport._session_id = "session-2"
                transport._protocol_version = "2025-11-25"
                return {"ok": True}
            with patch.object(transport, "_initialize_unlocked", side_effect=initialize):
                with self.assertRaisesRegex(RuntimeError, "first failure"):
                    await transport.ensure_initialized()
                await transport.ensure_initialized()
            await transport.close()
            self.assertEqual(calls, 2)

        asyncio.run(scenario())
