# Recovery procedures

Use this overlay when the obvious fix did not resolve a failure. Capture only
the returned structured result/error and relevant project output; never capture
tokens or secret-bearing environment values.

| Symptom | First response |
|---|---|
| `ModuleNotFoundError: httpx` | Run `python3 -m pip install -r client/requirements.txt`. |
| `./conduit` unavailable | Invoke `python3 client/conduit_cli.py`; prefer importing the SDK for the workflow. |
| 404/503/tunnel failure | Run `./conduit doctor`; verify `CONDUIT_URL`; create a new `Conduit` instance. |
| Live Conduit service needs source reload | If the operator approves and the host uses the tested user-systemd service, use the delayed restart procedure below. |
| Supplied session rejected | Create a new instance without `CONDUIT_SESSION_ID`, then call `system.health()` and `workspace.current()`. |
| Wrong workspace | On the persistent instance, call `workspace.bind(id)` before the tool request. |
| Optimistic conflict | Re-read the path/hash and rebuild the intended edit or batch. |
| Lost mutation response | Inspect the resulting state. Retry only the exact request with the same idempotency key when the tool supports it. |
| Tool result is an error | `Conduit.structured()` raises `ToolError`; inspect its machine-readable data and fix the server-side rejection rather than treating `{}` as success. |
| Shell quoting fails | Stop serial CLI construction. Call the SDK directly or use `terminal.runScript` for a short remote script. |
| Long command exceeds a sync timeout | Use `terminal.execAsync`, then cursor-aware `terminal.attach`. |

For security-boundary failures, also follow `client/ops/security.md` and run
`npm run test:security`. For unfamiliar repository rules, load the project
overlay before changing source. Report the actual root cause once resolved.


## Tested self-hosted service restart: user systemd

Use this only when the active project is `conduit-local`, source has already
been validated, and the operator explicitly approves restarting the live service.
The goal is to let the current terminal request return before the server process
is replaced; if the tunnel/session drops anyway, the operator can restart
manually.

### Confirm the service shape

The live host used this user service during validation:

```text
conduit-local.service - Conduit Local MCP server
user cgroup: /user.slice/user-1000.slice/user@1000.service/app.slice/conduit-local.service
service file: ~/.config/systemd/user/conduit-local.service
```

A Conduit terminal command usually needs the user bus variables set explicitly:

```bash
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
systemctl --user status conduit-local.service --no-pager
```

If you are unsure which unit owns the process, inspect the node process cgroup
before touching systemd:

```bash
ps -eo pid,ppid,pgid,sid,stat,comm,args | grep -E 'node .*conduit-local|dist/src/main\.js' | grep -v grep
cat /proc/<node-pid>/cgroup
```

### Schedule a delayed self-restart

Do not run `systemctl --user restart conduit-local.service` directly from a
request that depends on the same service. Instead schedule a one-shot timer a few
seconds in the future:

```bash
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
unit="conduit-local-self-restart-$(date +%s)"

systemd-run --user \
  --unit="$unit" \
  --description="One-shot self restart for conduit-local.service" \
  --on-active=3s \
  --setenv=XDG_RUNTIME_DIR=/run/user/1000 \
  --setenv=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  /usr/bin/systemctl --user restart conduit-local.service

systemctl --user list-timers --all --no-pager | grep "$unit" || true
```

Expected behavior from the tested run:

- the scheduling command returns successfully before restart;
- the service rebuilds through `ExecStartPre=npm run build`;
- the main PID changes;
- ngrok remains running separately;
- existing MCP sessions are lost, so create a fresh `Conduit()` instance.

### Verify after the restart

From the agent sandbox, prefer the SDK or the launcher fallback:

```bash
./conduit doctor || bash ./conduit doctor
./conduit --tool runtime.health || bash ./conduit --tool runtime.health
```

If the launcher lost its executable bit in the sandbox, restore it locally with
`chmod +x ./conduit` or invoke it through `bash ./conduit`; this does not affect
the remote service.

Then confirm the remote unit and repository state:

```bash
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
systemctl --user status conduit-local.service --no-pager
ps -eo pid,ppid,pgid,sid,stat,comm,args | grep -E 'node .*conduit-local|ngrok http|dist/src/main\.js' | grep -v grep
```

For a server-side patch, verify the live behavior that required the restart. In
the malformed-JSON hardening run, the post-restart check was:

```bash
curl -sS -i \
  -H 'ngrok-skip-browser-warning: 1' \
  -H 'Content-Type: application/json' \
  --data '{invalid' \
  "$CONDUIT_URL/admin/enroll/request"
```

Expected bounded response:

```http
HTTP/2 400
content-type: application/json; charset=utf-8

{"error":{"message":"Invalid JSON request body"}}
```

Report the before/after main PID, `doctor`/`runtime.health` result, and any live
behavior check. If the restart does not come back promptly, stop retrying noisy
operations and ask the operator to restart locally.
