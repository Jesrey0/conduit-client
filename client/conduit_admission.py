#!/usr/bin/env python3
"""Validate admission and install or verify the Conduit Python client.

No third-party Python packages are required for this bootstrap step. It fetches
the client bundle from the Conduit HTTP bootstrap endpoints by default, so it
does not depend on the active MCP workspace.

Hardening: this script refuses to write a corrupt client/. It (1) consumes a
server-provided manifest, (2) validates every fetched blob for sentinels and
empty-source markers before writing, (3) stages to a temp dir and moves into
place atomically (all-or-nothing), and (4) enforces py_compile plus launcher
syntax checks as smoke tests.

Invite-based admission support:
- if CONDUIT_CLIENT_TOKEN (or the local auth file) is present, use it;
- otherwise, if CONDUIT_INVITE_ID and CONDUIT_INVITE_SECRET are present,
  request enrollment, poll until approval, persist the returned token, and then
  use MCP normally.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PYTHON_SIGNALS = (
    "import ", "from ", "def ", "class ", "#!", '"""', "'''", "#", "=", "return", "async ",
)

HTTP_DEFAULT_HEADERS = {
    "User-Agent": "conduit-bootstrap/1.0",
    "ngrok-skip-browser-warning": "1",
}


class AuthRequiredError(RuntimeError):
    pass


class AuthRejectedError(RuntimeError):
    pass


def die(message: str, code: int = 1) -> "None":
    print(f"\n[bootstrap] ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def die_auth_required() -> "None":
    die(
        "Conduit admission is enabled.\n"
        "Set CONDUIT_CLIENT_TOKEN or provide invite credentials, then rerun:\n\n"
        '  export CONDUIT_CLIENT_TOKEN="ctkn..."\n'
        "  python3 bootstrap_conduit_client.py --overwrite\n\n"
        "For invite enrollment, set CONDUIT_INVITE_ID and CONDUIT_INVITE_SECRET. "
        "Set CONDUIT_AGENT_MODEL when the model/family is known; otherwise the bootstrap reports it as undisclosed."
    )


def _parse_conduit_url(url: str) -> urllib.parse.SplitResult:
    cleaned = url.strip()
    parsed = urllib.parse.urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        die(
            f"invalid CONDUIT_URL {url!r}; expected https://host, "
            "https://host/<secret-prefix>, or https://host/<secret-prefix>/mcp"
        )
    return parsed


def _rebuild_url(parsed: urllib.parse.SplitResult, path: str) -> str:
    return urllib.parse.urlunsplit(parsed._replace(path=path, query="", fragment=""))


def normalize_base_url(base_url: str) -> str:
    parsed = _parse_conduit_url(base_url.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path.endswith("/mcp"):
        path = path[:-4]
    return _rebuild_url(parsed, path)


def client_manifest_url(base_url: str) -> str:
    return f"{normalize_base_url(base_url)}/client/manifest.json"


def client_file_url(base_url: str, path: str) -> str:
    encoded_path = urllib.parse.quote(path, safe="")
    return f"{normalize_base_url(base_url)}/client/file?path={encoded_path}"


def health_url(base_url: str, *, deep: bool = False) -> str:
    suffix = "/health?deep=1" if deep else "/health"
    return f"{normalize_base_url(base_url)}{suffix}"


class AuthState:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load_token(self) -> str | None:
        if not self.path.exists():
            return None
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError(f"Conduit auth file must be a regular file with mode 0600: {self.path}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PermissionError(f"Conduit auth file is not owned by the current user: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schemaVersion") != 1:
            raise ValueError("Unsupported Conduit auth schema; re-enroll this client")
        token = data.get("clientToken")
        return token if isinstance(token, str) and token else None

    def save_token(self, token: str, *, server_url: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": 1,
            "clientToken": token,
            "serverUrl": server_url,
            "savedAt": time.time(),
        }
        temp_path = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)


class EnrollmentState:
    """Minimal local resume record for an in-flight enrollment request."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self, *, server_url: str, invite_id: str, provisioning_id: str | None) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        info = self.path.stat()
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise PermissionError(f"Conduit enrollment state must be a regular file with mode 0600: {self.path}")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise PermissionError(f"Conduit enrollment state is not owned by the current user: {self.path}")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        required = {"schemaVersion", "serverUrl", "inviteId", "requestId", "requestSecret", "savedAt"}
        if not isinstance(data, dict) or data.get("schemaVersion") != 1 or not required.issubset(data):
            raise ValueError("Invalid Conduit enrollment resume state; do not delete it until the operator reviews the pending request")
        expected = (normalize_base_url(server_url), invite_id, provisioning_id)
        actual = (data.get("serverUrl"), data.get("inviteId"), data.get("provisioningId"))
        if actual != expected:
            raise ValueError("Existing enrollment resume state belongs to a different server, invite, or provisioning package")
        if not all(isinstance(data.get(key), str) and data[key] for key in ("requestId", "requestSecret")):
            raise ValueError("Enrollment resume state is missing its request credentials")
        return data

    def save(self, *, server_url: str, invite_id: str, provisioning_id: str | None, request_id: str, request_secret: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": 1,
            "serverUrl": normalize_base_url(server_url),
            "inviteId": invite_id,
            **({"provisioningId": provisioning_id} if provisioning_id else {}),
            "requestId": request_id,
            "requestSecret": request_secret,
            "savedAt": time.time(),
        }
        temp_path = self.path.with_name(f"{self.path.name}.tmp-{os.getpid()}-{time.time_ns()}")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, self.path)
        finally:
            temp_path.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def enrollment_endpoint(base_url: str, relative_path: str) -> str:
    normalized = normalize_base_url(base_url)
    relative = relative_path.lstrip("/")
    return f"{normalized}/{relative}"


def post_json(url: str, payload: dict[str, Any], *, timeout: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json", **HTTP_DEFAULT_HEADERS, **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    return json.loads(text or "{}")


def get_text(url: str, *, timeout: int, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(
        url,
        headers={**HTTP_DEFAULT_HEADERS, **(headers or {})},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def get_json(url: str, *, timeout: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", **HTTP_DEFAULT_HEADERS, **(headers or {})},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="replace")
    return json.loads(text or "{}")


def detect_client_admission(base_url: str, *, timeout: int) -> bool | None:
    try:
        health = get_json(health_url(base_url), timeout=timeout)
    except Exception:
        return None
    diagnostics = health.get("diagnostics") if isinstance(health, dict) else None
    if isinstance(diagnostics, dict) and "clientAdmissionEnabled" in diagnostics:
        return bool(diagnostics.get("clientAdmissionEnabled"))
    if "clientAdmissionEnabled" in health:
        return bool(health.get("clientAdmissionEnabled"))
    return None


def fetch_client_files_direct(
    base_url: str,
    *,
    timeout: int,
    client_token: str | None = None,
    max_workers: int = 6,
) -> tuple[list[str], dict[str, str]]:
    if not 1 <= max_workers <= 16:
        raise ValueError("direct bundle fetch workers must be between 1 and 16")
    headers = {"Authorization": f"Bearer {client_token}"} if client_token else None
    manifest = get_json(client_manifest_url(base_url), timeout=timeout, headers=headers)
    raw_paths = manifest.get("files")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise RuntimeError(f"unexpected client manifest response: {json.dumps(manifest, indent=2)[:2000]}")

    paths = [str(raw_path) for raw_path in raw_paths]
    invalid = [path for path in paths if not path.startswith("client/")]
    if invalid:
        raise RuntimeError(f"client manifest returned invalid path: {invalid[0]}")
    if len(paths) != len(set(paths)):
        raise RuntimeError("client manifest returned duplicate paths")

    def fetch_one(path: str) -> tuple[str, str]:
        return path, get_text(client_file_url(base_url, path), timeout=timeout, headers=headers)

    files: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(max_workers, len(paths)), thread_name_prefix="conduit-bootstrap") as executor:
        pending = {executor.submit(fetch_one, path): path for path in paths}
        try:
            for future in as_completed(pending):
                path, content = future.result()
                files[path] = content
        except Exception:
            for future in pending:
                future.cancel()
            raise
    return paths, files


def describe_http_error(exc: urllib.error.HTTPError) -> str:
    detail = exc.read().decode("utf-8", errors="replace")
    return detail or exc.reason or f"HTTP {exc.code}"


def classify_auth_error(status: int, detail: str) -> RuntimeError | None:
    reason = ""
    try:
        parsed = json.loads(detail)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        data = error.get("data") if isinstance(error, dict) else None
        if isinstance(data, dict):
            reason = str(data.get("reason") or "")
        if not reason and isinstance(error, dict):
            reason = str(error.get("message") or "")
    except Exception:
        reason = detail

    normalized = reason.upper()
    if status == 401 and ("AUTH_REQUIRED" in normalized or "MISSING BEARER" in normalized):
        return AuthRequiredError(reason or detail)
    if status in {401, 403}:
        return AuthRejectedError(reason or detail)
    return None


def normalize_enrollment_model_id(raw_model_id: str | None) -> str:
    model_id = (raw_model_id or "undisclosed").strip() or "undisclosed"
    if model_id.lower() in {"unknown", "unspecified", "n/a", "not available", "none", "null"}:
        raise ValueError(
            'CONDUIT_AGENT_MODEL must name a model/model family or use "undisclosed"; '
            'omit the variable when Arena Agent Mode does not disclose the model'
        )
    return model_id


def enroll_client(
    base_url: str,
    *,
    invite_id: str,
    invite_secret: str,
    model_id: str,
    timeout_s: int,
    poll_s: float,
    enrollment_state: EnrollmentState,
    provisioning_id: str | None = None,
    requested_grant: dict[str, Any] | None = None,
) -> str:
    resumed = enrollment_state.load(
        server_url=base_url, invite_id=invite_id, provisioning_id=provisioning_id
    )
    if resumed:
        request_id = str(resumed["requestId"])
        request_secret = str(resumed["requestSecret"])
        print(f"resuming enrollment request {request_id}; keep this state file private until completion")
    else:
        label = os.getenv("CONDUIT_CLIENT_LABEL") or f"conduit-bootstrap:{uuid.uuid4()}"
        create = post_json(
            enrollment_endpoint(base_url, "/admin/enroll/request"),
            {
                "inviteId": invite_id,
                "inviteSecret": invite_secret,
                "label": label,
                "clientInfo": {"name": "conduit-bootstrap", "version": "1.0.0", "modelId": model_id},
                **({"requestedGrant": requested_grant} if requested_grant else {}),
            },
            timeout=max(30, timeout_s),
        )
        request_id = create.get("requestId")
        request_secret = create.get("requestSecret")
        if not request_id or not request_secret:
            raise RuntimeError(f"unexpected enroll/request response: {json.dumps(create, indent=2)}")
        enrollment_state.save(
            server_url=base_url,
            invite_id=invite_id,
            provisioning_id=provisioning_id,
            request_id=str(request_id),
            request_secret=str(request_secret),
        )
        print(f"enrollment request {request_id} is pending; it is safe to stop and rerun this same command")

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = post_json(
            enrollment_endpoint(base_url, "/admin/enroll/status"),
            {"requestId": request_id, "requestSecret": request_secret},
            timeout=max(30, timeout_s),
        )
        state = status.get("status")
        if state == "approved":
            token = status.get("clientToken")
            if not token:
                raise RuntimeError("enrollment was approved but no clientToken was returned")
            return str(token)
        if state == "denied":
            enrollment_state.clear()
            raise RuntimeError("enrollment request was denied by the admin")
        if state != "pending":
            raise RuntimeError(f"unexpected enrollment status payload: {json.dumps(status, indent=2)}")
        time.sleep(max(poll_s, 0.25))

    raise RuntimeError(
        "timed out waiting for admin approval; enrollment resume state was retained. "
        "Rerun the same bootstrap command after approval."
    )


def _has_python_signal(text: str) -> bool:
    return any(signal in text for signal in _PYTHON_SIGNALS)


def looks_corrupt(name: str, text: str) -> bool:
    if text.lstrip().startswith("<error>"):
        return True
    if name.endswith("requirements.txt") and "<error>" in text:
        return True
    if name.endswith(".py") and text.strip() and not _has_python_signal(text):
        return True
    return False


def validate_all(files: dict[str, str], requested: list[str], errors: dict[str, Any]) -> None:
    if errors:
        lines = "\n".join(
            f"  - {path}: {info.get('code', '?')}: {info.get('message', '')}" if isinstance(info, dict) else f"  - {path}: {info}"
            for path, info in errors.items()
        )
        die(f"read-multi reported {len(errors)} failed path(s); writing nothing:\n{lines}")

    missing = [path for path in requested if path not in files]
    if missing:
        die(f"{len(missing)} requested path(s) missing from the response; writing nothing:\n" + "\n".join(f"  - {path}" for path in missing))

    corrupt = [name for name, text in files.items() if looks_corrupt(name, text)]
    if corrupt:
        die(f"{len(corrupt)} fetched file(s) look corrupt; writing nothing:\n" + "\n".join(f"  - {name}" for name in corrupt))


def write_files_atomic(files: dict[str, str]) -> None:
    staging = Path(tempfile.mkdtemp(prefix="conduit-bootstrap-"))
    try:
        for path, content in sorted(files.items()):
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        for path in sorted(files):
            dest = Path(path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(staging / path), str(dest))
            print(f"wrote {dest}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def smoke_compile(files: dict[str, str]) -> None:
    failures: list[str] = []
    for path in sorted(files):
        if path.endswith(".py"):
            try:
                py_compile.compile(path, doraise=True)
            except py_compile.PyCompileError as exc:
                failures.append(f"{path}: {exc}")
    if failures:
        die("post-fetch py_compile failed:\n" + "\n".join(f"  - {failure}" for failure in failures))


def smoke_launcher(path: str | Path = "conduit") -> None:
    target = Path(path)
    if not target.exists():
        return
    bash = shutil.which("bash")
    if not bash:
        print("[bootstrap][warn] bash not found; skipping launcher syntax check", file=sys.stderr)
        return
    result = subprocess.run([bash, "-n", str(target)], text=True, capture_output=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        die(f"launcher syntax check failed for {target}:\n{detail}")


_ENV_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")


def _dotenv_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    escaped = escaped.replace('\"', '\\"').replace("$", "\\$").replace("`", "\\`")
    return f'"{escaped}"'


def _write_private_text_atomic(target: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent), text=True)
    try:
        os.close(fd)
        temp_path = Path(temporary)
        temp_path.write_text(content, encoding="utf-8")
        os.chmod(temp_path, 0o600)
        os.replace(temp_path, target)
    finally:
        try:
            Path(temporary).unlink()
        except FileNotFoundError:
            pass


def write_env_file(base_url: str, auth_path: str | Path, *, overwrite: bool) -> None:
    target = Path(".env")
    desired: dict[str, str | None] = {
        "CONDUIT_URL": normalize_base_url(base_url),
        "CONDUIT_AUTH_PATH": None if Path(auth_path) == Path.home() / ".conduit_auth.json" else str(auth_path),
    }
    agent_model = os.getenv("CONDUIT_AGENT_MODEL")
    if agent_model:
        desired["CONDUIT_AGENT_MODEL"] = agent_model

    if target.exists() and not overwrite:
        existing = target.read_text(encoding="utf-8").splitlines()
        merged: list[str] = []
        seen: set[str] = set()
        for line in existing:
            match = _ENV_ASSIGNMENT_RE.match(line)
            key = match.group(1) if match else None
            if key not in desired:
                merged.append(line)
                continue
            if key in seen:
                continue
            seen.add(key)
            value = desired[key]
            if value is not None:
                merged.append(f"{key}={_dotenv_quote(value)}")
        for key, value in desired.items():
            if key not in seen and value is not None:
                merged.append(f"{key}={_dotenv_quote(value)}")
        content = "\n".join(merged) + "\n"
        action = "updated .env (preserved existing entries)"
    else:
        content = "\n".join(
            f"{key}={_dotenv_quote(value)}" for key, value in desired.items() if value is not None
        ) + "\n"
        action = "replaced .env" if target.exists() else "wrote .env"

    _write_private_text_atomic(target, content)
    print(action)



def write_conduit_launcher(*, overwrite: bool) -> None:
    target = Path("conduit")
    if target.exists() and not overwrite:
        target.chmod(0o755)
        print(f"skip existing {target} (ensured executable permissions 0o755)")
        return
    target.write_text(
        """#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$ROOT/.env"

if [ -f "$ENV_FILE" ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|'#'*) continue ;;
    esac
    key="${line%%=*}"
    value="${line#*=}"
    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
      if [ "${#value}" -ge 2 ]; then
        first="${value:0:1}"
        last="${value: -1}"
        if { [ "$first" = '"' ] && [ "$last" = '"' ]; } || { [ "$first" = "'" ] && [ "$last" = "'" ]; }; then
          value="${value:1:${#value}-2}"
        fi
      fi
      export "$key=$value"
    fi
  done < "$ENV_FILE"
fi

exec python3 "$ROOT/client/conduit_cli.py" "$@"
""",
        encoding="utf-8",
    )
    target.chmod(0o755)
    print(f"wrote executable {target}")


def install_deps() -> None:
    print("installing Python client dependencies...")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r", "client/requirements.txt"], check=True)


def run_post_bootstrap_verify(*, client_token: str | None) -> None:
    if not Path("conduit").exists():
        print("[bootstrap][warn] ./conduit launcher not present; skipping command verification", file=sys.stderr)
        return
    env = os.environ.copy()
    if client_token and not env.get("CONDUIT_CLIENT_TOKEN"):
        env["CONDUIT_CLIENT_TOKEN"] = client_token
    checks = [
        ["./conduit", "--tool", "runtime.health"],
        ["./conduit", "doctor"],
        ["./conduit", "--tool", "workspace.current"],
    ]
    for command in checks:
        print(f"verify: {' '.join(command)}")
        subprocess.run(command, check=True, env=env)




def _split_csv_env(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(',') if part.strip()]

def load_provisioning(path: str, script_path: str, *, allow_expired_resume: bool = False) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"schemaVersion", "provisioningId", "purpose", "lifecycle", "authorization", "server", "requestedGrant", "invite", "bootstrap", "credentialHandling"}
    if not isinstance(data, dict) or data.get("schemaVersion") != 2 or not required.issubset(data):
        die("invalid Conduit provisioning envelope")
    authorization = data["authorization"]
    if (authorization.get("state") != "AUTHORIZED_TO_REQUEST_ENROLLMENT"
            or authorization.get("accessClass") not in {"LIVE_PROBATION", "REGULAR_OPERATOR_PROMOTION"}
            or authorization.get("approvalRequired") is not True):
        die("provisioning envelope is not authorized for enrollment")
    try:
        expires = datetime.fromisoformat(str(authorization["expiresAt"]).replace("Z", "+00:00"))
    except Exception:
        die("provisioning envelope has invalid expiry")
    if expires <= datetime.now(timezone.utc) and not allow_expired_resume:
        die("provisioning envelope has expired")
    url = str(data["server"].get("url", ""))
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        die("provisioning envelope has invalid Conduit URL")
    grant = data["requestedGrant"]
    if not isinstance(grant, dict) or not grant.get("privileges") or not grant.get("workspaceIds"):
        die("provisioning envelope requires a non-empty grant")
    bootstrap_url = str(data["bootstrap"].get("url", ""))
    expected_bootstrap_url = f"{normalize_base_url(url)}/bootstrap_conduit_client.py"
    if bootstrap_url != expected_bootstrap_url:
        die("provisioning envelope has invalid bootstrap URL")
    expected_hash = str(data["bootstrap"].get("sha256", ""))
    actual_hash = hashlib.sha256(Path(script_path).read_bytes()).hexdigest()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash) or actual_hash != expected_hash:
        die("bootstrap SHA-256 does not match the provisioning envelope")
    if data["purpose"] not in {"ENROLLMENT", "PROMOTE_CLIENT"} or data["lifecycle"] not in {"probation", "active"}:
        die("unsupported provisioning purpose or lifecycle")
    if data["purpose"] == "PROMOTE_CLIENT" and not data.get("replacesClientId"):
        die("promotion provisioning requires replacesClientId")
    handling = data["credentialHandling"]
    if (handling.get("resultingAuthPath") != "/home/user/.conduit_auth.json"
            or handling.get("enrollmentResumePath") != "/home/user/.conduit_enrollment.json"
            or handling.get("requiredMode") != "0600"):
        die("unsupported provisioning credential-handling policy")
    return data


def provisioning_summary(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "valid": True, "provisioningId": data["provisioningId"],
        "purpose": data["purpose"], "lifecycle": data["lifecycle"],
        **({"replacesClientId": data["replacesClientId"]} if data.get("replacesClientId") else {}),
        "state": data["authorization"]["state"], "accessClass": data["authorization"]["accessClass"], "expiresAt": data["authorization"]["expiresAt"],
        "server": data["server"]["url"], "bootstrapUrl": data["bootstrap"]["url"], "requestedGrant": data["requestedGrant"],
        "bootstrapHashMatches": True, "networkUsed": False, "filesWritten": [],
    }


def build_requested_grant(args: argparse.Namespace) -> dict[str, Any] | None:
    privileges = list(args.request_privilege or []) + _split_csv_env(os.getenv("CONDUIT_REQUEST_PRIVILEGES"))
    workspaces = list(args.request_workspace or []) + _split_csv_env(os.getenv("CONDUIT_REQUEST_WORKSPACES"))
    default_workspace = args.request_default_workspace or os.getenv("CONDUIT_REQUEST_DEFAULT_WORKSPACE")
    if not privileges and not workspaces and not default_workspace:
        return None
    if not privileges or not workspaces:
        die("requested grant requires at least one --request-privilege and one --request-workspace")
    grant: dict[str, Any] = {"privileges": privileges, "workspaceIds": workspaces}
    if default_workspace:
        grant["defaultWorkspaceId"] = default_workspace
    return grant

def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Conduit Local's Python client into this sandbox")
    parser.add_argument("--url", default=os.getenv("CONDUIT_URL"), help="Conduit base URL or /mcp URL. Defaults to CONDUIT_URL.")
    parser.add_argument("--invite-file", help="Versioned Conduit provisioning envelope containing a one-time invite.")
    parser.add_argument("--inspect-provisioning", action="store_true", help="Validate and summarize --invite-file without network or writes.")
    parser.add_argument("--invite-id", default=os.getenv("CONDUIT_INVITE_ID"), help="Optional invite id for enrollment.")
    parser.add_argument("--invite-secret", default=os.getenv("CONDUIT_INVITE_SECRET"), help="Optional invite secret for enrollment.")
    parser.add_argument("--auth-file", default=os.getenv("CONDUIT_AUTH_PATH", str(Path.home() / ".conduit_auth.json")), help="Local auth-state file for the approved client token (default: ~/.conduit_auth.json).")
    parser.add_argument("--enroll-timeout-s", type=int, default=int(os.getenv("CONDUIT_ENROLL_TIMEOUT_S", "300")), help="Maximum time to wait for admin approval.")
    parser.add_argument("--enroll-poll-s", type=float, default=float(os.getenv("CONDUIT_ENROLL_POLL_S", "5")), help="Polling interval while waiting for approval.")
    parser.add_argument("--enrollment-state-file", default=os.getenv("CONDUIT_ENROLLMENT_STATE_PATH", str(Path.home() / ".conduit_enrollment.json")), help="Mode-0600 local resume record for an in-flight enrollment (default: ~/.conduit_enrollment.json).")
    parser.add_argument("--fetch-workers", type=int, default=int(os.getenv("CONDUIT_BOOTSTRAP_FETCH_WORKERS", "6")), help="Bounded concurrent workers for direct bundle file downloads (1-16, default 6).")
    parser.add_argument("--request-privilege", action="append", default=[], help="Privilege to request during invite enrollment; repeat or use CONDUIT_REQUEST_PRIVILEGES=inspect,propose.")
    parser.add_argument("--request-workspace", action="append", default=[], help="Workspace id to request during invite enrollment; repeat or use CONDUIT_REQUEST_WORKSPACES=bar-coop-sys.")
    parser.add_argument("--request-default-workspace", help="Default workspace id to request during invite enrollment; or CONDUIT_REQUEST_DEFAULT_WORKSPACE.")
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument("--save-token", dest="save_token", action="store_true", default=True, help="Persist CONDUIT_CLIENT_TOKEN to the auth file for later ./conduit calls (default).")
    token_group.add_argument("--no-save-token", dest="save_token", action="store_false", help="Do not persist an environment-provided CONDUIT_CLIENT_TOKEN.")
    parser.add_argument("--install-deps", action="store_true", help="Run python -m pip install -r client/requirements.txt after writing files.")
    parser.add_argument("--verify", action="store_true", help="Run post-bootstrap health, doctor, and workspace checks.")
    parser.add_argument("--no-launcher", action="store_true", help="Do not create the ./conduit shorthand launcher")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite fetched client files and launcher; existing .env entries are still merged/preserved.")
    parser.add_argument("--overwrite-env", action="store_true", help="Replace .env with bootstrap-managed keys instead of preserving unrelated existing entries.")
    parser.add_argument("--list-only", action="store_true", help="Only print the remote client files that would be fetched")
    args = parser.parse_args()

    if args.inspect_provisioning and not args.invite_file:
        die("--inspect-provisioning requires --invite-file")
    provisioning = None
    if args.invite_file:
        provisioning = load_provisioning(
            args.invite_file,
            __file__,
            allow_expired_resume=not args.inspect_provisioning and Path(args.enrollment_state_file).exists(),
        )
        if args.inspect_provisioning:
            print(json.dumps(provisioning_summary(provisioning), indent=2))
            return 0
        args.url = provisioning["server"]["url"]
        args.invite_id = provisioning["invite"]["id"]
        args.invite_secret = provisioning["invite"]["secret"]
        args.request_privilege = list(provisioning["requestedGrant"]["privileges"])
        args.request_workspace = list(provisioning["requestedGrant"]["workspaceIds"])
        args.request_default_workspace = provisioning["requestedGrant"].get("defaultWorkspaceId")
        args.enrollment_state_file = provisioning["credentialHandling"]["enrollmentResumePath"]

    if not args.url:
        print("Error: provide --url or set CONDUIT_URL", file=sys.stderr)
        return 2

    auth_state = AuthState(args.auth_file)
    env_client_token = os.getenv("CONDUIT_CLIENT_TOKEN")
    saved_client_token = auth_state.load_token()
    force_invite_enrollment = bool(args.invite_file)
    client_token = None if force_invite_enrollment else (env_client_token or saved_client_token)
    pending_provisioned_token = False
    if bool(args.invite_id) != bool(args.invite_secret):
        die("provide both --invite-id and --invite-secret (or set both CONDUIT_INVITE_ID and CONDUIT_INVITE_SECRET)")

    admission_enabled = detect_client_admission(args.url, timeout=30)
    if admission_enabled and not client_token and not (args.invite_id and args.invite_secret):
        die_auth_required()

    if not client_token and args.invite_id and args.invite_secret:
        try:
            model_id = normalize_enrollment_model_id(os.getenv("CONDUIT_AGENT_MODEL"))
        except ValueError as exc:
            die(str(exc))
        requested_grant = build_requested_grant(args)
        print("requesting invite-based enrollment...")
        enrollment_state = EnrollmentState(args.enrollment_state_file)
        client_token = enroll_client(
            args.url,
            invite_id=args.invite_id,
            invite_secret=args.invite_secret,
            model_id=model_id,
            timeout_s=args.enroll_timeout_s,
            poll_s=args.enroll_poll_s,
            enrollment_state=enrollment_state,
            provisioning_id=provisioning.get("provisioningId") if provisioning else None,
            requested_grant=requested_grant,
        )
        if force_invite_enrollment:
            pending_provisioned_token = True
        else:
            auth_state.save_token(client_token, server_url=normalize_base_url(args.url))
            EnrollmentState(args.enrollment_state_file).clear()
            print(f"wrote auth token {args.auth_file}; cleared enrollment resume state")
    elif env_client_token and args.save_token and env_client_token != saved_client_token:
        auth_state.save_token(env_client_token, server_url=normalize_base_url(args.url))
        print(f"wrote auth token {args.auth_file}")
    elif env_client_token and not args.save_token:
        print("[bootstrap][warn] token was provided via environment and was not persisted; future shells must set CONDUIT_CLIENT_TOKEN", file=sys.stderr)

    try:
        print(f"fetching client bundle manifest from {client_manifest_url(args.url)}")
        paths, files = fetch_client_files_direct(
            args.url, timeout=120, client_token=client_token, max_workers=args.fetch_workers
        )
        validate_all(files, paths, {})
    except urllib.error.HTTPError as exc:
        detail = describe_http_error(exc)
        auth_error = classify_auth_error(exc.code, detail)
        if isinstance(auth_error, AuthRequiredError):
            die_auth_required()
        if auth_error is not None:
            die(f"Conduit rejected the supplied client token: {auth_error}")
        die(f"client bundle fetch failed with HTTP {exc.code}: {detail}")
    except Exception as exc:
        die(f"client bundle fetch failed: {exc}")

    print("remote client files:")
    for path in paths:
        print(f"  {path}")

    if args.list_only:
        return 0

    write_files_atomic(files)

    write_env_file(args.url, args.auth_file, overwrite=args.overwrite_env)
    if not args.no_launcher:
        write_conduit_launcher(overwrite=args.overwrite)

    smoke_compile(files)
    print("py_compile: OK")
    smoke_launcher()
    print("launcher syntax: OK")

    if args.install_deps:
        install_deps()

    if args.verify:
        run_post_bootstrap_verify(client_token=client_token)

    if pending_provisioned_token:
        auth_state.save_token(client_token, server_url=normalize_base_url(args.url))
        EnrollmentState(args.enrollment_state_file).clear()
        print(f"verified and atomically replaced auth token {args.auth_file}; cleared enrollment resume state")

    print("\nNext steps:")
    print("  Required onboarding: read client/AGENTS.md, then client/README.md and client/ops/OPS.md")
    if not args.install_deps:
        print("  python3 -m pip install -r client/requirements.txt")
    if not args.verify:
        print("  ./conduit --tool runtime.health")
        print("  ./conduit doctor")
        print("  ./conduit --tool workspace.current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
