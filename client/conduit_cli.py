#!/usr/bin/env python3
"""Minimal JSON bridge for Conduit Local's native async SDK.

The supported agent interface is ``async with Conduit()``. This command is a
one-shot diagnostic escape hatch: it opens one in-memory session, performs 
one request, emits compact structured JSON, and ends the session.
It intentionally has no aliases, assertion language, or local result files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from client.conduit.cli_doctor import doctor_result
from client.conduit.errors import ConduitError
from client.conduit.sdk import Conduit
from client.conduit.utils import compact_json, extract_dot_value, merge_params


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-shot JSON bridge for Conduit Local")
    parser.add_argument("--url", help="Conduit base URL or /mcp URL (default: CONDUIT_URL)")
    parser.add_argument("--workspace", help="Bind this invocation's session before its requested tool call")
    parser.add_argument("--tool", help="Canonical MCP tool name")
    parser.add_argument("--discover", action="store_true", help="Return the server tool list")
    parser.add_argument("--describe-tool", metavar="NAME", help="Return one tool's schema from the server tool list")
    parser.add_argument("--args", help="JSON object, or - to read one from stdin")
    parser.add_argument("--args-file", help="Path to a JSON object containing tool arguments")
    parser.add_argument("--param", action="append", default=[], help="Overlay one key=value tool argument; value is JSON-decoded when possible")
    parser.add_argument("--dry-run", action="store_true", help="Set dryRun=true in the requested tool arguments")
    parser.add_argument("--idempotency-key", help="Set idempotencyKey in the requested tool arguments")
    parser.add_argument("--extract", help="Project a dot path from structured JSON; supports indexes, *, length, and len")
    parser.add_argument("command", nargs="?", choices=["doctor"], help="Session-free readiness JSON")
    return parser.parse_args()


def load_arguments(args: argparse.Namespace) -> dict[str, Any]:
    if args.args and args.args_file:
        raise ValueError("--args and --args-file are mutually exclusive")
    raw: str | None = None
    if args.args == "-":
        raw = sys.stdin.read()
    elif args.args:
        raw = args.args
    elif args.args_file:
        with open(args.args_file, encoding="utf-8") as source:
            raw = source.read()
    payload: dict[str, Any] = {}
    if raw:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("tool arguments must be a JSON object")
        payload = decoded
    payload = merge_params(payload, args.param)
    if args.dry_run:
        payload["dryRun"] = True
    if args.idempotency_key and "idempotencyKey" not in payload:
        payload["idempotencyKey"] = args.idempotency_key
    return payload


def describe_tool(tools: list[dict[str, Any]], name: str) -> dict[str, Any]:
    for tool in tools:
        if tool.get("name") == name:
            return tool
    raise ValueError(f"tool not found: {name}")


def exit_code(result: Any) -> int:
    if not isinstance(result, dict) or result.get("exitCode") is None:
        return 0
    try:
        return int(result["exitCode"])
    except (TypeError, ValueError):
        return 0


def emit(value: Any) -> None:
    print(compact_json(value))


async def main() -> int:
    args = parse_args()
    if args.command == "doctor":
        url = args.url or os.getenv("CONDUIT_URL")
        result = await doctor_result(url)
        if args.extract:
            result = extract_dot_value(result, args.extract)
        emit(result)
        return 0 if result["ready"] else 1
    if sum(bool(value) for value in (args.tool, args.discover, args.describe_tool)) != 1:
        emit({"error": {"type": "UsageError", "message": "provide exactly one of --tool, --discover, or --describe-tool"}})
        return 2
    try:
        async with Conduit(args.url, workspace=args.workspace) as conduit:
            if args.discover:
                result: Any = await conduit.toolsList()
            elif args.describe_tool:
                result = describe_tool(await conduit.toolsList(), args.describe_tool)
            else:
                result = Conduit.structured(await conduit.call(args.tool, load_arguments(args)))
        if args.extract:
            result = extract_dot_value(result, args.extract)
        emit(result)
        return exit_code(result)
    except (ConduitError, ValueError, json.JSONDecodeError, OSError) as exc:
        emit({"error": {"type": type(exc).__name__, "message": str(exc)}})
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
