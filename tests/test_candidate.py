import json
import ast
import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "candidate" / "conduit_candidate.py"


class CandidateWorkflowTests(unittest.TestCase):
    def run_script(self, *args, check=True):
        values = list(map(str, args))
        env = dict(os.environ)
        if "--workspace" in values:
            workspace = Path(values[values.index("--workspace") + 1])
            env["CONDUIT_CANDIDATE_HOME"] = str(workspace.parent)
        return subprocess.run([sys.executable, str(SCRIPT), *values], check=check, capture_output=True, text=True, env=env)

    def test_inspect_is_read_only_and_declares_limits(self):
        with tempfile.TemporaryDirectory() as td:
            before = set(Path(td).iterdir())
            result = json.loads(self.run_script("inspect").stdout)
            self.assertFalse(result["writesFiles"])
            self.assertFalse(result["networkOperations"])
            self.assertEqual(before, set(Path(td).iterdir()))

    def test_source_has_no_network_imports_and_upload_fallback_is_identical(self):
        tree = ast.parse(SCRIPT.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import): imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module: imported.add(node.module.split(".")[0])
        self.assertTrue(imported.isdisjoint({"socket", "urllib", "http", "requests", "httpx", "asyncio"}))
        fallback = ROOT / "conduit_candidate_source.txt"
        self.assertEqual(SCRIPT.read_bytes(), fallback.read_bytes())
        sums = (ROOT / "SHA256SUMS.txt").read_text()
        self.assertIn(hashlib.sha256(SCRIPT.read_bytes()).hexdigest(), sums)

    def test_complete_candidate_flow(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "candidate"
            report = workspace / "report.json"
            self.run_script("prepare", "--workspace", workspace)
            challenge = json.loads((workspace / ".conduit-candidate/challenge.json").read_text())
            target = workspace / challenge["target"]["path"]
            target.write_text(challenge["target"]["after"])
            source = Path(challenge["privateInput"])
            auth = Path(challenge["privateAuthTarget"])
            auth.parent.mkdir(parents=True, exist_ok=True)
            auth.write_bytes(source.read_bytes())
            os.chmod(auth, 0o600)
            self.run_script("validate", "--workspace", workspace)
            verified = self.run_script("verify", "--workspace", workspace, "--report", report)
            result = json.loads(verified.stdout)
            self.assertEqual(result["outcome"], "COMPLETE")
            self.assertEqual(result["findings"], [])
            self.assertEqual(stat.S_IMODE(auth.stat().st_mode), 0o600)

    def test_verifier_rejects_spoofed_secret_in_repository(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td) / "candidate"
            report = workspace / "report.json"
            self.run_script("prepare", "--workspace", workspace)
            challenge = json.loads((workspace / ".conduit-candidate/challenge.json").read_text())
            (workspace / challenge["target"]["path"]).write_text(challenge["target"]["after"])
            source = Path(challenge["privateInput"])
            auth = Path(challenge["privateAuthTarget"])
            auth.parent.mkdir(parents=True, exist_ok=True); auth.write_bytes(source.read_bytes()); os.chmod(auth, 0o600)
            (workspace / "leak.txt").write_bytes(source.read_bytes())
            self.run_script("validate", "--workspace", workspace)
            result = self.run_script("verify", "--workspace", workspace, "--report", report, check=False)
            self.assertNotEqual(result.returncode, 0)
            codes = {item["code"] for item in json.loads(result.stdout)["findings"]}
            self.assertIn("SYNTHETIC_SECRET_IN_REPO", codes)


if __name__ == "__main__":
    unittest.main()
