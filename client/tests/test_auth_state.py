"""Auth-state and bearer-header tests."""
import asyncio, json, os, tempfile, unittest
from pathlib import Path
from unittest import mock
from client.conduit.auth import ConduitAuthState
from client.conduit.sdk import Conduit

class AuthStateTests(unittest.TestCase):
 def test_round_trip_and_mode(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"auth.json"; a=ConduitAuthState(p); a.save_token("ctkn.test",server_url="https://host")
   self.assertEqual(a.load_token(),"ctkn.test"); self.assertEqual(a.load_server_url(),"https://host"); self.assertEqual(p.stat().st_mode&0o777,0o600)
 def test_rejects_legacy_and_insecure(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"auth.json"; p.write_text(json.dumps({"client_token":"legacy"})); os.chmod(p,0o600)
   with self.assertRaises(ValueError): ConduitAuthState(p).load_token()
   p.write_text(json.dumps({"schemaVersion":1,"clientToken":"ctkn.test"})); os.chmod(p,0o644)
   with self.assertRaises(PermissionError): ConduitAuthState(p).load_token()
 def test_sdk_uses_auth_and_canonical_env_token(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"auth.json"; ConduitAuthState(p).save_token("ctkn.file",server_url="https://host")
   c=Conduit("https://host",auth_path=p); self.assertEqual(c.transport._client.headers.get("Authorization"),"Bearer ctkn.file"); asyncio.run(c.close())
   with mock.patch.dict(os.environ,{"CONDUIT_CLIENT_TOKEN":"ctkn.env"}):
    c=Conduit("https://host",auth_path=Path(td)/"missing"); self.assertEqual(c.transport._client.headers.get("Authorization"),"Bearer ctkn.env"); asyncio.run(c.close())

if __name__=="__main__": unittest.main()
