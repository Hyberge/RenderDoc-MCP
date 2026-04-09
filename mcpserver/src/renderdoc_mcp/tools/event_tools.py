"""Event navigation tools: list_actions, get_action, set_event, search_actions, find_draws."""

from __future__ import annotations

import re
from typing import Optional

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp.session import get_session
from renderdoc_mcp.util import (
    rd,
    to_json,
    serialize_action,
    serialize_action_detail,
    serialize_sd_object,
    flags_to_list,
    make_error,
    SHADER_STAGE_MAP,
)

# Map event_type string → ActionFlags mask
_EVENT_TYPE_MAP: dict[str, int] = {
    "draw": int(rd.ActionFlags.Drawcall),
    "dispatch": int(rd.ActionFlags.Dispatch),
    "clear": int(rd.ActionFlags.Clear),
    "copy": int(rd.ActionFlags.Copy),
    "resolve": int(rd.ActionFlags.Resolve),
}


def register(mcp: FastMCP):
    @mcp.tool()
    def list_actions(
        max_depth: int = 2,
        filter_flags: Optional[list[str]] = None,
        filter: Optional[str] = None,
        event_type: Optional[str] = None,
    ) -> str:
        """List the draw call / action tree of the current capture.

        Args:
            max_depth: Maximum depth to recurse into children (default 2).
            filter_flags: Optional list of ActionFlags names to filter by (e.g. ["Drawcall", "Clear"]).
                         Only actions matching ANY of these flags are included.
            filter: Case-insensitive substring to match against action names
                    (e.g. "shadow", "taa", "bloom", "reflection").
            event_type: Shorthand type filter: "draw", "dispatch", "clear", "copy", "resolve".
                        Overrides filter_flags if both are provided.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        # Resolve event_type shorthand first (overrides filter_flags)
        if event_type is not None:
            et = event_type.lower()
            if et != "all":
                type_mask = _EVENT_TYPE_MAP.get(et)
                if type_mask is None:
                    return to_json(make_error(
                        f"Unknown event_type: {event_type}. Valid: all, {', '.join(_EVENT_TYPE_MAP)}",
                        "API_ERROR",
                    ))
                filter_flags = None  # event_type takes precedence
            else:
                type_mask = 0
        else:
            type_mask = 0

        # Resolve flag filter
        flag_mask = type_mask
        if not type_mask and filter_flags:
            for name in filter_flags:
                val = getattr(rd.ActionFlags, name, None)
                if val is None:
                    return to_json(make_error(f"Unknown ActionFlag: {name}", "API_ERROR"))
                flag_mask |= val

        # Compile name filter
        name_filter = filter.lower() if filter else None

        sf = session.structured_file
        root_actions = session.get_root_actions()

        def should_include(action) -> bool:
            if flag_mask and not (action.flags & flag_mask):
                return False
            if name_filter and name_filter not in action.GetName(sf).lower():
                return False
            return True

        def _collect_leaf_matches(node: dict) -> list[dict]:
            """Flatten a nested filter result into a list of leaf-level matches."""
            leaves = []
            # If this node itself matches (included), it's a match entry
            direct = {"event_id": node["event_id"], "name": node["name"]}
            # Check if it has further children or matched_children
            if "children" in node:
                for ch in node["children"]:
                    leaves.extend(_collect_leaf_matches(ch))
            elif "matched_children" in node:
                for ch in node["matched_children"]:
                    # Recurse if the child has nested structure
                    if "children" in ch or "matched_children" in ch:
                        leaves.extend(_collect_leaf_matches(ch))
                    else:
                        leaves.append({"event_id": ch["event_id"], "name": ch["name"]})
            else:
                # Leaf node
                leaves.append(direct)
            return leaves

        def serialize_filtered(action, depth: int) -> dict | None:
            included = should_include(action)
            children = []
            # When filtering, always recurse into children regardless of depth
            # to find deep matches; only limit display expansion by max_depth
            if len(action.children) > 0:
                for c in action.children:
                    child = serialize_filtered(c, depth + 1)
                    if child is not None:
                        children.append(child)

            if not included and not children:
                return None

            result = {
                "event_id": action.eventId,
                "name": action.GetName(sf),
                "flags": flags_to_list(action.flags),
            }
            if action.numIndices > 0:
                result["num_indices"] = action.numIndices
            if children:
                if depth < max_depth:
                    result["children"] = children
                else:
                    # Beyond max_depth: flatten all matching descendants to a list
                    flat: list[dict] = []
                    for ch in children:
                        flat.extend(_collect_leaf_matches(ch))
                    result["matched_children_count"] = len(flat)
                    result["matched_children"] = flat
            elif depth >= max_depth and len(action.children) > 0:
                result["children_count"] = len(action.children)
            return result

        needs_filter = bool(flag_mask or name_filter)
        if needs_filter:
            actions = []
            for a in root_actions:
                r = serialize_filtered(a, 0)
                if r is not None:
                    actions.append(r)
        else:
            actions = [serialize_action(a, sf, max_depth=max_depth) for a in root_actions]

        return to_json({"actions": actions, "total": len(session.action_map)})

    @mcp.tool()
    def get_action(event_id: int) -> str:
        """Get detailed information about a specific action/draw call.

        Args:
            event_id: The event ID of the action to inspect.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        action = session.get_action(event_id)
        if action is None:
            return to_json(make_error(f"Event ID {event_id} not found", "INVALID_EVENT_ID"))

        return to_json(serialize_action_detail(action, session.structured_file))

    @mcp.tool()
    def set_event(event_id: int) -> str:
        """Navigate the replay to a specific event ID.

        This must be called before inspecting pipeline state, shader bindings, etc.
        Subsequent queries will reflect the state at this event.

        Args:
            event_id: The event ID to navigate to.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        err = session.set_event(event_id)
        if err:
            return to_json(err)

        action = session.get_action(event_id)
        name = action.GetName(session.structured_file) if action else "unknown"
        return to_json({"status": "ok", "event_id": event_id, "name": name})

    @mcp.tool()
    def search_actions(
        name_pattern: Optional[str] = None,
        flags: Optional[list[str]] = None,
    ) -> str:
        """Search for actions by name pattern and/or flags.

        Args:
            name_pattern: Regex pattern to match action names (case-insensitive).
            flags: List of ActionFlags names; actions matching ANY flag are included.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        flag_mask = 0
        if flags:
            for name in flags:
                val = getattr(rd.ActionFlags, name, None)
                if val is None:
                    return to_json(make_error(f"Unknown ActionFlag: {name}", "API_ERROR"))
                flag_mask |= val

        pattern = None
        if name_pattern:
            try:
                pattern = re.compile(name_pattern, re.IGNORECASE)
            except re.error as e:
                return to_json(make_error(f"Invalid regex pattern: {e}", "API_ERROR"))
        sf = session.structured_file
        results = []

        for eid, action in sorted(session.action_map.items()):
            if flag_mask and not (action.flags & flag_mask):
                continue
            action_name = action.GetName(sf)
            if pattern and not pattern.search(action_name):
                continue
            entry = {
                "event_id": eid,
                "name": action_name,
                "flags": flags_to_list(action.flags),
            }
            # Classify action type
            if action.flags & rd.ActionFlags.Drawcall:
                entry["action_type"] = "draw"
            elif action.flags & rd.ActionFlags.Dispatch:
                entry["action_type"] = "dispatch"
            elif action.flags & rd.ActionFlags.Clear:
                entry["action_type"] = "clear"
            elif action.flags & rd.ActionFlags.Copy:
                entry["action_type"] = "copy"
            elif action.flags & rd.ActionFlags.Resolve:
                entry["action_type"] = "resolve"
            elif len(action.children) > 0:
                entry["action_type"] = "marker"
                # Collect actual execution EIDs from children
                child_eids: list[dict] = []
                for c in action.children:
                    if c.flags & rd.ActionFlags.Dispatch:
                        child_eids.append({"event_id": c.eventId, "type": "dispatch"})
                    elif c.flags & rd.ActionFlags.Drawcall:
                        child_eids.append({"event_id": c.eventId, "type": "draw"})
                    elif c.flags & rd.ActionFlags.Clear:
                        child_eids.append({"event_id": c.eventId, "type": "clear"})
                if child_eids:
                    entry["child_execution_eids"] = child_eids
            else:
                entry["action_type"] = "other"
            results.append(entry)

        return to_json({"matches": results, "count": len(results)})

    @mcp.tool()
    def find_draws(
        blend: Optional[bool] = None,
        min_vertices: Optional[int] = None,
        texture_id: Optional[str] = None,
        shader_id: Optional[str] = None,
        render_target_id: Optional[str] = None,
        max_results: int = 50,
    ) -> str:
        """Search draw calls by rendering state filters.

        Iterates through draw calls checking pipeline state. This can be slow
        for large captures as it must set_event for each draw call.

        Args:
            blend: Filter by blend enabled state (True/False).
            min_vertices: Minimum vertex count to include.
            texture_id: Only draws using this texture resource ID.
            shader_id: Only draws using this shader resource ID.
            render_target_id: Only draws targeting this render target.
            max_results: Maximum results to return (default 50).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        sf = session.structured_file
        results = []
        saved_event = session.current_event

        for eid in sorted(session.action_map.keys()):
            if len(results) >= max_results:
                break

            action = session.action_map[eid]
            if not (action.flags & rd.ActionFlags.Drawcall):
                continue

            # Quick filters before expensive set_event
            if min_vertices is not None and action.numIndices < min_vertices:
                continue

            if render_target_id is not None:
                output_ids = [str(o) for o in action.outputs if int(o) != 0]
                if render_target_id not in output_ids:
                    continue

            # Need pipeline state for blend/texture/shader filters
            needs_state = (blend is not None or texture_id is not None or shader_id is not None)
            if needs_state:
                session.set_event(eid)
                state = session.controller.GetPipelineState()

                if blend is not None:
                    try:
                        color_blends = state.GetColorBlends()
                        blend_enabled = color_blends[0].enabled if color_blends else False
                        if blend_enabled != blend:
                            continue
                    except Exception:
                        continue

                if shader_id is not None:
                    found = False
                    for _sname, sstage in SHADER_STAGE_MAP.items():
                        if sstage == rd.ShaderStage.Compute:
                            continue
                        refl = state.GetShaderReflection(sstage)
                        if refl is not None and str(refl.resourceId) == shader_id:
                            found = True
                            break
                    if not found:
                        continue

                if texture_id is not None:
                    found = False
                    ps_refl = state.GetShaderReflection(rd.ShaderStage.Pixel)
                    if ps_refl is not None:
                        try:
                            all_ro = state.GetReadOnlyResources(rd.ShaderStage.Pixel)
                            for b in all_ro:
                                if str(b.descriptor.resource) == texture_id:
                                    found = True
                                    break
                        except Exception:
                            pass
                    if not found:
                        continue

            results.append({
                "event_id": eid,
                "name": action.GetName(sf),
                "flags": flags_to_list(action.flags),
                "num_indices": action.numIndices,
            })

        # Restore the original event position
        if saved_event is not None:
            session.set_event(saved_event)

        return to_json({"matches": results, "count": len(results), "max_results": max_results})

    # ── Helper: serialize a single SDChunk (API call) ──

    def _serialize_api_call(sf, event_id: int, chunk_index: int, include_params: bool) -> dict | None:
        """Serialize one API call from its SDChunk."""
        NO_CHUNK = 0xFFFFFFFF
        if chunk_index == NO_CHUNK:
            return None
        try:
            chunk = sf.chunks[chunk_index]
        except (IndexError, Exception):
            return None

        entry: dict = {
            "event_id": event_id,
            "name": str(chunk.name),
        }
        # Add metadata if available
        try:
            md = chunk.metadata
            if md.durationMicro >= 0:
                entry["duration_us"] = int(md.durationMicro)
            if md.threadID:
                entry["thread_id"] = int(md.threadID)
        except Exception:
            pass

        if include_params:
            params = []
            try:
                n = chunk.NumChildren()
                for i in range(n):
                    child = chunk.GetChild(i)
                    if child is None:
                        continue
                    params.append({
                        "name": str(child.name),
                        "type": str(child.type.name),
                        "value": serialize_sd_object(child),
                    })
            except Exception as exc:
                params.append({"error": f"Failed to serialize params: {exc}"})
            entry["params"] = params

        return entry

    @mcp.tool()
    def get_api_calls_by_action(
        event_id: int,
        include_params: bool = True,
    ) -> str:
        """Get all API calls associated with a specific action (draw/dispatch/clear/etc).

        Each action in RenderDoc has a list of preceding API events (state setup calls,
        resource barriers, etc.) that are not visible through list_actions. This tool
        exposes every raw API call with its full parameter values.

        Args:
            event_id: The event ID of the action to inspect.
            include_params: Whether to include full parameter values (default True).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        action = session.get_action(event_id)
        if action is None:
            return to_json(make_error(f"Event ID {event_id} not found in action map", "INVALID_EVENT_ID"))

        sf = session.structured_file
        api_calls = []
        for evt in action.events:
            entry = _serialize_api_call(sf, evt.eventId, evt.chunkIndex, include_params)
            if entry is not None:
                api_calls.append(entry)

        return to_json({
            "action_event_id": event_id,
            "action_name": action.GetName(sf),
            "api_calls": api_calls,
            "total": len(api_calls),
        })

    @mcp.tool()
    def get_api_calls_range(
        start_eid: int,
        end_eid: int,
        include_params: bool = True,
        name_filter: Optional[str] = None,
    ) -> str:
        """Get all API calls within an event ID range.

        Returns every raw API call (including non-action calls like SetRootCBV,
        ResourceBarrier, BeginQuery, etc.) whose event ID falls in [start_eid, end_eid].

        Args:
            start_eid: Start of the event ID range (inclusive).
            end_eid: End of the event ID range (inclusive).
            include_params: Whether to include full parameter values (default True).
            name_filter: Optional case-insensitive substring to filter by API function name
                        (e.g. "Barrier", "SetRoot", "BeginQuery").
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        if start_eid > end_eid:
            return to_json(make_error(
                f"start_eid ({start_eid}) must be <= end_eid ({end_eid})",
                "API_ERROR",
            ))

        sf = session.structured_file
        event_map = session.event_map
        name_lower = name_filter.lower() if name_filter else None

        api_calls = []
        for eid in sorted(event_map.keys()):
            if eid < start_eid:
                continue
            if eid > end_eid:
                break
            chunk_index = event_map[eid]
            entry = _serialize_api_call(sf, eid, chunk_index, include_params)
            if entry is None:
                continue
            if name_lower and name_lower not in entry["name"].lower():
                continue
            api_calls.append(entry)

        return to_json({
            "range": [start_eid, end_eid],
            "api_calls": api_calls,
            "total": len(api_calls),
            "name_filter": name_filter,
        })
