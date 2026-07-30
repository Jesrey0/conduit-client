"""Ed25519 verification for signed Conduit provisioning envelopes."""
from __future__ import annotations
import base64, json, re
from datetime import datetime
from pathlib import Path
from typing import Any
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_der_public_key

_ASCII_KEY = re.compile(r"^[\x20-\x7e]+$")

def _b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url value")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

def canonical_json(value: Any) -> str:
    """Canonical JSON subset v1: ASCII object keys, strings, bool/null, integers, arrays, objects."""
    if value is None: return "null"
    if value is True: return "true"
    if value is False: return "false"
    if isinstance(value, int) and not isinstance(value, bool): return str(value)
    if isinstance(value, float): raise ValueError("floating-point values are not supported in signed envelopes")
    if isinstance(value, str): return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, list): return "[" + ",".join(canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(k, str) and _ASCII_KEY.fullmatch(k) for k in value):
            raise ValueError("signed envelope object keys must be printable ASCII")
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical_json(value[k]) for k in sorted(value)) + "}"
    raise ValueError(f"unsupported signed envelope value type: {type(value).__name__}")

def signing_payload(envelope: dict[str, Any]) -> bytes:
    unsigned = {k: v for k, v in envelope.items() if k != "signature"}
    return canonical_json(unsigned).encode("utf-8")

def load_key_registry(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schemaVersion") != 1 or not isinstance(data.get("keys"), list):
        raise ValueError("invalid operator key registry")
    result: dict[str, dict[str, Any]] = {}
    for entry in data["keys"]:
        if not isinstance(entry, dict) or set(entry) - {"keyId","algorithm","publicKey","status","notBefore","retiredAt"}:
            raise ValueError("invalid operator key entry")
        key_id = entry.get("keyId")
        if not isinstance(key_id, str) or not key_id or key_id in result: raise ValueError("duplicate or invalid operator key id")
        if entry.get("algorithm") != "Ed25519" or entry.get("status") not in {"active","retired","revoked"}: raise ValueError("unsupported operator key metadata")
        result[key_id] = entry
    return result

def verify_envelope_signature(envelope: dict[str, Any], registry_path: Path) -> str:
    signature = envelope.get("signature")
    if not isinstance(signature, dict) or set(signature) != {"algorithm","keyId","value"}: raise ValueError("missing or malformed provisioning signature")
    if signature.get("algorithm") != "Ed25519": raise ValueError("unsupported provisioning signature algorithm")
    keys = load_key_registry(registry_path); key_id = signature.get("keyId"); entry = keys.get(key_id)
    if not entry: raise ValueError("unknown provisioning signing key")
    if entry["status"] == "revoked": raise ValueError("provisioning signing key is revoked")
    issued = datetime.fromisoformat(str(envelope.get("authorization",{}).get("issuedAt","")).replace("Z","+00:00"))
    if entry.get("notBefore") and issued < datetime.fromisoformat(entry["notBefore"].replace("Z","+00:00")): raise ValueError("envelope predates signing key validity")
    if entry["status"] == "retired":
        retired = entry.get("retiredAt")
        if not retired or issued > datetime.fromisoformat(retired.replace("Z","+00:00")): raise ValueError("envelope was issued after key retirement")
    loaded = load_der_public_key(_b64url_decode(entry["publicKey"]))
    if not isinstance(loaded, Ed25519PublicKey): raise ValueError("operator key is not Ed25519")
    try:
        loaded.verify(_b64url_decode(signature["value"]), signing_payload(envelope))
    except InvalidSignature as exc:
        raise ValueError("invalid provisioning signature") from exc
    return str(key_id)
