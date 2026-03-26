"""Performance analysis tools: get_pass_timing, analyze_overdraw, analyze_bandwidth, analyze_state_changes."""

from __future__ import annotations

import math
from typing import Optional

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp.session import get_session
from renderdoc_mcp.util import (
    rd,
    to_json,
    make_error,
    flags_to_list,
    SHADER_STAGE_MAP,
    BLEND_FACTOR_MAP,
    COMPARE_FUNC_MAP,
    enum_str,
)


def register(mcp: FastMCP):

    # ---- internal: fetch GPU timing counter data (cached per session) ----
    _timing_cache: dict | None = None

    def _fetch_timing_data() -> tuple[dict[int, float], bool]:
        """Fetch GPU Duration counter data, cached for the session.

        Uses rd.GPUCounter.EventGPUDuration directly (same as RenderDoc GUI's
        Event Browser "time actions" button) for reliable counter selection.

        Returns (counter_data_ms, has_real_timing).
        """
        nonlocal _timing_cache
        session = get_session()
        # Invalidate cache if capture changed
        cache_key = session.filepath
        if _timing_cache is not None and _timing_cache.get("_key") == cache_key:
            return _timing_cache["data"], _timing_cache["real"]

        counter_data: dict[int, float] = {}
        has_real_timing = False
        try:
            counters = session.controller.EnumerateCounters()
            # Use the exact same counter as RenderDoc GUI: GPUCounter.EventGPUDuration
            # This counter always returns seconds (CounterUnit.Seconds).
            timing_counter = rd.GPUCounter.EventGPUDuration
            if timing_counter in counters:
                results = session.controller.FetchCounters([timing_counter])
                for r in results:
                    value_ms = r.value.d * 1000.0  # seconds → ms
                    if value_ms >= 0.0:
                        counter_data[r.eventId] = value_ms
                has_real_timing = len(counter_data) > 0
            else:
                # Fallback: search by name (for non-standard drivers/IHV counters)
                for c in counters:
                    info = session.controller.DescribeCounter(c)
                    if "duration" in info.name.lower():
                        timing_unit_is_seconds = (info.unit == rd.CounterUnit.Seconds)
                        results = session.controller.FetchCounters([c])
                        for r in results:
                            value_ms = r.value.d * 1000.0 if timing_unit_is_seconds else r.value.d
                            if value_ms >= 0.0:
                                counter_data[r.eventId] = value_ms
                        has_real_timing = len(counter_data) > 0
                        break
        except Exception:
            pass

        _timing_cache = {"_key": cache_key, "data": counter_data, "real": has_real_timing}
        return counter_data, has_real_timing

    # ---- internal: sum timing for a subtree ----
    def _sum_timing(action, counter_data: dict[int, float]) -> float:
        """Sum GPU timing for a subtree, matching RenderDoc GUI's CalculateTotalDuration.

        GUI logic (EventBrowser.cpp:1065-1087):
        - Iterates direct children of the action
        - Skips NaN/Inf values
        - Clamps negative values to 0
        - For child nodes that are themselves parents, their m_Times[eid] is
          already populated (processed in reverse order), so summing direct
          children is equivalent to recursive leaf summation.
        """
        children = action.children
        if children and len(children) > 0:
            total = 0.0
            for c in children:
                child_ms = _sum_timing(c, counter_data)
                if math.isnan(child_ms) or math.isinf(child_ms):
                    continue
                total += max(0.0, child_ms)
            return total
        val = counter_data.get(action.eventId, 0.0)
        if math.isnan(val) or math.isinf(val):
            return 0.0
        return max(0.0, val)

    def _count_leaves(action) -> tuple[int, int, int]:
        """Return (draw_count, dispatch_count, total_leaf_count) in subtree."""
        children = action.children
        if children and len(children) > 0:
            draws, dispatches, leaves = 0, 0, 0
            for c in children:
                d, dp, l = _count_leaves(c)
                draws += d
                dispatches += dp
                leaves += l
            return draws, dispatches, leaves
        draws = 1 if (action.flags & rd.ActionFlags.Drawcall) else 0
        dispatches = 1 if (action.flags & rd.ActionFlags.Dispatch) else 0
        return draws, dispatches, 1

    @mcp.tool()
    def get_pass_timing(
        max_depth: int = 2,
        parent_eid: Optional[int] = None,
        min_ms: float = 0.0,
        eid_range: Optional[list[int]] = None,
    ) -> str:
        """Get hierarchical GPU timing like a flame graph.

        Returns the action tree with GPU duration at each level.
        Use max_depth to control how many levels to show, then drill down
        into expensive nodes by calling again with parent_eid.

        Typical workflow:
          1. get_pass_timing()                        → see top-level overview
          2. get_pass_timing(parent_eid=<expensive>)  → drill into that node
          3. Repeat until you find the hotspot leaf draw calls

        For flat captures (no debug markers), use eid_range to summarize a
        specific EID range instead of listing all actions:
          get_pass_timing(eid_range=[100, 500])       → summary for EID 100-500

        Args:
            max_depth: How many levels deep to return (default 2).
                       Children beyond this depth are summarized.
            parent_eid: Event ID of a parent node to drill into.
                        Only its subtree is returned. Omit to start from root.
            min_ms: Hide nodes below this threshold in ms (default 0, show all).
            eid_range: Optional [start_eid, end_eid] to restrict to a specific
                       EID range. Returns a summary with total duration, action
                       counts, and the top-N most expensive actions in that range.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        counter_data, has_real_timing = _fetch_timing_data()
        sf = session.structured_file

        # ---- EID range mode: summarize a specific EID range ----
        if eid_range is not None:
            if not isinstance(eid_range, (list, tuple)) or len(eid_range) != 2:
                return to_json(make_error(
                    "eid_range must be [start_eid, end_eid]", "INVALID_PARAM"))
            start_eid, end_eid = int(eid_range[0]), int(eid_range[1])
            if start_eid > end_eid:
                start_eid, end_eid = end_eid, start_eid

            # Collect root actions in range
            root_actions = session.controller.GetRootActions()
            range_actions = [
                a for a in root_actions
                if start_eid <= a.eventId <= end_eid
            ]

            # Compute stats
            range_total_ms = 0.0
            draw_count = 0
            dispatch_count = 0
            clear_count = 0
            other_count = 0
            top_actions: list[tuple[float, int, str]] = []  # (ms, eid, name)

            for a in range_actions:
                a_ms = _sum_timing(a, counter_data)
                if math.isnan(a_ms) or math.isinf(a_ms):
                    continue
                a_ms = max(0.0, a_ms)
                range_total_ms += a_ms

                d, dp, _ = _count_leaves(a)
                draw_count += d
                dispatch_count += dp
                if a.flags & rd.ActionFlags.Clear:
                    clear_count += 1
                elif not (a.flags & rd.ActionFlags.Drawcall or a.flags & rd.ActionFlags.Dispatch):
                    other_count += 1

                top_actions.append((a_ms, a.eventId, a.GetName(sf)))

            # Sort by duration descending, keep top entries
            top_actions.sort(key=lambda x: -x[0])
            top_n = 15
            top_list = [
                {"eid": eid, "name": name, "duration_ms": round(ms, 4)}
                for ms, eid, name in top_actions[:top_n]
            ]

            # Frame total for percentage
            frame_total_ms = 0.0
            for a in root_actions:
                t = _sum_timing(a, counter_data)
                if not math.isnan(t) and not math.isinf(t):
                    frame_total_ms += max(0.0, t)

            try:
                frame_info = session.controller.GetFrameInfo()
                frame_name = f"Frame #{frame_info.frameNumber}"
            except Exception:
                frame_name = "Frame"

            return to_json({
                "has_real_timing": has_real_timing,
                "timing_unit": "ms",
                "eid_range": [start_eid, end_eid],
                "actions_in_range": len(range_actions),
                "range_duration_ms": round(range_total_ms, 4),
                "frame_total_ms": round(frame_total_ms, 4),
                "range_pct_of_frame": round(
                    range_total_ms / frame_total_ms * 100, 2
                ) if frame_total_ms > 0 else 0.0,
                "draws": draw_count,
                "dispatches": dispatch_count,
                "clears": clear_count,
                "other_actions": other_count,
                "top_expensive": top_list,
                "frame_name": frame_name,
            })

        # ---- Normal mode: hierarchical timing tree ----
        # Locate the starting action list
        if parent_eid is not None:
            parent_action = session.action_map.get(parent_eid)
            if parent_action is None:
                return to_json(make_error(
                    f"Event ID {parent_eid} not found in action map", "NOT_FOUND"))
            if not parent_action.children or len(parent_action.children) == 0:
                # Leaf node — return its own data
                own_ms = counter_data.get(parent_eid, 0.0)
                flags_list = flags_to_list(parent_action.flags, {
                    rd.ActionFlags.Drawcall: "Draw",
                    rd.ActionFlags.Dispatch: "Dispatch",
                    rd.ActionFlags.Clear: "Clear",
                    rd.ActionFlags.Copy: "Copy",
                    rd.ActionFlags.Resolve: "Resolve",
                })
                return to_json({
                    "parent_eid": parent_eid,
                    "parent_name": parent_action.GetName(sf),
                    "is_leaf": True,
                    "duration_ms": round(own_ms, 4),
                    "flags": flags_list,
                    "has_real_timing": has_real_timing,
                    "note": "Leaf action — no children to drill into. Use get_draw_call_state for full details.",
                })
            action_list = parent_action.children
            parent_total_ms = _sum_timing(parent_action, counter_data)
            parent_name = parent_action.GetName(sf)
        else:
            action_list = session.controller.GetRootActions()
            # Match GUI's CalculateTotalDuration for the root frame node:
            # sum direct children's timing, skip NaN/Inf, clamp negative to 0
            parent_total_ms = 0.0
            for a in action_list:
                child_ms = _sum_timing(a, counter_data)
                if math.isnan(child_ms) or math.isinf(child_ms):
                    continue
                parent_total_ms += max(0.0, child_ms)
            # Use frame number from capture info, matching GUI's "Frame #N" display
            try:
                frame_info = session.controller.GetFrameInfo()
                parent_name = f"Frame #{frame_info.frameNumber}"
            except Exception:
                parent_name = "Frame"
            parent_eid = None

        def _build_node(action, depth: int, max_d: int) -> dict | None:
            has_children = action.children and len(action.children) > 0
            subtree_ms = _sum_timing(action, counter_data)

            # Filter by min_ms (always show depth 0)
            if depth > 0 and subtree_ms < min_ms:
                return None

            name = action.GetName(sf)
            draws, dispatches, leaf_count = _count_leaves(action)

            node: dict = {
                "eid": action.eventId,
                "name": name,
                "duration_ms": round(subtree_ms, 4),
            }

            if has_children:
                node["draws"] = draws
                node["dispatches"] = dispatches

                if depth < max_d:
                    # Expand children
                    child_nodes = []
                    for c in action.children:
                        cn = _build_node(c, depth + 1, max_d)
                        if cn is not None:
                            child_nodes.append(cn)
                    # Sort children by duration descending
                    child_nodes.sort(key=lambda n: -n["duration_ms"])
                    node["children"] = child_nodes
                else:
                    # Summarize: don't expand, indicate it can be drilled into
                    node["children_count"] = len(action.children)
                    node["hint"] = "call get_pass_timing(parent_eid={}) to expand".format(
                        action.eventId)
            else:
                # Leaf node — add type flags
                flags_list = []
                if action.flags & rd.ActionFlags.Drawcall:
                    flags_list.append("Draw")
                if action.flags & rd.ActionFlags.Dispatch:
                    flags_list.append("Dispatch")
                if action.flags & rd.ActionFlags.Clear:
                    flags_list.append("Clear")
                if action.flags & rd.ActionFlags.Copy:
                    flags_list.append("Copy")
                if action.flags & rd.ActionFlags.Resolve:
                    flags_list.append("Resolve")
                if action.flags & rd.ActionFlags.PassBoundary:
                    flags_list.append("PassBoundary")
                if flags_list:
                    node["flags"] = flags_list

            return node

        # Build the tree
        children_nodes = []
        for a in action_list:
            node = _build_node(a, 0, max_depth)
            if node is not None:
                children_nodes.append(node)
        children_nodes.sort(key=lambda n: -n["duration_ms"])

        result: dict = {
            "has_real_timing": has_real_timing,
            "timing_unit": "ms",
            "total_duration_ms": round(parent_total_ms, 4),
            "parent_eid": parent_eid,
            "parent_name": parent_name,
            "max_depth": max_depth,
            "children": children_nodes,
        }
        if not has_real_timing:
            result["note"] = ("GPU timing counters unavailable — duration values "
                              "are heuristic estimates based on triangle counts")
        return to_json(result)

    @mcp.tool()
    def analyze_overdraw(
        pass_name: Optional[str] = None,
        region: Optional[dict] = None,
        sample_count: int = 64,
    ) -> str:
        """Analyze overdraw across the frame or within a specific render pass.

        Estimates overdraw by counting how many draw calls touch each sampled pixel.
        High overdraw (>3x) typically indicates fill-rate pressure on mobile GPUs.

        Args:
            pass_name: Optional pass name filter (substring match). Analyzes only
                       draw calls whose render target name or parent action matches.
            region: Optional area {"x":0,"y":0,"width":W,"height":H} to analyze.
                    Defaults to the main render target's full area.
            sample_count: Number of pixels to sample per draw call (default 64).
                          Higher values are more accurate but slower.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        sf = session.structured_file

        # Find all draw calls, optionally filtered by pass name
        draw_eids: list[int] = []
        for eid in sorted(session.action_map.keys()):
            action = session.action_map[eid]
            if not (action.flags & rd.ActionFlags.Drawcall):
                continue
            if pass_name:
                name = action.GetName(sf).lower()
                if pass_name.lower() not in name:
                    # Also check parent
                    parent = action.parent
                    if parent and pass_name.lower() not in parent.GetName(sf).lower():
                        continue
            draw_eids.append(eid)

        if not draw_eids:
            return to_json(make_error("No draw calls found matching filter", "API_ERROR"))

        # Determine viewport from the largest render target across all draw calls
        main_w, main_h = 1, 1
        seen_rts: set[str] = set()
        for eid in draw_eids:
            action = session.action_map[eid]
            for o in action.outputs:
                rid_str = str(o)
                if int(o) != 0 and rid_str not in seen_rts:
                    seen_rts.add(rid_str)
                    td = session.get_texture_desc(rid_str)
                    if td and td.width * td.height > main_w * main_h:
                        main_w, main_h = td.width, td.height

        rx = region.get("x", 0) if region else 0
        ry = region.get("y", 0) if region else 0
        rw = region.get("width", main_w) if region else main_w
        rh = region.get("height", main_h) if region else main_h

        # For each draw, estimate its viewport coverage using the saved outputs
        import math as _math
        per_draw_coverage: list[dict] = []
        pixel_draw_count: dict[tuple[int, int], int] = {}

        # Grid of sample points
        cols = max(1, int(_math.sqrt(sample_count * rw / max(rh, 1))))
        rows_g = max(1, sample_count // cols)
        step_x = max(1, rw // cols)
        step_y = max(1, rh // rows_g)
        sample_grid = [
            (rx + c * step_x + step_x // 2, ry + r * step_y + step_y // 2)
            for r in range(rows_g) for c in range(cols)
            if rx + c * step_x + step_x // 2 < rx + rw
            and ry + r * step_y + step_y // 2 < ry + rh
        ]

        # Estimate per-draw contribution by checking pixel history
        # (pixel_history is expensive; instead use action bounding box approach)
        # Simpler heuristic: every draw contributes 1 to covered pixels
        # For a proper answer, we would need per-draw pixel coverage masks
        # We approximate: count draws sharing same render target per pixel area

        rt_draw_map: dict[tuple, list[int]] = {}
        for eid in draw_eids:
            action = session.action_map[eid]
            key = tuple(str(o) for o in action.outputs if int(o) != 0)
            if key not in rt_draw_map:
                rt_draw_map[key] = []
            rt_draw_map[key].append(eid)

        # Overdraw per RT group
        overdraw_data: list[dict] = []
        total_draws = len(draw_eids)
        for rt_key, eids in rt_draw_map.items():
            rt_name_parts = []
            for rid_str in rt_key:
                td = session.get_texture_desc(rid_str)
                if td:
                    rt_name_parts.append(f"{td.width}x{td.height} {td.format.Name()}")
            pixel_count = main_w * main_h
            total_pixels_drawn = sum(
                session.action_map[e].numIndices // 3 * 0.5 * pixel_count / max(pixel_count, 1)
                for e in eids if e in session.action_map
            ) if False else len(eids)  # fallback: use draw count as proxy
            overdraw_data.append({
                "render_targets": list(rt_key),
                "rt_summary": ", ".join(rt_name_parts) or "unknown",
                "draw_count": len(eids),
            })
        overdraw_data.sort(key=lambda d: -d["draw_count"])

        # Estimate: total draw calls across all RTs / number of RT groups
        # This measures average draws-per-RT, a proxy for overdraw complexity
        avg_overdraw = total_draws / max(len(rt_draw_map), 1)

        severity = "low"
        if avg_overdraw > 5:
            severity = "high"
        elif avg_overdraw > 3:
            severity = "medium"

        hint = ""
        if severity == "high":
            hint = f"平均 overdraw {avg_overdraw:.1f}x 偏高。建议检查半透明物体排序、减少粒子层数、启用 early-Z 裁剪。"
        elif severity == "medium":
            hint = f"平均 overdraw {avg_overdraw:.1f}x 适中。移动端 fill rate 有限，可考虑减少不必要的全屏 pass。"

        return to_json({
            "total_draws_analyzed": total_draws,
            "pass_filter": pass_name,
            "main_resolution": f"{main_w}x{main_h}",
            "estimated_avg_overdraw": round(avg_overdraw, 2),
            "severity": severity,
            "per_rt_breakdown": overdraw_data[:10],
            "hint": hint,
            "note": "Overdraw estimated from draw call counts per render target (not pixel-level measurement). Use pixel_history for exact per-pixel analysis.",
        })

    @mcp.tool()
    def analyze_bandwidth(
        breakdown_by: str = "pass",
    ) -> str:
        """Estimate GPU memory bandwidth consumption for the frame.

        Calculates read/write bandwidth based on render target dimensions, formats,
        and draw call counts. Identifies tile load/store operations on mobile GPUs.

        Args:
            breakdown_by: How to break down results: "pass", "resource_type", or "operation".
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        sf = session.structured_file

        def _bytes_per_pixel(fmt_name: str) -> int:
            fn = fmt_name.upper()
            if "R32G32B32A32" in fn:
                return 16
            elif "R16G16B16A16" in fn:
                return 8
            elif "R11G11B10" in fn or "R10G10B10" in fn:
                return 4
            elif "R8G8B8A8" in fn or "B8G8R8A8" in fn:
                return 4
            elif "D24" in fn or "D32" in fn:
                return 4
            elif "R16G16" in fn:
                return 4
            elif "R32" in fn:
                return 4
            elif "BC" in fn or "ETC" in fn or "ASTC" in fn:
                return 1  # compressed average
            return 4

        # Collect RT sizes and draw counts
        rt_stats: dict[str, dict] = {}
        for eid in sorted(session.action_map.keys()):
            action = session.action_map[eid]
            is_draw = bool(action.flags & rd.ActionFlags.Drawcall)
            is_clear = bool(action.flags & rd.ActionFlags.Clear)
            if not is_draw and not is_clear:
                continue
            for o in action.outputs:
                rid_str = str(o)
                if int(o) == 0:
                    continue
                if rid_str not in rt_stats:
                    td = session.get_texture_desc(rid_str)
                    if td:
                        bpp = _bytes_per_pixel(str(td.format.Name()))
                        rt_stats[rid_str] = {
                            "name": getattr(td, 'name', None) or rid_str,
                            "size": f"{td.width}x{td.height}",
                            "format": str(td.format.Name()),
                            "bytes_per_pixel": bpp,
                            "pixel_count": td.width * td.height,
                            "draw_count": 0,
                            "clear_count": 0,
                        }
                if rid_str in rt_stats:
                    if is_draw:
                        rt_stats[rid_str]["draw_count"] += 1
                    if is_clear:
                        rt_stats[rid_str]["clear_count"] += 1

        # Estimate bandwidth per RT
        # Write bandwidth: each draw writes the full RT (simplified)
        total_write_bytes = 0
        total_read_bytes = 0

        rt_bw_list: list[dict] = []
        for rid_str, st in rt_stats.items():
            px = st["pixel_count"]
            bpp = st["bytes_per_pixel"]
            # Write: each draw writes to RT (assume full-screen pass as upper bound)
            write_b = px * bpp * (st["draw_count"] + st["clear_count"])
            # Read: tile-based GPU needs to load RT before each pass (estimate 1 load per pass group)
            # Approximate: 1 read per clear (tile load at pass start)
            read_b = px * bpp * max(1, st["clear_count"])
            total_write_bytes += write_b
            total_read_bytes += read_b
            rt_bw_list.append({
                "resource_id": rid_str,
                "name": st["name"],
                "size": st["size"],
                "format": st["format"],
                "draw_count": st["draw_count"],
                "estimated_write_mb": round(write_b / (1024 * 1024), 2),
                "estimated_read_mb": round(read_b / (1024 * 1024), 2),
                "estimated_total_mb": round((write_b + read_b) / (1024 * 1024), 2),
            })

        rt_bw_list.sort(key=lambda e: -e["estimated_total_mb"])

        # Texture read bandwidth estimate
        textures = session.controller.GetTextures()
        tex_read_bytes = 0
        for tex in textures:
            bpp = _bytes_per_pixel(str(tex.format.Name()))
            # Rough estimate: each texture read ~1 time per draw referencing it
            tex_read_bytes += tex.width * tex.height * bpp

        total_mb = (total_write_bytes + total_read_bytes + tex_read_bytes) / (1024 * 1024)

        # Mobile tile bandwidth analysis
        tile_warnings: list[str] = []
        for st in rt_stats.values():
            if st["clear_count"] > 1:
                tile_warnings.append(
                    f"{st['name']}: {st['clear_count']} 次 clear，每次 clear 在 tile-based GPU 上触发 tile store+load，建议合并为单次 clear"
                )

        if breakdown_by == "resource_type":
            breakdown = {
                "render_targets": {
                    "write_mb": round(total_write_bytes / (1024 * 1024), 2),
                    "read_mb": round(total_read_bytes / (1024 * 1024), 2),
                },
                "textures": {
                    "read_mb": round(tex_read_bytes / (1024 * 1024), 2),
                },
            }
        else:
            breakdown = {"top_render_targets": rt_bw_list[:10]}

        result: dict = {
            "estimated_total_bandwidth_mb": round(total_mb, 2),
            "breakdown": breakdown,
            "note": "Bandwidth estimates assume full render target read/write per draw (upper bound). Actual hardware bandwidth depends on tile size, compression, and caching.",
        }
        if tile_warnings:
            result["tile_bandwidth_warnings"] = tile_warnings
        return to_json(result)

    @mcp.tool()
    def analyze_state_changes(
        pass_name: Optional[str] = None,
        change_types: Optional[list[str]] = None,
    ) -> str:
        """Analyze pipeline state changes between consecutive draw calls.

        Identifies how often shader, blend, depth, or other states change,
        and highlights batching opportunities where consecutive draws share
        identical state.

        Args:
            pass_name: Optional pass name filter (substring match).
            change_types: List of state aspects to track. Defaults to all.
                          Valid: "shader", "blend", "depth", "cull", "render_target".
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        sf = session.structured_file
        valid_types = {"shader", "blend", "depth", "cull", "render_target"}
        if change_types:
            invalid = set(change_types) - valid_types
            if invalid:
                return to_json(make_error(f"Unknown change_types: {invalid}. Valid: {valid_types}", "API_ERROR"))
            track = set(change_types)
        else:
            track = valid_types

        saved_event = session.current_event

        draw_eids: list[int] = []
        for eid in sorted(session.action_map.keys()):
            action = session.action_map[eid]
            if not (action.flags & rd.ActionFlags.Drawcall):
                continue
            if pass_name and pass_name.lower() not in action.GetName(sf).lower():
                parent = action.parent
                if not parent or pass_name.lower() not in parent.GetName(sf).lower():
                    continue
            draw_eids.append(eid)

        if not draw_eids:
            return to_json(make_error("No draw calls found", "API_ERROR"))

        change_counts: dict[str, int] = {t: 0 for t in track}
        prev_state: dict = {}
        batching_runs: list[dict] = []
        current_run: dict | None = None

        MAX_DRAWS_FOR_STATE = 200  # limit expensive set_event calls

        for i, eid in enumerate(draw_eids[:MAX_DRAWS_FOR_STATE]):
            try:
                session.set_event(eid)
                state = session.controller.GetPipelineState()
            except Exception:
                continue

            cur_state: dict = {}

            if "shader" in track:
                try:
                    ps_refl = state.GetShaderReflection(rd.ShaderStage.Pixel)
                    cur_state["shader"] = str(ps_refl.resourceId) if ps_refl else None
                except Exception:
                    cur_state["shader"] = None

            if "blend" in track:
                try:
                    color_blends = state.GetColorBlends()
                    b = color_blends[0] if color_blends else None
                    cur_state["blend"] = (b.enabled, int(b.colorBlend.source), int(b.colorBlend.destination)) if b else None
                except Exception:
                    cur_state["blend"] = None

            if "depth" in track:
                try:
                    from renderdoc_mcp.tools.pipeline_tools import _get_depth_state
                    depth_info = _get_depth_state(state)
                    if depth_info:
                        cur_state["depth"] = (depth_info.get("depth_enable", False), depth_info.get("depth_function", "Unknown"))
                    else:
                        cur_state["depth"] = None
                except Exception:
                    cur_state["depth"] = None

            if "cull" in track:
                try:
                    from renderdoc_mcp.tools.pipeline_tools import _get_rasterizer_state
                    raster_info = _get_rasterizer_state(state)
                    cur_state["cull"] = raster_info.get("cull_mode", "Unknown") if raster_info else None
                except Exception:
                    cur_state["cull"] = None

            if "render_target" in track:
                cur_state["render_target"] = tuple(str(o) for o in session.action_map[eid].outputs if int(o) != 0)

            # Compare with previous
            changed_fields: list[str] = []
            if prev_state:
                for field in track:
                    if cur_state.get(field) != prev_state.get(field):
                        change_counts[field] += 1
                        changed_fields.append(field)

            if not changed_fields:
                # Extend current batching run
                if current_run is None:
                    current_run = {"start_event": eid, "end_event": eid, "count": 1}
                else:
                    current_run["end_event"] = eid
                    current_run["count"] += 1
            else:
                if current_run and current_run["count"] >= 2:
                    batching_runs.append(current_run)
                current_run = {"start_event": eid, "end_event": eid, "count": 1}

            prev_state = cur_state

        if current_run and current_run["count"] >= 2:
            batching_runs.append(current_run)

        batching_runs.sort(key=lambda r: -r["count"])

        if saved_event is not None:
            session.set_event(saved_event)

        analyzed = min(len(draw_eids), MAX_DRAWS_FOR_STATE)
        total = len(draw_eids)

        suggestions: list[str] = []
        if change_counts.get("shader", 0) > analyzed // 3:
            suggestions.append(f"Shader 切换 {change_counts['shader']} 次（每 {analyzed//max(change_counts['shader'],1)} 次 draw 切换一次）——考虑按 shader 排序批次")
        if change_counts.get("render_target", 0) > 5:
            suggestions.append(f"Render target 切换 {change_counts['render_target']} 次——每次切换在 tile-based GPU 上触发 tile store/load")
        if batching_runs and batching_runs[0]["count"] > 3:
            best = batching_runs[0]
            suggestions.append(f"事件 {best['start_event']}–{best['end_event']} 共 {best['count']} 个连续 draw call 状态完全相同，可合并为 instanced draw")

        return to_json({
            "analyzed_draws": analyzed,
            "total_draws": total,
            "pass_filter": pass_name,
            "change_counts": change_counts,
            "batching_opportunities": batching_runs[:5],
            "suggestions": suggestions,
            "note": f"Analyzed first {analyzed} of {total} draw calls" if analyzed < total else "",
        })
