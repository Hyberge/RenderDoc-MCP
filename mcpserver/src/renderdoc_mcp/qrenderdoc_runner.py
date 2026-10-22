"""Run RenderDoc replay scripts inside qrenderdoc.exe with a guaranteed exit.

qrenderdoc runs ``--python`` scripts during startup, before ``QApplication.exec()``.
A direct ``app.quit()`` there is a no-op (Qt documents that exit/quit does nothing
when the event loop is not running), so the GUI stays alive forever. Every script
launched through this module is wrapped so that:

* the quit is scheduled on the Qt event loop (``QTimer.singleShot``), and
* the parent process enforces a wall-clock timeout and kills the process tree.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from typing import Optional

DEFAULT_QRENDERDOC = r"C:\Program Files\RenderDoc\qrenderdoc.exe"
DEFAULT_TIMEOUT_S = 300
MAX_TIMEOUT_S = 3600
LOG_TAIL_LINES = 400

_ENV_RESULT = "RENDERDOC_MCP_RESULT"
_ENV_LOG = "RENDERDOC_MCP_LOG"
_ENV_SCRIPT = "RENDERDOC_MCP_SCRIPT"
_ENV_CAPTURE = "RENDERDOC_MCP_CAPTURE"

# Wrapper executed by qrenderdoc --python. It never relies on the user script to
# terminate the GUI, and it always leaves a result file behind.
_BOOTSTRAP = '''\
import json
import builtins
import os
import runpy
import sys
import traceback

_RESULT = os.environ["RENDERDOC_MCP_RESULT"]
_LOG = os.environ["RENDERDOC_MCP_LOG"]
_SCRIPT = os.environ["RENDERDOC_MCP_SCRIPT"]

_log = open(_LOG, "w", encoding="utf-8", buffering=1)
sys.stdout = _log
sys.stderr = _log

sys.path.insert(0, os.path.dirname(_SCRIPT))

from PySide2 import QtCore, QtWidgets

# qrenderdoc injects its capture context into the script globals; runpy uses a fresh
# dict, so re-publish it for user scripts that reference `pyrenderdoc` directly.
_context = globals().get("pyrenderdoc")
if _context is None:
    try:
        import __main__

        _context = getattr(__main__, "pyrenderdoc", None)
    except Exception:
        _context = None
if _context is not None:
    builtins.pyrenderdoc = _context


def _quit() -> None:
    app = QtWidgets.QApplication.instance()
    if app is not None:
        # quit() alone is not enough: qrenderdoc keeps running until its windows are
        # closed. Direct calls are also ignored before exec() starts, so schedule.
        QtCore.QTimer.singleShot(250, _graceful_quit)
        # Fallback: qrenderdoc can sit in teardown with the event loop blocked.
        QtCore.QTimer.singleShot(5000, _hard_quit)


def _graceful_quit() -> None:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    closed = 0
    for widget in app.topLevelWidgets():
        try:
            if widget.close():
                closed += 1
        except Exception:
            traceback.print_exc()
    app.quit()
    print("quit: closed %d windows, app.quit() returned" % closed, file=sys.stdout)


def _hard_quit() -> None:
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(0)


def _write_status(payload: dict) -> None:
    try:
        with open(_RESULT, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except Exception:
        pass


status = {"ok": True}
try:
    runpy.run_path(
        _SCRIPT,
        run_name="__main__",
        init_globals={"pyrenderdoc": _context} if _context is not None else None,
    )
except BaseException:
    status = {"ok": False, "error": traceback.format_exc()}
finally:
    if not os.path.exists(_RESULT):
        _write_status(status)
    sys.stdout.flush()
    sys.stderr.flush()
    _quit()
'''


def _resolve_executable(explicit: Optional[str]) -> str:
    return explicit or os.environ.get("RENDERDOC_QRENDERDOC_PATH") or DEFAULT_QRENDERDOC


def _read_log(path: str) -> tuple[list[str], int]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return [], 0
    except Exception as exc:
        return [f"<failed to read log: {exc}>"], 0
    total = len(lines)
    return lines[-LOG_TAIL_LINES:], total


def _read_result(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None


def _kill_tree(pid: int) -> None:
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        pass


def run_qrenderdoc_script(
    code: Optional[str] = None,
    script_path: Optional[str] = None,
    capture_path: Optional[str] = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_S,
    qrenderdoc_path: Optional[str] = None,
) -> dict:
    """Run a Python script under qrenderdoc.exe and always tear the GUI down.

    Exactly one of ``code`` or ``script_path`` is required. The script runs in
    qrenderdoc's embedded interpreter (so ``import renderdoc`` and ``pyrenderdoc``
    are available); stdout/stderr are captured into the returned log.
    """
    if bool(code) == bool(script_path):
        return {
            "error": "Provide exactly one of 'code' or 'script_path'.",
            "code": "INVALID_ARGUMENT",
        }

    exe = _resolve_executable(qrenderdoc_path)
    if not os.path.isfile(exe):
        return {
            "error": f"qrenderdoc executable not found: {exe}",
            "code": "QRENDERDOC_NOT_FOUND",
            "hint": "Set RENDERDOC_QRENDERDOC_PATH or pass qrenderdoc_path.",
        }

    if script_path is not None and not os.path.isfile(script_path):
        return {
            "error": f"Script not found: {script_path}",
            "code": "SCRIPT_NOT_FOUND",
        }

    timeout_seconds = max(5, min(int(timeout_seconds), MAX_TIMEOUT_S))

    work_dir = os.path.join(tempfile.gettempdir(), f"renderdoc_mcp_script_{uuid.uuid4().hex[:10]}")
    os.makedirs(work_dir, exist_ok=True)

    user_script = os.path.join(work_dir, "user_script.py")
    if code is not None:
        with open(user_script, "w", encoding="utf-8") as f:
            f.write(code)
    else:
        with open(script_path, "r", encoding="utf-8") as src:
            body = src.read()
        with open(user_script, "w", encoding="utf-8") as f:
            f.write(body)

    bootstrap = os.path.join(work_dir, "bootstrap.py")
    with open(bootstrap, "w", encoding="utf-8") as f:
        f.write(_BOOTSTRAP)

    result_file = os.path.join(work_dir, "result.json")
    log_file = os.path.join(work_dir, "script.log")

    env = dict(os.environ)
    env[_ENV_RESULT] = result_file
    env[_ENV_LOG] = log_file
    env[_ENV_SCRIPT] = user_script
    if capture_path:
        env[_ENV_CAPTURE] = capture_path

    started = time.monotonic()
    proc = subprocess.Popen(
        [exe, "--python", bootstrap],
        cwd=work_dir,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    timed_out = False
    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass

    duration = round(time.monotonic() - started, 2)
    log_tail, log_lines = _read_log(log_file)
    result = _read_result(result_file)

    if timed_out:
        status = "timeout"
    elif isinstance(result, dict) and result.get("ok") is False:
        status = "script_error"
    elif proc.returncode == 0:
        status = "ok"
    else:
        status = "error"

    payload = {
        "status": status,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "duration_seconds": duration,
        "qrenderdoc": exe,
        "work_dir": work_dir,
        "log_lines": log_lines,
        "log_tail": log_tail,
        "result": result,
    }
    if timed_out:
        payload["error"] = (
            f"Script exceeded {timeout_seconds}s and the qrenderdoc process tree was killed. "
            "Raise timeout_seconds for long replays, or narrow the work in the script."
        )
    return payload
