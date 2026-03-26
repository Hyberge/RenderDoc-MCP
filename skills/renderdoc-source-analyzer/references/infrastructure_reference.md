# Infrastructure Reference

This document covers the non-driver infrastructure: GUI, command-line tool, injection shim, test framework, and supporting libraries.

## Qt GUI: qrenderdoc

**Directory**: `qrenderdoc/`

### Why This Architecture

The GUI is a Qt5 application with an embedded Python interpreter (via SWIG). It's designed around two key constraints:
1. **Replay happens on a dedicated thread** — GPU operations can't run on the GUI thread
2. **Multiple panels observe the same state** — when the user selects a new event, all panels update

### CaptureContext — The GUI Hub

**File**: `qrenderdoc/Code/CaptureContext.h`

CaptureContext is to the GUI what the `RenderDoc` singleton is to the core library. It:
- Manages capture file lifecycle (`LoadCapture` / `CloseCapture`)
- Provides thread-safe replay access via `ReplayManager`
- Tracks current event selection and notifies all registered panels
- Coordinates all panel instances (texture viewer, event browser, pipeline state, etc.)

**Why `ICaptureViewer` matters**: All GUI panels implement the `ICaptureViewer` interface to receive `OnEventChanged()` / `OnCaptureLoaded()` / `OnCaptureClosed()` callbacks. When adding a new panel, you MUST implement this interface and register with CaptureContext.

### ReplayManager Threading Model

**File**: `qrenderdoc/Code/ReplayManager.h`

**The critical rule**: Never call `IReplayController` directly from the GUI thread. All replay operations must go through `ReplayManager`:
- `AsyncInvoke(callback)` — queues work on the replay thread, returns immediately
- `BlockInvoke(callback)` — queues work and blocks the GUI until done (use sparingly)

**Why**: Replay operations acquire GPU resources and can take milliseconds to seconds. Calling them on the GUI thread freezes the UI. `ReplayManager` serializes all replay operations on a dedicated thread.

### GUI Panel Directory

```
qrenderdoc/Windows/
├── TextureViewer        — Texture display (zoom, range, channels, overlays)
├── BufferViewer         — Mesh/buffer data viewer
├── PipelineState/       — Per-API pipeline state viewers (4 implementations)
├── EventBrowser         — Action/event tree browser
├── APIInspector         — Raw API call inspector
├── ShaderViewer         — Shader source/disassembly viewer + debugger
├── PixelHistoryView     — Pixel modification history
├── ShaderMessageViewer  — Shader debug printf output
├── DebugMessageView     — Debug message log
├── PerformanceCounterViewer — GPU counter results
├── ResourceInspector    — Resource detail viewer
├── StatisticsViewer     — Frame statistics
├── TimelineBar          — Visual event timeline
├── CommentView          — User annotations
└── Dialogs/             — Various dialog boxes
```

### SWIG Python Bindings (pyrenderdoc)

**Directory**: `qrenderdoc/Code/pyrenderdoc/`

> **📖 Full Reference**: See [pyrenderdoc_reference.md](pyrenderdoc_reference.md) for the complete architecture, file inventory, type conversion system, build system, and development workflows.

**Why SWIG**: SWIG auto-generates Python wrappers for C++ interfaces, enabling:
- Interactive Python console in the GUI
- Python extension scripts
- The entire functional test framework (`util/test/`)

**Dual module architecture**:
- `renderdoc.i` → `renderdoc.pyd/.so` — Core replay API (`IReplayController`, data types, 67 enumerations). Standalone module.
- `qrenderdoc.i` → `qrenderdoc.pyd/.so` — GUI API (`ICaptureContext`, `IMainWindow`, extension interfaces). Depends on `renderdoc` via `%import`.

**Custom SWIG fork**: Uses `renderdoc-modified-7` from `https://github.com/baldurk/swig`. Built from source on Linux/macOS via CMake ExternalProject; pre-built on Windows from `3rdparty/swig/`.

**Key subsystems**:
- **TypeConversion<T>** (`pyconversion.h`) — Template system converting all C++ types (primitives, strings, enums, datetime, pairs, arrays, pointers) to/from Python
- **Container handling** (`container_handling.i/.h`) — Maps `rdcarray<T>` to full Python list semantics (26 operations including slicing, sorting, iteration)
- **External reference counting** (`structured_conversion.h`, `ext_refcounts.h/.i`) — Cross-language lifetime management for `SDChunk`, `SDObject`, `SDFile` tree structures
- **Function conversion** (`function_conversion.h`) — Python callables → C++ `std::function<>` with GIL management and cross-thread exception propagation

**`PythonContext`** (`PythonContext.cpp`, 40KB) manages the embedded Python interpreter with:
- GIL management and thread safety
- Module initialization via `PyImport_AppendInittab`
- stdout/stderr redirection to GUI log panel
- Extension loading (`register(version, pyrenderdoc)` protocol)
- Code completion via `rlcompleter`
- Optional PySide2/Shiboken2 integration for QWidget ↔ Python conversion

**Modification impact**: When adding new methods to `IReplayController` or new data types, you must update `renderdoc.i` if they should be accessible from Python. Ensure `TypeConversion<T>` exists for any new types, and add `TEMPLATE_ARRAY_INSTANTIATE` for new `rdcarray<T>` types. Forgetting these steps causes runtime crashes or missing Python API surface.

---

## Command Line Tool: renderdoccmd

**Directory**: `renderdoccmd/`

### Command Pattern

All CLI commands inherit from `Command` base class:
```cpp
struct Command {
  virtual void AddOptions(cmdline::parser &parser) = 0;
  virtual int Execute(cmdline::parser &parser) = 0;
  virtual const char *Description() = 0;
  virtual bool IsInternalOnly() = 0;
};
```

### Available Commands

| Command | Purpose |
|---------|---------|
| `capture` | Launch and capture an application |
| `inject` | Inject into running process |
| `replay` | Replay a capture headlessly |
| `remoteserver` | Start remote replay server |
| `cap2json` | Convert capture to JSON (structured export — useful for debugging!) |
| `convert` | Convert between capture formats |
| `thumb` | Extract thumbnail from capture |
| `test` | Run functional tests |
| `version` | Print version info |
| `embed` | Embed capture data (Android) |
| `androidpatch` | Patch Android APK for capture |
| `vulkanlayer` | Register/manage Vulkan layer |

### Platform Entry Points

Each platform has its own entry point file:
- `renderdoccmd_win32.cpp` — Windows (WinMain + console attachment)
- `renderdoccmd_linux.cpp` — Linux (X11/Wayland display initialization)
- `renderdoccmd_apple.cpp` — macOS
- `renderdoccmd_android.cpp` — Android native activity (for on-device replay)

Android builds can generate an APK for `renderdoccmd` for on-device capture/replay.

**Modification tips**: To add a new command, create a `Command` subclass, implement all 4 virtual methods, and register it in the command map.

---

## Global Injection Shim: renderdocshim

**Directory**: `renderdocshim/`

**What it does**: A tiny DLL injected into ALL processes via Windows global hooks that:
1. Reads target path from shared memory
2. Compares with current process path
3. If match → `LoadLibrary("renderdoc.dll")`
4. If no match → does nothing

**Why zero CRT**: This DLL is injected into **every running process on the system**. It MUST:
- Have minimal footprint (no heap allocations)
- Avoid CRT initialization deadlocks in `DllMain`
- Avoid CRT version conflicts with target applications
- Use only stack memory and raw Win32 API

**CRITICAL**: Never add any C runtime function call to this file. No `malloc`, no `printf`, no `<string.h>`, no `<stdio.h>`. Everything must use direct Win32 API and stack-only memory.

---

## Functional Test Framework

**Directory**: `util/test/`

### How Tests Work

```
run_tests.py orchestrator
  → For each test case:
      1. Launch C++ demo application (in demos/ directory)
      2. Capture frame via renderdoc API
      3. Open capture in Python test script
      4. Verify: action tree, pipeline state, pixel values,
         shader reflection, debug trace correctness
      5. Report pass/fail
```

### Test Structure

- `tests/` — Python test scripts organized by API (D3D11/, D3D12/, GL/, Vulkan/, General/)
- `demos/` — C++ demo applications that produce specific GPU workloads
- `rdtest/` — Python test infrastructure (base `TestCase` class, helpers)

### Writing a Test

```python
import renderdoc as rd
import rdtest

class Test_MyFeature(rdtest.TestCase):
    demos_test_name = 'VK/VK_My_Demo'  # Links to C++ demo binary
    
    def check_capture(self):
        action = self.find_action("Draw")
        self.controller.SetFrameEvent(action.eventId, False)
        
        pipe = self.controller.GetPipelineState()
        self.check(pipe.GetShader(rd.ShaderStage.Pixel) != rd.ResourceId.Null())
        self.check_pixel_value(pipe.GetOutputTarget(0), 100, 100, [1.0, 0.0, 0.0, 1.0])
```

**Statistics**: 186 test cases, process-isolated, cross-API, CI-integrated (Windows + Linux).

**Modification tips**: When adding a new RenderDoc feature, add a corresponding functional test. Create the C++ demo that exercises the feature, then write a Python test that verifies the capture/replay behavior.

---

## Third-Party Libraries

**Directory**: `renderdoc/3rdparty/`

| Library | Purpose | Why Chosen |
|---------|---------|-----------|
| **lz4** | Fast chunk compression | Speed: decompression speed critical for replay startup |
| **zstd** | High-ratio compression | File size: optional for smaller .rdc files |
| **miniz** | ZIP support | Structured export output format |
| **glslang** | GLSL/HLSL → SPIR-V | Required for shader editing/hot-reload in Vulkan |
| **SPIRV-Tools** | SPIR-V validation | Validates generated/edited SPIR-V |
| **SPIRV-Cross** | SPIR-V cross-compilation | SPIR-V → GLSL/HLSL/MSL for display |
| **stb** | Image loading/saving | Simple header-only image I/O |
| **jpeg-compressor** | Thumbnail generation | Capture file thumbnails |
| **tinyexr** | EXR format support | HDR texture export |
| **pugixml** | XML parsing | Settings and config files |
| **Scintilla** | Code editor widget | Shader source editing in GUI |
| **toolwindowmanager** | Docking windows | GUI panel layout management |
| **plthook** | PLT hooking | Linux/Android function interception |
| **interceptor-lib** | Android hooking | Android-specific function interception |

---

## Logging and Diagnostics

**File**: `renderdoc/common/common.h`

Logging macros: `RDCLOG` (info), `RDCDEBUG` (debug-only), `RDCWARN` (warning), `RDCERR` (error), `RDCFATAL` (fatal + breakpoint in debug). Assertions: `RDCASSERT` (debug-only), `RDCCOMPILE_ASSERT` (compile-time).

## Format Packing

**File**: `renderdoc/maths/formatpacking.h`

Handles GPU pixel format ↔ float conversion: `ConvertComponents()` for reading, `EncodeFormattedComponents()` for writing. Includes half-float, R11G11B10, R9G9B9E5, and all BC/ETC/ASTC block compression decoders.

## Key Design Patterns Summary

| Pattern | Where Used | Why |
|---------|-----------|-----|
| **Wrapped Object** | All drivers (except Metal) | Transparent API call interception |
| **Factory + Registration** | Driver creation | Plugin-like driver loading without compile-time coupling |
| **Template Specialization** | ResourceManager, Serialiser | Code reuse across drivers with type safety |
| **Singleton** | RenderDoc, CaptureContext | Central coordination points |
| **State Machine** | CaptureState, FrameRefType | Explicit lifecycle management with optimization opportunities |
| **Proxy** | ReplayProxy | Transparent local/remote replay |
| **Command** | renderdoccmd | Extensible CLI without modifying core code |
| **Observer** | ICaptureViewer → panels | GUI event propagation |
| **RAII** | ScopedLock, ScopedChunk | Resource safety and scope-based cleanup |
