"""Session management tools: open_capture, close_capture, list_captures, switch_capture, get_capture_info, get_frame_overview."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp.session import (
    get_session,
    open_session,
    close_session,
    switch_session,
    list_sessions,
)
from renderdoc_mcp.util import rd, to_json, make_error, flags_to_list
from renderdoc_mcp.runtime import IDLE_TIMEOUT_MINUTES


def _get_gpu_quirks(driver_name: str) -> list[str]:
    """Return known GPU/API quirks based on the driver name string."""
    quirks: list[str] = []
    name_upper = driver_name.upper()

    if "ADRENO" in name_upper:
        quirks.extend([
            "Adreno: mediump float 实际精度可能低于 spec 要求，关键计算建议强制 highp",
            "Adreno: R11G11B10_FLOAT 没有符号位，写入负值会被 clamp 到 0 或产生未定义行为",
            "Adreno: 某些驱动版本 textureLod 在 fragment shader 中存在 bug",
        ])
    elif "MALI" in name_upper:
        quirks.extend([
            "Mali: R11G11B10_FLOAT 存储负值行为未定义",
            "Mali: 大量 discard 指令会导致 early-Z 失效，影响 tile-based 性能",
            "Mali: 某些型号 mediump 向量运算累积精度问题",
        ])
    elif "POWERVR" in name_upper or "IMAGINATION" in name_upper:
        quirks.extend([
            "PowerVR: Tile-based deferred rendering，避免不必要的 framebuffer load/store",
            "PowerVR: 避免大量纹理采样器绑定",
        ])
    elif "APPLE" in name_upper:
        quirks.extend([
            "Apple GPU: High-efficiency TBDR，注意 tile memory 带宽",
            "Apple: float16 在 Metal 上性能优秀，建议用于后处理",
        ])

    if "OPENGL ES" in name_upper or "GLES" in name_upper:
        quirks.append("OpenGL ES: 注意精度限定符 (highp/mediump/lowp) 的正确使用")
        quirks.append("OpenGL ES: 扩展依赖需检查 GL_EXTENSIONS 兼容性")

    return quirks


def register(mcp: FastMCP):
    @mcp.tool()
    def open_capture(
        filepath: str,
        shader_debug_paths: Optional[list[str]] = None,
    ) -> str:
        """Open a RenderDoc capture (.rdc) file for analysis.

        Multiple captures can be open simultaneously. Each capture is tracked by
        its filepath. Opening a new capture does NOT close previously opened ones —
        use close_capture or list_captures to manage them.

        The newly opened capture becomes the active session for all subsequent tool
        calls. Use switch_capture to switch between open captures.

        If the same filepath is opened again, the existing session is simply
        re-activated (no duplicate open).

        Args:
            filepath: Absolute path to the .rdc capture file.
            shader_debug_paths: Optional list of directories to search for
                separate shader debug info (PDB files). This enables HLSL
                source-level view in disassemble_shader, matching RenderDoc
                GUI's "Shader debug info search paths" setting.
                Example: ["E:/Project/Saved/ShaderSymbols"]
        """
        result = open_session(filepath, shader_debug_paths=shader_debug_paths)
        if IDLE_TIMEOUT_MINUTES > 0:
            result.setdefault("idle_auto_close_minutes", IDLE_TIMEOUT_MINUTES)
        return to_json(result)

    @mcp.tool()
    def close_capture(filepath: Optional[str] = None) -> str:
        """Close a capture file and free its resources.

        If filepath is provided, closes that specific capture.
        If omitted, closes the currently active capture.

        After closing, the active session automatically switches to the most
        recently opened remaining capture (if any).

        Args:
            filepath: Path of the capture to close. Defaults to the active capture.
        """
        return to_json(close_session(filepath))

    @mcp.tool()
    def list_captures() -> str:
        """List all currently open capture files and their status.

        Returns each capture's filepath, API type, total action count,
        current event, and whether it is the active session.
        """
        sessions = list_sessions()
        return to_json({
            "captures": sessions,
            "count": len(sessions),
            "idle_auto_close_minutes": IDLE_TIMEOUT_MINUTES if IDLE_TIMEOUT_MINUTES > 0 else "disabled",
        })

    @mcp.tool()
    def switch_capture(filepath: str) -> str:
        """Switch the active capture session to the specified file.

        All subsequent tool calls will operate on this capture until
        switch_capture is called again or the capture is closed.

        Args:
            filepath: Absolute path of an already-opened capture to activate.
                      Use list_captures to see available captures.
        """
        return to_json(switch_session(filepath))

    @mcp.tool()
    def get_capture_info() -> str:
        """Get information about the currently active capture.

        Returns API type, file path, action count, texture/buffer counts,
        and known GPU quirks based on the detected driver/GPU.
        """
        session = get_session()
        if session is None:
            return to_json(make_error("No capture file is open. Use open_capture first.", "NO_CAPTURE_OPEN"))

        controller = session.controller
        textures = controller.GetTextures()
        buffers = controller.GetBuffers()

        driver_name = session.driver_name

        # Detect resolution from most-used render target
        rt_draw_counts: dict[str, int] = {}
        for _eid, action in session.action_map.items():
            if action.flags & rd.ActionFlags.Drawcall:
                for o in action.outputs:
                    if int(o) != 0:
                        key = str(o)
                        rt_draw_counts[key] = rt_draw_counts.get(key, 0) + 1
        resolution = "unknown"
        main_color_format = "unknown"
        if rt_draw_counts:
            top_rid = max(rt_draw_counts, key=lambda k: rt_draw_counts[k])
            tex_desc = session.get_texture_desc(top_rid)
            if tex_desc is not None:
                resolution = f"{tex_desc.width}x{tex_desc.height}"
                main_color_format = str(tex_desc.format.Name())

        info = {
            "filepath": session.filepath,
            "api": driver_name,
            "resolution": resolution,
            "main_color_format": main_color_format,
            "total_actions": len(session.action_map),
            "textures": len(textures),
            "buffers": len(buffers),
            "current_event": session.current_event,
            "known_gpu_quirks": _get_gpu_quirks(driver_name),
        }
        return to_json(info)

    @mcp.tool()
    def get_frame_overview() -> str:
        """Get a frame-level statistics overview of the current capture.

        Returns action counts by type, texture/buffer memory totals,
        main render targets with draw counts, and estimated resolution.
        """
        session = get_session()
        if session is None:
            return to_json(make_error("No capture file is open. Use open_capture first.", "NO_CAPTURE_OPEN"))

        err = session.require_open()
        if err:
            return to_json(err)

        controller = session.controller

        # Count actions by type
        draw_calls = 0
        clears = 0
        dispatches = 0
        for _eid, action in session.action_map.items():
            if action.flags & rd.ActionFlags.Drawcall:
                draw_calls += 1
            if action.flags & rd.ActionFlags.Clear:
                clears += 1
            if action.flags & rd.ActionFlags.Dispatch:
                dispatches += 1

        # Texture memory
        textures = controller.GetTextures()
        tex_total_bytes = 0
        for tex in textures:
            size = 0
            try:
                if hasattr(tex, "byteSize") and tex.byteSize > 0:
                    size = tex.byteSize
            except Exception:
                pass
            if size == 0:
                bpp = 4
                fmt_name = str(tex.format.Name()).upper()
                if "R16G16B16A16" in fmt_name:
                    bpp = 8
                elif "R32G32B32A32" in fmt_name:
                    bpp = 16
                elif "R8G8B8A8" in fmt_name or "B8G8R8A8" in fmt_name:
                    bpp = 4
                elif "R16G16" in fmt_name:
                    bpp = 4
                elif "R32" in fmt_name and "G32" not in fmt_name:
                    bpp = 4
                elif "R16" in fmt_name and "G16" not in fmt_name:
                    bpp = 2
                elif "R8" in fmt_name and "G8" not in fmt_name:
                    bpp = 1
                elif "BC" in fmt_name or "ETC" in fmt_name or "ASTC" in fmt_name:
                    bpp = 1
                elif "D24" in fmt_name or "D32" in fmt_name:
                    bpp = 4
                elif "D16" in fmt_name:
                    bpp = 2
                size = tex.width * tex.height * max(tex.depth, 1) * max(tex.arraysize, 1) * bpp
                if tex.mips > 1:
                    size = int(size * 1.33)
            tex_total_bytes += size

        # Buffer memory
        buffers = controller.GetBuffers()
        buf_total_bytes = sum(buf.length for buf in buffers)

        # Detect render targets and their draw counts
        rt_draw_counts: dict[str, int] = {}
        for _eid, action in sorted(session.action_map.items()):
            if not (action.flags & rd.ActionFlags.Drawcall):
                continue
            for o in action.outputs:
                if int(o) != 0:
                    key = str(o)
                    rt_draw_counts[key] = rt_draw_counts.get(key, 0) + 1

        render_targets = []
        for rid_str, count in sorted(rt_draw_counts.items(), key=lambda x: -x[1]):
            rt_entry: dict = {"resource_id": rid_str, "draw_count": count}
            tex_desc = session.get_texture_desc(rid_str)
            if tex_desc is not None:
                rt_entry["size"] = f"{tex_desc.width}x{tex_desc.height}"
                rt_entry["format"] = str(tex_desc.format.Name())
            render_targets.append(rt_entry)

        resolution = "unknown"
        if render_targets:
            top_rt = render_targets[0]
            if "size" in top_rt:
                resolution = top_rt["size"]

        result = {
            "filepath": session.filepath,
            "api": session.driver_name,
            "resolution": resolution,
            "total_actions": len(session.action_map),
            "draw_calls": draw_calls,
            "clears": clears,
            "dispatches": dispatches,
            "textures": {
                "count": len(textures),
                "total_memory_mb": round(tex_total_bytes / (1024 * 1024), 2),
            },
            "buffers": {
                "count": len(buffers),
                "total_memory_mb": round(buf_total_bytes / (1024 * 1024), 2),
            },
            "render_targets": render_targets,
        }
        return to_json(result)
