"""safeEdit() unit tests: no-op detection and dry-run preview enforcement."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock

from client.conduit.errors import ToolError
from client.conduit.sdk import Conduit, safeEdit


def _edit_result(applied: bool, changed: bool) -> dict[str, Any]:
    return {
        "path": "f.txt",
        "diff": "Index: f.txt\n--- f.txt\n+++ f.txt\n",
        "applied": applied,
        "summary": {"status": "ok", "message": "Edited f.txt"},
        "mutation": {"operation": "edit", "changed": changed, "dryRun": False, "paths": ["f.txt"]},
    }


class FakeFilesystem:
    def __init__(self, results: list[dict[str, Any]]):
        self.edit = AsyncMock(side_effect=results)


class FakeConduit:
    def __init__(self, results: list[dict[str, Any]]):
        self.files = FakeFilesystem(results)


class SafeEditNoOpDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_raises_when_dry_run_preview_shows_no_effective_change(self) -> None:
        preview = _edit_result(applied=False, changed=False)
        conduit = FakeConduit([preview])

        with self.assertRaises(ToolError) as ctx:
            await safeEdit(conduit, "f.txt", [{"oldText": "x", "newText": "x"}])  # type: ignore[arg-type]
        self.assertIn("no effective change", str(ctx.exception))
        self.assertEqual(conduit.files.edit.call_count, 1)

    async def test_raises_when_real_apply_reports_applied_false(self) -> None:
        preview = _edit_result(applied=False, changed=True)
        real_noop = _edit_result(applied=False, changed=False)
        conduit = FakeConduit([preview, real_noop])

        with self.assertRaises(ToolError) as ctx:
            await safeEdit(conduit, "f.txt", [{"oldText": "x", "newText": "y"}])  # type: ignore[arg-type]
        self.assertIn("Failed to apply", str(ctx.exception))
        self.assertEqual(conduit.files.edit.call_count, 2)

    async def test_succeeds_when_preview_and_apply_both_show_a_real_change(self) -> None:
        preview = _edit_result(applied=False, changed=True)
        real_change = _edit_result(applied=True, changed=True)
        conduit = FakeConduit([preview, real_change])

        result = await safeEdit(conduit, "f.txt", [{"oldText": "x", "newText": "y"}])  # type: ignore[arg-type]
        self.assertTrue(result["applied"])
        self.assertTrue(result["mutation"]["changed"])
        self.assertEqual(conduit.files.edit.call_count, 2)


if __name__ == "__main__":
    unittest.main()