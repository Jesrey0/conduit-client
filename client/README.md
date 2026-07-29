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

## Source-first admission and direct handoff

For invite enrollment or promotion, the operator supplies one schema-v3 envelope pinned to an exact commit of this repository:

```bash
git checkout <client.commit from conduit_provisioning.json>
python3 client/conduit_admission.py inspect --provisioning /home/user/conduit_provisioning.json
python3 client/conduit_admission.py enroll --provisioning /home/user/conduit_provisioning.json
```

The admission CLI executes no server-supplied code. It validates the clone/commit, submits or resumes enrollment, verifies the actual visible workspace grant, atomically writes `/home/user/.conduit_auth.json` at mode `0600`, removes resume state after success, and writes `/home/user/.conduit_admission_report.json`.

For a trusted sandbox where this source is already installed, the local operator may instead issue a direct auth file. This bypasses invite enrollment only; it cannot bypass a model or platform prohibition on external connections.

`python3 client/conduit_cli.py doctor` performs compact readiness diagnostics without creating an MCP session.

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

`c.files.read`'s `start_index`/`max_length` use JavaScript decoded-string indexing
(UTF-16 code-unit offsets) — not line numbers, Unicode code-point indexes, or raw file-byte offsets. Use `head`/`tail` for line-oriented
reads, and resume with the returned `nextStartIndex`.


## Protocol and retry policy

The client negotiates MCP `2025-11-25`, sends `notifications/initialized`, and includes the negotiated `MCP-Protocol-Version` on subsequent requests. Retry classification is not handwritten in Python. `client/conduit/tool-policy.json` is synchronized byte-for-byte from the server's canonical generated policy; the client fails fast if it is absent or invalid.

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
