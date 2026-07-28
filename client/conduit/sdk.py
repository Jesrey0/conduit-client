#!/usr/bin/env python3
"""Agent-native async SDK for Conduit Local."""

from __future__ import annotations

import asyncio
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .auth import ConduitAuthState
from .errors import ToolError
from .transport import IDEMPOTENCY_AWARE_TOOLS, MCPTransport
from .types import MCPToolResponse, SearchResult, TerminalExecResult
from .utils import mcp_error_payload


_CLIENT_ENV_KEYS = {
    "CONDUIT_URL",
    "CONDUIT_AUTH_PATH",
    "CONDUIT_CLIENT_TOKEN",
    "CONDUIT_TOKEN",
}


def _client_env_path() -> Path:
    """Return the source-client env file beside the client bundle root."""
    return Path(__file__).resolve().parents[2] / ".env"


def _strip_env_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_client_env_defaults(env_path: Path | None = None) -> dict[str, str]:
    """Read safe client defaults without mutating process environment."""
    path = env_path or _client_env_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except OSError:
        return {}

    defaults: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in _CLIENT_ENV_KEYS:
            defaults[key] = _strip_env_quotes(value)
    return defaults


class WorkspaceNamespace:
    def __init__(self, c: Conduit): self._c = c
    async def current(self) -> dict[str, Any] | None: return self._c.structured(await self._c.call("workspace.current")).get("workspace")
    async def list(self) -> list[dict[str, Any]]: return self._c.structured(await self._c.call("workspace.list")).get("workspaces", [])
    async def add(self, id: str, name: str, root: str, *, source: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"id": id, "name": name, "root": root}
        if source: args["source"] = source
        return self._c.structured(await self._c.call("workspace.add", args))
    async def remove(self, id: str) -> dict[str, Any]: return self._c.structured(await self._c.call("workspace.remove", {"id": id}))
    async def bind(self, id: str) -> dict[str, Any]: return self._c.structured(await self._c.call("workspace.bind", {"id": id}))


class GitNamespace:
    def __init__(self, c: Conduit): self._c = c
    async def status(self, repo_path: str = ".") -> dict[str, Any]: return self._c.structured(await self._c.call("git.status", {"repoPath": repo_path}))
    async def diff(self, *, repo_path: str = ".", target: str | None = None) -> dict[str, Any]:
        args = {"repoPath": repo_path}
        if target: args["target"] = target
        return self._c.structured(await self._c.call("git.diff", args))
    async def diffUnstaged(self, repo_path: str = ".") -> dict[str, Any]: return self._c.structured(await self._c.call("git.diff-unstaged", {"repoPath": repo_path}))
    async def diffStaged(self, repo_path: str = ".") -> dict[str, Any]: return self._c.structured(await self._c.call("git.diff-staged", {"repoPath": repo_path}))
    async def add(self, files: list[str] | None = None, *, repo_path: str = ".", idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"repoPath": repo_path, "files": files or ["."]}
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("git.add", args))
    async def commit(self, message: str, *, repo_path: str = ".", idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"repoPath": repo_path, "message": message}
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("git.commit", args))
    async def checkout(self, branch_name: str, *, repo_path: str = ".", idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"repoPath": repo_path, "branchName": branch_name}
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("git.checkout", args))
    async def createBranch(self, branch_name: str, *, base_branch: str | None = None, repo_path: str = ".", idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"repoPath": repo_path, "branchName": branch_name}
        if base_branch: args["baseBranch"] = base_branch
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("git.create-branch", args))
    async def checkoutNewBranch(self, branch_name: str, *, base_branch: str | None = None, repo_path: str = ".") -> dict[str, Any]:
        await self.createBranch(branch_name, base_branch=base_branch, repo_path=repo_path)
        return await self.checkout(branch_name, repo_path=repo_path)
    async def log(self, *, repo_path: str = ".", limit: int = 10, cursor: str | None = None, start_timestamp: str | None = None, end_timestamp: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"repoPath": repo_path, "limit": limit}
        if cursor: args["cursor"] = cursor
        if start_timestamp: args["startTimestamp"] = start_timestamp
        if end_timestamp: args["endTimestamp"] = end_timestamp
        return self._c.structured(await self._c.call("git.log", args))
    async def show(self, revision: str, *, repo_path: str = ".") -> dict[str, Any]: return self._c.structured(await self._c.call("git.show", {"repoPath": repo_path, "revision": revision}))
    async def reset(self, *, mode: str = "mixed", revision: str | None = None, repo_path: str = ".", idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"repoPath": repo_path, "mode": mode}
        if revision: args["revision"] = revision
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("git.reset", args))
    async def branch(self, *, repo_path: str = ".", branch_type: str | None = None, contains: str | None = None, not_contains: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"repoPath": repo_path}
        if branch_type: args["branchType"] = branch_type
        if contains: args["contains"] = contains
        if not_contains: args["notContains"] = not_contains
        return self._c.structured(await self._c.call("git.branch", args))


class FilesystemNamespace:
    def __init__(self, c: Conduit): self._c = c
    async def listDir(self, path: str = ".", *, recursive: bool = False) -> list[dict[str, Any]]: return self._c.structured(await self._c.call("filesystem.list", {"path": path, "recursive": recursive})).get("entries", [])
    async def read(self, path: str, *, head: int | None = None, tail: int | None = None, start_index: int | None = None, max_length: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path}
        if head is not None: args["head"] = head
        if tail is not None: args["tail"] = tail
        if start_index is not None: args["startIndex"] = start_index
        if max_length is not None: args["maxLength"] = max_length
        return self._c.structured(await self._c.call("filesystem.read", args))
    async def readMulti(self, paths: list[str], *, max_length_per_file: int | None = None, max_total_length: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"paths": paths}
        if max_length_per_file is not None: args["maxLengthPerFile"] = max_length_per_file
        if max_total_length is not None: args["maxTotalLength"] = max_total_length
        return self._c.structured(await self._c.call("filesystem.read-multi", args))
    async def readMedia(self, path: str, *, mode: str = "inline", max_inline_bytes: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path, "mode": mode}
        if max_inline_bytes is not None: args["maxInlineBytes"] = max_inline_bytes
        return self._c.structured(await self._c.call("filesystem.read-media", args))
    async def write(self, path: str, content: str, *, base_hash: str | None = None, dry_run: bool = False, create_only: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path, "content": content, "dryRun": dry_run, "createOnly": create_only}
        if base_hash is not None: args["baseHash"] = base_hash
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.write", args))
    async def edit(self, path: str, edits: list[dict[str, Any]], *, base_hash: str | None = None, dry_run: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path, "edits": edits, "dryRun": dry_run}
        if base_hash is not None: args["baseHash"] = base_hash
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.edit", args))
    async def batch(self, operations: list[dict[str, Any]], *, dry_run: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"operations": operations, "dryRun": dry_run}
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.batch", args))
    async def mkdir(self, path: str, *, recursive: bool = True, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path, "recursive": recursive}
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.mkdir", args))
    async def move(self, source: str, destination: str, *, conflict_policy: str = "error", expected_type: str | None = None, base_hash: str | None = None, allow_vcs_metadata: bool = False, max_entries: int | None = None, dry_run: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"source": source, "destination": destination, "conflictPolicy": conflict_policy, "allowVcsMetadata": allow_vcs_metadata, "dryRun": dry_run}
        if expected_type: args["expectedType"] = expected_type
        if base_hash: args["baseHash"] = base_hash
        if max_entries: args["maxEntries"] = max_entries
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.move", args))
    async def copy(self, source: str, destination: str, *, conflict_policy: str = "error", expected_type: str | None = None, base_hash: str | None = None, allow_vcs_metadata: bool = False, max_entries: int | None = None, dry_run: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"source": source, "destination": destination, "conflictPolicy": conflict_policy, "allowVcsMetadata": allow_vcs_metadata, "dryRun": dry_run}
        if expected_type: args["expectedType"] = expected_type
        if base_hash: args["baseHash"] = base_hash
        if max_entries: args["maxEntries"] = max_entries
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.copy", args))
    async def delete(self, path: str, *, recursive: bool = False, missing_ok: bool = False, expected_type: str | None = None, base_hash: str | None = None, allow_vcs_metadata: bool = False, max_entries: int | None = None, dry_run: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path, "recursive": recursive, "missingOk": missing_ok, "allowVcsMetadata": allow_vcs_metadata, "dryRun": dry_run}
        if expected_type: args["expectedType"] = expected_type
        if base_hash is not None: args["baseHash"] = base_hash
        if max_entries: args["maxEntries"] = max_entries
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.delete", args))
    async def deleteBatch(self, targets: list[dict[str, Any]], *, max_total_entries: int | None = None, dry_run: bool = False, idempotency_key: str | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"targets": targets, "dryRun": dry_run}
        if max_total_entries: args["maxTotalEntries"] = max_total_entries
        if idempotency_key: args["idempotencyKey"] = idempotency_key
        return self._c.structured(await self._c.call("filesystem.delete-batch", args))
    async def find(self, *, pattern: str | None = None, path: str = ".", max_depth: int | None = None, include_patterns: list[str] | None = None, exclude_patterns: list[str] | None = None, respect_gitignore: bool = True, include_default_ignored: bool = False) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"path": path, "respectGitignore": respect_gitignore, "includeDefaultIgnored": include_default_ignored}
        if pattern: args["pattern"] = pattern
        if max_depth is not None: args["maxDepth"] = max_depth
        if include_patterns: args["includePatterns"] = include_patterns
        if exclude_patterns: args["excludePatterns"] = exclude_patterns
        return self._c.structured(await self._c.call("filesystem.find", args)).get("matches", [])
    async def grep(self, pattern: str, *, path: str = ".", include_patterns: list[str] | None = None, exclude_patterns: list[str] | None = None, before_context: int = 0, after_context: int = 0, respect_gitignore: bool = True, max_output_length: int | None = None, max_line_length: int | None = None, timeout_ms: int = 30000) -> dict[str, Any]:
        args: dict[str, Any] = {"path": path, "pattern": pattern, "beforeContext": before_context, "afterContext": after_context, "respectGitignore": respect_gitignore, "timeoutMs": timeout_ms}
        if include_patterns: args["includePatterns"] = include_patterns
        if exclude_patterns: args["excludePatterns"] = exclude_patterns
        if max_output_length: args["maxOutputLength"] = max_output_length
        if max_line_length: args["maxLineLength"] = max_line_length
        return self._c.structured(await self._c.call("filesystem.grep", args))
    async def tree(self, path: str = ".", *, max_depth: int | None = None, respect_gitignore: bool = True) -> list[dict[str, Any]]:
        args: dict[str, Any] = {"path": path, "respectGitignore": respect_gitignore}
        if max_depth is not None: args["maxDepth"] = max_depth
        return self._c.structured(await self._c.call("filesystem.tree", args)).get("tree", [])
    async def info(self, path: str, *, include_hash: bool = False) -> dict[str, Any]: return self._c.structured(await self._c.call("filesystem.info", {"path": path, "hash": include_hash}))
    async def search(self, query: str) -> list[SearchResult]: return self._c.structured(await self._c.call("search", {"query": query})).get("results", [])
    async def searchRead(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        results = await self.search(query)
        return [await self.read(item["id"]) for item in results[:limit]]


class TerminalNamespace:
    def __init__(self, c: Conduit): self._c = c
    async def exec(self, executable: str, args: list[str] | None = None, *, path: str = ".", timeout_ms: int = 30_000, env: dict[str, str] | None = None) -> TerminalExecResult:
        payload: dict[str, Any] = {"executable": executable, "args": args or [], "path": path, "timeoutMs": timeout_ms}
        if env is not None: payload["env"] = env
        return self._c.structured(await self._c.call("terminal.exec", payload))  # type: ignore[return-value]
    async def execAsync(self, executable: str, args: list[str] | None = None, *, path: str = ".", timeout_ms: int = 120_000, env: dict[str, str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"executable": executable, "args": args or [], "path": path, "timeoutMs": timeout_ms}
        if env is not None: payload["env"] = env
        return self._c.structured(await self._c.call("terminal.exec_async", payload))
    async def runScript(self, script: str, *, interpreter: str = "bash", interpreter_args: list[str] | None = None, args: list[str] | None = None, extension: str | None = None, cleanup: bool = True, path: str = ".", timeout_ms: int = 30_000, env: dict[str, str] | None = None) -> TerminalExecResult:
        payload = self._c._script_payload(script, interpreter, interpreter_args, args, extension, cleanup, path, timeout_ms, env)
        return self._c.structured(await self._c.call("terminal.run_script", payload))  # type: ignore[return-value]
    async def runScriptAsync(self, script: str, *, interpreter: str = "bash", interpreter_args: list[str] | None = None, args: list[str] | None = None, extension: str | None = None, cleanup: bool = True, path: str = ".", timeout_ms: int = 120_000, env: dict[str, str] | None = None) -> dict[str, Any]:
        payload = self._c._script_payload(script, interpreter, interpreter_args, args, extension, cleanup, path, timeout_ms, env)
        return self._c.structured(await self._c.call("terminal.run_script_async", payload))
    async def attach(self, process_id: str, *, wait_ms: int = 0, tail_bytes: int = 65_536, stdout_after: int | None = None, stderr_after: int | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"id": process_id, "waitMs": wait_ms, "tailBytes": tail_bytes}
        if stdout_after is not None: args["stdoutAfter"] = stdout_after
        if stderr_after is not None: args["stderrAfter"] = stderr_after
        return self._c.structured(await self._c.call("terminal.attach", args))
    async def stop(self, process_id: str) -> dict[str, Any]: return self._c.structured(await self._c.call("terminal.stop", {"id": process_id}))
    async def wait(self, process_id: str, *, timeout_s: float = 600.0, poll_s: float = 2.0, tail_bytes: int = 200_000) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        snapshot: dict[str, Any] = {}
        while time.monotonic() < deadline:
            snapshot = await self.attach(process_id, wait_ms=int(min(poll_s, 30.0) * 1000), tail_bytes=tail_bytes)
            if snapshot.get("complete"): return snapshot
            await asyncio.sleep(min(poll_s, 5.0))
        raise TimeoutError(f"Process {process_id} did not finish within {timeout_s}s")


class SystemNamespace:
    def __init__(self, c: Conduit): self._c = c
    async def info(self) -> dict[str, Any]: return self._c.structured(await self._c.call("system.info"))
    async def resources(self) -> dict[str, Any]: return self._c.structured(await self._c.call("system.resources"))
    async def dependencies(self) -> dict[str, Any]: return self._c.structured(await self._c.call("system.dependencies"))
    async def health(self) -> dict[str, Any]: return self._c.structured(await self._c.call("runtime.health"))


class Conduit:
    """High-level client optimized for autonomous agent loops."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        workspace: str | None = None,
        auth_path: str | os.PathLike[str] | None = None,
        client_token: str | None = None,
        sessionId: str | None = None,
        timeout: float = 120.0,
    ):
        """Create an in-memory client session bound to an optional workspace."""
        env_defaults = _load_client_env_defaults()
        resolved_auth_path = auth_path or os.getenv("CONDUIT_AUTH_PATH") or env_defaults.get("CONDUIT_AUTH_PATH")
        self.auth = ConduitAuthState(resolved_auth_path)
        self.base_url = (
            base_url
            or os.getenv("CONDUIT_URL")
            or env_defaults.get("CONDUIT_URL")
            or self.auth.load_server_url()
            or "http://localhost:3000"
        ).rstrip("/")
        resolved_token = (
            client_token
            or os.getenv("CONDUIT_CLIENT_TOKEN")
            or os.getenv("CONDUIT_TOKEN")
            or env_defaults.get("CONDUIT_CLIENT_TOKEN")
            or env_defaults.get("CONDUIT_TOKEN")
            or self.auth.load_token()
        )
        supplied_session_id = sessionId or os.getenv("CONDUIT_SESSION_ID")
        self._externally_owned_session = bool(supplied_session_id)
        self._closed = False
        self.workspaceId = workspace
        self.transport = MCPTransport(
            self.base_url,
            timeout=timeout,
            client_token=resolved_token,
            workspace=workspace,
        )
        if supplied_session_id:
            self.transport._session_id = supplied_session_id  # type: ignore
        self._tool_cache: list[dict[str, Any]] | None = None

        # Namespaced API
        self.workspace = WorkspaceNamespace(self)
        self.git = GitNamespace(self)
        self.files = FilesystemNamespace(self)
        self.terminal = TerminalNamespace(self)
        self.system = SystemNamespace(self)

    async def __aenter__(self) -> "Conduit":
        await self.transport.ensure_initialized()
        if self.workspaceId:
            await self.workspace.bind(self.workspaceId)
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        try:
            await self.close()
        except Exception:
            if exc_type is None:
                raise

    @property
    def sessionId(self) -> str | None:
        """Current MCP session id."""
        return self.transport.sessionId

    def sessionEnvironment(self) -> dict[str, str]:
        """Return the explicit environment mapping for a child process."""
        if not self.sessionId:
            raise RuntimeError("Conduit has not initialized a session yet")
        return {"CONDUIT_SESSION_ID": self.sessionId}

    async def close(self, *, terminate_session: bool | None = None) -> None:
        """Close the client."""
        if self._closed:
            return
        should_terminate = terminate_session if terminate_session is not None else not self._externally_owned_session
        try:
            if should_terminate:
                await self.transport.terminate()
            await self.transport.close()
        finally:
            self._closed = True

    async def call(self, name: str, arguments: dict[str, Any] | None = None) -> MCPToolResponse:
        return await self.transport.call(name, arguments or {})  # type: ignore[return-value]

    @staticmethod
    def structured(result: MCPToolResponse) -> dict[str, Any]:
        if result.get("isError"):
            blocks = result.get("content", []) or []
            message = next((
                b.get("text", "")
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "")
            ), "Tool returned an error")
            raise ToolError(message, code=-32000, data=mcp_error_payload(result))
        return result.get("structuredContent", {})

    @staticmethod
    def text(result: MCPToolResponse) -> str:
        blocks = result.get("content", [])
        return "\\n".join(block.get("text", "") for block in blocks if isinstance(block, dict))

    async def toolsList(self) -> list[dict[str, Any]]:
        result = await self.transport.tools_list()
        return result.get("tools", [])

    async def discover(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        if refresh or self._tool_cache is None:
            self._tool_cache = await self.toolsList()
        return self._tool_cache

    async def toolNames(self) -> list[str]:
        return [tool.get("name", "") for tool in await self.discover()]

    async def hasTool(self, name: str) -> bool:
        return name in await self.toolNames()

    @staticmethod
    def _script_payload(
        script: str, interpreter: str, interpreter_args: list[str] | None,
        args: list[str] | None, extension: str | None, cleanup: bool,
        path: str, timeout_ms: int, env: dict[str, str] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "script": script, "interpreter": interpreter, "path": path,
            "timeoutMs": timeout_ms, "cleanup": cleanup,
        }
        if interpreter_args is not None: payload["interpreterArgs"] = interpreter_args
        if args is not None: payload["args"] = args
        if extension is not None:
            normalized_extension = extension.lstrip(".")
            if not normalized_extension:
                raise ValueError("extension must contain a suffix after leading dots")
            if re.fullmatch(r"[A-Za-z0-9_+-]{1,16}", normalized_extension) is None:
                raise ValueError("extension must use 1-16 letters, digits, underscore, plus, or hyphen")
            payload["extension"] = normalized_extension
        if env is not None: payload["env"] = env
        return payload


async def safeEdit(conduit: Conduit, path: str, edits: list[dict[str, Any]], *, base_hash: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    preview = await conduit.files.edit(path, edits, dry_run=True, base_hash=base_hash)
    if not preview.get("mutation", {}).get("changed"):
        raise ToolError(f"Dry run for {path} produced no effective change (edits matched but content is identical)", -32000, preview)
    result = await conduit.files.edit(path, edits, dry_run=False, base_hash=base_hash, idempotency_key=idempotency_key)
    if not result.get("mutation", {}).get("changed") or not result.get("applied"):
        raise ToolError(f"Failed to apply edits to {path}", -32000, result)
    return result


async def safeCommit(conduit: Conduit, message: str, *, files: list[str] | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
    if files is not None:
        await conduit.git.add(files)
    diff = await conduit.git.diffStaged()
    if not diff.get("diff", "").strip():
        raise ToolError("No staged changes to commit", -32000, diff)
    return await conduit.git.commit(message, idempotency_key=idempotency_key)
