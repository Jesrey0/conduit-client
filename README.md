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

## Candidate assessment

```bash
python3 candidate/conduit_candidate.py inspect
```

## Security

- Keep `/home/user/.conduit_auth.json` a regular current-user-owned file at mode `0600`.
- Never print or upload bearer tokens.
- The server-side grant and projected MCP tools are authoritative.
- A public client cannot override a model/platform prohibition on external connections.

See `SECURITY.md` and `client/AGENTS.md`.
