# renderdoc-mcp

MCP server for [RenderDoc](https://renderdoc.org/) — let AI assistants analyze GPU frame captures (`.rdc` files) for graphics debugging and performance analysis.

Built on the [Model Context Protocol](https://modelcontextprotocol.io/), works with Claude Desktop, Cursor, VS Code, and any MCP-compatible client.

## Features

- **42 tools** covering the full RenderDoc analysis workflow
- **10 high-level tools** for one-call analysis (draw call state, frame overview, diff, batch export, pixel region sampling, etc.)
- **3 built-in prompts** for guided debugging
- **Human-readable output** — blend modes, depth functions, topology shown as names not numbers
- **GPU quirk detection** — auto-identifies Adreno/Mali/PowerVR/Apple-specific pitfalls from driver name
- **Headless** — no GUI needed, runs entirely via RenderDoc's Python replay API
- Supports D3D11, D3D12, OpenGL, Vulkan, OpenGL ES captures

## How It Works

```
AI Assistant ←—MCP—→ renderdoc-mcp server ←—Python API—→ renderdoc.pyd ←→ GPU replay
```

The server uses RenderDoc's headless replay API (`renderdoc.pyd`) to:
1. Open `.rdc` capture files without the GUI
2. Replay frames and query pipeline state at any event
3. Extract textures, buffers, shader data, and pixel history
4. Return structured JSON for the AI to reason about

## Build from Source

This repository includes RenderDoc source as a Git submodule and provides automated build scripts to compile `renderdoc.pyd` and install the MCP server in one step.

### Prerequisites

- **Python ≥ 3.6** — full installation with development headers and libs
  - Download from [python.org](https://www.python.org/downloads/)
  - Make sure to check **"Add Python to PATH"** during installation
- **Visual Studio 2015 or later** — with **C++ build tools** (Desktop development with C++)
  - Download from [visualstudio.microsoft.com](https://visualstudio.microsoft.com/downloads/)

### Build & Install

#### Option A: Double-click (simplest)

Double-click `build_and_install.bat` — it runs the PowerShell script with the correct execution policy.

#### Option B: PowerShell

```powershell
# If execution policy blocks the script:
powershell -ExecutionPolicy Bypass -File .\build_and_install.ps1

# Or with a clean build:
powershell -ExecutionPolicy Bypass -File .\build_and_install.ps1 -Clean
```

#### Script Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-Configuration` | `Release` | Build configuration (`Release` / `Debug`) |
| `-Platform` | `x64` | Target platform |
| `-Clean` | off | Clean build — runs MSBuild Clean and removes intermediates first |

#### What the script does

1. **Detect** Python and Visual Studio / MSBuild environment
2. **Patch** `python.props` to work with standard Python installations (removes `.zip` file checks)
3. **Build** `renderdoc.pyd` and `renderdoc.dll` via MSBuild
4. **Collect** build outputs to `build/x64/`
5. **Install** MCP server via `pip install -e`
6. **Generate** `mcp.json` configuration file

After a successful build, you'll find the outputs in:

```
build/x64/
├── renderdoc.pyd          # Python module
├── renderdoc.dll          # Core library
└── ...
```

## Configure Your MCP Client

The build script generates a `mcp.json` file. Copy its contents to your MCP client configuration:

<details>
<summary><b>Claude Desktop</b></summary>

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "python",
      "args": ["-m", "renderdoc_mcp.server"],
      "env": {
        "RENDERDOC_MODULE_PATH": "X:\\renderdoc-mcp\\build\\x64"
      }
    }
  }
}
```

</details>

<details>
<summary><b>Cursor</b></summary>

Add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "python",
      "args": ["-m", "renderdoc_mcp.server"],
      "env": {
        "RENDERDOC_MODULE_PATH": "X:\\renderdoc-mcp\\build\\x64"
      }
    }
  }
}
```

</details>

<details>
<summary><b>VS Code</b></summary>

Add to `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "renderdoc": {
      "command": "python",
      "args": ["-m", "renderdoc_mcp.server"],
      "env": {
        "RENDERDOC_MODULE_PATH": "X:\\renderdoc-mcp\\build\\x64"
      }
    }
  }
}
```

</details>

> **Note:** Replace the paths with your actual Python executable and build output directory. The generated `mcp.json` already contains the correct paths for your system.

## Usage Examples

Once configured, just talk to your AI assistant:

> "Open `frame.rdc` and show me what's happening in the frame"

> "Find the draw call that renders the character model and check its pipeline state"

> "Why is my shadow map rendering black? Check the depth pass"

> "Analyze performance — are there any redundant draw calls?"

### Typical Tool Flow

```
open_capture("frame.rdc")                     # Load the capture
├── get_capture_info()                         # API, GPU, known_gpu_quirks
├── get_frame_overview()                       # Frame-level stats and render passes
├── get_draw_call_state(142)                   # Complete draw call state in one call
├── diff_draw_calls(140, 142)                  # Compare two draw calls
├── export_draw_textures(142, "./tex/")        # Batch export all bound textures
├── save_render_target(142, "./rt.png")        # Save render target snapshot
├── analyze_render_passes()                    # Auto-detect render pass boundaries
├── sample_pixel_region(rt_id, 0,0,512,512)   # Scan RT region for NaN/Inf/negatives
├── pixel_history(id, 512, 384)               # Debug a specific pixel
├── export_mesh(142, "./mesh.obj")             # Export mesh as OBJ
└── close_capture()                            # Clean up
```

For performance and diagnostics:

```
get_pass_timing(granularity="pass")      # Find most expensive render passes
analyze_overdraw()                        # Fill-rate pressure estimate
analyze_bandwidth()                       # Memory bandwidth estimate
analyze_state_changes()                   # Batching opportunities
diagnose_negative_values()               # Find NaN/Inf/negative color values
diagnose_precision_issues()              # R11G11B10, D16, SRGB mismatches
diagnose_reflection_mismatch()           # Reflection artifact diagnosis
diagnose_mobile_risks()                  # Comprehensive mobile GPU risk check
```

## Tools Reference

### Session Management (4)

| Tool | Description |
|------|-------------|
| `open_capture` | Open a `.rdc` file (auto-closes previous) |
| `close_capture` | Close current capture and free resources |
| `get_capture_info` | Capture metadata: API, action count, resolution, **known_gpu_quirks** |
| `get_frame_overview` | Frame-level statistics: action counts by type, texture/buffer memory, render targets |

### Event Navigation (5)

| Tool | Description |
|------|-------------|
| `list_actions` | List draw call / action tree with `filter` and `event_type` support |
| `get_action` | Full detail for a single action |
| `set_event` | Navigate to an event (required before pipeline queries) |
| `search_actions` | Search by name pattern and/or action flags |
| `find_draws` | Search draw calls by rendering state: blend, min vertices, texture/shader/RT binding |

### Pipeline Inspection (4)

| Tool | Description |
|------|-------------|
| `get_pipeline_state` | Full state: topology, viewports, rasterizer, blend, depth, stencil |
| `get_shader_bindings` | Constant buffers, SRVs, UAVs, samplers for a shader stage |
| `get_vertex_inputs` | Vertex attributes, vertex/index buffer bindings |
| `get_draw_call_state` | **One-call draw analysis**: action + blend + depth + stencil + rasterizer + textures + RTs + shaders |

### Resource Analysis (4)

| Tool | Description |
|------|-------------|
| `list_textures` | All textures (filterable by format, min width) |
| `list_buffers` | All buffers (filterable by min size) |
| `list_resources` | All named resources (filterable by type, name pattern) |
| `get_resource_usage` | Which events read/write a resource |

### Data Extraction (8)

| Tool | Description |
|------|-------------|
| `save_texture` | Export to PNG, JPG, BMP, TGA, HDR, EXR, or DDS |
| `get_buffer_data` | Read buffer bytes (hex dump or float32 array) |
| `pick_pixel` | RGBA value at a coordinate |
| `get_texture_stats` | Per-channel min/max/avg with anomaly detection (NaN/Inf/negative) |
| `read_texture_pixels` | Read a rectangular region of pixels (up to 64×64) |
| `export_draw_textures` | Batch export all textures bound to a draw call |
| `save_render_target` | Save RT snapshot at an event (color + optional depth) |
| `export_mesh` | Export mesh as OBJ with positions, normals, UVs |

### Shader Analysis (3)

| Tool | Description |
|------|-------------|
| `disassemble_shader` | Shader disassembly with auto fallback chain, `search` and `line_range` support |
| `get_shader_reflection` | Input/output signatures, resource binding layout |
| `get_cbuffer_contents` | Actual constant buffer variable values with `filter` support |

### Advanced (6)

| Tool | Description |
|------|-------------|
| `pixel_history` | Full per-pixel modification history across all events |
| `get_post_vs_data` | Post-transform vertex data (VS out / GS out) |
| `diff_draw_calls` | Compare two draw calls with human-readable implications |
| `analyze_render_passes` | Auto-detect render pass boundaries |
| `sample_pixel_region` | Uniform-grid scan of RT region — detects NaN/Inf/negative/overexposed |
| `debug_shader_at_pixel` | Per-pixel shader debug with variable trace |

### Performance Analysis (4)

| Tool | Description |
|------|-------------|
| `get_pass_timing` | Most expensive render passes (GPU counters or heuristic fallback) |
| `analyze_overdraw` | Overdraw estimate per render target group |
| `analyze_bandwidth` | Write/read bandwidth estimate per render target |
| `analyze_state_changes` | Finds redundant state-change patterns and batching opportunities |

### Diagnostics (4)

| Tool | Description |
|------|-------------|
| `diagnose_negative_values` | Scans float RTs for negative/NaN/Inf — finds first introducing event |
| `diagnose_precision_issues` | R11G11B10 sign-bit loss, shallow depth buffers, SRGB/linear mismatches |
| `diagnose_reflection_mismatch` | Compares reflection passes against main scene draws |
| `diagnose_mobile_risks` | Comprehensive mobile GPU risk check (precision / performance / compatibility) |

## Prompts

Built-in prompt templates to guide AI through common workflows:

| Prompt | Description |
|--------|-------------|
| `debug_draw_call` | Deep-dive a single draw call: pipeline → shaders → cbuffers → outputs |
| `find_rendering_issue` | Systematic diagnosis from a problem description |
| `analyze_performance` | Frame-wide perf analysis: pass timing, overdraw, bandwidth, state changes |

## Project Structure

```
renderdoc-mcp/
├── renderdoc/                  # RenderDoc source (Git submodule)
├── mcpserver/                  # MCP server Python package
│   ├── src/renderdoc_mcp/
│   │   ├── server.py           # FastMCP server, prompt definitions
│   │   ├── session.py          # Capture lifecycle, resource caches
│   │   ├── util.py             # Serialization, enum maps, module loader
│   │   └── tools/              # 9 tool modules (42 tools total)
│   ├── tests/
│   └── pyproject.toml
├── build/                      # Build outputs (renderdoc.pyd, .dll)
├── build_and_install.ps1       # Automated build script (PowerShell)
├── build_and_install.bat       # Wrapper — double-click to build
└── mcp.json                    # Generated MCP client configuration
```
