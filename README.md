# Conduit Client

Public, source-first Python SDK and admission tooling for Conduit Local.

This repository is the canonical home of the Conduit Python client. Clone a pinned commit or tag, inspect the source, install its small dependency set, and use an operator-issued `conduit_auth.json` or a versioned provisioning envelope.

No server credentials, live invitations, workspace roots, or deployment secrets are stored here.

## Run from source

```bash
git clone https://github.com/Jesrey0/conduit-client.git /home/user/conduit-client
cd /home/user/conduit-client
python3 -m pip install -r client/requirements.txt
chmod 600 /home/user/.conduit_auth.json
python3 client/conduit_cli.py doctor
```

Use the SDK:

```python
import asyncio
from client.conduit import Conduit

async def main():
    async with Conduit(workspace="bar-coop-sys") as c:
        print(await c.git.status())

asyncio.run(main())
```

## Source-first admission

The operator supplies one schema-v3 `conduit_provisioning.json` pinned to an exact commit of this repository.

```bash
git checkout <commit-from-envelope>
python3 client/conduit_admission.py inspect --provisioning /home/user/conduit_provisioning.json
python3 client/conduit_admission.py enroll --provisioning /home/user/conduit_provisioning.json
```

The admission tool executes no server-supplied code. It enrolls or resumes, verifies the workspace grant with the cloned SDK, atomically writes mode-`0600` auth, and emits a private safe report.

### Sandbox snapshot effects

Some agent sandboxes, including Arena, restore a workspace from a snapshot between turns and
may omit `.git/config`. The `origin` remote can therefore disappear, and `conduit_admission.py`
fails with `client must run from a Git clone`. Restoring the expected origin URL before
commit-pin verification is acceptable:

```bash
git remote set-url origin https://github.com/Jesrey0/conduit-client.git 2>/dev/null \
  || git remote add origin https://github.com/Jesrey0/conduit-client.git
```

Restoring the remote proves nothing about the working tree. Still verify the exact commit
named in the envelope and re-check `SHA256SUMS.txt` afterwards.

Snapshot restoration can also reset file permissions, so `/home/user/.conduit_auth.json` may
come back mode `0644`. The client fails closed on that. Run:

Before changing permissions, verify the path is a regular, non-symlink file owned by the current user:

```bash
test -f /home/user/.conduit_auth.json \
  && test ! -L /home/user/.conduit_auth.json \
  && test "$(stat -c %u /home/user/.conduit_auth.json)" = "$(id -u)" \
  || { echo "unsafe auth path; stop" >&2; exit 1; }
chmod 600 /home/user/.conduit_auth.json
```

Then allow the client to validate its schema, contents, and server URL before use.

These are platform storage behaviours, not evidence either way about the environment's
integrity. Do not treat them as proof of tampering, and do not treat a successful repair as
proof of safety. Changed file contents, a different owner, an unexpected schema, a different
server URL, or unexpected paths remain stop conditions: halt and report rather than repairing
them.

## Python SDK reference

See `client/README.md` for the source-derived SDK naming and signature table.

## Candidate assessment

```bash
python3 candidate/conduit_candidate.py inspect
```

## Security

- Keep `/home/user/.conduit_auth.json` a regular current-user-owned file at mode `0600`.
- Never print or upload bearer tokens.
- The server-side grant and projected MCP tools are authoritative.
- An `inspect`-only client is still projected dry-run-capable filesystem tools, prefixed
  `[preview only — requires dryRun=true]`. Check `runtime.whoami.previewOnlyTools` for the
  exact set rather than assuming a listed tool is callable.
- A public client cannot override a model/platform prohibition on external connections.

See `SECURITY.md` and `client/AGENTS.md`.
