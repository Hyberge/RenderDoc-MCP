"""Inspection tools: get_debug_messages, get_api_properties, get_frame_info,
get_diagnostic_overview, get_descriptor_access, get_resource_details."""

from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp.runtime import query_cached_message
from renderdoc_mcp.session import get_session
from renderdoc_mcp.util import (
    rd,
    to_json,
    make_error,
)



def register(mcp: FastMCP):

    @mcp.tool()
    def get_debug_messages() -> str:
        """Get API validation and driver diagnostic messages from the capture.

        Returns debug messages sorted by severity (High first).
        Useful for quickly identifying API misuse, performance warnings, and driver issues.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        controller = session.controller
        messages = controller.GetDebugMessages()

        # Severity ordering for sort
        _severity_order = {"High": 0, "Medium": 1, "Low": 2, "Info": 3}

        results = []
        for msg in messages:
            severity_str = str(msg.severity).replace("MessageSeverity.", "")
            category_str = str(msg.category).replace("MessageCategory.", "")
            source_str = str(msg.source).replace("MessageSource.", "")
            results.append({
                "event_id": msg.eventId,
                "severity": severity_str,
                "category": category_str,
                "source": source_str,
                "description": msg.description,
            })

        results.sort(key=lambda m: _severity_order.get(m["severity"], 99))

        # Summary counts
        high = sum(1 for m in results if m["severity"] == "High")
        medium = sum(1 for m in results if m["severity"] == "Medium")
        low = sum(1 for m in results if m["severity"] == "Low")
        info = sum(1 for m in results if m["severity"] == "Info")

        return to_json({
            "messages": results,
            "count": len(results),
            "summary": f"{high} high, {medium} medium, {low} low, {info} info",
        })

    @mcp.tool()
    def get_api_properties() -> str:
        """Get API properties and capability information for the current capture.

        Returns pipeline type, GPU vendor, and whether features like pixel history
        and shader debugging are supported. Call this before using pixel_history or
        debug_shader_at_pixel to know if they will work.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        controller = session.controller
        props = controller.GetAPIProperties()

        result = {
            "pipeline_type": str(props.pipelineType).replace("GraphicsAPI.", ""),
            "local_renderer": str(props.localRenderer).replace("GraphicsAPI.", ""),
            "vendor": str(props.vendor).replace("GPUVendor.", ""),
            "pixel_history_supported": props.pixelHistory,
            "shader_debugging_supported": props.shaderDebugging,
            "degraded": props.degraded,
            "rgp_capture_supported": props.rgpCapture,
        }

        # Add capability hints for LLM
        hints = []
        if not props.pixelHistory:
            hints.append("pixel_history tool will NOT work for this capture/API")
        if not props.shaderDebugging:
            hints.append("debug_shader_at_pixel / debug_vertex / debug_compute_thread will fall back to reflection-only mode")
        if props.degraded:
            hints.append("Capture is running in DEGRADED mode — some features may be unavailable or incorrect")
        if hints:
            result["hints"] = hints

        return to_json(result)

    @mcp.tool()
    def get_frame_info() -> str:
        """Get detailed frame information including capture time, file size, and frame statistics.

        Returns precise data from RenderDoc's FrameDescription, including draw/dispatch/index/instance
        counts computed by RenderDoc itself (more accurate than manual counting).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        controller = session.controller

        try:
            frame_infos = controller.GetFrameInfo()
        except Exception as e:
            return to_json(make_error(f"GetFrameInfo failed: {type(e).__name__}: {e}"))

        # GetFrameInfo returns a list of FrameDescription; usually just one frame
        results = []
        for frame in frame_infos:
            entry: dict = {
                "frame_number": frame.frameNumber,
            }
            # File sizes
            try:
                entry["compressed_file_size"] = frame.compressedFileSize
                entry["uncompressed_file_size"] = frame.uncompressedFileSize
            except Exception:
                pass

            # Capture time
            try:
                entry["capture_time"] = frame.captureTime
            except Exception:
                pass

            # Frame statistics
            try:
                stats = frame.stats
                stat_dict: dict = {}
                # Try to extract common stat fields
                for attr in ["draws", "dispatches", "indices", "instances",
                             "vertices", "primitives", "drawCalls",
                             "constants", "samplers", "resourceBinds",
                             "blendChanges", "depthStencilChanges", "rasterChanges",
                             "outputChanges"]:
                    val = getattr(stats, attr, None)
                    if val is not None:
                        stat_dict[attr] = val
                if stat_dict:
                    entry["statistics"] = stat_dict
            except Exception:
                pass

            # Debug messages not bound to specific events
            try:
                if frame.debugMessages:
                    entry["frame_debug_messages"] = len(frame.debugMessages)
            except Exception:
                pass

            results.append(entry)

        if len(results) == 1:
            return to_json(results[0])
        return to_json({"frames": results, "count": len(results)})

    @mcp.tool()
    def get_diagnostic_overview() -> str:
        """One-shot diagnostic overview of the current frame.

        Aggregates: API properties, debug message summary, format precision risks,
        frame statistics highlights, and known GPU quirks into a single response.
        Best tool to call first after opening a capture — gives LLM a quick picture
        of potential issues before deeper investigation.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        controller = session.controller
        sf = session.structured_file
        result: dict = {}
        warnings: list[str] = []

        # 1. API Properties
        try:
            props = controller.GetAPIProperties()
            result["api"] = {
                "type": str(props.pipelineType).replace("GraphicsAPI.", ""),
                "vendor": str(props.vendor).replace("GPUVendor.", ""),
                "pixel_history": props.pixelHistory,
                "shader_debugging": props.shaderDebugging,
                "degraded": props.degraded,
            }
            if props.degraded:
                warnings.append("Capture is in DEGRADED mode")
        except Exception:
            pass

        # 2. Debug messages summary
        try:
            msgs = controller.GetDebugMessages()
            if msgs:
                high = sum(1 for m in msgs if "High" in str(m.severity))
                medium = sum(1 for m in msgs if "Medium" in str(m.severity))
                result["debug_messages"] = {
                    "total": len(msgs),
                    "high_severity": high,
                    "medium_severity": medium,
                }
                if high > 0:
                    # Include first high-severity message as sample
                    for m in msgs:
                        if "High" in str(m.severity):
                            result["debug_messages"]["first_high"] = {
                                "event_id": m.eventId,
                                "description": m.description[:200],
                            }
                            break
                    warnings.append(f"{high} high-severity debug messages found")
            else:
                result["debug_messages"] = {"total": 0}
        except Exception:
            pass

        # 3. Quick frame stats
        draw_count = 0
        dispatch_count = 0
        clear_count = 0
        for _eid, action in session.action_map.items():
            if action.flags & rd.ActionFlags.Drawcall:
                draw_count += 1
            if action.flags & rd.ActionFlags.Dispatch:
                dispatch_count += 1
            if action.flags & rd.ActionFlags.Clear:
                clear_count += 1
        result["frame_stats"] = {
            "total_actions": len(session.action_map),
            "draw_calls": draw_count,
            "dispatches": dispatch_count,
            "clears": clear_count,
        }

        # 4. Format precision risks (quick scan)
        textures = controller.GetTextures()
        precision_risks: list[dict] = []
        r11g11b10_count = 0
        d16_count = 0
        for tex in textures:
            fmt = str(tex.format.Name()).upper()
            if "R11G11B10" in fmt:
                r11g11b10_count += 1
            if "D16" in fmt and "D16" not in [r.get("format") for r in precision_risks]:
                d16_count += 1

        if r11g11b10_count > 0:
            precision_risks.append({
                "format": "R11G11B10_FLOAT",
                "count": r11g11b10_count,
                "severity": "high",
                "issue": "No sign bit — negative values clamp to 0 or undefined",
            })
            warnings.append(f"{r11g11b10_count} R11G11B10_FLOAT textures (no sign bit)")
        if d16_count > 0:
            precision_risks.append({
                "format": "D16",
                "count": d16_count,
                "severity": "medium",
                "issue": "16-bit depth — z-fighting likely at far plane",
            })
        if precision_risks:
            result["precision_risks"] = precision_risks

        # 5. GPU quirks
        from renderdoc_mcp.tools.session_tools import _get_gpu_quirks
        quirks = _get_gpu_quirks(session.driver_name)
        if quirks:
            result["gpu_quirks"] = quirks

        # 6. Resolution from most-used RT
        rt_draw_counts: dict[str, int] = {}
        for _eid, action in session.action_map.items():
            if action.flags & rd.ActionFlags.Drawcall:
                for o in action.outputs:
                    if int(o) != 0:
                        key = str(o)
                        rt_draw_counts[key] = rt_draw_counts.get(key, 0) + 1
        if rt_draw_counts:
            top_rid = max(rt_draw_counts, key=lambda k: rt_draw_counts[k])
            tex_desc = session.get_texture_desc(top_rid)
            if tex_desc is not None:
                result["main_resolution"] = f"{tex_desc.width}x{tex_desc.height}"
                result["main_color_format"] = str(tex_desc.format.Name())

        if warnings:
            result["warnings"] = warnings

        # Suggest next steps
        result["suggested_next_steps"] = [
            "get_frame_overview — for detailed render target and memory breakdown",
            "list_actions — to browse the action tree",
            "get_debug_messages — for full debug message list" if result.get("debug_messages", {}).get("total", 0) > 0 else None,
            "diagnose_negative_values — if HDR / IBL issues suspected",
        ]
        result["suggested_next_steps"] = [s for s in result["suggested_next_steps"] if s]

        return to_json(result)

    @mcp.tool()
    def get_descriptor_access(event_id: Optional[int] = None) -> str:
        """Get descriptor access details at the current or specified event.

        Shows which descriptors were accessed by each shader stage, including
        the descriptor type, index, array element, and whether it was statically unused.
        Most useful for D3D12 and Vulkan captures with modern binding models.

        Args:
            event_id: Optional event ID to navigate to first. Uses current event if omitted.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        controller = session.controller

        try:
            accesses = controller.GetDescriptorAccess()
        except Exception as e:
            return to_json(make_error(
                f"GetDescriptorAccess not supported or failed: {type(e).__name__}: {e}. "
                "This API is primarily useful for D3D12/Vulkan captures.",
                "API_ERROR",
            ))

        results = []
        for acc in accesses:
            entry: dict = {}
            try:
                entry["stage"] = str(acc.stage).replace("ShaderStage.", "")
            except Exception:
                entry["stage"] = "unknown"
            try:
                entry["type"] = str(acc.type).replace("DescriptorType.", "")
            except Exception:
                pass
            try:
                entry["index"] = acc.index
            except Exception:
                pass
            try:
                entry["array_element"] = acc.arrayElement
            except Exception:
                pass
            try:
                entry["statically_unused"] = acc.staticallyUnused
            except Exception:
                pass
            try:
                if hasattr(acc, "descriptorStore"):
                    entry["descriptor_store"] = str(acc.descriptorStore)
            except Exception:
                pass
            try:
                if hasattr(acc, "byteOffset"):
                    entry["byte_offset"] = acc.byteOffset
            except Exception:
                pass
            results.append(entry)

        return to_json({
            "event_id": session.current_event,
            "accesses": results,
            "count": len(results),
            "note": "Use get_shader_bindings for a higher-level view of resource bindings" if not results else "",
        })

    @mcp.tool()
    def get_resource_details(resource_id: str) -> str:
        """Get detailed information about a specific resource, including parent/derived relationships.

        Returns resource type, name, parent resources (that this was created from),
        derived resources (views created from this), and annotations.

        Args:
            resource_id: The resource ID string (as returned by other tools).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        controller = session.controller
        resources = controller.GetResources()

        target = None
        for res in resources:
            if str(res.resourceId) == resource_id:
                target = res
                break

        if target is None:
            return to_json(make_error(f"Resource ID '{resource_id}' not found", "INVALID_RESOURCE_ID"))

        result: dict = {
            "resource_id": resource_id,
            "type": str(target.type).replace("ResourceType.", ""),
        }

        # Name
        try:
            name = target.name
            if not isinstance(name, str):
                name = name.decode("utf-8", errors="replace") if hasattr(name, "decode") else repr(name)
            result["name"] = name
            result["autogenerated_name"] = target.autogeneratedName
        except Exception:
            result["name"] = resource_id

        # Parent resources
        try:
            parents = [str(p) for p in target.parentResources if int(p) != 0]
            if parents:
                result["parent_resources"] = parents
        except Exception:
            pass

        # Derived resources (views)
        try:
            derived = [str(d) for d in target.derivedResources if int(d) != 0]
            if derived:
                result["derived_resources"] = derived
        except Exception:
            pass

        # Initialisation chunks
        try:
            if target.initialisationChunks:
                result["initialisation_chunk_count"] = len(target.initialisationChunks)
        except Exception:
            pass

        # If it's a texture, add texture-specific info
        tex_desc = session.get_texture_desc(resource_id)
        if tex_desc is not None:
            result["texture_info"] = {
                "width": tex_desc.width,
                "height": tex_desc.height,
                "depth": tex_desc.depth,
                "array_size": tex_desc.arraysize,
                "mips": tex_desc.mips,
                "format": str(tex_desc.format.Name()),
                "ms_samples": tex_desc.msSamp,
            }
            try:
                result["texture_info"]["byte_size"] = tex_desc.byteSize
            except Exception:
                pass
            try:
                result["texture_info"]["cubemap"] = tex_desc.cubemap
            except Exception:
                pass

        # If it's a buffer, add buffer-specific info
        buffers = controller.GetBuffers()
        for buf in buffers:
            if str(buf.resourceId) == resource_id:
                result["buffer_info"] = {
                    "length": buf.length,
                    "creation_flags": buf.creationFlags,
                }
                try:
                    if buf.gpuAddress:
                        result["buffer_info"]["gpu_address"] = f"0x{buf.gpuAddress:x}"
                except Exception:
                    pass
                break

        # Usage summary
        try:
            rid = session.resolve_resource_id(resource_id)
            if rid:
                usages = controller.GetUsage(rid)
                usage_types: dict[str, int] = {}
                for u in usages:
                    uname = str(u.usage).replace("ResourceUsage.", "")
                    usage_types[uname] = usage_types.get(uname, 0) + 1
                result["usage_summary"] = {
                    "total_events": len(usages),
                    "by_type": usage_types,
                }
        except Exception:
            pass

        return to_json(result)

    @mcp.tool()
    def get_cached_message(
        message_id: str,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
        collection: Optional[str] = None,
        context_lines: int = 2,
    ) -> str:
        """Query a cached oversized tool result by id.

        When a tool returns an oversized payload, the server stores the full content
        in-memory and returns a message_id instead. Use this tool to page through the
        cached result, switch collection views, or search within the cached payload.

        Args:
            message_id: The cached message ID returned by another tool.
            page: 1-based page number.
            page_size: Number of items per page.
            search: Optional case-insensitive search string.
            collection: Optional collection/view key from available_views.
            context_lines: Search context lines for text views.
        """
        result = query_cached_message(
            message_id=message_id,
            page=page,
            page_size=page_size,
            search=search,
            collection=collection,
            context_lines=context_lines,
        )
        return to_json(result, allow_cache=False)

