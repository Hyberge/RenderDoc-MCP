"""RenderDoc session manager - multi-capture registry with active session switching."""

from __future__ import annotations

import os

from renderdoc_mcp.util import rd, make_error


class RenderDocSession:
    """Manages a single RenderDoc capture file's lifecycle."""

    def __init__(self):
        self._cap = None  # CaptureFile
        self._controller = None  # ReplayController
        self._filepath: str | None = None
        self._current_event: int | None = None
        self._action_map: dict[int, object] = {}
        self._event_map: dict[int, int] = {}  # eventId → chunkIndex (all API events)
        self._structured_file = None
        self._resource_id_cache: dict[str, object] = {}  # str(resourceId) → resourceId
        self._texture_desc_cache: dict[str, object] = {}  # str(resourceId) → TextureDescription

    @property
    def is_open(self) -> bool:
        return self._controller is not None

    @property
    def filepath(self) -> str | None:
        return self._filepath

    @property
    def controller(self):
        return self._controller

    @property
    def structured_file(self):
        return self._structured_file

    @property
    def current_event(self) -> int | None:
        return self._current_event

    @property
    def driver_name(self) -> str:
        """Get the API/driver name of the current capture."""
        return self._cap.DriverName() if self._cap else "unknown"

    @property
    def action_map(self) -> dict[int, object]:
        """Get the event_id -> ActionDescription mapping."""
        return self._action_map

    @property
    def event_map(self) -> dict[int, int]:
        """Get the event_id -> chunkIndex mapping for all API events."""
        return self._event_map

    def require_open(self) -> dict | None:
        """Return an error dict if no capture is open, else None."""
        if not self.is_open:
            return make_error("No capture file is open. Use open_capture first.", "NO_CAPTURE_OPEN")
        return None

    def open(self, filepath: str, shader_debug_paths: list[str] | None = None) -> dict:
        """Open a .rdc capture file. Caller must ensure replay is initialized.

        Args:
            filepath: Absolute path to the .rdc file.
            shader_debug_paths: Optional list of directories to search for
                separate shader debug info (PDB files). Must be set before
                opening the capture.
        """
        if not os.path.isfile(filepath):
            return make_error(f"File not found: {filepath}", "API_ERROR")

        # Set shader debug info search paths before opening capture.
        if shader_debug_paths:
            self._set_shader_debug_paths(shader_debug_paths)

        cap = rd.OpenCaptureFile()
        result = cap.OpenFile(filepath, "", None)
        if result != rd.ResultCode.Succeeded:
            cap.Shutdown()
            return make_error(f"Failed to open file: {result}", "API_ERROR")

        if not cap.LocalReplaySupport():
            cap.Shutdown()
            return make_error("Capture cannot be replayed on this machine", "API_ERROR")

        result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
        if result != rd.ResultCode.Succeeded:
            cap.Shutdown()
            return make_error(f"Failed to initialize replay: {result}", "API_ERROR")

        self._cap = cap
        self._controller = controller
        self._filepath = filepath
        self._current_event = None
        self._structured_file = controller.GetStructuredFile()

        # Build action map
        self._action_map = {}
        self._build_action_map(controller.GetRootActions())

        # Build event map (eid → chunkIndex for all API events)
        self._event_map = {}
        self._build_event_map(controller.GetRootActions())

        # Build resource caches
        self._resource_id_cache = {}
        self._texture_desc_cache = {}
        textures = controller.GetTextures()
        buffers = controller.GetBuffers()
        for tex in textures:
            key = str(tex.resourceId)
            self._resource_id_cache[key] = tex.resourceId
            self._texture_desc_cache[key] = tex
        for buf in buffers:
            key = str(buf.resourceId)
            self._resource_id_cache[key] = buf.resourceId
        for res in controller.GetResources():
            key = str(res.resourceId)
            if key not in self._resource_id_cache:
                self._resource_id_cache[key] = res.resourceId

        # Gather summary
        root_actions = controller.GetRootActions()

        result_info = {
            "filepath": filepath,
            "api": cap.DriverName(),
            "total_actions": len(self._action_map),
            "root_actions": len(root_actions),
            "textures": len(textures),
            "buffers": len(buffers),
        }
        if shader_debug_paths:
            result_info["shader_debug_paths"] = shader_debug_paths

        return result_info

    def _build_action_map(self, actions):
        """Recursively index all actions by event_id."""
        for a in actions:
            self._action_map[a.eventId] = a
            if len(a.children) > 0:
                self._build_action_map(a.children)

    def _build_event_map(self, actions):
        """Recursively build eid → chunkIndex mapping from all action events."""
        NO_CHUNK = 0xFFFFFFFF  # APIEvent.NoChunk
        for a in actions:
            for evt in a.events:
                if evt.chunkIndex != NO_CHUNK:
                    self._event_map[evt.eventId] = evt.chunkIndex
            if len(a.children) > 0:
                self._build_event_map(a.children)

    def _set_shader_debug_paths(self, paths: list[str]):
        """Configure shader debug info search paths via RenderDoc config system."""
        try:
            paths_obj = rd.SetConfigSetting("DXBC.Debug.SearchDirPaths")
            if paths_obj is not None:
                paths_obj.DeleteChildren()
                for p in paths:
                    child = rd.makeSDString("$el", p)
                    paths_obj.AddChild(child)
        except Exception:
            pass

    def resolve_resource_id(self, resource_id_str: str):
        """Resolve a resource ID string to a ResourceId object, or None."""
        return self._resource_id_cache.get(resource_id_str)

    def get_texture_desc(self, resource_id_str: str):
        """Get a TextureDescription by resource ID string, or None."""
        return self._texture_desc_cache.get(resource_id_str)

    def close(self) -> dict:
        """Close the current capture."""
        if not self.is_open:
            return {"status": "no capture was open"}

        filepath = self._filepath
        self._controller.Shutdown()
        self._cap.Shutdown()
        self._controller = None
        self._cap = None
        self._filepath = None
        self._current_event = None
        self._action_map = {}
        self._event_map = {}
        self._structured_file = None
        self._resource_id_cache = {}
        self._texture_desc_cache = {}
        return {"status": "closed", "filepath": filepath}

    def set_event(self, event_id: int) -> dict | None:
        """Navigate to a specific event. Returns error dict or None on success."""
        if event_id not in self._action_map:
            return make_error(f"Event ID {event_id} not found", "INVALID_EVENT_ID")
        self._controller.SetFrameEvent(event_id, True)
        self._current_event = event_id
        return None

    def ensure_event(self, event_id: int | None) -> dict | None:
        """If event_id is given, set it. If no event is current, return error."""
        if event_id is not None:
            return self.set_event(event_id)
        if self._current_event is None:
            return make_error("No event selected. Use set_event or pass event_id.", "INVALID_EVENT_ID")
        return None

    def get_action(self, event_id: int):
        """Get an ActionDescription by event_id, or None."""
        return self._action_map.get(event_id)

    def get_root_actions(self):
        return self._controller.GetRootActions()


# ── Module-level multi-session registry ──

_sessions: dict[str, RenderDocSession] = {}
_active_session_id: str | None = None
_replay_initialized: bool = False


def _ensure_initialized():
    """Initialize the RenderDoc replay system once globally."""
    global _replay_initialized
    if not _replay_initialized:
        rd.InitialiseReplay(rd.GlobalEnvironment(), [])
        _replay_initialized = True


def get_session() -> RenderDocSession | None:
    """Return the currently active RenderDocSession, or None if none is active."""
    if _active_session_id is None:
        return None
    return _sessions.get(_active_session_id)


def open_session(filepath: str, shader_debug_paths: list[str] | None = None) -> dict:
    """Open a capture file as a new session and activate it.

    If the filepath is already open, the existing session is activated and
    its info returned (no re-open). Otherwise a new session is created.

    Args:
        filepath: Absolute path to the .rdc file.
        shader_debug_paths: Optional shader PDB search directories.

    Returns:
        dict with capture info on success, or error dict on failure.
    """
    global _active_session_id

    filepath = os.path.normpath(filepath)
    _ensure_initialized()

    # Idempotent: already open → just activate and return info
    if filepath in _sessions:
        _active_session_id = filepath
        sess = _sessions[filepath]
        return {
            "filepath": filepath,
            "api": sess.driver_name,
            "total_actions": len(sess.action_map),
            "textures": len(sess.controller.GetTextures()),
            "buffers": len(sess.controller.GetBuffers()),
            "status": "already_open_activated",
        }

    sess = RenderDocSession()
    result = sess.open(filepath, shader_debug_paths=shader_debug_paths)

    if "error" in result:
        return result

    _sessions[filepath] = sess
    _active_session_id = filepath
    result["active_captures"] = len(_sessions)
    return result


def switch_session(filepath: str) -> dict:
    """Switch the active session to the given filepath.

    Args:
        filepath: Path of the already-opened capture to activate.

    Returns:
        dict with status info, or error dict if not found.
    """
    global _active_session_id

    filepath = os.path.normpath(filepath)
    if filepath not in _sessions:
        keys = list(_sessions.keys())
        return make_error(
            f"Capture '{filepath}' is not open. Open captures: {keys}",
            "SESSION_NOT_FOUND",
        )
    _active_session_id = filepath
    sess = _sessions[filepath]
    return {
        "status": "switched",
        "active_filepath": filepath,
        "api": sess.driver_name,
        "total_actions": len(sess.action_map),
        "current_event": sess.current_event,
    }


def list_sessions() -> list[dict]:
    """Return info about all open capture sessions."""
    result = []
    for fp, sess in _sessions.items():
        result.append({
            "filepath": fp,
            "api": sess.driver_name,
            "total_actions": len(sess.action_map),
            "current_event": sess.current_event,
            "is_active": fp == _active_session_id,
        })
    return result


def close_session(filepath: str | None = None) -> dict:
    """Close a specific capture session (or the active one if filepath is None).

    After closing, the active session is switched to the most-recently-opened
    remaining session, if any.

    Args:
        filepath: Path of the capture to close, or None to close the active one.

    Returns:
        dict with status info.
    """
    global _active_session_id

    target = os.path.normpath(filepath) if filepath else _active_session_id
    if target is None:
        return {"status": "no capture was open"}
    if target not in _sessions:
        return make_error(f"Capture '{target}' is not open.", "SESSION_NOT_FOUND")

    sess = _sessions.pop(target)
    result = sess.close()

    # Update active pointer
    if _active_session_id == target:
        _active_session_id = next(reversed(_sessions), None)

    result["remaining_captures"] = len(_sessions)
    result["active_filepath"] = _active_session_id
    return result


def close_all_sessions() -> dict:
    """Close all open capture sessions.

    Does NOT call ShutdownReplay — that is reserved for shutdown_all().
    """
    global _active_session_id

    closed = []
    errors = []
    for fp, sess in list(_sessions.items()):
        try:
            sess.close()
            closed.append(fp)
        except Exception as exc:
            errors.append({"filepath": fp, "error": str(exc)})
    _sessions.clear()
    _active_session_id = None

    result: dict = {"status": "all_closed", "closed": closed}
    if errors:
        result["errors"] = errors
    return result


def shutdown_all() -> None:
    """Close all sessions and deinitialize the replay system. Call only at process exit."""
    global _replay_initialized

    close_all_sessions()
    if _replay_initialized:
        rd.ShutdownReplay()
        _replay_initialized = False
