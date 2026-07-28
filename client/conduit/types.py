"""Typing helpers for Conduit Local client users."""

from __future__ import annotations

import sys
from typing import Any, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    try:
        from typing_extensions import NotRequired
    except ImportError:  # pragma: no cover – best-effort for 3.10 without extras
        from typing import Optional as _Opt
        NotRequired = _Opt  # type: ignore[assignment,misc]


class ContentBlock(TypedDict, total=False):
    type: str
    text: str


class MCPToolResponse(TypedDict, total=False):
    content: list[ContentBlock]
    structuredContent: dict[str, Any]
    isError: bool
    _meta: dict[str, Any]


class SearchResult(TypedDict):
    id: str
    title: str
    url: str


class TerminalExecResult(TypedDict):
    runner: str
    cwd: str
    executable: str
    args: list[str]
    displayCommand: str
    exitCode: int | None
    stdout: str
    stderr: str
    timedOut: bool
    durationMs: int | float
