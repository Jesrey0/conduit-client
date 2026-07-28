# Conduit Local agent client

This bundle is for agents that cannot connect to the MCP endpoint directly.
The canonical interface is the Python async SDK. Keep one `Conduit` instance
alive for the agent workflow; do not spin up `./conduit` for every tool call.

## Model

The agent sandbox contains this client, its approved-client token, and
temporary workflow artifacts. Conduit tools operate in the separate runtime
workspace. Start a workflow by reading the target project's instructions and
ensuring the correct workspace is bound.

The client persists only local authorization state: `.conduit_auth.json` holds an approved bearer token with private permissions. MCP session state remains in process memory. Bootstrap may also
maintain `.env` with connection configuration such as `CONDUIT_URL`.

## Native SDK

A `Conduit` instance is the unit of workspace isolation and session ownership.
The workspace is an explicit, immutable property of a client instance.

```python
from client.conduit import Conduit

async with Conduit(workspace="conduit-local") as c:
    curr = await c.workspace.current()
    matches = await c.search("session transport")
    source = await c.files.read(matches[0]["id"])
    status = await c.git.status()
    await c.terminal.exec("npm", ["test"])
```

Capabilities are grouped into namespaced properties:
- `c.workspace.*` — List, current, and administrative workspace actions.
- `c.git.*` — Status, diff, commit, log, and branch operations.
- `c.files.*` — Read, read-multi, write, edit, batch, and filesystem discovery.
- `c.terminal.*` — Exec, attach, wait, and stop process operations.

`Conduit` creates one in-memory MCP session and one reusable `httpx` client.
The context manager closes that owned server session when the workflow ends.

To operate on multiple workspaces or parallelize workers, create separate
instances:

```python
async with Conduit(workspace="conduit-local") as c1, \
           Conduit(workspace="another-repo") as c2:
    status1 = await c1.git.status()
    status2 = await c2.git.status()
```

## JSON CLI bridge

`./conduit` is a one-shot bridge for bootstrap diagnostics. It always emits one
compact JSON value to stdout.

```bash
./conduit --tool runtime.health
./conduit --tool filesystem.read --param path=README.md --extract content
./conduit --workspace project-id --tool git.status
./conduit --discover
./conduit doctor
```

`--workspace` allows pinning a one-shot invocation to a specific project.
The bridge creates and terminates its own session per invocation unless an 
external `CONDUIT_SESSION_ID` is supplied.

## Bootstrap and admission

### Direct Local Credential Handoff

This is a local operator credential handoff for a sandbox where the Conduit client is already installed. It does not install the SDK, perform Arena enrollment, or bypass a model/platform prohibition on external connections. The command requires an explicit lifecycle, server URL, grant, and private output path; it never prints the bearer token. (Recommended for restricted agents & sandboxes)

Agents or environments that cannot or refuse to run external bootstrap scripts can authenticate declaratively without participating in invite enrollment:

1. **Option A: Environment Variables**:
   Set `CONDUIT_CLIENT_TOKEN` and canonical `CONDUIT_URL` in the environment. The Python SDK directly uses these credentials.

2. **Option B: Direct Auth File**:
   Generate a pre-issued credential using:
   ```bash
   npm run admin -- client issue-direct --label "Agent" --lifecycle active --server-url "https://approved.example/conduit-local" --privilege inspect --workspace default --default-workspace default --out ~/.conduit_auth.json
   ```
   The `.conduit_auth.json` file is written with mode `0600` and loaded directly by `ConduitAuthState` / `Conduit` SDK without any script execution or polling loop.

### Scripted Enrollment Flow

Set `CONDUIT_URL`, then run `python3 bootstrap_conduit_client.py --overwrite
--install-deps --verify`. With client admission enabled, provide the invite id and secret for enrollment.
Admission is privilege-scoped: the operator-created invite or approval must grant
explicit privileges and workspace ids. Bootstrap can request a scope, but the
server/admin grant remains authoritative. Example observer enrollment request:

```bash
python3 bootstrap_conduit_client.py --overwrite --install-deps --verify \
  --request-privilege inspect \
  --request-privilege propose \
  --request-workspace bar-coop-sys \
  --request-default-workspace bar-coop-sys
```

Bootstrap writes the approved token to `.conduit_auth.json` and the normalized
connection URL to `.env`.

While approval is pending, bootstrap stores a narrowly scoped resume capability at
`~/.conduit_enrollment.json` with mode `0600`. It contains no invite secret and is
bound to the server, invite, and provisioning ID. The same bootstrap command may
remain polling or be stopped and rerun after approval. Bootstrap removes this file
only after the approved credential has been verified and atomically persisted (or
a denial is observed). After the first approved-token response, recovery remains
available for two hours; beyond that, revoke and reprovision the inaccessible client. Never reveal, upload, edit, or copy it into a Conduit
workspace.

`./conduit doctor` returns compact readiness JSON without creating an MCP
session.

## Protocol and retry policy

The client negotiates MCP `2025-11-25`, sends `notifications/initialized`, and includes the negotiated `MCP-Protocol-Version` on subsequent requests. Retry classification is not handwritten in Python: `client/conduit/tool-policy.json` is generated from the server's canonical `src/server/tool-manifest.ts` during `npm run build`, and the client fails fast if the generated policy is absent or invalid.

## Recovery

The transport retries transient read-safe requests and idempotency-protected
mutations. If a session is rejected after a server restart, it reinitializes
once where replay is safe. For persistent failures, create a new `Conduit`
instance.

For repository workflow and mutation discipline, read `client/ops/OPS.md`.


## Auth file contract

The auth file uses one canonical versioned JSON schema (`schemaVersion`, `clientToken`, `serverUrl`, `savedAt`), must be a regular file owned by the current user, and must have mode `0600`. Legacy key spellings are rejected; re-enroll instead of carrying compatibility aliases.

## Script execution guidance

Use `terminal.exec` for short executable/argument vectors. Use `terminal.runScript` for multiline source, embedded JSON, heredoc-like content, or any argument approaching the 4,000-character limit. Pass source directly in the `script` field rather than nesting it in `bash -lc` or a generated quoting wrapper. The Python SDK accepts suffix-style extensions such as `.py` and sends the canonical wire value `py`; the server wire schema remains strict.
