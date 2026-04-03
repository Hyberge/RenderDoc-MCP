"""Data extraction tools: save_texture, get_buffer_data, pick_pixel, get_texture_stats, export_draw_textures, save_render_target, export_mesh."""

from __future__ import annotations

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp.session import get_session
from renderdoc_mcp.util import (
    rd,
    to_json,
    make_error,
    enum_str,
    FILE_TYPE_MAP,
    SHADER_STAGE_MAP,
    MESH_DATA_STAGE_MAP,
    TOPOLOGY_MAP,
    TEXTURE_DIM_MAP,
)


MAX_BUFFER_READ = 65536  # 64 KB limit


# ---------------------------------------------------------------------------
# Pixel value formatting helper (used by pick_pixel and advanced_tools.find_pixels)
# ---------------------------------------------------------------------------

# CompType enum values (from renderdoc/api/replay/replay_enums.h)
_COMP_TYPE_FLOAT_LIKE = {0, 1, 2, 3, 6, 7, 8, 9}  # Typeless, Float, UNorm, SNorm, UScaled, SScaled, Depth, UNormSRGB
_COMP_TYPE_UINT = {4}  # UInt
_COMP_TYPE_SINT = {5}  # SInt

_CHANNEL_NAMES = ["r", "g", "b", "a"]
_DEPTH_CHANNEL_NAMES_1 = ["depth"]
_DEPTH_CHANNEL_NAMES_2 = ["depth", "stencil"]


def _format_pixel_value(val, tex_desc) -> dict:
    """Extract pixel channels and data type from a PixelValue based on texture format.

    Args:
        val: PixelValue object from PickPixel.
        tex_desc: TextureDescription object (may be None; falls back to full float4).

    Returns:
        dict with "value", "data_type", "format_name", "raw".
    """
    raw = {
        "float": [val.floatValue[i] for i in range(4)],
        "uint": [val.uintValue[i] for i in range(4)],
        "int": [val.intValue[i] for i in range(4)],
    }

    if tex_desc is None:
        return {
            "value": {ch: val.floatValue[i] for i, ch in enumerate(_CHANNEL_NAMES)},
            "data_type": "float",
            "format_name": "unknown",
            "raw": raw,
        }

    fmt = tex_desc.format
    format_name = str(fmt.Name())

    # Special / compressed formats: PickPixel already decompresses to float4
    if fmt.Special():
        return {
            "value": {ch: val.floatValue[i] for i, ch in enumerate(_CHANNEL_NAMES)},
            "data_type": "float",
            "format_name": format_name,
            "raw": raw,
        }

    comp_type_val = int(fmt.compType)
    comp_count = int(fmt.compCount) if fmt.compCount > 0 else 4
    is_depth = (comp_type_val == 8)  # CompType.Depth

    # Determine accessor and data type label
    if comp_type_val in _COMP_TYPE_SINT:
        accessor = lambda i: val.intValue[i]
        data_type = "sint"
    elif comp_type_val in _COMP_TYPE_UINT:
        accessor = lambda i: val.uintValue[i]
        data_type = "uint"
    else:
        accessor = lambda i: val.floatValue[i]
        data_type = "float"

    # Channel names
    if is_depth:
        if comp_count >= 2:
            ch_names = _DEPTH_CHANNEL_NAMES_2
        else:
            ch_names = _DEPTH_CHANNEL_NAMES_1
    else:
        ch_names = _CHANNEL_NAMES[:comp_count]

    value = {}
    for i, ch in enumerate(ch_names):
        value[ch] = accessor(i)

    return {
        "value": value,
        "data_type": data_type,
        "format_name": format_name,
        "raw": raw,
    }


def register(mcp: FastMCP):
    @mcp.tool()
    def save_texture(
        resource_id: str,
        output_path: str,
        file_type: str = "png",
        mip: int = 0,
        event_id: Optional[int] = None,
    ) -> str:
        """Save a texture resource to an image file.

        Args:
            resource_id: The texture resource ID string.
            output_path: Absolute path for the output file.
            file_type: Output format: png, jpg, bmp, tga, hdr, exr, dds (default: png).
            mip: Mip level to save (default 0). Use -1 for all mips (DDS only).
            event_id: Optional event ID to navigate to first.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        tex_id = session.resolve_resource_id(resource_id)
        if tex_id is None:
            return to_json(make_error(f"Texture resource '{resource_id}' not found", "INVALID_RESOURCE_ID"))

        ft = FILE_TYPE_MAP.get(file_type.lower())
        if ft is None:
            return to_json(make_error(f"Unknown file type: {file_type}. Valid: {list(FILE_TYPE_MAP.keys())}", "API_ERROR"))

        output_path = os.path.normpath(output_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        texsave = rd.TextureSave()
        texsave.resourceId = tex_id
        texsave.destType = ft
        texsave.mip = mip
        texsave.slice.sliceIndex = 0
        texsave.alpha = rd.AlphaMapping.Preserve

        session.controller.SaveTexture(texsave, output_path)

        return to_json({
            "status": "saved",
            "output_path": output_path,
            "resource_id": resource_id,
            "file_type": file_type,
            "mip": mip,
        })

    @mcp.tool()
    def get_buffer_data(
        resource_id: str,
        offset: int = 0,
        length: int = 256,
        format: str = "hex",
    ) -> str:
        """Read raw data from a buffer resource.

        Args:
            resource_id: The buffer resource ID string.
            offset: Byte offset to start reading from (default 0).
            length: Number of bytes to read (default 256, max 65536).
            format: Output format: "hex" for hex dump, "floats" to interpret as float32 array.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        buf_id = session.resolve_resource_id(resource_id)
        if buf_id is None:
            return to_json(make_error(f"Buffer resource '{resource_id}' not found", "INVALID_RESOURCE_ID"))

        length = min(length, MAX_BUFFER_READ)
        data = session.controller.GetBufferData(buf_id, offset, length)

        result: dict = {
            "resource_id": resource_id,
            "offset": offset,
            "bytes_read": len(data),
        }

        if format == "floats":
            import struct
            num_floats = len(data) // 4
            floats = list(struct.unpack_from(f"{num_floats}f", data))
            result["data"] = [round(f, 6) for f in floats]
            result["format"] = "float32"
        else:
            # Hex dump - show lines of 16 bytes
            lines = []
            for i in range(0, len(data), 16):
                chunk = data[i:i+16]
                hex_part = " ".join(f"{b:02x}" for b in chunk)
                lines.append(f"{offset+i:08x}: {hex_part}")
            result["data"] = "\n".join(lines)
            result["format"] = "hex"

        return to_json(result)

    @mcp.tool()
    def pick_pixel(
        resource_id: str,
        x: int,
        y: int,
        mip_level: int = 0,
        array_slice: int = 0,
        sample_index: int = 0,
        event_id: Optional[int] = None,
    ) -> str:
        """Get the value of a specific pixel in a texture.

        Returns pixel data formatted according to the texture's actual format
        (float/uint/sint, correct channel count). Also includes texture metadata
        and raw values in all representations.

        Args:
            resource_id: The texture resource ID string.
            x: X coordinate of the pixel.
            y: Y coordinate of the pixel.
            mip_level: Mip level to read (default 0).
            array_slice: Array slice, 3D texture depth slice, or cubemap face index (default 0).
                         Cubemap face order: 0=+X 1=-X 2=+Y 3=-Y 4=+Z 5=-Z.
            sample_index: MSAA sample index (default 0).
            event_id: Optional event ID to navigate to first.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        tex_id = session.resolve_resource_id(resource_id)
        if tex_id is None:
            return to_json(make_error(f"Texture resource '{resource_id}' not found", "INVALID_RESOURCE_ID"))

        tex_desc = session.get_texture_desc(resource_id)

        # --- Parameter validation ---
        if tex_desc is not None:
            # Mip level
            if mip_level < 0 or mip_level >= tex_desc.mips:
                return to_json(make_error(
                    f"mip_level {mip_level} out of range [0, {tex_desc.mips - 1}]",
                    "INVALID_PARAMETER",
                ))
            # Effective dimensions at this mip
            eff_w = max(1, tex_desc.width >> mip_level)
            eff_h = max(1, tex_desc.height >> mip_level)
            if x < 0 or x >= eff_w:
                return to_json(make_error(
                    f"x={x} out of range [0, {eff_w - 1}] at mip {mip_level}",
                    "INVALID_PARAMETER",
                ))
            if y < 0 or y >= eff_h:
                return to_json(make_error(
                    f"y={y} out of range [0, {eff_h - 1}] at mip {mip_level}",
                    "INVALID_PARAMETER",
                ))
            # Array slice / depth slice
            max_slice = max(tex_desc.arraysize, tex_desc.depth) - 1
            if max_slice < 0:
                max_slice = 0
            if array_slice < 0 or array_slice > max_slice:
                return to_json(make_error(
                    f"array_slice {array_slice} out of range [0, {max_slice}]",
                    "INVALID_PARAMETER",
                ))
            # MSAA sample
            max_sample = max(tex_desc.msSamp, 1) - 1
            if sample_index < 0 or sample_index > max_sample:
                return to_json(make_error(
                    f"sample_index {sample_index} out of range [0, {max_sample}]",
                    "INVALID_PARAMETER",
                ))

        # --- Pick pixel ---
        sub = rd.Subresource(mip_level, array_slice, sample_index)
        val = session.controller.PickPixel(tex_id, x, y, sub, rd.CompType.Typeless)

        # --- Format pixel value ---
        formatted = _format_pixel_value(val, tex_desc)

        result: dict = {
            "resource_id": resource_id,
            "x": x,
            "y": y,
            "mip_level": mip_level,
            "array_slice": array_slice,
            "sample_index": sample_index,
            "value": formatted["value"],
            "data_type": formatted["data_type"],
            "format_name": formatted["format_name"],
            "raw": formatted["raw"],
        }

        # --- Texture metadata ---
        if tex_desc is not None:
            result["texture_info"] = {
                "name": tex_desc.name if hasattr(tex_desc, "name") else "",
                "width": tex_desc.width,
                "height": tex_desc.height,
                "depth": tex_desc.depth,
                "array_size": tex_desc.arraysize,
                "mips": tex_desc.mips,
                "format": str(tex_desc.format.Name()),
                "dimension": enum_str(tex_desc.dimension, TEXTURE_DIM_MAP, "Dim."),
                "cubemap": getattr(tex_desc, "cubemap", False),
                "ms_samples": tex_desc.msSamp,
            }

        return to_json(result)

    @mcp.tool()
    def get_texture_stats(
        resource_id: str,
        event_id: Optional[int] = None,
        all_slices: bool = False,
    ) -> str:
        """Get min/max/average statistics for a texture at the current event.

        Args:
            resource_id: The texture resource ID string.
            event_id: Optional event ID to navigate to first.
            all_slices: If True, return per-slice/per-face statistics. Useful for
                        cubemaps (returns stats for each of the 6 faces) and texture arrays.
                        Default False returns only slice 0.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        tex_id = session.resolve_resource_id(resource_id)
        if tex_id is None:
            return to_json(make_error(f"Texture resource '{resource_id}' not found", "INVALID_RESOURCE_ID"))

        tex_desc = session.get_texture_desc(resource_id)

        def _minmax_to_dict(mm):
            mn, mx = mm[0], mm[1]
            r_min, g_min, b_min, a_min = mn.floatValue[0], mn.floatValue[1], mn.floatValue[2], mn.floatValue[3]
            r_max, g_max, b_max, a_max = mx.floatValue[0], mx.floatValue[1], mx.floatValue[2], mx.floatValue[3]
            has_neg = any(v < 0 for v in [r_min, g_min, b_min])
            import math
            has_inf = any(math.isinf(v) for v in [r_max, g_max, b_max])
            has_nan = any(math.isnan(v) for v in [r_min, g_min, b_min, r_max, g_max, b_max])
            d: dict = {
                "min": {"r": r_min, "g": g_min, "b": b_min, "a": a_min},
                "max": {"r": r_max, "g": g_max, "b": b_max, "a": a_max},
                "has_negative": has_neg,
                "has_inf": has_inf,
                "has_nan": has_nan,
            }
            warnings = []
            if has_nan:
                warnings.append("⚠️ 检测到 NaN 值")
            if has_inf:
                warnings.append("⚠️ 检测到 Inf 值")
            if has_neg:
                warnings.append("⚠️ 检测到负值")
            if warnings:
                d["warnings"] = warnings
            return d

        result: dict = {"resource_id": resource_id}
        if tex_desc is not None:
            result["name"] = tex_desc.name if hasattr(tex_desc, "name") else ""
            result["size"] = f"{tex_desc.width}x{tex_desc.height}"
            result["format"] = str(tex_desc.format.Name())
            result["mips"] = tex_desc.mips
            result["array_size"] = tex_desc.arraysize

        # Cubemap face names
        _FACE_NAMES = ["+X", "-X", "+Y", "-Y", "+Z", "-Z"]

        if all_slices and tex_desc is not None:
            num_slices = max(tex_desc.arraysize, 1)
            is_cubemap = getattr(tex_desc, "cubemap", False) or num_slices == 6 or (hasattr(tex_desc, "dimension") and "Cube" in str(tex_desc.dimension))
            per_slice = []
            for s in range(num_slices):
                try:
                    mm = session.controller.GetMinMax(tex_id, rd.Subresource(0, s, 0), rd.CompType.Typeless)
                    entry = _minmax_to_dict(mm)
                    if is_cubemap and s < 6:
                        entry["face"] = _FACE_NAMES[s]
                    else:
                        entry["slice"] = s
                    per_slice.append(entry)
                except Exception as e:
                    per_slice.append({"slice": s, "error": str(e)})
            result["per_slice_stats"] = per_slice
        else:
            mm = session.controller.GetMinMax(tex_id, rd.Subresource(0, 0, 0), rd.CompType.Typeless)
            result.update(_minmax_to_dict(mm))

        return to_json(result)

    @mcp.tool()
    def read_texture_pixels(
        resource_id: str,
        x: int,
        y: int,
        width: int,
        height: int,
        mip_level: int = 0,
        array_slice: int = 0,
        event_id: Optional[int] = None,
    ) -> str:
        """Read actual pixel values from a rectangular region of a texture.

        Returns float RGBA values for each pixel. Region is capped at 64x64.
        Useful for precisely checking IBL cubemap faces, LUT textures, history buffers, etc.

        Args:
            resource_id: The texture resource ID string.
            x: Top-left X coordinate of the region.
            y: Top-left Y coordinate of the region.
            width: Region width (max 64).
            height: Region height (max 64).
            mip_level: Mip level to read (default 0).
            array_slice: Array slice or cubemap face index (default 0).
                         Cubemap face order: 0=+X 1=-X 2=+Y 3=-Y 4=+Z 5=-Z.
            event_id: Optional event ID to navigate to first.
        """
        import struct as _struct
        import math as _math

        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        tex_id = session.resolve_resource_id(resource_id)
        if tex_id is None:
            return to_json(make_error(f"Texture resource '{resource_id}' not found", "INVALID_RESOURCE_ID"))

        width = min(width, 64)
        height = min(height, 64)

        pixels: list[list] = []
        anomalies: list[dict] = []

        for py in range(y, y + height):
            row: list = []
            for px in range(x, x + width):
                try:
                    val = session.controller.PickPixel(
                        tex_id, px, py,
                        rd.Subresource(mip_level, array_slice, 0),
                        rd.CompType.Typeless,
                    )
                    r, g, b, a = (val.floatValue[0], val.floatValue[1],
                                  val.floatValue[2], val.floatValue[3])
                    pixel = [round(r, 6), round(g, 6), round(b, 6), round(a, 6)]
                    row.append(pixel)

                    # Anomaly detection
                    for ch_idx, ch_val in enumerate(pixel[:3]):
                        ch_name = "rgba"[ch_idx]
                        if _math.isnan(ch_val):
                            anomalies.append({"x": px, "y": py, "channel": ch_name, "type": "NaN"})
                        elif _math.isinf(ch_val):
                            anomalies.append({"x": px, "y": py, "channel": ch_name, "type": "Inf", "value": str(ch_val)})
                        elif ch_val < 0:
                            anomalies.append({"x": px, "y": py, "channel": ch_name, "type": "negative", "value": round(ch_val, 6)})
                except Exception as e:
                    row.append({"error": str(e)})
            pixels.append(row)

        result: dict = {
            "resource_id": resource_id,
            "region": {"x": x, "y": y, "width": width, "height": height},
            "mip_level": mip_level,
            "array_slice": array_slice,
            "pixels": pixels,
        }
        if anomalies:
            result["anomalies"] = anomalies
            result["anomaly_count"] = len(anomalies)
        return to_json(result)

    @mcp.tool()
    def export_draw_textures(
        event_id: int,
        output_dir: str,
        skip_small: bool = True,
    ) -> str:
        """Batch export all textures bound to a draw call's pixel shader.

        Args:
            event_id: The event ID of the draw call.
            output_dir: Directory to save exported textures.
            skip_small: Skip textures 4x4 or smaller (placeholder textures). Default True.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.set_event(event_id)
        if err:
            return to_json(err)

        state = session.controller.GetPipelineState()
        ps_refl = state.GetShaderReflection(rd.ShaderStage.Pixel)
        if ps_refl is None:
            return to_json(make_error("No pixel shader bound at this event", "API_ERROR"))

        output_dir = os.path.normpath(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        exported = []
        skipped = []
        try:
            all_ro = state.GetReadOnlyResources(rd.ShaderStage.Pixel)
            ro_by_index: dict = {}
            for b in all_ro:
                ro_by_index.setdefault(b.access.index, []).append(b)
        except Exception:
            ro_by_index = {}

        for i, ro_refl in enumerate(ps_refl.readOnlyResources):
            for b in ro_by_index.get(i, []):
                rid_str = str(b.descriptor.resource)
                tex_desc = session.get_texture_desc(rid_str)
                if tex_desc is None:
                    continue

                if skip_small and tex_desc.width <= 4 and tex_desc.height <= 4:
                    skipped.append({"name": ro_refl.name, "resource_id": rid_str,
                                    "size": f"{tex_desc.width}x{tex_desc.height}"})
                    continue

                filename = f"{ro_refl.name}_{tex_desc.width}x{tex_desc.height}.png"
                # Sanitize filename
                filename = filename.replace("/", "_").replace("\\", "_")
                out_path = os.path.join(output_dir, filename)

                texsave = rd.TextureSave()
                texsave.resourceId = tex_desc.resourceId
                texsave.destType = rd.FileType.PNG
                texsave.mip = 0
                texsave.slice.sliceIndex = 0
                texsave.alpha = rd.AlphaMapping.Preserve
                session.controller.SaveTexture(texsave, out_path)

                exported.append({
                    "name": ro_refl.name,
                    "resource_id": rid_str,
                    "size": f"{tex_desc.width}x{tex_desc.height}",
                    "output_path": out_path,
                })

        return to_json({
            "event_id": event_id,
            "exported": exported,
            "exported_count": len(exported),
            "skipped": skipped,
            "skipped_count": len(skipped),
        })

    @mcp.tool()
    def save_render_target(
        event_id: int,
        output_path: str,
        save_depth: bool = False,
    ) -> str:
        """Save the current render target(s) at a specific event.

        Args:
            event_id: The event ID to capture the render target from.
            output_path: Output file path or directory. If directory, auto-names the file.
            save_depth: Also save the depth target (default False).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.set_event(event_id)
        if err:
            return to_json(err)

        state = session.controller.GetPipelineState()
        output_path = os.path.normpath(output_path)
        saved = []

        # Save color target
        outputs = state.GetOutputTargets()
        color_target = None
        for o in outputs:
            if int(o.resource) != 0:
                color_target = o
                break

        if color_target is None:
            return to_json(make_error("No color render target bound at this event", "API_ERROR"))

        rid_str = str(color_target.resource)
        tex_desc = session.get_texture_desc(rid_str)

        if os.path.isdir(output_path):
            fname = f"rt_color_eid{event_id}.png"
            color_path = os.path.join(output_path, fname)
        else:
            color_path = output_path
            os.makedirs(os.path.dirname(color_path) or ".", exist_ok=True)

        texsave = rd.TextureSave()
        texsave.resourceId = color_target.resource
        texsave.destType = rd.FileType.PNG
        texsave.mip = 0
        texsave.slice.sliceIndex = 0
        texsave.alpha = rd.AlphaMapping.Preserve
        session.controller.SaveTexture(texsave, color_path)

        color_info: dict = {"type": "color", "resource_id": rid_str, "output_path": color_path}
        if tex_desc is not None:
            color_info["size"] = f"{tex_desc.width}x{tex_desc.height}"
            color_info["format"] = str(tex_desc.format.Name())
        saved.append(color_info)

        # Optionally save depth target
        if save_depth:
            try:
                dt = state.GetDepthTarget()
                if int(dt.resource) != 0:
                    dt_rid = str(dt.resource)
                    if os.path.isdir(output_path):
                        depth_path = os.path.join(output_path, f"rt_depth_eid{event_id}.png")
                    else:
                        base, ext = os.path.splitext(color_path)
                        depth_path = f"{base}_depth{ext}"

                    texsave = rd.TextureSave()
                    texsave.resourceId = dt.resource
                    texsave.destType = rd.FileType.PNG
                    texsave.mip = 0
                    texsave.slice.sliceIndex = 0
                    texsave.alpha = rd.AlphaMapping.Preserve
                    session.controller.SaveTexture(texsave, depth_path)

                    depth_info: dict = {"type": "depth", "resource_id": dt_rid, "output_path": depth_path}
                    dt_desc = session.get_texture_desc(dt_rid)
                    if dt_desc is not None:
                        depth_info["size"] = f"{dt_desc.width}x{dt_desc.height}"
                        depth_info["format"] = str(dt_desc.format.Name())
                    saved.append(depth_info)
            except Exception as exc:
                saved.append({"type": "depth", "error": f"Failed to save depth: {exc}"})

        return to_json({"event_id": event_id, "saved": saved, "count": len(saved)})

    @mcp.tool()
    def export_mesh(
        event_id: int,
        output_path: str,
    ) -> str:
        """Export mesh data from a draw call as OBJ format.

        Uses post-vertex-shader data to get transformed positions, normals, and UVs.

        Args:
            event_id: The event ID of the draw call.
            output_path: Output file path for the .obj file.
        """
        import struct as _struct

        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.set_event(event_id)
        if err:
            return to_json(err)

        postvs = session.controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
        if postvs.vertexResourceId == rd.ResourceId.Null():
            return to_json(make_error("No post-VS data available for this event", "API_ERROR"))

        state = session.controller.GetPipelineState()
        vs_refl = state.GetShaderReflection(rd.ShaderStage.Vertex)
        if vs_refl is None:
            return to_json(make_error("No vertex shader bound", "API_ERROR"))

        # Parse output signature to find position/normal/uv offsets
        out_sig = vs_refl.outputSignature
        pos_idx = None
        norm_idx = None
        uv_idx = None
        offset_map: list[tuple[str, int, int]] = []  # (semantic, start_float, comp_count)
        float_offset = 0
        for sig in out_sig:
            name = (sig.semanticName or sig.varName or "").upper()
            if "POSITION" in name:
                pos_idx = float_offset
            elif "NORMAL" in name:
                norm_idx = float_offset
            elif "TEXCOORD" in name and uv_idx is None:
                uv_idx = float_offset
            offset_map.append((name, float_offset, sig.compCount))
            float_offset += sig.compCount

        if pos_idx is None:
            # Fallback: assume first 4 floats are position
            pos_idx = 0

        num_verts = postvs.numIndices
        data = session.controller.GetBufferData(
            postvs.vertexResourceId, postvs.vertexByteOffset,
            num_verts * postvs.vertexByteStride,
        )

        floats_per_vertex = postvs.vertexByteStride // 4
        if floats_per_vertex == 0:
            return to_json(make_error(
                f"Invalid vertex stride ({postvs.vertexByteStride} bytes), cannot parse mesh data",
                "API_ERROR",
            ))

        # Parse all vertices
        positions = []
        normals = []
        uvs = []
        for i in range(num_verts):
            off = i * postvs.vertexByteStride
            if off + postvs.vertexByteStride > len(data):
                break
            vfloats = list(_struct.unpack_from(f"{floats_per_vertex}f", data, off))

            # Position (x, y, z) - skip w
            if pos_idx is not None and pos_idx + 3 <= len(vfloats):
                positions.append((vfloats[pos_idx], vfloats[pos_idx + 1], vfloats[pos_idx + 2]))
            else:
                positions.append((0.0, 0.0, 0.0))

            if norm_idx is not None and norm_idx + 3 <= len(vfloats):
                normals.append((vfloats[norm_idx], vfloats[norm_idx + 1], vfloats[norm_idx + 2]))

            if uv_idx is not None and uv_idx + 2 <= len(vfloats):
                uvs.append((vfloats[uv_idx], vfloats[uv_idx + 1]))

        # Write OBJ
        output_path = os.path.normpath(output_path)
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        # Determine topology for correct face generation
        try:
            topo = state.GetPrimitiveTopology()
            topo_name = enum_str(topo, TOPOLOGY_MAP, "Topology.")
        except Exception:
            topo_name = "TriangleList"

        lines = [f"# Exported from RenderDoc MCP - event {event_id}"]
        lines.append(f"# Vertices: {len(positions)}, Topology: {topo_name}")

        for p in positions:
            lines.append(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}")

        for n in normals:
            lines.append(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}")

        for uv in uvs:
            lines.append(f"vt {uv[0]:.6f} {uv[1]:.6f}")

        # Build triangle indices based on topology
        has_normals = len(normals) == len(positions)
        has_uvs = len(uvs) == len(positions)
        triangles: list[tuple[int, int, int]] = []  # 0-indexed

        if topo_name == "TriangleStrip":
            for i in range(len(positions) - 2):
                if i % 2 == 0:
                    triangles.append((i, i + 1, i + 2))
                else:
                    triangles.append((i, i + 2, i + 1))  # flip winding
        elif topo_name == "TriangleFan":
            for i in range(1, len(positions) - 1):
                triangles.append((0, i, i + 1))
        else:
            # TriangleList and all other topologies default to list
            for i in range(0, len(positions) - 2, 3):
                triangles.append((i, i + 1, i + 2))

        for t in triangles:
            i1, i2, i3 = t[0] + 1, t[1] + 1, t[2] + 1  # OBJ is 1-indexed
            if has_normals and has_uvs:
                lines.append(f"f {i1}/{i1}/{i1} {i2}/{i2}/{i2} {i3}/{i3}/{i3}")
            elif has_normals:
                lines.append(f"f {i1}//{i1} {i2}//{i2} {i3}//{i3}")
            elif has_uvs:
                lines.append(f"f {i1}/{i1} {i2}/{i2} {i3}/{i3}")
            else:
                lines.append(f"f {i1} {i2} {i3}")

        with open(output_path, "w") as f:
            f.write("\n".join(lines) + "\n")

        return to_json({
            "event_id": event_id,
            "output_path": output_path,
            "topology": topo_name,
            "vertices": len(positions),
            "normals": len(normals),
            "uvs": len(uvs),
            "triangles": len(triangles),
        })

    @mcp.tool()
    def get_texture_histogram(
        resource_id: str,
        min_val: float = 0.0,
        max_val: float = 1.0,
        channels: str = "rgb",
        event_id: Optional[int] = None,
    ) -> str:
        """Get the histogram (256 buckets) of a texture's color distribution.

        Useful for quickly analyzing brightness distribution, detecting HDR overflow,
        checking if values are clamped, or identifying underexposed/overexposed regions.

        Args:
            resource_id: The texture resource ID string.
            min_val: Minimum value for histogram range (default 0.0).
            max_val: Maximum value for histogram range (default 1.0).
            channels: Which channels to include: "rgb", "r", "g", "b", "a" (default "rgb").
            event_id: Optional event ID to navigate to first.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        tex_id = session.resolve_resource_id(resource_id)
        if tex_id is None:
            return to_json(make_error(f"Texture resource '{resource_id}' not found", "INVALID_RESOURCE_ID"))

        # Build channel bitmask: R=1, G=2, B=4, A=8
        channel_map = {"r": True, "g": True, "b": True, "a": False}
        ch_lower = channels.lower()
        if ch_lower == "rgb":
            channel_map = {"r": True, "g": True, "b": True, "a": False}
        elif ch_lower == "rgba":
            channel_map = {"r": True, "g": True, "b": True, "a": True}
        elif ch_lower in ("r", "g", "b", "a"):
            channel_map = {"r": ch_lower == "r", "g": ch_lower == "g",
                           "b": ch_lower == "b", "a": ch_lower == "a"}

        try:
            histogram = session.controller.GetHistogram(
                tex_id,
                rd.Subresource(0, 0, 0),
                rd.CompType.Typeless,
                min_val, max_val,
                (channel_map["r"], channel_map["g"], channel_map["b"], channel_map["a"]),
            )
        except Exception as e:
            return to_json(make_error(f"GetHistogram failed: {type(e).__name__}: {e}"))

        # Convert to list
        buckets = list(histogram)

        # Compute summary statistics
        total_pixels = sum(buckets)
        peak_bucket = max(range(len(buckets)), key=lambda i: buckets[i]) if buckets else 0
        peak_value = min_val + (peak_bucket + 0.5) * (max_val - min_val) / max(len(buckets), 1)

        # Check distribution characteristics
        bucket_range = max_val - min_val
        lower_quarter = sum(buckets[:64])
        upper_quarter = sum(buckets[192:])
        middle_half = sum(buckets[64:192])

        characteristics: list[str] = []
        if total_pixels > 0:
            if lower_quarter / total_pixels > 0.8:
                characteristics.append("heavily concentrated in dark values — possible underexposure")
            if upper_quarter / total_pixels > 0.8:
                characteristics.append("heavily concentrated in bright values — possible overexposure or HDR overflow")
            if buckets[-1] > total_pixels * 0.1 and max_val <= 1.0:
                characteristics.append(f"significant pixel count ({buckets[-1]}) in max bucket — values may be clamped at {max_val}")
            if buckets[0] > total_pixels * 0.1 and min_val >= 0.0:
                characteristics.append(f"significant pixel count ({buckets[0]}) in min bucket — values may be clamped at {min_val}")

        # Compact the histogram for output (256 values is fine for JSON)
        result: dict = {
            "resource_id": resource_id,
            "range": {"min": min_val, "max": max_val},
            "channels": channels,
            "total_pixels": total_pixels,
            "bucket_count": len(buckets),
            "histogram": buckets,
            "peak_bucket": peak_bucket,
            "peak_value": round(peak_value, 4),
        }
        if characteristics:
            result["characteristics"] = characteristics

        # Add texture info
        tex_desc = session.get_texture_desc(resource_id)
        if tex_desc is not None:
            result["texture"] = f"{tex_desc.width}x{tex_desc.height} {tex_desc.format.Name()}"

        return to_json(result)
