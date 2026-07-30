from __future__ import annotations
import base64,json,tempfile,unittest
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from client.conduit.signing import canonical_json, signing_payload, verify_envelope_signature

def b64(v:bytes)->str: return base64.urlsafe_b64encode(v).decode().rstrip("=")

class SigningTests(unittest.TestCase):
 def signed(self):
  private=Ed25519PrivateKey.generate(); public=private.public_key().public_bytes(Encoding.DER,PublicFormat.SubjectPublicKeyInfo)
  env={"schemaVersion":4,"authorization":{"issuedAt":"2026-07-30T01:00:00Z"},"server":{"url":"https://example.test"},"requestedGrant":{"privileges":["inspect"],"workspaceIds":["x"]}}
  env["signature"]={"algorithm":"Ed25519","keyId":"k1","value":b64(private.sign(signing_payload(env)))}
  return env,{"schemaVersion":1,"keys":[{"keyId":"k1","algorithm":"Ed25519","publicKey":b64(public),"status":"active","notBefore":"2026-07-30T00:00:00Z"}]}
 def test_canonical_subset_is_stable_and_rejects_floats(self):
  self.assertEqual(canonical_json({"b":[True,"é"],"a":3}),'{"a":3,"b":[true,"é"]}')
  with self.assertRaises(ValueError): canonical_json({"x":1.5})
 def test_shared_typescript_vector(self):
  root=Path(__file__).resolve().parents[2]; vector=json.loads((root/"test-vectors/provisioning-signature-v1.json").read_text())
  self.assertEqual(canonical_json({k:v for k,v in vector["envelope"].items() if k!="signature"}),vector["canonicalPayload"])
  for case in vector["canonicalCases"]: self.assertEqual(canonical_json(case["value"]),case["canonical"])
  self.assertEqual(verify_envelope_signature(vector["envelope"],root/"keys/operators.json"),vector["keyId"])
 def test_verifies_and_rejects_tampering_unknown_and_revoked_keys(self):
  env,registry=self.signed()
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/"keys.json"; p.write_text(json.dumps(registry)); self.assertEqual(verify_envelope_signature(env,p),"k1")
   tampered=json.loads(json.dumps(env)); tampered["server"]["url"]="https://evil.test"
   with self.assertRaisesRegex(ValueError,"invalid provisioning signature"): verify_envelope_signature(tampered,p)
   unknown=json.loads(json.dumps(env)); unknown["signature"]["keyId"]="missing"
   with self.assertRaisesRegex(ValueError,"unknown"): verify_envelope_signature(unknown,p)
   registry["keys"][0]["status"]="retired"; registry["keys"][0]["retiredAt"]="2026-07-30T02:00:00Z"; p.write_text(json.dumps(registry))
   self.assertEqual(verify_envelope_signature(env,p),"k1")
   late=json.loads(json.dumps(env)); late["authorization"]["issuedAt"]="2026-07-30T03:00:00Z"
   late["signature"]["value"]=env["signature"]["value"]
   with self.assertRaisesRegex(ValueError,"after key retirement"): verify_envelope_signature(late,p)
   registry["keys"][0]["status"]="revoked"; registry["keys"][0].pop("retiredAt",None); p.write_text(json.dumps(registry))
   with self.assertRaisesRegex(ValueError,"revoked"): verify_envelope_signature(env,p)

if __name__=="__main__": unittest.main()
