"""qrenderdoc script execution tool: run scripts in qrenderdoc's embedded interpreter."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp.qrenderdoc_runner import DEFAULT_TIMEOUT_S, run_qrenderdoc_script as _run
from renderdoc_mcp.util import to_json


def register(mcp: FastMCP):
    @mcp.tool()
    def run_qrenderdoc_script(
        code: Optional[str] = None,
        script_path: Optional[str] = None,
        capture_path: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_S,
        qrenderdoc_path: Optional[str] = None,
    ) -> str:
        """Run a Python script inside qrenderdoc.exe with a guaranteed process exit.

        Use this only when the script needs qrenderdoc's GUI-side API (`pyrenderdoc`,
        `PySide2`, extensions). For plain capture inspection prefer `open_capture`
        and the regular tools — they run headless in this server.

        The wrapper handles the known startup-quit trap: a `--python` script runs
        before Qt's event loop starts, so `app.quit()` there is silently ignored and
        the GUI never exits. Quit is queued on the event loop instead, and the
        process tree is killed if the script exceeds `timeout_seconds`.

        Args:
            code: Script source to execute. Mutually exclusive with `script_path`.
            script_path: Path to an existing .py file to execute.
            capture_path: Optional .rdc path, exposed to the script as the
                RENDERDOC_MCP_CAPTURE environment variable.
            timeout_seconds: Wall-clock limit; the qrenderdoc process tree is
                killed on expiry. Defaults to 300.
            qrenderdoc_path: Override the qrenderdoc executable; otherwise
                RENDERDOC_QRENDERDOC_PATH or the standard install path is used.

        Returns:
            JSON with status/exit_code/duration, the captured stdout+stderr tail
            (`log_tail`), and `result` — the JSON the script wrote itself, or the
            wrapper's error report when the script raised.
        """
        return to_json(_run(
            code=code,
            script_path=script_path,
            capture_path=capture_path,
            timeout_seconds=timeout_seconds,
            qrenderdoc_path=qrenderdoc_path,
        ))
