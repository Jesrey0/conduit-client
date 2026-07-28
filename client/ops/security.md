# Security Hardening and Audit Flow

> Load this file when touching Conduit transport, admission, admin API, route prefix, crawler protection, proxy/IP handling, or other security-boundary code.
>
> **Expected posture is fail-closed:** uncertain or unauthorized traffic should be rejected visibly rather than allowed silently.

## Current hardening posture

- **Crawler / spider blocking:** known crawler, scraper, and AI crawler User-Agents are blocked on every route except `/robots.txt`. Blocked requests receive HTTP `403` with a plain `Forbidden` body. `/robots.txt` remains reachable so crawlers can observe the disallow policy.
- **Crawler bypasses are explicit:** `CRAWLER_BYPASS_AGENTS` is an optional, case-insensitive administrator regex for authorized automation. It is compiled at config load; invalid patterns are warned about and result in **no bypass** rather than weakening the crawler block.
- **Authenticated ChatGPT MCP compatibility is narrow:** `ChatGPT-User` may proceed past the crawler guard only on a configured MCP path, only while client admission is enabled, and only when the request carries a syntactically valid bearer header. The normal approved-client token validator remains authoritative; missing, invalid, disabled, or revoked credentials still fail closed.
- **Crawler blocks are logged:** block logs include source IP, path, and User-Agent for audit visibility.
- **Proxy / IP handling:** Express `trust proxy` defaults to disabled and must be explicitly configured for a known proxy topology. Source IP logging uses `req.ip` / socket data rather than hand-parsing spoofable `X-Forwarded-For` headers.
- **Client admission defaults toward safety:** when a non-loopback `PUBLIC_BASE_URL` is detected and `ENABLE_CLIENT_ADMISSION` is unset, the server defaults admission on and requires approved bearer tokens for MCP.
- **Approved clients are privilege-scoped:** every approved client must carry an explicit grant. Missing grants fail closed with `CLIENT_POLICY_MISSING`; there is no legacy implicit full-access default. Tool projection and call-time checks both enforce privileges, and workspace lists/binds are filtered by the client's allowed workspace ids.
- **Admin API token gate:** the loopback admin API requires `X-Admin-Token` matching the generated `admin-api-key.txt`. Missing expected-token state or a mismatched / missing request token is unauthorized.
- **Configuration assertions:** `PORT` and `CONDUIT_ADMIN_PORT` must be valid, distinct TCP ports; invalid or colliding values abort startup.
- **Registered workspace context only:** tools accept workspace-relative paths and cannot override the active root. Use `workspace.bind` to select an allowed registered workspace and `workspace.add` only with admin privilege.
- **Deep health protection:** shallow health remains public for bootstrap discovery. Deep diagnostics require an approved token on configured public deployments and omit workspace roots, workspace ids/names, and workspace counts.
- **Terminal environment minimization:** terminal commands receive an allowlisted environment plus caller-provided overrides, rather than the full server process environment. Configure passthrough with `CONDUIT_TERMINAL_ENV_PASSTHROUGH` when needed.
- **Terminal destructive-command guard:** both sync and async terminal commands pass through one service-level pre-spawn evaluator. High-confidence raw destructive Git/filesystem commands are blocked with stable rule metadata and semantic-tool remediation; blocked-command logs store a SHA-256 fingerprint rather than raw command text.
- **Admin-store fail-closed persistence:** admin token / signing / client files are written with restrictive permissions, corrupt security JSON causes a loud startup / API error, and multi-file invite/request/client transitions use a write-ahead snapshot journal that is replayed after an interrupted write.
- **Boundary request schemas:** public enrollment and loopback admin request bodies are parsed with bounded Zod schemas; objects and oversized values are rejected rather than coerced into strings.
- **Public client-bundle deny policy:** manifest/file routes reject hidden path segments, lock files, tests/caches, and the fixed default secret/credential filename policy even when an operator customizes filesystem denylist settings.
- **MCP authentication ordering:** admission-enabled POST/GET/DELETE requests validate the bearer token before session-header/session lookup errors are returned.
- **Origin and CORS policy:** requests that include `Origin` must exactly match the configured `PUBLIC_BASE_URL` origin or receive `403`. Responses retain wildcard CORS without `Access-Control-Allow-Credentials`; bearer validation remains authoritative.
- **Bounded public 404s:** unregistered public routes return a generic JSON error without echoing the requested path.

The recent audit fixes commonly referred to as **SEC-01 through SEC-04** cover this cluster: admin API authentication, public / admission fail-closed defaults, safe proxy/IP and regex handling, and explicit crawler 403 logging.

## Security validation lab checklist

For any security-boundary change, create a scratch run and validate with targeted commands before committing:

```python
async with Conduit() as conduit:
    workspace = await conduit.workspace.current()
    status = await conduit.git.status()
```

Recommended remote validation:

```python
await conduit.terminal.exec("npm run test:security", timeout_ms=120000)
```

When tests are not enough, use a throwaway local server / fixture (and remember the bundle-routing mirror in `src/transports/streamable-http.ts` whenever you add or move files under `client/`) and verify these behaviors explicitly:

- crawler User-Agent to a non-`/robots.txt` route returns `403 Forbidden`;
- the same crawler User-Agent can read `/robots.txt`;
- authorized non-crawler Conduit clients (`conduit-bootstrap`, `conduit-doctor`, CLI) are not blocked by crawler middleware;
- invalid `CRAWLER_BYPASS_AGENTS` does not crash request handling and does not create an unintended allow-all bypass;
- admin endpoints reject missing / wrong `X-Admin-Token`;
- public / non-loopback exposure does not accidentally leave MCP unauthenticated;
- tool schemas expose no `workspaceRoot`; workspace binding outside the authenticated grant is rejected;
- deep health diagnostics do not leak local paths, workspace ids/names, or workspace counts;
- terminal stop / get semantics preserve session ownership;
- terminal sync and async execution both block the same destructive command fixtures before process creation, while inert quoted examples and documented safer variants remain allowed;
- admin-store files are mode-restricted, corrupt security JSON fails closed, and an interrupted transaction journal is replayed before state is exposed;
- malformed/oversized enrollment and admin request bodies fail schema validation instead of being string-coerced;
- privilege grants are explicit, missing grants are denied, inspect-only clients cannot see or call mutation/terminal/system/admin tools, propose clients can only call dry-run-capable filesystem tools with `dryRun=true`, and workspace binding outside the grant is denied;
- enrollment endpoints throttle repeated request / status polling;
- port validation rejects invalid or colliding main / admin ports;
- the client bundle (`/client/manifest.json` + `/client/file`) rejects existing and nonexistent hidden/auth/state/key/lock fixtures with the same bounded 400 response and never lists them in the manifest;
- admission-enabled GET/DELETE authenticate before reporting missing/unknown MCP sessions;
- foreign Origins receive `403`; configured-origin preflight remains wildcard without allow-credentials and never bypasses bearer authentication;
- unknown public routes return bounded JSON 404 responses without reflecting the route path.

Do not weaken a guard to make a flaky test pass. If a legitimate automation tool is blocked with 403, document and configure a narrow `CRAWLER_BYPASS_AGENTS` pattern rather than removing crawler protection.

- Deep health diagnostics require an approved bearer token when `PUBLIC_BASE_URL` is non-loopback; shallow health remains public for bootstrap discovery.
