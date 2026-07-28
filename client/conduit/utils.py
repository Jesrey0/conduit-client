"""Small utilities shared by the CLI and SDK."""

from __future__ import annotations

import json
from typing import Any, Iterable


def extract_dot_value(data: Any, path: str, default: Any = None) -> Any:
    """Extract a nested value from dictionaries/lists using dot notation.

    Supports list indexes, ``length``/``len``, and ``*`` wildcards. This is a
    projection helper only: validation belongs in the calling agent runtime.
    """
    missing = object()

    def descend(value: Any, segments: list[str]) -> Any:
        if not segments:
            return value
        segment, rest = segments[0], segments[1:]
        if segment in {"length", "len"}:
            try:
                return descend(len(value), rest)
            except TypeError:
                return missing
        if segment == "*":
            if not isinstance(value, list):
                return missing
            values = [descend(item, rest) for item in value]
            return missing if any(item is missing for item in values) else values
        if isinstance(value, dict):
            return descend(value[segment], rest) if segment in value else missing
        if isinstance(value, list) and segment.isdigit():
            index = int(segment)
            return descend(value[index], rest) if 0 <= index < len(value) else missing
        return missing

    result = descend(data, [segment for segment in path.split(".") if segment])
    return default if result is missing else result


def parse_param_value(value: str) -> Any:
    """Parse a CLI parameter value with JSON semantics when possible."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def merge_params(base: dict[str, Any], params: Iterable[str] | None) -> dict[str, Any]:
    """Overlay repeated key=value CLI params onto a JSON argument object."""
    merged = dict(base)
    for item in params or []:
        if "=" not in item:
            raise ValueError(f"--param must be key=value, got: {item}")
        key, value = item.split("=", 1)
        if not key:
            raise ValueError("--param key cannot be empty")
        merged[key] = parse_param_value(value)
    return merged


def mcp_error_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Read Conduit's canonical machine-readable tool-error metadata."""
    meta = result.get("_meta")
    if not isinstance(meta, dict):
        return {}
    detail = meta.get("io.github.jesrey0.conduit/error")
    return detail if isinstance(detail, dict) and detail else {}


def compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
