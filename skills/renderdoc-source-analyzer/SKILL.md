---
name: renderdoc-source-analyzer
description: "Comprehensive knowledge base for understanding, developing, debugging, and extending the RenderDoc graphics debugger C++ source code. Covers the full codebase architecture: core singleton and capture state machine, 5 graphics API drivers (D3D11/D3D12/Vulkan/OpenGL/Metal) with the Wrapped Object pattern, ResourceManager template, serialization framework, ReplayProxy remote replay, shader processing subsystem (DXBC/DXIL/SPIR-V), IHV vendor integration (AMD/NVIDIA/Intel/ARM), OS abstraction layer, hook framework, pyrenderdoc Python binding subsystem (SWIG interface export, TypeConversion<T> template, container handling, external reference counting, PythonContext runtime, PySide2 integration), Qt5 GUI, command-line tool, and functional test framework. Use this skill when working on renderdoc/ source code, adding new driver features, debugging capture/replay issues, understanding the build system, modifying Python bindings, or extending any subsystem."
---

# RenderDoc Source Code Analyzer

## Core Directives

When you are loaded for a RenderDoc source code task, you MUST follow these rules:

1. **Locate the driver and subsystem first.** Before writing any code, confirm which driver (D3D11 / D3D12 / Vulkan / OpenGL / Metal) and which subsystem (capture / replay / serialization / shader / GUI / CLI) the user is working in. Different drivers have fundamentally different architectures (COM wrapping vs Vulkan Layer vs global GL state vs ObjC swizzling).

2. **Follow the Wrapped Object Pattern.** Every driver modification must preserve the three-step interception pattern: **intercept → record chunk → forward to real API**. Never bypass the serialization step during capture, and never skip the replay step during loading.

3. **Check cross-module impact before editing.** RenderDoc has deep coupling between interfaces. When modifying an interface or data type, trace ALL dependents using the Cross-Module Consistency rules below. A common mistake is updating only one driver and forgetting the other 3 + ReplayProxy + SWIG bindings.

4. **Search, never read-through, for large driver files.** Four driver header files exceed 100KB (`vk_core.h` 167KB, `gl_driver.h` 164KB, `renderdoc_replay.h` 107KB, `d3d12_device.h` ~80KB). Always use targeted search to locate the function or section you need. Never attempt to read these files in full.

## Task Routing

| User Intent | Workflow | First Action |
|-------------|----------|-------------|
| "Add support for a new API call" | [Adding a Serialized API Call](#adding-a-new-serialized-api-call) | Identify the driver → find its Chunk enum → add new value at the end |
| "Add/extend a replay feature" | [Extending IReplayDriver](#extending-ireplaydriver) | Modify interface → update ALL 4 driver implementations + ReplayProxy + SWIG |
| "Add a new GUI panel" | [Adding a GUI Panel](#adding-a-gui-panel) | Create widget in `qrenderdoc/Windows/` → register with CaptureContext |
| "Add/modify Python bindings" | See [pyrenderdoc_reference.md](references/pyrenderdoc_reference.md) | Identify type → add to `renderdoc.i` → ensure TypeConversion<T> exists → add TEMPLATE_ARRAY_INSTANTIATE if needed |
| "Debug Python binding issues" | See [pyrenderdoc_reference.md](references/pyrenderdoc_reference.md) | Enable debug build → check interface_check.h validation → verify TypeConversion specialization |
| "Add Python callback support" | See [pyrenderdoc_reference.md](references/pyrenderdoc_reference.md) | Define std::function signature → add SIMPLE_TYPEMAPS → ConvertFunc<> handles conversion |
| "Capture is missing data / wrong" | [Debugging Capture Issues](#debugging-capture-issues) | Check CaptureState → verify ResourceManager dirty tracking → inspect FrameRefType |
| "Replay shows wrong results" | [Debugging Replay Issues](#debugging-replay-issues) | Structured export via `cap2json` → check initial contents → verify SetFrameEvent |
| "Modify shader processing" | See [drivers_reference.md](references/drivers_reference.md) shader section | Identify shader format (DXBC/DXIL/SPIR-V) → locate the correct module |
| "Work on build system / CMake" | See Build System section below | Check the appropriate CMakeLists.txt at the right level |

## Critical Constraints

### Cross-Module Consistency (MUST follow)

- When modifying `IReplayDriver` (in `replay_driver.h`): you **MUST** update ALL of these:
  1. `D3D11Replay` (`driver/d3d11/d3d11_replay.h/.cpp`)
  2. `D3D12Replay` (`driver/d3d12/d3d12_replay.h/.cpp`)
  3. `VulkanReplay` (`driver/vulkan/vk_replay.h/.cpp`)
  4. `GLReplay` (`driver/gl/gl_replay.h/.cpp`)
  5. `ReplayProxy` (`core/replay_proxy.h/.cpp`) — add `IMPLEMENT_FUNCTION_PROXIED` entry
  6. If the method is exposed to Python: update SWIG bindings (`qrenderdoc/Code/pyrenderdoc/renderdoc.i`)
  7. If the method returns/accepts new types: ensure `TypeConversion<T>` specialization exists in `pyconversion.h`, and add `TEMPLATE_ARRAY_INSTANTIATE` for any new `rdcarray<T>` types

- When modifying public interfaces in `renderdoc_replay.h`: also update `ReplayController` (`replay/replay_controller.h/.cpp`) and the C entry points in `entry_points.cpp`.

- When modifying data types in `data_types.h` or `shader_types.h`: check all serialization code that reads/writes these types — old captures must remain loadable.

### Serialization Compatibility (MUST follow)

- New Chunk enum values **MUST be appended at the end**. Never reuse or reorder existing chunk IDs — this breaks backward compatibility with older .rdc files.
- Every `Serialise_*` function **MUST** work in both `Reading` and `Writing` modes. The same code path handles capture (write) and replay (read).
- After adding a new chunk type, register it in the driver's `ProcessChunk()` / `ContextProcessChunk()` method.

### Common Traps (NEVER do these)

- ❌ **NEVER** use any C runtime functions in `renderdocshim` — it is a zero-CRT DLL. No `malloc`, no `printf`, no `<string.h>`. All functionality uses raw Win32 API and stack-only memory.
- ❌ **NEVER** assume Metal driver implements `IReplayDriver` — it is early-stage development. `MetalReplay` only provides `GetResourceDesc()`. `ENABLE_METAL` defaults to OFF.
- ❌ **NEVER** read-through `vk_core.h` (167KB) or `gl_driver.h` (164KB). Use search to locate target functions.
- ❌ **NEVER** depend on initial content existence in the `CompleteWrite` optimization path — ResourceManager may skip initial content preparation for fully-overwritten resources.
- ❌ **NEVER** confuse `ResourceId` (64-bit globally unique ID assigned by RenderDoc) with API-native handles (`ID3D11Buffer*`, `VkBuffer`, `GLuint`). They are different namespaces.
- ❌ **NEVER** modify `FrameRefType` state transitions without understanding the optimization consequences — incorrect transitions can cause missing resources in captures or unnecessary data in .rdc files.
- ❌ **NEVER** add shared mutable state to D3D12 serialization paths — D3D12 uses thread-local serializers because it allows multi-threaded command recording.
- ❌ **NEVER** forget `TEMPLATE_ARRAY_INSTANTIATE` when adding new `rdcarray<T>` types to Python bindings — SWIG will generate broken wrappers that crash at runtime.
- ❌ **NEVER** hold the Python GIL while calling back into C++ replay functions — this causes deadlocks with the replay thread. Use `ScopedFuncCall` or release GIL explicitly.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│              renderdoc.dll / librenderdoc.so             │
│                                                          │
│  Hook Framework → Graphics API Drivers (Wrapped Object)  │
│  [D3D11 | D3D12 | Vulkan | GL | Metal]                  │
│       │                                                  │
│  ResourceManager<Config> → Serialiser<Mode> → Chunks     │
│       │                                                  │
│  Core: RenderDoc Singleton (driver registry, state)      │
│       │                                                  │
│  Replay: CaptureFile → ReplayController → IReplayDriver  │
│          ReplayProxy (transparent local/remote)           │
│                                                          │
│  Shader Processing: DXBC / DXIL / SPIR-V                 │
│  OS Abstraction: Process/Threading/Network/FileIO         │
└─────────────────────────────────────────────────────────┘
┌──────────────────┐  ┌──────────────┐  ┌──────────────────┐
│ qrenderdoc       │  │ renderdoccmd │  │ renderdocshim    │
│ (Qt5 GUI)        │  │ (12+ cmds)  │  │ (Zero-CRT shim)  │
│  └─pyrenderdoc/  │  └──────────────┘  └──────────────────┘
│    ├renderdoc.pyd│  
│    ├qrenderdoc.pyd  
│    └PythonContext│  
└──────────────────┘
```

### Key Concepts (see [architecture.md](references/architecture.md) for details)

- **Wrapped Object Pattern**: All drivers intercept API calls by wrapping original objects — intercept → record → forward. Each driver has its own wrapping mechanism (COM, Layer, global state, swizzling).
- **CaptureState Machine**: `BackgroundCapturing ⇄ ActiveCapturing` (capture) / `LoadingReplaying → ActiveReplaying` (replay) / `StructuredExport`. All driver code is state-conditional.
- **ResourceManager Template**: Per-driver template for resource tracking, dirty detection, FrameRefType optimization, and initial content management. Configuration requires 4 typedefs.
- **Serialization → Chunk → RDC**: API calls become chunks via `Serialiser<Mode>`, written to .rdc file sections with optional compression (LZ4/Zstd).
- **IReplayDriver Hierarchy**: `IRemoteDriver` (minimal) → `IReplayDriver` (full ~40+ methods) → implemented by 4 drivers. `ReplayProxy` wraps for transparent remote replay.
- **ReplayProxy**: Same `IReplayDriver` interface, transparently proxied over network via `IMPLEMENT_FUNCTION_PROXIED` macro. Supports delta transfer and resource caching.

## Key Source Files

| File | Purpose |
|------|---------|
| `renderdoc/core/core.h` | RenderDoc singleton, CaptureState, driver registry |
| `renderdoc/core/resource_manager.h` | ResourceManager template, FrameRefType |
| `renderdoc/core/replay_proxy.h` | ReplayProxy, IMPLEMENT_FUNCTION_PROXIED |
| `renderdoc/replay/replay_controller.h` | ReplayController (public API impl) |
| `renderdoc/replay/replay_driver.h` | IRemoteDriver / IReplayDriver interfaces |
| `renderdoc/replay/capture_file.cpp` | CaptureFile (ICaptureFile impl) |
| `renderdoc/replay/entry_points.cpp` | Public C API exports |
| `renderdoc/api/replay/renderdoc_replay.h` | 8 core interfaces (107KB — search only!) |
| `renderdoc/api/replay/data_types.h` | Core data structures |
| `renderdoc/api/replay/replay_enums.h` | 67 enumerations |
| `renderdoc/serialise/serialiser.h` | Serialiser template, Chunk system |
| `renderdoc/serialise/rdcfile.h` | RDC file format |
| `renderdoc/os/os_specific.h` | OS abstraction layer |
| `renderdoc/hooks/hooks.h` | Hook framework |
| `renderdoc/driver/d3d11/d3d11_device.h` | D3D11 wrapped device |
| `renderdoc/driver/d3d12/d3d12_device.h` | D3D12 wrapped device (15 versions, ~80KB) |
| `renderdoc/driver/vulkan/vk_core.h` | Vulkan wrapped driver (167KB — search only!) |
| `renderdoc/driver/gl/gl_driver.h` | OpenGL wrapped driver (164KB — search only!) |
| `renderdoc/driver/metal/metal_device.h` | Metal driver (early dev, no replay) |
| `qrenderdoc/Code/pyrenderdoc/renderdoc.i` | Core Python module SWIG interface (22 headers, ~90 template instantiations) |
| `qrenderdoc/Code/pyrenderdoc/qrenderdoc.i` | GUI Python module SWIG interface (imports renderdoc.i) |
| `qrenderdoc/Code/pyrenderdoc/pyconversion.h` | TypeConversion<T> template system for C++ ↔ Python |
| `qrenderdoc/Code/pyrenderdoc/PythonContext.cpp` | Embedded Python interpreter management (40KB) |
| `qrenderdoc/Code/CaptureContext.h` | GUI central hub |
| `renderdoccmd/renderdoccmd.h` | CLI Command base class |

## Build System

- **Windows**: Visual Studio .sln (primary) or CMake
- **Linux/macOS**: CMake
- **Android**: CMake with NDK cross-compilation

Key CMake options (all ON by default except Metal):
`ENABLE_GL`, `ENABLE_GLES`, `ENABLE_EGL`, `ENABLE_VULKAN`, `ENABLE_METAL` (OFF),
`ENABLE_RENDERDOCCMD`, `ENABLE_QRENDERDOC`, `ENABLE_PYRENDERDOC`,
`ENABLE_XLIB`, `ENABLE_XCB`, `ENABLE_WAYLAND`,
`ENABLE_ASAN/TSAN/MSAN` (OFF)

## Development Workflows

### Adding a New Serialized API Call

1. Add enum value to driver's chunk enum (e.g., `D3D11Chunk::NewCall`) — **append at end, never reuse IDs**
2. In the wrapped object, implement the intercept-record-forward pattern:
   ```cpp
   HRESULT WrappedID3D11Device::NewCall(params...) {
     HRESULT ret;
     SERIALISE_TIME_CALL(ret = m_pDevice->NewCall(params...));
     if(IsActiveCapturing(m_State)) {
       SCOPED_SERIALISE_CHUNK(D3D11Chunk::NewCall);
       Serialise_NewCall(GET_SERIALISER, params...);
       m_ContextRecord->AddChunk(scope.Get());
     }
     return ret;
   }
   ```
3. Implement the dual-mode serialisation function (must handle both Reading and Writing):
   ```cpp
   bool WrappedID3D11Device::Serialise_NewCall(SerialiserType &ser, params...) {
     SERIALISE_ELEMENT(param1);
     SERIALISE_ELEMENT(param2);
     SERIALISE_CHECK_READ_ERRORS();
     if(IsReplayingAndReading()) {
       m_pDevice->NewCall(param1, param2);
     }
     return true;
   }
   ```
4. Register the chunk in the driver's `ProcessChunk()` for replay dispatch
5. ⚠️ **Trap**: Forgetting step 4 means the chunk is recorded but silently ignored during replay

### Extending IReplayDriver

1. Add virtual method to `IReplayDriver` in `replay_driver.h`
2. Implement in **ALL 4 drivers** (D3D11Replay, D3D12Replay, VulkanReplay, GLReplay)
3. Add proxy support in `ReplayProxy` via `IMPLEMENT_FUNCTION_PROXIED`
4. Expose via `ReplayController` if public
5. Add to `renderdoc_replay.h` interface if it's a public API method
6. Update SWIG bindings (`renderdoc.i`) if exposed to Python
7. ⚠️ **Trap**: Forgetting step 3 causes crashes when using remote replay. Forgetting step 6 causes Python extension failures.

### Adding a GUI Panel

1. Create widget class in `qrenderdoc/Windows/` inheriting from `QFrame` and implementing `ICaptureViewer`
2. Register with `CaptureContext` for event notifications (SetEventID callbacks)
3. Add menu entry in `MainWindow`
4. Use `ReplayManager` for thread-safe replay access — **never call IReplayController directly from the GUI thread**
5. ⚠️ **Trap**: Calling replay functions on the GUI thread causes deadlocks. Always go through `ReplayManager::AsyncInvoke()` or `BlockInvoke()`.

### Debugging Capture Issues

1. Check `CaptureState` transitions — is the driver correctly in `ActiveCapturing`?
2. Verify `ResourceManager` dirty tracking — is the resource marked dirty?
3. Check `FrameRefType` — is it `CompleteWrite` (initial content skipped) when it shouldn't be?
4. Use `cap2json` command to inspect serialized chunks — are all expected chunks present?
5. Compare initial contents between capture and replay — are they byte-identical?
6. ⚠️ **Trap**: `FrameRefType::CompleteWrite` causes ResourceManager to skip initial content. If the resource isn't actually fully overwritten, replay will show corruption.

### Debugging Replay Issues

1. Use structured export (`cap2json`) to inspect chunk data — verify parameters are correct
2. Check `ReadLogInitialisation()` — does the driver correctly parse all chunks?
3. Verify `ApplyInitialContents()` — are resources restored to their frame-start state?
4. Check `SetFrameEvent()` replays to the correct point in the action list
5. Use overlay rendering to visualize pipeline state issues
6. ⚠️ **Trap**: If a new chunk type was added but not registered in `ProcessChunk()`, it will be silently skipped during replay with no error.

## Reference Documents

- **[Architecture Reference](references/architecture.md)**: Deep design rationale for core singleton, CaptureState, ResourceManager, serialization, ReplayProxy, RDC file format — explains *why* things are designed this way and *what breaks* if you change them
- **[Drivers Reference](references/drivers_reference.md)**: Per-driver architecture differences (D3D11 COM / D3D12 threading / Vulkan Layer / GL global state / Metal swizzling), shader processing pipelines, IHV integration, driver capability matrix
- **[Infrastructure Reference](references/infrastructure_reference.md)**: GUI development (CaptureContext, ReplayManager threading model), CLI commands, renderdocshim zero-CRT design, functional test framework, third-party libraries
- **[PyRenderDoc Reference](references/pyrenderdoc_reference.md)**: Python binding subsystem — SWIG interface export mechanism, TypeConversion<T> template system, container handling (rdcarray ↔ Python list), external reference counting (SDChunk/SDObject/SDFile), function/callback conversion, PythonContext embedded runtime, PySide2/Shiboken integration, build system (MSVC + CMake), dependencies, and development workflows for adding/modifying Python bindings
