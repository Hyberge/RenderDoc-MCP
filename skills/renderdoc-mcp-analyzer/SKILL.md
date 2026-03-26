---
name: renderdoc-mcp-analyzer
description: "This skill provides comprehensive knowledge for developing, extending, debugging, and maintaining the RenderDoc MCP Server -- a Python-based MCP server (54 tools, 3 prompts) that enables AI assistants to analyze GPU frame captures (.rdc files) for graphics debugging and performance analysis. This skill should be used when working on renderdoc-mcp source code, adding new tools, fixing bugs in existing tools, understanding the architecture, or extending diagnostic capabilities. It covers the full codebase (server, session, runtime, util, 10 tool modules), the RenderDoc Python replay API, and common graphics debugging workflows (pipeline state, shader analysis, pixel diagnostics, performance profiling, mobile GPU risk assessment)."
---

# RenderDoc MCP Analyzer Skill

## Overview

The RenderDoc MCP Server is a Python-based Model Context Protocol server enabling AI-assisted GPU frame capture analysis. It provides **54 tools** across 10 categories and **3 built-in prompt templates** for guided debugging workflows.

### When to Use This Skill

- Developing new tools or extending existing tools in the renderdoc-mcp codebase
- Debugging issues with MCP tool implementations (incorrect API calls, serialization errors)
- Understanding the architecture: how session management, tool registration, and data flow work
- Adding support for new RenderDoc Python API features
- Optimizing diagnostic tools (negative value scanning, precision checks, mobile risk assessment)
- Working on any file under `src/renderdoc_mcp/`

## Architecture

```
AI Client ←—MCP (stdio)—→ FastMCP Server (server.py)
                              │
                              ├── RenderDocSession (session.py) — singleton lifecycle manager
                              │     ├── CaptureFile handle
                              │     ├── ReplayController handle
                              │     ├── action_map cache (eventId → ActionDescription)
                              │     ├── event_map cache (eventId → chunkIndex, all API events)
                              │     ├── resource_id_cache (str → ResourceId)
                              │     └── texture_desc_cache (str → TextureDescription)
                              │
                              ├── runtime.py — timeout guard, large-result cache, cached-message query
                              ├── util.py — module loading, enum maps, serialization helpers
                              │
                              └── tools/ — 10 modules, each with register(mcp) pattern
                                    ├── session_tools.py      (4 tools)
                                    ├── event_tools.py        (7 tools)
                                    ├── pipeline_tools.py     (4 tools)
                                    ├── resource_tools.py     (4 tools)
                                    ├── data_tools.py         (9 tools)
                                    ├── shader_tools.py       (3 tools)
                                    ├── advanced_tools.py     (8 tools)
                                    ├── performance_tools.py  (4 tools)
                                    ├── diagnostic_tools.py   (4 tools)
                                    └── inspection_tools.py   (7 tools)

```

## Key Development Patterns

### Tool Registration Pattern

Every tool module follows this exact structure:

```python
from renderdoc_mcp.session import get_session
from renderdoc_mcp.util import rd, to_json, make_error

def register(mcp: FastMCP):
    @mcp.tool()
    def tool_name(param: type, optional_param: Optional[type] = None) -> str:
        """Description. Args: section in docstring."""
        session = get_session()
        err = session.require_open()       # Always check capture is open
        if err:
            return to_json(err)
        err = session.ensure_event(event_id)  # If tool needs event context
        if err:
            return to_json(err)
        # ... business logic using session.controller ...
        return to_json(result_dict)
```

### Critical Implementation Rules

1. **All tools return JSON strings** via `to_json()` — never raw dicts
2. **Always call `require_open()` first** — returns error dict if no capture loaded
3. **Use `ensure_event()` for pipeline queries** — many tools accept optional `event_id`
4. **Resource IDs are strings** — use `session.resolve_resource_id(str)` to get `ResourceId` objects
5. **Error handling**: wrap RenderDoc API calls in try/except, append warnings to result
6. **Restore state**: tools like `find_draws` and `analyze_state_changes` save/restore `current_event`
7. **Enum values**: always convert to human-readable names using `enum_str()` and the mapping dicts in `util.py`. Never use `str(enum).replace("EnumClass.", "")` — SWIG enums don't include class prefix in str()
8. **Anomaly detection**: tools that read pixel data should flag NaN/Inf/negative values automatically
9. **Timeouts are automatic**: all tools are wrapped by `runtime.install_tool_runtime_guards()` before registration
10. **Oversized payloads are cached**: `to_json()` auto-caches large results and returns a `message_id` for `get_cached_message`

### PipeState API Pitfalls (Common Mistakes)

The `PipeState` object from `controller.GetPipelineState()` is API-agnostic but has a limited interface. These are the most common mistakes:

| Wrong (does NOT exist) | Correct | Notes |
|------------------------|---------|-------|
| `state.GetViewports()` | `state.GetViewport(index)` | Returns single Viewport; iterate index 0..N |
| `state.GetScissors()` | `state.GetScissor(index)` | Returns single Scissor; iterate index 0..N |
| `state.GetColorBlend()` | `state.GetColorBlends()` | Returns `List[ColorBlend]` directly (no `.blends` sub-field) |
| `state.GetStencilState()` | `state.GetStencilFaces()` | Returns `(front: StencilFace, back: StencilFace)` pair |
| `state.GetDepthState()` | API-specific state | Use `GetD3D12PipelineState().outputMerger.depthStencilState` etc. |
| `state.GetRasterizer()` | API-specific state | Use `GetD3D12PipelineState().rasterizer.state` etc. |

For depth/rasterizer state, use `_get_depth_state(state)` and `_get_rasterizer_state(state)` helpers in `pipeline_tools.py` — they handle all 4 APIs (D3D11/D3D12/Vulkan/GL).

### RenderDoc API Gotchas

- `GetFrameInfo()` returns a **single** `FrameDescription`, NOT a list
- `GetHistogram()` takes channels as a **tuple** `(R,G,B,A)`, not 4 separate bools (6 args total, not 9)
- `FrameStatistics.recorded` is only `True` for D3D11 captures; D3D12/Vulkan/GL don't collect frame statistics
- SWIG sub-objects (e.g. `DrawcallStats`, `DispatchStats`) are not JSON-serializable — extract their numeric attributes manually
- `GetCBufferVariableContents()` requires correct `byteOffset` and `byteSize` from `cb_bind.descriptor.byteOffset/byteSize`. Passing `0, 0` causes all-zero reads on D3D12 because cbuffers are typically bound at non-zero offsets within a large buffer (via Root CBV / Descriptor Table). This matches RenderDoc GUI behavior in `BufferViewer.cpp:3745`


### Session Lifecycle

```
open_capture(path)
  → rd.InitialiseReplay() (first time only)
  → rd.OpenCaptureFile() → cap.OpenFile() → cap.OpenCapture()
  → Build action_map, event_map, resource_id_cache, texture_desc_cache

set_event(eid) / ensure_event(eid)
  → controller.SetFrameEvent(eid, True)
  → Updates session._current_event

Pipeline queries (after set_event):
  → controller.GetPipelineState() → state.Get*()

close_capture()
  → controller.Shutdown(), cap.Shutdown()
  → Clear all caches

shutdown() (atexit)
  → close_capture() + rd.ShutdownReplay()
```

### Adding a New Tool

To add a new tool to the MCP server:

1. **Choose the right module** based on category (session/event/pipeline/resource/data/shader/advanced/performance/diagnostic)
2. **Implement the function** inside the module's `register(mcp)` function using `@mcp.tool()` decorator
3. **Follow the pattern**: require_open → ensure_event → try/except API calls → return to_json
4. **Add human-readable output**: convert enums, include warnings, add diagnosis hints
5. **Update README.md** tool table with the new tool description
6. **Test with mocks**: the test suite uses mock objects for `renderdoc` module

### RenderDoc API Usage

For detailed API reference, consult `references/renderdoc_api.md`. Key points:

- `controller.GetPipelineState()` returns a generic `PipeState` object — it does NOT have `GetViewports()`, `GetDepthState()`, or `GetRasterizer()` methods (see PipeState API Pitfalls above)
- Pixel operations need `rd.Subresource(mip, slice, 0)` and `rd.CompType.Typeless`
- Shader debugging via `controller.DebugPixel()` may not work on all API/GPU combinations — always implement fallback
- `GetDisassemblyTargets()` returns available targets; iterate and try each for best compatibility
- GPU timing counters via `EnumerateCounters()`/`FetchCounters()` may not be available — fall back to heuristics

### Common Debugging Workflows

**Render Bug Investigation:**
```
open_capture → get_capture_info (GPU quirks)
→ list_actions (filter relevant pass)
→ get_draw_call_state (one-shot overview)
→ get_pipeline_state + get_shader_bindings (deep dive)
→ get_api_calls_by_action (raw API calls with params)
→ sample_pixel_region (scan for anomalies)
→ pixel_history (trace specific pixel)
→ debug_shader_at_pixel (step through shader)
```

**API Call Investigation:**
```
get_api_calls_by_action(event_id) → see all state setup calls for a draw
get_api_calls_range(start, end) → scan raw API calls in EID range
get_api_calls_range(start, end, name_filter="Barrier") → find specific API calls
```

**Performance Analysis:**
```
open_capture → get_frame_overview
→ get_pass_timing (find expensive passes)
→ analyze_overdraw + analyze_bandwidth
→ analyze_state_changes (batching opportunities)
→ diagnose_mobile_risks (comprehensive check)
```

**Negative Values / 爆闪:**
```
diagnose_negative_values (auto-scan all float RTs)
→ First introduced at event X → set_event(X)
→ get_shader_bindings + get_cbuffer_contents (check IBL/SH params)
→ disassemble_shader(search="SampleSH") (find problematic code)
→ debug_shader_at_pixel on anomalous pixel
```

## Reference Documents

For detailed information, load these reference files as needed:

- `references/architecture.md` — Full project structure, core components, session lifecycle, and caching strategy
- `references/tools_reference.md` — All 54 tools with parameters, return values, and RenderDoc API mappings
- `references/renderdoc_api.md` — RenderDoc Python API classes, methods, and data structures used by this server
