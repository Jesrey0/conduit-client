"""Tests for the shared atomic-write helper (item #8 of the refactor plan).

client/conduit/auth.py and client/conduit/session.py previously each
implemented their own copy of the temp-file + fsync + os.replace pattern.
This file tests the extracted shared implementation directly, in addition
to the existing indirect coverage via test_auth_state.py/test_session_state.py.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from client.conduit.atomic_file import write_json_atomic


class AtomicFileTests(unittest.TestCase):
    def test_writes_and_reads_back_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "data.json"
            write_json_atomic(path, {"a": 1, "b": "two"})
            import json
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"a": 1, "b": "two"})

    def test_leaves_no_temp_file_on_success(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "data.json"
            write_json_atomic(path, {"a": 1})
            leftovers = [p.name for p in Path(td).iterdir() if ".tmp-" in p.name]
            self.assertEqual(leftovers, [])

    def test_creates_parent_directories(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "dir" / "data.json"
            write_json_atomic(path, {"a": 1})
            self.assertTrue(path.exists())

    def test_applies_mode_when_provided(self):
        if os.name == "nt":
            self.skipTest("POSIX file mode semantics only")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "secret.json"
            write_json_atomic(path, {"token": "x"}, mode=0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_no_mode_leaves_default_permissions_untouched(self):
        if os.name == "nt":
            self.skipTest("POSIX file mode semantics only")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            write_json_atomic(path, {"a": 1})
            # No mode= was requested, so the file should NOT have been forced
            # to 0o600 - it keeps whatever the umask-default open() mode is.
            # We only assert it's readable/writable by the owner, not a
            # specific restrictive mode, since that's controlled by umask.
            self.assertTrue(os.access(path, os.R_OK | os.W_OK))

    def test_overwrites_existing_file_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "data.json"
            write_json_atomic(path, {"version": 1})
            write_json_atomic(path, {"version": 2})
            import json
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"version": 2})

    def test_temp_file_is_cleaned_up_even_if_write_partially_completes(self):
        # Simulate a failure during json.dump by writing invalid data that
        # would raise (a non-serializable object), and confirm no .tmp- file
        # is left behind afterward.
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "data.json"

            class NotSerializable:
                pass

            with self.assertRaises(TypeError):
                write_json_atomic(path, {"bad": NotSerializable()})  # type: ignore[dict-item]

            leftovers = [p.name for p in Path(td).iterdir() if ".tmp-" in p.name]
            self.assertEqual(leftovers, [])
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
