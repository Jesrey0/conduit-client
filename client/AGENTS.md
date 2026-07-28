# Portable Conduit client instructions

This contract belongs to the public source-first Conduit client. A remote project may provide
its own `AGENTS.md`; read and follow that project guidance after connecting.

## Onboarding

1. Read `client/README.md` for the agent-sandbox versus Conduit-runtime model.
2. Read `client/ops/OPS.md` for project orientation, safe mutation, and Git.
3. Prefer the native `async with Conduit(workspace="id")` SDK. Use namespaces:
   `c.workspace`, `c.git`, `c.files`, and `c.terminal`.
4. Call `c.workspace.current()`, `c.git.status()`, and `c.git.log()` before work.
5. Read the remote project's `README.md`, `AGENTS.md`, and relevant docs.

## Boundaries

- The agent sandbox holds this client, private auth state, and disposable
  artifacts. Conduit calls affect the remote runtime workspace.
- Keep credentials private. Do not print tokens or auth files.
- **Immutable Workspace:** Bind the workspace id at client creation. Create a
  new `Conduit` instance for another workspace or parallel worker. Workspace
  binding can still fail if the approved client token is not granted that
  workspace.
- The only persisted client state is authorization (`.conduit_auth.json`); MCP session and negotiated protocol state remain in memory.
  Do not create session caches or lock files.

## Defaults

```text
search -> c.files.read
c.files / c.git tools
c.terminal.execAsync + c.terminal.attach
```

Use `readMulti` before broad edits, then `batch` with current `baseHash`
values for one logical multi-file mutation. Use dry runs and idempotency keys.

## Overlays

- `client/ops/overlays/project.md` — unfamiliar/project-specific work
- `client/ops/security.md` — security-boundary changes
- `client/ops/overlays/ledger.md` — supervised ledger/data reentry
- `client/ops/recovery.md` — a failure not resolved by the obvious fix

For scripts longer than a few lines, embedded source/JSON, or fragile shell quoting, pass the source directly to `c.terminal.runScript`; do not wrap it in `terminal.exec("bash", ["-lc", ...])`. Script extensions are canonicalized by the SDK (`.py` becomes `py`).
