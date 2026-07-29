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

```bash
chmod 600 /home/user/.conduit_auth.json
```

before loading auth.

These are platform storage behaviours, not evidence either way about the environment's
integrity. Do not treat them as proof of tampering, and do not treat a successful repair as
proof of safety. Changed file contents, a different owner, an unexpected schema, a different
server URL, or unexpected paths remain stop conditions: halt and report rather than repairing
them.

## Python SDK naming

SDK methods are camelCase; their keyword arguments are Python `snake_case`. The SDK
converts those keywords to the MCP wire fields, which are camelCase — so `max_depth`
becomes `maxDepth` and `repo_path` becomes `repoPath` on the wire. Write `snake_case` in
Python and expect camelCase in returned structured content. No `camelCase` keyword aliases
are provided.

The table below covers the calls whose naming is most often guessed wrong. Signatures are
abbreviated; check `client/conduit/sdk.py` for the full parameter list.

| Purpose | Python SDK call | Important kwargs |
|---|---|---|
| List directory | `c.files.listDir(path=".")` | `recursive` — the method is `listDir`, not `list` |
| Find files | `c.files.find(pattern=..., path=".")` | `max_depth`, `include_patterns`, `exclude_patterns`, `respect_gitignore` |
| Read text | `c.files.read(path)` | `start_index`, `max_length`, `head`, `tail` (no `start_line`/`end_line` kwargs in the SDK) |
| Read many | `c.files.readMulti(paths)` | `max_length_per_file`, `max_total_length` |
| Grep | `c.files.grep(pattern, path=".")` | `before_context`, `after_context`, `max_output_length`, `timeout_ms` (no `regex` kwarg) |
| Git status | `c.git.status(repo_path=".")` | `repo_path` |
| Git log | `c.git.log(repo_path=".")` | `limit` (not `maxCount`), `cursor`, `start_timestamp`, `end_timestamp` |
| Terminal execution | `c.terminal.exec(executable, args)` | `path`, `timeout_ms` (sync ceiling 120000), `env` |
| Script execution | `c.terminal.runScript(script)` | `interpreter` (default `bash`), `interpreter_args`, `args`, `extension`, `cleanup`, `path`, `timeout_ms`, `env` |

`c.files.read`'s `start_index`/`max_length` are character-index pagination over the decoded
string — not line numbers and not raw file-byte offsets. Use `head`/`tail` for line-oriented
reads, and resume with the returned `nextStartIndex`.

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
