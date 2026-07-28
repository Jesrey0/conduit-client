from __future__ import annotations

import threading
import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from client.conduit_admission import EnrollmentState, fetch_client_files_direct, load_provisioning, provisioning_summary


class DirectBundleFetchTests(unittest.TestCase):
    def test_fetches_manifest_files_with_bounded_concurrency_and_stable_paths(self):
        paths = [f"client/file-{index}.py" for index in range(8)]
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_get_text(url: str, *, timeout: int, headers=None) -> str:
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                time.sleep(0.03)
                path = url.split("path=", 1)[1]
                return f"content:{path}"
            finally:
                with lock:
                    active -= 1

        with patch(
            "client.conduit_admission.get_json",
            return_value={"files": paths},
        ), patch(
            "client.conduit_admission.get_text",
            side_effect=fake_get_text,
        ):
            returned_paths, files = fetch_client_files_direct(
                "https://host.example/secret",
                timeout=5,
                client_token="ctkn.test",
                max_workers=3,
            )

        self.assertEqual(returned_paths, paths)
        self.assertEqual(set(files), set(paths))
        self.assertGreaterEqual(max_active, 2)
        self.assertLessEqual(max_active, 3)

    def test_rejects_invalid_worker_limits_before_network_access(self):
        with patch("client.conduit_admission.get_json") as get_json:
            for workers in (0, 17):
                with self.subTest(workers=workers):
                    with self.assertRaisesRegex(ValueError, "between 1 and 16"):
                        fetch_client_files_direct(
                            "https://host.example",
                            timeout=5,
                            max_workers=workers,
                        )
            get_json.assert_not_called()

    def test_rejects_invalid_or_duplicate_manifest_paths_before_file_fetch(self):
        for manifest, message in [
            ({"files": ["client/a.py", "../outside.py"]}, "invalid path"),
            ({"files": ["client/a.py", "client/a.py"]}, "duplicate paths"),
        ]:
            with self.subTest(manifest=manifest), patch(
                "client.conduit_admission.get_json",
                return_value=manifest,
            ), patch("client.conduit_admission.get_text") as get_text:
                with self.assertRaisesRegex(RuntimeError, message):
                    fetch_client_files_direct(
                        "https://host.example",
                        timeout=5,
                        max_workers=2,
                    )
                get_text.assert_not_called()


class EnrollmentStateTests(unittest.TestCase):
    def test_saves_loads_and_clears_private_bound_resume_state(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".conduit_enrollment.json"
            state = EnrollmentState(path)
            state.save(
                server_url="https://example.test/conduit-local",
                invite_id="inv_test",
                provisioning_id="prov_test",
                request_id="req_test",
                request_secret="reqsec_private",
            )
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            loaded = state.load(
                server_url="https://example.test/conduit-local",
                invite_id="inv_test",
                provisioning_id="prov_test",
            )
            self.assertEqual(loaded["requestId"], "req_test")
            self.assertEqual(loaded["requestSecret"], "reqsec_private")
            self.assertNotIn("inviteSecret", loaded)
            with self.assertRaises(ValueError):
                state.load(
                    server_url="https://other.test/conduit-local",
                    invite_id="inv_test",
                    provisioning_id="prov_test",
                )
            state.clear()
            self.assertFalse(path.exists())

    def test_rejects_permissive_resume_state_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".conduit_enrollment.json"
            path.write_text("{}")
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                EnrollmentState(path).load(
                    server_url="https://example.test/conduit-local",
                    invite_id="inv_test",
                    provisioning_id=None,
                )


class ProvisioningInspectionTests(unittest.TestCase):
    def envelope(self, script: Path, *, expires_delta=60):
        return {
            "schemaVersion": 2, "provisioningId": "prov_test", "purpose": "ENROLLMENT", "lifecycle": "probation",
            "authorization": { "state": "AUTHORIZED_TO_REQUEST_ENROLLMENT", "accessClass": "LIVE_PROBATION", "issuedAt": datetime.now(timezone.utc).isoformat(), "expiresAt": (datetime.now(timezone.utc) + timedelta(seconds=expires_delta)).isoformat(), "approvalRequired": True },
            "server": { "url": "https://example.test/conduit-local" },
            "requestedGrant": { "privileges": ["inspect"], "workspaceIds": ["playground"], "defaultWorkspaceId": "playground" },
            "invite": { "id": "inv_test", "secret": "invsec_private", "singleUse": True },
            "bootstrap": { "url": "https://example.test/conduit-local/bootstrap_conduit_client.py", "sha256": hashlib.sha256(script.read_bytes()).hexdigest() },
            "credentialHandling": { "resultingAuthPath": "/home/user/.conduit_auth.json", "enrollmentResumePath": "/home/user/.conduit_enrollment.json", "requiredMode": "0600" }
        }

    def test_validates_and_redacts_provisioning_summary(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "bootstrap.py"; script.write_text("print('ok')")
            envelope = Path(td) / "provisioning.json"; envelope.write_text(json.dumps(self.envelope(script)))
            loaded = load_provisioning(str(envelope), str(script))
            summary = provisioning_summary(loaded)
            self.assertTrue(summary["valid"])
            self.assertNotIn("invite", summary)
            self.assertNotIn("invsec_private", json.dumps(summary))
            self.assertFalse(summary["networkUsed"])

    def test_rejects_expired_or_hash_mismatched_provisioning(self):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "bootstrap.py"; script.write_text("print('ok')")
            for name, data in [("expired", self.envelope(script, expires_delta=-1)), ("hash", {**self.envelope(script), "bootstrap": {"url": "https://example.test/conduit-local/bootstrap_conduit_client.py", "sha256": "0" * 64}})]:
                envelope = Path(td) / f"{name}.json"; envelope.write_text(json.dumps(data))
                with self.assertRaises(SystemExit): load_provisioning(str(envelope), str(script))


if __name__ == "__main__":
    unittest.main()
