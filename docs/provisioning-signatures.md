# Provisioning signature verification

Schema-v4 envelopes are Ed25519-signed. The admission CLI first verifies that it is running the exact client repository and commit declared by the envelope. It then loads the checksum-covered `keys/operators.json` from that commit and verifies the signature before expiry evaluation or any network request.

The signature covers every envelope field except `signature`, including server URL, client commit, exact grant, expiry, invite credentials, lifecycle, and replacement relationship. Unsigned envelopes, unknown/revoked keys, invalid key metadata, malformed base64url, canonicalization failures, and signature mismatches fail closed.

The pinned client commit remains the root of trust for the public-key registry. A signature cannot make an untrusted client commit trustworthy. After a key compromise, do not authorize an older client commit whose registry still trusts that key.

See `test-vectors/provisioning-signature-v1.json` for the shared TypeScript/Python canonicalization and signature vector.
