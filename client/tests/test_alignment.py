import json
import unittest
import inspect
from client.conduit.sdk import Conduit, FilesystemNamespace, GitNamespace, WorkspaceNamespace, TerminalNamespace, SystemNamespace
from client.conduit.errors import ToolError

class AlignmentSurfaceTests(unittest.TestCase):
    def test_sdk_exposes_namespaced_api(self):
        c = Conduit("https://host")
        self.assertTrue(hasattr(c, "files"))
        self.assertTrue(hasattr(c, "git"))
        self.assertTrue(hasattr(c, "workspace"))
        self.assertTrue(hasattr(c, "terminal"))
        self.assertTrue(hasattr(c, "system"))

    def test_sdk_no_flat_methods(self):
        c = Conduit("https://host")
        # Check some that used to be there
        for flat in ["read_file", "git_status", "workspace_current"]:
            self.assertFalse(hasattr(c, flat), f"Legacy flat method {flat} still exists")

    def test_files_namespace(self):
        params = inspect.signature(FilesystemNamespace.read).parameters
        self.assertIn("start_index", params)
        self.assertIn("max_length", params)
        self.assertIn("head", params)
        self.assertIn("tail", params)

        multi = inspect.signature(FilesystemNamespace.readMulti).parameters
        self.assertIn("max_length_per_file", multi)
        self.assertIn("max_total_length", multi)

    def test_git_namespace(self):
        params = inspect.signature(GitNamespace.status).parameters
        self.assertIn("repo_path", params)

    def test_terminal_namespace(self):
        params = inspect.signature(TerminalNamespace.exec).parameters
        self.assertIn("executable", params)
        self.assertIn("args", params)

    def test_structured_raises_on_error_result(self):
        err = {"isError": True, "content": [{"type": "text", "text": "FILE_EXISTS: nope"}]}
        with self.assertRaises(ToolError) as ctx:
            Conduit.structured(err)
        self.assertIn("FILE_EXISTS", str(ctx.exception))

    def test_structured_returns_content_on_success(self):
        ok = {"structuredContent": {"success": True}}
        self.assertEqual(Conduit.structured(ok), {"success": True})

if __name__ == "__main__":
    unittest.main()
