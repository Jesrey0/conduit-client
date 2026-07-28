"""FLAG-2 client unit tests: redirects must not be followed.

Stdlib-only (unittest). Run from the repo root:

    python3 -m unittest client.tests.test_transport_redirects -v

MCPTransport carries a long-lived Authorization: Bearer <client_token>
header. httpx's default redirect behavior re-sends the same headers
(including Authorization) to the redirect target, which would leak the
bearer token to an unexpected host if the server (or an intermediary, e.g.
a misbehaving tunnel) ever issued a redirect. The transport must construct
its httpx.AsyncClient with follow_redirects=False so redirects are never
silently followed.
"""

from __future__ import annotations

import unittest

from client.conduit.transport import MCPTransport


class RedirectPolicyTests(unittest.TestCase):
    def test_client_is_constructed_with_redirects_disabled(self):
        t = MCPTransport("https://host/mcp", client_token="ctkn.example")
        try:
            # httpx.AsyncClient exposes the configured redirect policy via
            # follow_redirects on the underlying client.
            self.assertFalse(t._client.follow_redirects)
        finally:
            t._client._transport = None  # avoid unawaited-close warnings in sync test

    def test_client_is_constructed_with_redirects_disabled_without_token(self):
        # Same policy applies even when no client_token is set (defense in
        # depth / consistency), since a token may be attached later via
        # header mutation in future code paths.
        t = MCPTransport("https://host/mcp")
        try:
            self.assertFalse(t._client.follow_redirects)
        finally:
            t._client._transport = None


if __name__ == "__main__":
    unittest.main()
