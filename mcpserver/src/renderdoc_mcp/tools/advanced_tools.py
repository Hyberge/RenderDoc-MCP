"""Advanced tools: pixel_history, get_post_vs_data, diff_draw_calls, analyze_render_passes,
sample_pixel_region, debug_shader_at_pixel."""

from __future__ import annotations

import math
import struct
from typing import Optional

from mcp.server.fastmcp import FastMCP

from renderdoc_mcp.session import get_session
from renderdoc_mcp.util import (
    rd,
    to_json,
    make_error,
    flags_to_list,
    MESH_DATA_STAGE_MAP,
    serialize_shader_variable,
    SHADER_STAGE_MAP,
)


def _build_inst_info_lookup(trace) -> dict:
    """Build a lookup from instruction index to InstructionSourceInfo.

    instInfo is sorted by instruction but may be sparse. For a given instruction,
    the correct entry is the lower bound (largest instruction <= target).
    """
    entries = []
    try:
        for info in trace.instInfo:
            entries.append(info)
    except Exception:
        pass
    return entries


def _find_inst_info(entries: list, instruction: int):
    """Binary search for the InstructionSourceInfo covering this instruction."""
    if not entries:
        return None
    lo, hi = 0, len(entries) - 1
    result = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if entries[mid].instruction <= instruction:
            result = entries[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def _build_source_var_map(trace) -> dict[str, str]:
    """Build a global debug_var_name -> best_source_name mapping from trace.sourceVars.

    trace.sourceVars contains mappings like:
      name='TileCoord.x', variables=[DebugVariableReference(name='_238', component=0)]
      name='View.View_TranslatedWorldToClip', variables=[DebugVariableReference(name='View[0]', component=0), ...]

    We build a lookup: '_238' -> 'TileCoord', '_239' -> 'TileCoord' (strip .x/.y suffix for grouping).
    """
    mapping: dict[str, str] = {}
    try:
        for sv in trace.sourceVars:
            src_name = sv.name
            if not src_name:
                continue
            for dbg_ref in sv.variables:
                if dbg_ref.name and dbg_ref.name not in mapping:
                    mapping[dbg_ref.name] = src_name
    except Exception:
        pass
    return mapping


def _get_inst_source_var_map(inst_info) -> dict[str, str]:
    """Build debug_var_name -> source_name mapping from per-instruction sourceVars.

    Per-instruction sourceVars have higher priority (local variables like TileCoord.x).
    """
    mapping: dict[str, str] = {}
    if inst_info is None:
        return mapping
    try:
        for sv in inst_info.sourceVars:
            src_name = sv.name
            if not src_name:
                continue
            for dbg_ref in sv.variables:
                if dbg_ref.name:
                    mapping[dbg_ref.name] = src_name
    except Exception:
        pass
    return mapping


def _collect_all_states(session, trace) -> list:
    """Run ContinueDebug to completion, return all ShaderDebugState objects."""
    all_states: list = []
    while True:
        new_states = session.controller.ContinueDebug(trace.debugger)
        if not new_states:
            break
        all_states.extend(new_states)
    return all_states


def _get_visible_source_vars(inst_info, inst_src_map: dict, global_src_map: dict) -> list[dict]:
    """Get all visible source-level variables at an instruction, with their current values
    resolved through the debug variable mapping."""
    if inst_info is None:
        return []
    result: list[dict] = []
    try:
        for svm in inst_info.sourceVars:
            if not svm.name or svm.undefinedValue:
                continue
            result.append({"name": svm.name, "rows": svm.rows, "columns": svm.columns})
    except Exception:
        pass
    return result


def _get_step_location(inst_info) -> dict | None:
    """Extract source location from InstructionSourceInfo."""
    if inst_info is None:
        return None
    try:
        li = inst_info.lineInfo
        if li.fileIndex >= 0 and li.lineStart > 0:
            loc: dict = {"file_index": li.fileIndex, "line": li.lineStart}
            if li.lineEnd != li.lineStart:
                loc["line_end"] = li.lineEnd
            return loc
    except Exception:
        pass
    return None


def _serialize_change(change, inst_src_map: dict, global_src_map: dict) -> dict | None:
    """Serialize a ShaderVariableChange with source name mapping."""
    var = change.after
    if not var.name:
        return None
    dbg_name = var.name
    source_name = inst_src_map.get(dbg_name) or global_src_map.get(dbg_name)
    sv = serialize_shader_variable(var)
    if source_name and source_name != dbg_name:
        sv["source_name"] = source_name
    # Anomaly detection
    val = sv.get("value", [])
    if isinstance(val, list):
        flat = [v for v in val if isinstance(v, (int, float))]
        display = source_name or dbg_name
        if any(math.isnan(v) for v in flat):
            sv["warning"] = f"⚠️ {display} contains NaN"
        elif any(math.isinf(v) for v in flat):
            sv["warning"] = f"⚠️ {display} contains Inf"
    return sv


def _run_shader_debug(
    session,
    trace,
    mode: str = "summary",
    watch_variables: list[str] | None = None,
    run_to_step: int | None = None,
    run_to_line: int | None = None,
) -> dict:
    """Execute shader debug trace with different analysis modes.

    Modes:
        summary: (default) Returns inputs, outputs, branch decisions, anomalies,
                 and call chain. Compact and LLM-friendly.
        watch:   Track specific source variables' value changes over execution.
                 Requires watch_variables.
        anomaly: Find the first step where NaN/Inf/negative appears,
                 return context around it.
        step:    Run to a specific step index (run_to_step) or source line
                 (run_to_line), return all visible source variables at that point.
    """
    inst_entries = _build_inst_info_lookup(trace)
    global_src_map = _build_source_var_map(trace)
    all_states = _collect_all_states(session, trace)

    if not all_states:
        return {"error": "No debug states returned", "total_states": 0}

    total_states = len(all_states)

    # ── step mode: run to a specific step/line and dump visible variables ──
    if mode == "step":
        target_state = None
        target_inst_info = None

        if run_to_step is not None:
            for st in all_states:
                if st.stepIndex >= run_to_step:
                    target_state = st
                    break
            if target_state is None:
                target_state = all_states[-1]
        elif run_to_line is not None:
            # Try exact match first
            for st in all_states:
                info = _find_inst_info(inst_entries, st.nextInstruction)
                if info and info.lineInfo.lineStart == run_to_line:
                    target_state = st
                    target_inst_info = info
                    break
            # If exact line not found, find the nearest reachable line after target
            if target_state is None:
                best_state = None
                best_info = None
                best_line = None
                for st in all_states:
                    info = _find_inst_info(inst_entries, st.nextInstruction)
                    if info and info.lineInfo.lineStart > 0:
                        line = info.lineInfo.lineStart
                        if line >= run_to_line:
                            if best_line is None or line < best_line:
                                best_state = st
                                best_info = info
                                best_line = line
                            if line == best_line:
                                break  # found closest, stop
                if best_state is not None:
                    target_state = best_state
                    target_inst_info = best_info
                    # Will add a note about the line being optimized out
            if target_state is None:
                # Collect which lines were actually executed for diagnosis
                executed_lines = sorted(set(
                    info.lineInfo.lineStart
                    for st in all_states
                    for info in [_find_inst_info(inst_entries, st.nextInstruction)]
                    if info and info.lineInfo.lineStart > 0
                ))
                nearby = [l for l in executed_lines if abs(l - run_to_line) <= 20]
                return {
                    "mode": "step",
                    "error": f"Source line {run_to_line} not reached during execution (may be optimized out)",
                    "total_states": total_states,
                    "nearby_executed_lines": nearby or executed_lines[-10:],
                    "hint": "Try one of the nearby_executed_lines instead",
                }
        else:
            target_state = all_states[-1]

        instruction = target_state.nextInstruction
        if target_inst_info is None:
            target_inst_info = _find_inst_info(inst_entries, instruction)
        inst_src_map = _get_inst_source_var_map(target_inst_info)

        visible_vars = _get_visible_source_vars(target_inst_info, inst_src_map, global_src_map)
        location = _get_step_location(target_inst_info)

        result_dict: dict = {
            "mode": "step",
            "total_states": total_states,
            "stopped_at": {
                "step": target_state.stepIndex,
                "instruction": instruction,
                "source_location": location,
                "callstack": list(target_state.callstack) if target_state.callstack else [],
            },
            "visible_source_variables": visible_vars,
        }
        # Note if we had to snap to a different line
        if run_to_line is not None and location and location.get("line") != run_to_line:
            result_dict["note"] = (
                f"Line {run_to_line} was optimized out by the compiler. "
                f"Stopped at nearest reachable line {location.get('line')} instead."
            )
        return result_dict

    # ── anomaly mode: find first NaN/Inf/negative ──
    if mode == "anomaly":
        for st in all_states:
            instruction = st.nextInstruction
            inst_info = _find_inst_info(inst_entries, instruction)
            inst_src_map = _get_inst_source_var_map(inst_info)

            for change in st.changes:
                sv = _serialize_change(change, inst_src_map, global_src_map)
                if sv and "warning" in sv:
                    location = _get_step_location(inst_info)
                    # Collect context: a few steps before and after
                    step_idx = st.stepIndex
                    context_steps: list[dict] = []
                    for ctx_st in all_states:
                        if abs(ctx_st.stepIndex - step_idx) <= 3:
                            ctx_info = _find_inst_info(inst_entries, ctx_st.nextInstruction)
                            ctx_src_map = _get_inst_source_var_map(ctx_info)
                            ctx_changes = []
                            for c in ctx_st.changes:
                                s = _serialize_change(c, ctx_src_map, global_src_map)
                                if s:
                                    ctx_changes.append(s)
                            if ctx_changes:
                                context_steps.append({
                                    "step": ctx_st.stepIndex,
                                    "source_location": _get_step_location(ctx_info),
                                    "variables": ctx_changes,
                                })
                    return {
                        "mode": "anomaly",
                        "total_states": total_states,
                        "anomaly_found": True,
                        "first_anomaly": {
                            "step": step_idx,
                            "instruction": instruction,
                            "source_location": location,
                            "variable": sv,
                            "callstack": list(st.callstack) if st.callstack else [],
                        },
                        "context": context_steps,
                    }

        return {
            "mode": "anomaly",
            "total_states": total_states,
            "anomaly_found": False,
            "message": "No NaN/Inf values detected during shader execution",
        }

    # ── watch mode: track specific variables ──
    if mode == "watch":
        if not watch_variables:
            return {"error": "watch mode requires watch_variables parameter"}

        watch_lower = [w.lower() for w in watch_variables]
        history: dict[str, list[dict]] = {}  # var_name -> list of {step, value, ...}

        for st in all_states:
            instruction = st.nextInstruction
            inst_info = _find_inst_info(inst_entries, instruction)
            inst_src_map = _get_inst_source_var_map(inst_info)

            # Check source vars visible at this step
            if inst_info:
                for svm in inst_info.sourceVars:
                    if not svm.name or svm.undefinedValue:
                        continue
                    if not any(w in svm.name.lower() for w in watch_lower):
                        continue
                    # Check if any of this source var's debug refs changed
                    dbg_refs = set()
                    for r in svm.variables:
                        if r.name:
                            dbg_refs.add(r.name)
                    for change in st.changes:
                        if change.after.name in dbg_refs:
                            sv = _serialize_change(change, inst_src_map, global_src_map)
                            if sv:
                                key = svm.name
                                if key not in history:
                                    history[key] = []
                                location = _get_step_location(inst_info)
                                history[key].append({
                                    "step": st.stepIndex,
                                    "source_location": location,
                                    "value": sv.get("value"),
                                    **({"warning": sv["warning"]} if "warning" in sv else {}),
                                })
                                break

        return {
            "mode": "watch",
            "total_states": total_states,
            "watched_variables": {k: v for k, v in history.items()},
            "variables_found": len(history),
        }

    # ── summary mode (default): compact overview ──
    # Collect: inputs, outputs, unique callstack functions, anomalies, branch count
    inputs: list[dict] = []
    try:
        for inp in trace.inputs:
            if inp.name:
                inputs.append(serialize_shader_variable(inp))
    except Exception:
        pass

    # Get final state's visible source vars as "outputs"
    last_state = all_states[-1]
    last_inst_info = _find_inst_info(inst_entries, last_state.nextInstruction)
    last_src_map = _get_inst_source_var_map(last_inst_info)
    final_visible = _get_visible_source_vars(last_inst_info, last_src_map, global_src_map)

    # Unique functions in callstack
    all_functions: list[str] = []
    seen_funcs: set[str] = set()
    for st in all_states:
        if st.callstack:
            for fn in st.callstack:
                if fn not in seen_funcs:
                    seen_funcs.add(fn)
                    all_functions.append(fn)

    # Find anomalies
    anomalies: list[dict] = []
    for st in all_states:
        inst_info = _find_inst_info(inst_entries, st.nextInstruction)
        inst_src_map = _get_inst_source_var_map(inst_info)
        for change in st.changes:
            sv = _serialize_change(change, inst_src_map, global_src_map)
            if sv and "warning" in sv:
                anomalies.append({
                    "step": st.stepIndex,
                    "source_location": _get_step_location(inst_info),
                    "variable": sv,
                })
                if len(anomalies) >= 10:
                    break
        if len(anomalies) >= 10:
            break

    # Unique source lines executed (for branch analysis)
    executed_lines: set[int] = set()
    for st in all_states:
        inst_info = _find_inst_info(inst_entries, st.nextInstruction)
        if inst_info and inst_info.lineInfo.lineStart > 0:
            executed_lines.add(inst_info.lineInfo.lineStart)

    result: dict = {
        "mode": "summary",
        "total_states": total_states,
        "unique_source_lines_executed": len(executed_lines),
        "functions_called": all_functions,
        "inputs": inputs[:20],
        "final_visible_variables": final_visible[:30],
    }
    if anomalies:
        result["anomalies"] = anomalies
        result["anomaly_count"] = len(anomalies)
    else:
        result["anomalies"] = []

    return result


def register(mcp: FastMCP):

    @mcp.tool()
    def sample_pixel_region(
        event_id: Optional[int] = None,
        resource_id: Optional[str] = None,
        region: Optional[dict] = None,
        sample_count: int = 256,
        anomaly_threshold: float = 10.0,
    ) -> str:
        """Batch-sample pixels from a render target and auto-detect anomalies.

        Scans for NaN / Inf / negative values and extreme-bright pixels.
        Best tool for locating anomalous color values and IBL / HDR issues.

        Args:
            event_id: Sample at this event's render target state. Uses current event if omitted.
            resource_id: Specific render target resource ID. If omitted, uses the first
                         color output of the current event.
            region: Optional sampling region {"x":0,"y":0,"width":W,"height":H}.
                    If omitted, samples the full texture uniformly.
            sample_count: Number of sample points (default 256, max 1024).
            anomaly_threshold: Pixels with any channel exceeding this value are flagged
                               as extreme-bright (useful for HDR targets). Default 10.0.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        # Resolve render target
        if resource_id is not None:
            tex_id = session.resolve_resource_id(resource_id)
            if tex_id is None:
                return to_json(make_error(f"Resource '{resource_id}' not found", "INVALID_RESOURCE_ID"))
            tex_desc = session.get_texture_desc(resource_id)
        else:
            state = session.controller.GetPipelineState()
            try:
                outputs = state.GetOutputTargets()
                color_target = next((o for o in outputs if int(o.resource) != 0), None)
            except Exception:
                color_target = None
            if color_target is None:
                return to_json(make_error("No color render target at current event", "API_ERROR"))
            rid_str = str(color_target.resource)
            tex_id = session.resolve_resource_id(rid_str)
            tex_desc = session.get_texture_desc(rid_str)
            resource_id = rid_str

        if tex_desc is None:
            return to_json(make_error("Could not get texture description", "API_ERROR"))

        # Determine sampling region
        tex_w, tex_h = tex_desc.width, tex_desc.height
        if region:
            rx = max(0, region.get("x", 0))
            ry = max(0, region.get("y", 0))
            rw = min(region.get("width", tex_w), tex_w - rx)
            rh = min(region.get("height", tex_h), tex_h - ry)
        else:
            rx, ry, rw, rh = 0, 0, tex_w, tex_h

        sample_count = min(sample_count, 1024)

        # Generate a uniform grid of sample points
        import math as _math
        cols = max(1, int(_math.sqrt(sample_count * rw / max(rh, 1))))
        rows = max(1, sample_count // cols)
        step_x = max(1, rw // cols)
        step_y = max(1, rh // rows)

        sample_points: list[tuple[int, int]] = []
        for r in range(rows):
            for c in range(cols):
                px = rx + c * step_x + step_x // 2
                py = ry + r * step_y + step_y // 2
                if px < rx + rw and py < ry + rh:
                    sample_points.append((px, py))

        nan_count = inf_count = neg_count = bright_count = 0
        hotspots: list[dict] = []
        values_r: list[float] = []
        values_g: list[float] = []
        values_b: list[float] = []

        for px, py in sample_points:
            try:
                val = session.controller.PickPixel(
                    tex_id, px, py, rd.Subresource(0, 0, 0), rd.CompType.Typeless
                )
                r, g, b = val.floatValue[0], val.floatValue[1], val.floatValue[2]
            except Exception:
                continue

            pixel = [round(r, 6), round(g, 6), round(b, 6)]
            anomaly_type: str | None = None

            if any(_math.isnan(v) for v in [r, g, b]):
                nan_count += 1
                anomaly_type = "NaN"
            elif any(_math.isinf(v) for v in [r, g, b]):
                inf_count += 1
                anomaly_type = "Inf"
                pixel = ["+Inf" if _math.isinf(v) and v > 0 else ("-Inf" if _math.isinf(v) else v) for v in pixel]
            elif any(v < 0 for v in [r, g, b]):
                neg_count += 1
                anomaly_type = "negative"
            elif max(r, g, b) > anomaly_threshold:
                bright_count += 1
                anomaly_type = "extreme_bright"

            if anomaly_type:
                hotspots.append({"pixel": [px, py], "value": pixel, "type": anomaly_type})

            if not any(_math.isnan(v) or _math.isinf(v) for v in [r, g, b]):
                values_r.append(r)
                values_g.append(g)
                values_b.append(b)

        total = len(sample_points)
        anomaly_total = nan_count + inf_count + neg_count + bright_count

        stats: dict = {}
        for ch, vals in [("r", values_r), ("g", values_g), ("b", values_b)]:
            if vals:
                stats[ch] = {
                    "min": round(min(vals), 6),
                    "max": round(max(vals), 6),
                    "mean": round(sum(vals) / len(vals), 6),
                }

        # Limit hotspots returned
        MAX_HOTSPOTS = 20
        hotspots_out = hotspots[:MAX_HOTSPOTS]

        # Build diagnosis hint
        hints: list[str] = []
        if inf_count > 0:
            hints.append(f"Inf 像素 {inf_count} 个——建议对这些像素执行 pixel_history 追踪来源")
        if neg_count > 0:
            hints.append(f"负值像素 {neg_count} 个——可能来自 IBL SH 采样或 HDR 溢出，建议检查写入该 RT 的 draw call")
        if nan_count > 0:
            hints.append(f"NaN 像素 {nan_count} 个——通常由除以零或 0/0 运算引起")
        if bright_count > 0:
            hints.append(f"极亮像素 {bright_count} 个（>{anomaly_threshold}）——可能溢出或曝光异常")

        result = {
            "resource_id": resource_id,
            "render_target": f"{getattr(tex_desc, 'name', None) or resource_id} ({tex_desc.format.Name()}) {tex_w}x{tex_h}",
            "total_samples": total,
            "anomalies": {
                "nan_count": nan_count,
                "inf_count": inf_count,
                "negative_count": neg_count,
                "extreme_bright_count": bright_count,
                "anomaly_total": anomaly_total,
                "anomaly_rate": f"{anomaly_total / max(total, 1) * 100:.1f}%",
            },
            "hotspots": hotspots_out,
            "statistics": stats,
        }
        if hints:
            result["diagnosis_hint"] = " | ".join(hints)
        return to_json(result)

    @mcp.tool()
    def debug_shader_at_pixel(
        event_id: int,
        pixel_x: int,
        pixel_y: int,
        stage: str = "pixel",
        mode: str = "summary",
        watch_variables: Optional[list[str]] = None,
        run_to_step: Optional[int] = None,
        run_to_line: Optional[int] = None,
    ) -> str:
        """Debug the shader at a specific pixel using RenderDoc's shader debugger.

        Args:
            event_id: The event ID of the draw call.
            pixel_x: X coordinate (render target space, origin top-left).
            pixel_y: Y coordinate.
            stage: Shader stage to debug: "pixel" (default) or "vertex".
            mode: Debug analysis mode:
                  "summary" (default) - compact overview: inputs, outputs, anomalies, call chain.
                  "watch" - track specific variables (requires watch_variables).
                  "anomaly" - find first NaN/Inf and return context around it.
                  "step" - run to specific step/line, show all visible variables.
            watch_variables: Variable names to track (for "watch" mode, also filters other modes).
            run_to_step: Stop at this step index (for "step" mode).
            run_to_line: Stop at this source line number (for "step" mode).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.set_event(event_id)
        if err:
            return to_json(err)

        stage_enum = SHADER_STAGE_MAP.get(stage.lower())
        if stage_enum is None:
            return to_json(make_error(f"Unknown shader stage: {stage}", "API_ERROR"))

        state = session.controller.GetPipelineState()
        refl = state.GetShaderReflection(stage_enum)
        if refl is None:
            return to_json(make_error(f"No shader bound at stage '{stage}'", "API_ERROR"))

        # Collect pixel value before debugging
        pixel_val_info: dict = {}
        try:
            outputs = state.GetOutputTargets()
            if outputs:
                first_rt = next((o for o in outputs if int(o.resource) != 0), None)
                if first_rt:
                    rt_id = first_rt.resource
                    pv = session.controller.PickPixel(
                        rt_id, pixel_x, pixel_y,
                        rd.Subresource(0, 0, 0), rd.CompType.Typeless
                    )
                    pixel_val_info = {
                        "current_pixel_rgba": [
                            round(pv.floatValue[0], 6), round(pv.floatValue[1], 6),
                            round(pv.floatValue[2], 6), round(pv.floatValue[3], 6),
                        ],
                    }
                    r, g, b = pv.floatValue[0], pv.floatValue[1], pv.floatValue[2]
                    anomalies = []
                    if any(math.isnan(v) for v in [r, g, b]):
                        anomalies.append("NaN detected in output pixel")
                    if any(math.isinf(v) for v in [r, g, b]):
                        anomalies.append("Inf detected in output pixel")
                    if any(v < 0 for v in [r, g, b]):
                        anomalies.append(f"Negative value in output: [{r:.4f}, {g:.4f}, {b:.4f}]")
                    if anomalies:
                        pixel_val_info["pixel_anomalies"] = anomalies
        except Exception:
            pass

        # Attempt shader debugging
        trace_result: dict | None = None
        debug_error: str | None = None
        try:
            if stage_enum == rd.ShaderStage.Pixel:
                trace = session.controller.DebugPixel(
                    pixel_x, pixel_y,
                    rd.DebugPixelInputs(),
                )
            else:
                trace = None

            if trace is not None and trace.debugger is not None:
                trace_result = _run_shader_debug(
                    session, trace, mode=mode,
                    watch_variables=watch_variables,
                    run_to_step=run_to_step, run_to_line=run_to_line,
                )
                try:
                    session.controller.FreeTrace(trace)
                except Exception:
                    pass
            else:
                debug_error = "DebugPixel returned no debugger (may not be supported for this API/GPU)"
                if trace is not None:
                    try:
                        session.controller.FreeTrace(trace)
                    except Exception:
                        pass
        except Exception as e:
            debug_error = f"Shader debugging failed: {type(e).__name__}: {e}"

        result: dict = {
            "event_id": event_id,
            "pixel": [pixel_x, pixel_y],
            "stage": stage,
            "shader_resource_id": str(refl.resourceId),
            "entry_point": refl.entryPoint,
        }
        result.update(pixel_val_info)

        if trace_result is not None:
            result["debug_trace"] = trace_result
        else:
            result["debug_note"] = debug_error or "Shader trace unavailable"
            result["fallback_info"] = {
                "constant_blocks": [cb.name for cb in refl.constantBlocks],
                "read_only_resources": [r.name for r in refl.readOnlyResources],
                "suggestion": "Use get_cbuffer_contents and pixel_history for manual investigation",
            }

        return to_json(result)

    @mcp.tool()
    def debug_vertex(
        event_id: int,
        vertex_id: int,
        instance_id: int = 0,
        index: int = 0,
        view: int = 0,
        mode: str = "summary",
        watch_variables: Optional[list[str]] = None,
        run_to_step: Optional[int] = None,
        run_to_line: Optional[int] = None,
    ) -> str:
        """Debug the vertex shader at a specific vertex using RenderDoc's shader debugger.

        Args:
            event_id: The event ID of the draw call.
            vertex_id: The vertex ID to debug.
            instance_id: Instance ID (default 0).
            index: Index value (default 0).
            view: Multiview index (default 0).
            mode: Debug analysis mode:
                  "summary" (default) - compact overview: inputs, outputs, anomalies, call chain.
                  "watch" - track specific variables (requires watch_variables).
                  "anomaly" - find first NaN/Inf and return context around it.
                  "step" - run to specific step/line, show all visible variables.
            watch_variables: Variable names to track (for "watch" mode, also filters other modes).
            run_to_step: Stop at this step index (for "step" mode).
            run_to_line: Stop at this source line number (for "step" mode).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.set_event(event_id)
        if err:
            return to_json(err)

        state = session.controller.GetPipelineState()
        refl = state.GetShaderReflection(rd.ShaderStage.Vertex)
        if refl is None:
            return to_json(make_error("No vertex shader bound at this event", "API_ERROR"))

        result: dict = {
            "event_id": event_id,
            "vertex_id": vertex_id,
            "instance_id": instance_id,
            "stage": "vertex",
            "shader_resource_id": str(refl.resourceId),
            "entry_point": refl.entryPoint,
        }

        # Attempt shader debugging
        trace_result: dict | None = None
        debug_error: str | None = None
        try:
            trace = session.controller.DebugVertex(vertex_id, instance_id, index, view)

            if trace is not None and trace.debugger is not None:
                trace_result = _run_shader_debug(
                    session, trace, mode=mode,
                    watch_variables=watch_variables,
                    run_to_step=run_to_step, run_to_line=run_to_line,
                )
                try:
                    session.controller.FreeTrace(trace)
                except Exception:
                    pass
            else:
                debug_error = "DebugVertex returned no debugger (may not be supported for this API/GPU)"
                if trace is not None:
                    try:
                        session.controller.FreeTrace(trace)
                    except Exception:
                        pass
        except Exception as e:
            debug_error = f"Vertex shader debugging failed: {type(e).__name__}: {e}"

        if trace_result is not None:
            result["debug_trace"] = trace_result
        else:
            result["debug_note"] = debug_error or "Shader trace unavailable"
            result["fallback_info"] = {
                "input_signature": [s.semanticIdxName or s.varName for s in refl.inputSignature],
                "output_signature": [s.semanticIdxName or s.varName for s in refl.outputSignature],
                "suggestion": "Use get_post_vs_data to inspect transformed vertices, or get_cbuffer_contents to check constants",
            }

        return to_json(result)

    @mcp.tool()
    def debug_compute_thread(
        event_id: int,
        group_id: Optional[list[int]] = None,
        thread_id: Optional[list[int]] = None,
        mode: str = "summary",
        watch_variables: Optional[list[str]] = None,
        run_to_step: Optional[int] = None,
        run_to_line: Optional[int] = None,
    ) -> str:
        """Debug a compute shader thread using RenderDoc's shader debugger.

        Args:
            event_id: The event ID of the dispatch call.
            group_id: Work group ID as [x, y, z] (default [0, 0, 0]).
            thread_id: Thread ID within the group as [x, y, z] (default [0, 0, 0]).
            mode: Debug analysis mode:
                  "summary" (default) - compact overview: inputs, outputs, anomalies, call chain.
                  "watch" - track specific variables (requires watch_variables).
                  "anomaly" - find first NaN/Inf and return context around it.
                  "step" - run to specific step/line, show all visible variables.
            watch_variables: Variable names to track (for "watch" mode, also filters other modes).
            run_to_step: Stop at this step index (for "step" mode).
            run_to_line: Stop at this source line number (for "step" mode).
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.set_event(event_id)
        if err:
            return to_json(err)

        state = session.controller.GetPipelineState()
        refl = state.GetShaderReflection(rd.ShaderStage.Compute)
        if refl is None:
            return to_json(make_error("No compute shader bound at this event", "API_ERROR"))

        gid = group_id or [0, 0, 0]
        tid = thread_id or [0, 0, 0]
        # Ensure 3-element tuples
        gid = (gid + [0, 0, 0])[:3]
        tid = (tid + [0, 0, 0])[:3]

        result: dict = {
            "event_id": event_id,
            "group_id": gid,
            "thread_id": tid,
            "stage": "compute",
            "shader_resource_id": str(refl.resourceId),
            "entry_point": refl.entryPoint,
        }

        trace_result: dict | None = None
        debug_error: str | None = None
        try:
            trace = session.controller.DebugThread(tuple(gid), tuple(tid))

            if trace is not None and trace.debugger is not None:
                trace_result = _run_shader_debug(
                    session, trace, mode=mode,
                    watch_variables=watch_variables,
                    run_to_step=run_to_step, run_to_line=run_to_line,
                )
                try:
                    session.controller.FreeTrace(trace)
                except Exception:
                    pass
            else:
                debug_error = "DebugThread returned no debugger (may not be supported for this API/GPU)"
                if trace is not None:
                    try:
                        session.controller.FreeTrace(trace)
                    except Exception:
                        pass
        except Exception as e:
            debug_error = f"Compute shader debugging failed: {type(e).__name__}: {e}"

        if trace_result is not None:
            result["debug_trace"] = trace_result
        else:
            result["debug_note"] = debug_error or "Shader trace unavailable"
            result["fallback_info"] = {
                "constant_blocks": [cb.name for cb in refl.constantBlocks],
                "read_write_resources": [rw.name for rw in refl.readWriteResources],
                "suggestion": "Use get_cbuffer_contents and get_shader_bindings to inspect compute shader state",
            }

        return to_json(result)

    @mcp.tool()
    def pixel_history(
        resource_id: str,
        x: int,
        y: int,
        event_id: Optional[int] = None,
    ) -> str:
        """Get the full modification history of a pixel across all events in the frame.

        Shows every event that wrote to this pixel, with before/after values and
        pass/fail status (depth test, stencil test, etc.).

        Args:
            resource_id: The texture resource ID (must be a render target).
            x: X coordinate of the pixel.
            y: Y coordinate of the pixel.
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

        history = session.controller.PixelHistory(
            tex_id, x, y, rd.Subresource(0, 0, 0), rd.CompType.Typeless,
        )

        results = []
        for mod in history:
            passed = mod.Passed()
            entry: dict = {
                "event_id": mod.eventId,
                "passed": passed,
            }
            # Add failure reasons when the modification did not pass
            if not passed:
                failure_reasons = []
                try:
                    if mod.backfaceCulled:
                        failure_reasons.append("backface_culled")
                    if mod.depthTestFailed:
                        failure_reasons.append("depth_test_failed")
                    if mod.stencilTestFailed:
                        failure_reasons.append("stencil_test_failed")
                    if mod.scissorClipped:
                        failure_reasons.append("scissor_clipped")
                    if mod.shaderDiscarded:
                        failure_reasons.append("shader_discarded")
                    if mod.depthClipped:
                        failure_reasons.append("depth_clipped")
                except Exception:
                    pass
                if failure_reasons:
                    entry["failure_reasons"] = failure_reasons
            # Pre-modification value
            pre = mod.preMod
            entry["pre_value"] = {
                "r": pre.col.floatValue[0],
                "g": pre.col.floatValue[1],
                "b": pre.col.floatValue[2],
                "a": pre.col.floatValue[3],
                "depth": pre.depth,
                "stencil": pre.stencil,
            }
            # Post-modification value
            post = mod.postMod
            entry["post_value"] = {
                "r": post.col.floatValue[0],
                "g": post.col.floatValue[1],
                "b": post.col.floatValue[2],
                "a": post.col.floatValue[3],
                "depth": post.depth,
                "stencil": post.stencil,
            }

            # Check if pixel actually changed
            entry["pixel_changed"] = (
                pre.col.floatValue[0] != post.col.floatValue[0]
                or pre.col.floatValue[1] != post.col.floatValue[1]
                or pre.col.floatValue[2] != post.col.floatValue[2]
                or pre.col.floatValue[3] != post.col.floatValue[3]
            )

            results.append(entry)

        return to_json({
            "resource_id": resource_id,
            "x": x,
            "y": y,
            "modifications": results,
            "count": len(results),
        })

    @mcp.tool()
    def get_post_vs_data(
        stage: str = "vsout",
        max_vertices: int = 100,
        event_id: Optional[int] = None,
    ) -> str:
        """Get post-vertex-shader transformed vertex data for the current draw call.

        Args:
            stage: Data stage: "vsin" (vertex input), "vsout" (after vertex shader),
                  "gsout" (after geometry shader). Default: "vsout".
            max_vertices: Maximum number of vertices to return (default 100).
            event_id: Optional event ID to navigate to first.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)
        if err:
            return to_json(err)

        mesh_stage = MESH_DATA_STAGE_MAP.get(stage.lower())
        if mesh_stage is None:
            return to_json(make_error(
                f"Unknown mesh stage: {stage}. Valid: {list(MESH_DATA_STAGE_MAP.keys())}",
                "API_ERROR",
            ))

        postvs = session.controller.GetPostVSData(0, 0, mesh_stage)

        if postvs.vertexResourceId == rd.ResourceId.Null():
            return to_json(make_error("No post-VS data available for current event", "API_ERROR"))

        # Get the output signature to know the attribute layout
        state = session.controller.GetPipelineState()
        if stage.lower() == "vsin":
            # For vertex input, use vertex input attributes
            attrs = state.GetVertexInputs()
            attr_info = [{"name": a.name, "format": str(a.format.Name())} for a in attrs]
        else:
            # Use the appropriate shader stage reflection for output signature
            refl_stage = rd.ShaderStage.Vertex
            if stage.lower() == "gsout":
                gs_refl = state.GetShaderReflection(rd.ShaderStage.Geometry)
                if gs_refl is not None:
                    refl_stage = rd.ShaderStage.Geometry
            vs_refl = state.GetShaderReflection(refl_stage)
            if vs_refl is None:
                return to_json(make_error("No shader bound for requested stage", "API_ERROR"))
            attr_info = []
            for sig in vs_refl.outputSignature:
                name = sig.semanticIdxName if sig.varName == "" else sig.varName
                attr_info.append({
                    "name": name,
                    "var_type": str(sig.varType),
                    "comp_count": sig.compCount,
                    "system_value": str(sig.systemValue),
                })

        # Read vertex data
        num_verts = min(postvs.numIndices, max_vertices)
        data = session.controller.GetBufferData(
            postvs.vertexResourceId, postvs.vertexByteOffset,
            num_verts * postvs.vertexByteStride,
        )

        # Parse vertices as float arrays
        vertices = []
        floats_per_vertex = postvs.vertexByteStride // 4
        if floats_per_vertex == 0:
            return to_json(make_error(
                f"Invalid vertex stride ({postvs.vertexByteStride} bytes), cannot parse vertex data",
                "API_ERROR",
            ))
        for i in range(num_verts):
            offset = i * postvs.vertexByteStride
            if offset + postvs.vertexByteStride > len(data):
                break
            vertex_floats = list(struct.unpack_from(
                f"{floats_per_vertex}f", data, offset
            ))
            vertices.append([round(f, 6) for f in vertex_floats])

        return to_json({
            "stage": stage,
            "event_id": session.current_event,
            "attributes": attr_info,
            "vertex_stride": postvs.vertexByteStride,
            "total_vertices": postvs.numIndices,
            "returned_vertices": len(vertices),
            "vertices": vertices,
        })

    @mcp.tool()
    def diff_draw_calls(eid1: int, eid2: int) -> str:
        """Compare two draw calls and return their state differences.

        Useful for understanding what changed between two similar draw calls.

        Args:
            eid1: Event ID of the first draw call.
            eid2: Event ID of the second draw call.
        """
        from renderdoc_mcp.tools.pipeline_tools import _get_draw_state_dict

        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        state1 = _get_draw_state_dict(session, eid1)
        if "error" in state1:
            return to_json(state1)

        state2 = _get_draw_state_dict(session, eid2)
        if "error" in state2:
            return to_json(state2)

        raw_diff = _diff_dicts(state1, state2)
        differences = _add_implications(raw_diff)

        return to_json({
            "eid1": eid1,
            "eid2": eid2,
            "differences": differences,
            "identical": len(differences) == 0,
            "summary": (
                f"发现 {len(differences)} 处差异" if differences
                else "两个 draw call 的 pipeline state 完全相同"
            ),
        })

    @mcp.tool()
    def analyze_render_passes() -> str:
        """Auto-detect render pass boundaries and summarize each pass.

        Detects passes by Clear actions and output target changes.
        Returns a list of render passes with draw count, RT info, and event range.
        """
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)

        sf = session.structured_file
        passes: list[dict] = []
        current_pass: dict | None = None
        last_outputs: tuple | None = None

        for eid in sorted(session.action_map.keys()):
            action = session.action_map[eid]
            is_clear = bool(action.flags & rd.ActionFlags.Clear)
            is_draw = bool(action.flags & rd.ActionFlags.Drawcall)

            if not is_clear and not is_draw:
                continue

            # Determine current outputs
            outputs = tuple(str(o) for o in action.outputs if int(o) != 0)

            # Detect pass boundary: clear or output target change
            new_pass = False
            if is_clear:
                new_pass = True
            elif outputs and outputs != last_outputs:
                new_pass = True

            if new_pass:
                # Start a new pass
                if current_pass is not None:
                    passes.append(current_pass)
                current_pass = {
                    "pass_index": len(passes),
                    "start_event": eid,
                    "end_event": eid,
                    "start_action": action.GetName(sf),
                    "draw_count": 0,
                    "clear_count": 0,
                    "render_targets": list(outputs) if outputs else [],
                }

            if current_pass is None:
                current_pass = {
                    "pass_index": 0,
                    "start_event": eid,
                    "end_event": eid,
                    "start_action": action.GetName(sf),
                    "draw_count": 0,
                    "clear_count": 0,
                    "render_targets": list(outputs) if outputs else [],
                }

            current_pass["end_event"] = eid
            if is_draw:
                current_pass["draw_count"] += 1
            if is_clear:
                current_pass["clear_count"] += 1

            if outputs:
                last_outputs = outputs
                # Update RT info if changed within pass
                for o in outputs:
                    if o not in current_pass["render_targets"]:
                        current_pass["render_targets"].append(o)

        if current_pass is not None:
            passes.append(current_pass)

        # Enrich with RT size info
        for p in passes:
            rt_info = []
            for rid_str in p["render_targets"]:
                entry: dict = {"resource_id": rid_str}
                tex_desc = session.get_texture_desc(rid_str)
                if tex_desc is not None:
                    entry["size"] = f"{tex_desc.width}x{tex_desc.height}"
                    entry["format"] = str(tex_desc.format.Name())
                rt_info.append(entry)
            p["render_target_info"] = rt_info

        return to_json({
            "passes": passes,
            "total_passes": len(passes),
        })


def _diff_dicts(d1: dict, d2: dict, path: str = "") -> dict:
    """Recursively diff two dicts, returning only differing keys."""
    diff: dict = {}
    all_keys = set(d1.keys()) | set(d2.keys())

    for key in all_keys:
        key_path = f"{path}.{key}" if path else key
        v1 = d1.get(key)
        v2 = d2.get(key)

        if v1 == v2:
            continue

        if isinstance(v1, dict) and isinstance(v2, dict):
            sub = _diff_dicts(v1, v2, key_path)
            if sub:
                diff[key] = sub
        elif isinstance(v1, list) and isinstance(v2, list):
            if v1 != v2:
                diff[key] = {"eid1": v1, "eid2": v2}
        else:
            diff[key] = {"eid1": v1, "eid2": v2}

    return diff


# ── Implication rules for diff_draw_calls ──
# Each entry: (field_path_suffix, implication_template)
def _implication_for(suffix: str, v1, v2) -> str | None:
    """Return a human-readable implication string for a known diff field."""
    s1, s2 = str(v1), str(v2)
    if suffix == "blend.enabled":
        detail = "透明度混合激活，颜色会与背景混合叠加" if v2 else "关闭 blend，输出将直接覆盖目标像素（不透明模式）"
        return f"Blend toggle: {s1}→{s2}。{detail}"
    if suffix == "blend.color_src":
        return f"颜色源混合因子: {s1}→{s2}。可能导致输出颜色亮度/透明度变化"
    if suffix == "blend.color_dst":
        return f"颜色目标混合因子: {s1}→{s2}。影响背景色在最终结果中的权重"
    if suffix == "blend.color_op":
        return f"颜色混合运算: {s1}→{s2}。运算方式改变可能导致颜色整体偏移"
    if suffix == "blend.alpha_src":
        return f"Alpha 源混合因子: {s1}→{s2}"
    if suffix == "depth.test":
        detail = "关闭后物体可能穿透其他几何体" if not v2 else "开启后需要确保深度 buffer 正确初始化"
        return f"深度测试: {s1}→{s2}。{detail}"
    if suffix == "depth.write":
        detail = "关闭后该 draw call 不更新深度 buffer，适用于透明物体" if not v2 else "开启后会更新深度值"
        return f"深度写入: {s1}→{s2}。{detail}"
    if suffix == "depth.func":
        return f"深度比较函数: {s1}→{s2}。可能导致物体遮挡关系或消失问题"
    if suffix == "stencil.enabled":
        return f"模板测试: {s1}→{s2}"
    if suffix == "rasterizer.cull":
        return f"剔除模式: {s1}→{s2}。可能影响背面/正面可见性，倒影 pass 通常需要反转 cull"
    if suffix == "rasterizer.front_ccw":
        return f"正面朝向: {s1}→{s2}。CCW/CW 切换后背面剔除方向翻转"
    if suffix == "topology":
        return f"图元拓扑: {s1}→{s2}"
    return None

_IMPLICATION_SUFFIXES = [
    "blend.enabled", "blend.color_src", "blend.color_dst", "blend.color_op",
    "blend.alpha_src", "depth.test", "depth.write", "depth.func",
    "stencil.enabled", "rasterizer.cull", "rasterizer.front_ccw", "topology",
]


def _add_implications(diff: dict) -> list[dict]:
    """Flatten a nested diff dict into a list with implication annotations."""
    results: list[dict] = []

    def _flatten(d: dict, path: str):
        for key, val in d.items():
            key_path = f"{path}.{key}" if path else key
            if isinstance(val, dict):
                if "eid1" in val or "eid2" in val:
                    v1 = val.get("eid1")
                    v2 = val.get("eid2")
                    entry: dict = {"field": key_path, "eid1": v1, "eid2": v2}
                    for suffix in _IMPLICATION_SUFFIXES:
                        if key_path.endswith(suffix):
                            imp = _implication_for(suffix, v1, v2)
                            if imp:
                                entry["implication"] = imp
                            break
                    results.append(entry)
                else:
                    _flatten(val, key_path)

    _flatten(diff, "")
    return results
