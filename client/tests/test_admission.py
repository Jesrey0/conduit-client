from __future__ import annotations
import json, subprocess, tempfile, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import client.conduit_admission as admission

class SourceAdmissionTests(unittest.TestCase):
    def envelope(self, root: Path):
        commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
        return {"schemaVersion":3,"provisioningId":"prov_test","purpose":"ENROLLMENT","lifecycle":"probation","authorization":{"state":"AUTHORIZED_TO_REQUEST_ENROLLMENT","accessClass":"LIVE_PROBATION","issuedAt":datetime.now(timezone.utc).isoformat(),"expiresAt":(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),"approvalRequired":True},"server":{"url":"https://example.test/conduit-local"},"client":{"repository":"https://github.com/Jesrey0/conduit-client","commit":commit,"entrypoint":"client/conduit_admission.py","minimumPython":"3.12"},"requestedGrant":{"privileges":["inspect"],"workspaceIds":["playground"],"defaultWorkspaceId":"playground"},"invite":{"id":"inv_test","secret":"private","singleUse":True},"credentialHandling":{"resultingAuthPath":"/home/user/.conduit_auth.json","enrollmentResumePath":"/home/user/.conduit_enrollment.json","admissionReportPath":"/home/user/.conduit_admission_report.json","requiredMode":"0600"}}
    def test_loads_v3_and_verifies_clone_pin(self):
        root=Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"p.json"; p.write_text(json.dumps(self.envelope(root)))
            d=admission.load_envelope(p); source=admission.verify_source(d)
            self.assertEqual(source["commit"],d["client"]["commit"])
    def test_rejects_expired(self):
        root=Path(__file__).resolve().parents[2]; d=self.envelope(root); d["authorization"]["expiresAt"]=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/"p.json"; p.write_text(json.dumps(d))
            with self.assertRaises(SystemExit): admission.load_envelope(p)

if __name__=="__main__": unittest.main()
