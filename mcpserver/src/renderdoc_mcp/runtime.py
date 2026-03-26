"""Runtime guards: tool timeout handling, large-result caching, and cached message queries."""

from __future__ import annotations

import atexit
import contextvars
import functools
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable, Optional

DEFAULT_TOOL_TIMEOUT_MS = max(1000, int(os.environ.get("RENDERDOC_MCP_TOOL_TIMEOUT_MS", "10000")))
MAX_DIRECT_RESPONSE_CHARS = max(2000, int(os.environ.get("RENDERDOC_MCP_MAX_RESPONSE_CHARS", "4096")))
CACHE_MAX_ENTRIES = max(10, int(os.environ.get("RENDERDOC_MCP_CACHE_MAX_ENTRIES", "128")))
CACHE_DEFAULT_PAGE_SIZE = max(1, int(os.environ.get("RENDERDOC_MCP_CACHE_DEFAULT_PAGE_SIZE", "50")))
CACHE_MAX_PAGE_SIZE = max(CACHE_DEFAULT_PAGE_SIZE, int(os.environ.get("RENDERDOC_MCP_CACHE_MAX_PAGE_SIZE", "200")))

# Per-tool timeout overrides (tool_name -> timeout_ms)
_TOOL_TIMEOUT_OVERRIDES: dict[str, int] = {
    "open_capture": 60000,  # 60s — large captures with shader debug path scanning take longer
}


CACHE_PREVIEW_ITEMS = max(1, int(os.environ.get("RENDERDOC_MCP_CACHE_PREVIEW_ITEMS", "3")))
CACHE_PREVIEW_LINES = max(1, int(os.environ.get("RENDERDOC_MCP_CACHE_PREVIEW_LINES", "12")))

_PRIMARY_COLLECTION_KEYS = [
    "actions",
    "matches",
    "api_calls",
    "textures",
    "buffers",
    "resources",
    "usages",
    "messages",
    "accesses",
    "variables",
    "trace",
    "hotspots",
    "passes",
    "items",
    "vertices",
    "lines",
]

_current_tool_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "renderdoc_mcp_current_tool_name",
    default="unknown_tool",
)


class MessageCache:
    """In-memory cache for oversized tool results."""

    def __init__(self, max_entries: int = CACHE_MAX_ENTRIES):
        self.max_entries = max_entries
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, tool_name: str, payload: Any) -> dict[str, Any]:
        serialized = _json_dumps(payload)
        entry = {
            "message_id": f"msg_{uuid.uuid4().hex[:12]}",
            "tool_name": tool_name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "char_count": len(serialized),
            "payload": payload,
            "views": _describe_views(payload),
        }
        with self._lock:
            self._entries[entry["message_id"]] = entry
            self._entries.move_to_end(entry["message_id"])
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return entry

    def get(self, message_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            entry = self._entries.get(message_id)
            if entry is None:
                return None
            self._entries.move_to_end(message_id)
            return entry

    def clear(self):
        with self._lock:
            self._entries.clear()


_message_cache = MessageCache()
_tool_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="renderdoc-mcp-tool")
_tool_state_lock = threading.Lock()
_active_future: Future | None = None
_active_tool_name: str | None = None
_active_started_at: float | None = None


@atexit.register
def _shutdown_runtime_executor():
    _tool_executor.shutdown(wait=False, cancel_futures=False)


def install_tool_runtime_guards(mcp) -> None:
    """Monkey-patch FastMCP.add_tool so every tool gets timeout / cache guards."""
    if getattr(mcp, "_renderdoc_runtime_installed", False):
        return

    original_add_tool = mcp.add_tool

    @functools.wraps(original_add_tool)
    def guarded_add_tool(fn: Callable[..., Any], *args, **kwargs):
        wrapped = wrap_tool_function(fn, kwargs.get("name") or getattr(fn, "__name__", "tool"))
        return original_add_tool(wrapped, *args, **kwargs)

    mcp.add_tool = guarded_add_tool
    mcp._renderdoc_runtime_installed = True


def wrap_tool_function(fn: Callable[..., Any], tool_name: str) -> Callable[..., Any]:
    """Wrap a tool with single-thread execution and timeout handling."""

    tool_timeout_ms = _TOOL_TIMEOUT_OVERRIDES.get(tool_name, DEFAULT_TOOL_TIMEOUT_MS)

    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        global _active_future, _active_tool_name, _active_started_at

        with _tool_state_lock:
            if _active_future is not None and not _active_future.done():
                elapsed = time.monotonic() - (_active_started_at or time.monotonic())
                return _json_dumps({
                    "error": (
                        f"Another tool is still running: {_active_tool_name}. "
                        f"Wait for it to finish or restart the MCP server."
                    ),
                    "code": "TOOL_BUSY",
                    "running_tool": _active_tool_name,
                    "running_for_seconds": round(elapsed, 3),
                })

            future = _tool_executor.submit(_run_tool_call, fn, tool_name, args, kwargs)
            _active_future = future
            _active_tool_name = tool_name
            _active_started_at = time.monotonic()
            future.add_done_callback(_clear_active_future)

        try:
            return future.result(timeout=tool_timeout_ms / 1000.0)
        except FutureTimeoutError:
            elapsed = time.monotonic() - (_active_started_at or time.monotonic())
            return _json_dumps({
                "error": (
                    f"Tool '{tool_name}' timed out after {tool_timeout_ms}ms. "
                    "The tool is still finishing in the background; wait or restart the MCP server if it remains stuck."
                ),
                "code": "TOOL_TIMEOUT",
                "tool_name": tool_name,
                "timeout_ms": tool_timeout_ms,
                "elapsed_ms": int(elapsed * 1000),
            })
        except Exception as exc:
            return _json_dumps({
                "error": f"Tool '{tool_name}' failed: {type(exc).__name__}: {exc}",
                "code": "TOOL_EXECUTION_ERROR",
                "tool_name": tool_name,
            })

    return wrapped


def _run_tool_call(fn: Callable[..., Any], tool_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    token = _current_tool_name.set(tool_name)
    try:
        return fn(*args, **kwargs)
    finally:
        _current_tool_name.reset(token)


def _clear_active_future(future: Future):
    global _active_future, _active_tool_name, _active_started_at
    with _tool_state_lock:
        if future is _active_future:
            _active_future = None
            _active_tool_name = None
            _active_started_at = None


def prepare_tool_result(obj: Any, *, allow_cache: bool = True) -> str:
    """Serialize a tool result, caching oversized payloads automatically."""
    serialized = _json_dumps(obj)
    if len(serialized) <= MAX_DIRECT_RESPONSE_CHARS:
        return serialized

    if not allow_cache:
        preview = _build_preview(obj)
        return _json_dumps({
            "error": (
                "Result is too large for direct output. Reduce page_size or narrow the query."
            ),
            "code": "RESULT_TOO_LARGE",
            "max_direct_chars": MAX_DIRECT_RESPONSE_CHARS,
            "preview": preview,
        })

    entry = _message_cache.put(_current_tool_name.get(), obj)
    envelope = {
        "status": "cached",
        "message": "Result too large for direct output. Full content has been cached on the MCP server.",
        "message_id": entry["message_id"],
        "tool_name": entry["tool_name"],
        "approx_chars": entry["char_count"],
        "max_direct_chars": MAX_DIRECT_RESPONSE_CHARS,
        "available_views": entry["views"],
        "preview": _build_preview(obj),
        "next_step": {
            "tool": "get_cached_message",
            "arguments": {
                "message_id": entry["message_id"],
                "page": 1,
                "page_size": CACHE_DEFAULT_PAGE_SIZE,
            },
        },
    }
    return _json_dumps(envelope)


def query_cached_message(
    message_id: str,
    page: int = 1,
    page_size: int = CACHE_DEFAULT_PAGE_SIZE,
    search: Optional[str] = None,
    collection: Optional[str] = None,
    context_lines: int = 2,
) -> dict[str, Any]:
    """Query a cached oversized message by id with paging or search."""
    entry = _message_cache.get(message_id)
    if entry is None:
        return {
            "error": f"Cached message '{message_id}' not found",
            "code": "INVALID_MESSAGE_ID",
        }

    page = max(1, page)
    page_size = max(1, min(page_size, CACHE_MAX_PAGE_SIZE))
    context_lines = max(0, min(context_lines, 10))
    payload = entry["payload"]

    selected_key, selected_type, selected_value = _select_view(payload, collection)
    result: dict[str, Any] = {
        "message_id": message_id,
        "tool_name": entry["tool_name"],
        "created_at": entry["created_at"],
        "approx_chars": entry["char_count"],
        "available_views": entry["views"],
        "selected_view": {
            "key": selected_key,
            "type": selected_type,
        },
    }

    if selected_type == "list":
        items = list(selected_value)
        if search:
            needle = search.lower()
            filtered = [item for item in items if needle in _search_blob(item)]
            result["search"] = {"query": search, "matches": len(filtered)}
            items = filtered
        slice_items, pagination = _paginate_sequence(items, page, page_size)
        result["items"] = slice_items
        result["pagination"] = pagination
        return result

    if selected_type == "lines":
        lines = list(selected_value)
        if search:
            matched_blocks = _search_lines(lines, search, context_lines)
            slice_items, pagination = _paginate_sequence(matched_blocks, page, page_size)
            result["search"] = {"query": search, "matches": len(matched_blocks), "context_lines": context_lines}
            result["matches"] = slice_items
            result["pagination"] = pagination
            return result

        numbered_lines = [
            {"line_number": idx + 1, "text": line}
            for idx, line in enumerate(lines)
        ]
        slice_items, pagination = _paginate_sequence(numbered_lines, page, page_size)
        result["lines"] = slice_items
        result["pagination"] = pagination
        return result

    pretty_lines = _pretty_json_lines(payload)
    if search:
        matched_blocks = _search_lines(pretty_lines, search, context_lines)
        slice_items, pagination = _paginate_sequence(matched_blocks, page, page_size)
        result["search"] = {"query": search, "matches": len(matched_blocks), "context_lines": context_lines}
        result["matches"] = slice_items
        result["pagination"] = pagination
        return result

    numbered_lines = [
        {"line_number": idx + 1, "text": line}
        for idx, line in enumerate(pretty_lines)
    ]
    slice_items, pagination = _paginate_sequence(numbered_lines, page, page_size)
    result["lines"] = slice_items
    result["pagination"] = pagination
    return result


def _select_view(payload: Any, collection: Optional[str]) -> tuple[str, str, Any]:
    views = _describe_views(payload)
    if collection:
        for view in views:
            if view["key"] == collection:
                return _materialize_view(payload, view["key"], view["type"])
        return "$pretty_json", "lines", _pretty_json_lines(payload)

    if payload is None:
        return "$root", "value", payload

    if isinstance(payload, list):
        return "$root", "list", payload

    if isinstance(payload, str):
        return "$text", "lines", payload.splitlines() if payload.splitlines() else [payload]

    if isinstance(payload, dict):
        for key in _PRIMARY_COLLECTION_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return key, "list", value
        list_candidates = [(k, v) for k, v in payload.items() if isinstance(v, list)]
        if list_candidates:
            key, value = max(list_candidates, key=lambda item: len(item[1]))
            return key, "list", value
        text_candidates = [(k, v) for k, v in payload.items() if isinstance(v, str) and "\n" in v]
        if text_candidates:
            key, value = max(text_candidates, key=lambda item: len(item[1]))
            return key, "lines", value.splitlines()

    return "$pretty_json", "lines", _pretty_json_lines(payload)


def _materialize_view(payload: Any, key: str, view_type: str) -> tuple[str, str, Any]:
    if key == "$root":
        return key, view_type, payload
    if key == "$text":
        text = payload if isinstance(payload, str) else _json_dumps(payload)
        return key, "lines", text.splitlines() if text.splitlines() else [text]
    if key == "$pretty_json":
        return key, "lines", _pretty_json_lines(payload)
    if isinstance(payload, dict):
        value = payload.get(key)
        if view_type == "lines" and isinstance(value, str):
            return key, "lines", value.splitlines()
        return key, view_type, value
    return "$pretty_json", "lines", _pretty_json_lines(payload)


def _describe_views(payload: Any) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    if isinstance(payload, list):
        views.append({"key": "$root", "type": "list", "count": len(payload)})
        return views
    if isinstance(payload, str):
        lines = payload.splitlines()
        views.append({"key": "$text", "type": "lines", "count": len(lines) if lines else 1})
        return views
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, list):
                views.append({"key": key, "type": "list", "count": len(value)})
            elif isinstance(value, str) and "\n" in value:
                views.append({"key": key, "type": "lines", "count": len(value.splitlines())})
    if not views:
        views.append({"key": "$pretty_json", "type": "lines", "count": len(_pretty_json_lines(payload))})
    return views


def _build_preview(payload: Any) -> Any:
    key, view_type, view_value = _select_view(payload, None)
    if view_type == "list":
        preview_items = list(view_value[:CACHE_PREVIEW_ITEMS])
        return {
            "selected_view": {"key": key, "type": view_type},
            "items": preview_items,
        }
    if view_type == "lines":
        lines = list(view_value[:CACHE_PREVIEW_LINES])
        return {
            "selected_view": {"key": key, "type": view_type},
            "lines": [
                {"line_number": idx + 1, "text": line}
                for idx, line in enumerate(lines)
            ],
        }
    return payload


def _paginate_sequence(items: list[Any], page: int, page_size: int) -> tuple[list[Any], dict[str, Any]]:
    total_items = len(items)
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size
    pagination = {
        "page": page,
        "page_size": page_size,
        "total_items": total_items,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "prev_page": page - 1 if page > 1 else None,
        "next_page": page + 1 if page < total_pages else None,
    }
    return items[start:end], pagination


def _search_blob(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    try:
        return _json_dumps(value).lower()
    except Exception:
        return str(value).lower()


def _search_lines(lines: list[str], search: str, context_lines: int) -> list[dict[str, Any]]:
    needle = search.lower()
    matches: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if needle not in line.lower():
            continue
        start = max(0, idx - context_lines)
        end = min(len(lines), idx + context_lines + 1)
        matches.append({
            "match_line": idx + 1,
            "range": [start + 1, end],
            "lines": [
                {"line_number": line_idx + 1, "text": lines[line_idx]}
                for line_idx in range(start, end)
            ],
        })
    return matches


def _pretty_json_lines(payload: Any) -> list[str]:
    return json.dumps(payload, ensure_ascii=False, indent=2).splitlines()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
