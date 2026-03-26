# Architecture Reference

This document explains **why** RenderDoc's core systems are designed the way they are, and **what breaks** if you modify them incorrectly.

## Core Singleton: RenderDoc Class

**File**: `renderdoc/core/core.h`

**Why it exists**: RenderDoc needs a central hub to coordinate driver registration, capture state, and global settings across all graphics APIs. The singleton pattern ensures exactly one instance manages all state.

**How it works**: `RenderDoc::Inst()` is the global access point. Drivers self-register during static initialization via `RegisterDriver()`, providing a factory function that creates their `IReplayDriver`. When a capture file is opened, the singleton looks up the driver type from the .rdc header and calls the registered factory.

**Modification impact**:
- Adding a new driver: register via `RegisterDriver()` with the appropriate `RDCDriver` enum value, provide a `ReplayDriverCreator`, optionally a `StructuredProcessor`
- Changing capture flow: affects `StartFrameCapture()` → `EndFrameCapture()` cycle — all drivers' `IFrameCapturer` implementations depend on this
- Changing the driver registry: affects the entire replay pipeline — `CaptureFile::OpenCapture()` depends on it

## CaptureState State Machine

**File**: `renderdoc/core/core.h`

**Why it exists**: Every piece of driver code must behave differently depending on whether we're passively monitoring (background), actively recording a frame (capture), loading a file (loading), or replaying (active replay). The state machine makes these branches explicit.

**States and transitions**:
```
BackgroundCapturing ──StartFrameCapture()──→ ActiveCapturing
       ↑                                          │
       └──────── EndFrameCapture() ───────────────┘
                 (writes .rdc file)

LoadingReplaying ──PostCreateInit()──→ ActiveReplaying
LoadingReplaying ──(structured only)──→ StructuredExport
```

**Why this matters for you**: Nearly all driver wrapper functions contain `if(IsActiveCapturing(m_State))` or `if(IsReplayingAndReading())` branches. When adding a new API call wrapper, you must handle the correct state. Helper functions:
- `IsActiveCapturing()` — true only during frame recording, triggers chunk serialization
- `IsReplayingAndReading()` — true during `LoadingReplaying`, triggers actual API replay
- `IsActiveReplaying()` — true after loading, the "steady state" for inspection queries
- `IsBackgroundCapturing()` — true normally, minimal tracking only

**Modification impact**: Changing state transitions affects every driver. Adding a new state requires updating all `IsCaptureMode()`/`IsReplayMode()` helpers and auditing every state branch in every driver.

## ResourceManager Template

**File**: `renderdoc/core/resource_manager.h`

**Why it exists**: All 4 mature drivers need the same resource tracking logic (creation, dirty detection, frame references, initial content management), but each driver has different resource types (`ID3D11Buffer*` vs `VkBuffer` vs `GLuint`). The template avoids duplicating ~2000 lines of tracking logic.

**Template configuration**: Each driver must provide 4 typedefs:
```cpp
// Example: D3D11
struct D3D11ResourceManagerConfiguration {
  typedef WrappedID3D11DeviceChild *WrappedResourceType;
  typedef ID3D11DeviceChild *RealResourceType;
  typedef D3D11ResourceRecord RecordType;
  typedef D3D11InitialContents InitialContentData;
};
```

**Modification impact**: Changes to ResourceManager affect ALL 4 drivers simultaneously. Test changes across D3D11, D3D12, Vulkan, and GL.

### FrameRefType State Machine

**Why it exists**: Optimization. If a resource is fully overwritten (`CompleteWrite`) before any read in a frame, we can skip capturing its initial content — saving significant time and file size. If a resource is never referenced (`None`), we skip it entirely.

**State transitions**:
```
None ─→ Read             (read-only resources share initial content)
None ─→ PartialWrite     (partially written, need initial content)
None ─→ CompleteWrite    (fully overwritten, skip initial content!)
CompleteWrite ─→ Read    (oops, read after overwrite — escalate to ReadBeforeWrite)
```

**Modification impact**: Incorrect FrameRefType transitions cause:
- **Missing initial content** (if `CompleteWrite` is incorrectly assigned) → replay corruption
- **Bloated captures** (if resources are never marked `CompleteWrite` when they should be) → larger .rdc files, slower capture
- **Missing resources** (if `None` is incorrectly assigned) → replay crashes

### Initial Content System

**Why it exists**: To correctly replay a frame, resources must be in their exact frame-start state. The initial content system snapshots dirty resources before frame capture begins.

**Flow**:
```
Frame start:
  1. MarkDirtyResources() — build dirty list from tracking
  2. PrepareInitialContents() — snapshot current GPU state of dirty resources
  3. Serialise initial contents into .rdc capture stream

Replay:
  1. Read initial contents from .rdc file
  2. ApplyInitialContents() — restore resources to frame-start state
  3. Replay API call chunks from that point
```

**Modification impact**: Changes to initial content logic must be tested with captures containing both clean (never-written-to) and dirty (pre-modified) resources.

## Serialization Framework

**File**: `renderdoc/serialise/serialiser.h`

**Why it's dual-mode**: The same `Serialise_*` function handles both capture (writing) and replay (reading). This ensures the data format is always symmetric — you can't accidentally write a field without reading it, or vice versa.

```cpp
template <SerialiserMode Mode>
class Serialiser {
  // Serialise("name", value) writes when Mode==Writing, reads when Mode==Reading
};
```

**The Chunk system**: Each API call becomes a `Chunk` with a unique ID, timestamp, thread ID, and serialized parameters. Chunks are written into a stream, optionally compressed, and stored in the .rdc file.

```
API call → SCOPED_SERIALISE_CHUNK(ChunkEnum::Value) → Serialise parameters → Chunk written
```

**Memory management**: `ChunkPagePool` provides page-based allocation to avoid per-chunk heap overhead. `ChunkAllocator` is thread-local — critical for D3D12's multi-threaded command recording model.

**Modification impact**:
- Adding a new Chunk: append to the driver's chunk enum (NEVER reorder — breaks old captures)
- Modifying serialized data: old captures with the old format will fail to load unless you add version handling
- Changing the Chunk header format: breaks ALL existing .rdc files

## RDC File Format

**File**: `renderdoc/serialise/rdcfile.h`

**Why it's sectioned**: Different data has different access patterns. The main chunk stream is large and sequential; thumbnails need random access; metadata is small JSON. Sections allow independent compression and selective reading.

```
┌────────────────────────┐
│ Magic: "RENDERDOC"     │
│ Version header         │
├────────────────────────┤
│ Section 0: Frame Capture  (main chunk stream, compressed)
│ Section 1: Thumbnails     (JPEG/PNG, random access)
│ Section 2: Extended Info   (JSON metadata)
│ Section 3: Resource Renames
│ Section N: Driver-specific
├────────────────────────┤
│ Section index          │
└────────────────────────┘
```

**Compression**: Each section independently compressed (LZ4 for speed, Zstd for ratio, or none).

**Modification impact**: Adding new section types is safe. Changing the magic bytes or header format breaks all existing files. The section index is at the end of the file — changing its format requires a major version bump.

## ReplayProxy

**File**: `renderdoc/core/replay_proxy.h`

**Why it exists**: RenderDoc supports remote replay (capture on Android, replay on PC). The proxy makes this transparent — `ReplayController` doesn't know if it's talking to a local driver or a remote one.

```
Local:  ReplayController → D3D11Replay (direct)
Remote: ReplayController → ReplayProxy ──socket──→ RemoteServer → D3D11Replay
```

**How it works**: The `IMPLEMENT_FUNCTION_PROXIED` macro generates proxied versions of every `IReplayDriver` method. In local mode, calls go directly to the driver. In remote mode, parameters are serialized, sent over the network, executed remotely, and results sent back.

**Key optimizations**:
- `DeltaTransferBytes()` — only sends changed bytes of large resources
- Resource caching — proxy caches textures/buffers after first fetch
- Replay state sync — current event ID synced across network

**Modification impact**: When adding a new `IReplayDriver` method, you **MUST** also add a `IMPLEMENT_FUNCTION_PROXIED` entry. Forgetting this causes the method to silently no-op in remote mode, which is very hard to debug.

## ReplayController

**File**: `renderdoc/replay/replay_controller.h`

**Why it exists**: It's the public API implementation that wraps `IReplayDriver` and adds action tree management, post-processing, and convenience features.

**Key internal flow — PostCreateInit**:
```
1. Load capture → driver.ReadLogInitialisation() — parses all chunks
2. Build action tree from serialized chunks (hierarchy of draw calls, dispatches, etc.)
3. Apply AddFakeMarkers() if no user debug markers present
4. Set initial event to first action
5. Cache action hierarchy in FrameRecord
```

**Modification impact**: Changes to `ReplayController` affect all APIs uniformly. If you need API-specific behavior, it should go in the `IReplayDriver` implementation, not here.

## CaptureFile

**File**: `renderdoc/replay/capture_file.cpp`

**Why the two-phase design**: `OpenFile()` is cheap (just reads metadata), while `OpenCapture()` is expensive (creates GPU context, loads all resources). This lets the GUI display file info, thumbnails, and structured data without GPU initialization.

**Flow**:
```
CaptureFile::OpenFile(path)     → Reads .rdc header, metadata, thumbnail
CaptureFile::GetStructuredData() → Parses chunks without GPU (for cap2json)
CaptureFile::OpenCapture(opts)  → Creates GPU context, full driver init
  → Looks up driver type from .rdc header
  → RenderDoc::Inst().CreateReplayDriver(type, rdc, &driver)
  → driver wrapped in ReplayController
  → Returns IReplayController* to caller
```

## OS Abstraction Layer

**File**: `renderdoc/os/os_specific.h`

**Why namespace-based (not classes)**: These are stateless utility functions, not objects. Namespaces provide clean grouping without instantiation overhead.

Key namespaces: `Process` (PID, injection, environment), `Threading` (threads, locks, SpinLock, JobSystem), `Network` (client/server sockets), `FileIO` (paths, temp files), `Callstack` (stack walking, symbol resolution), `Atomic` (atomic ops).

**Modification impact**: Platform-specific implementations are in `os/win32/`, `os/posix/`, `os/posix/android/`. Adding a new OS function requires implementing it on all supported platforms.

## Hook Framework

**File**: `renderdoc/hooks/hooks.h`

**Why different per platform**: Each OS has a fundamentally different mechanism for intercepting function calls:
- **Windows**: IAT (Import Address Table) patching — modifies the process's import table
- **Linux**: `LD_PRELOAD` + `dlsym` interception — interposes shared library symbols
- **Android**: PLT (Procedure Linkage Table) hooking + `interceptor-lib` — needed because Android's linker works differently

**Modification impact**: Hook additions must be tested on each platform. Windows hooks are applied at DLL load time; Linux hooks are set up via preloading.

## Data Types and Enumerations

**Files**: `renderdoc/api/replay/data_types.h`, `replay_enums.h`, `shader_types.h`, `common_pipestate.h`, `pipestate.h`

These define the vocabulary shared between all drivers and the public API. Key types:
- `ActionDescription` — a draw call / dispatch / clear / copy with event ID, flags, and parameters
- `ResourceFormat` — GPU pixel/vertex format (type, width, components, special encoding)
- `TextureDescription` / `BufferDescription` — resource metadata
- `ShaderReflection` — shader input/output signatures, constant buffer layouts, resource bindings
- `PipeState` — API-agnostic pipeline state abstraction (shaders, viewports, blend, depth, stencil)

There are **67 enumerations** in `replay_enums.h`. The most important: `GraphicsAPI`, `ShaderStage`, `ResourceUsage`, `ActionFlags`, `CompType`, `BindType`.

**Modification impact**: These types are serialized, exposed to Python via SWIG, and used across all drivers. Adding a new field to a struct or a new enum value requires checking serialization compatibility and SWIG regeneration.

## Python Binding Subsystem (pyrenderdoc)

> **📖 Full Reference**: See [pyrenderdoc_reference.md](pyrenderdoc_reference.md) for the complete file inventory, detailed conversion system, and development workflows.

**Why it exists**: RenderDoc needs Python bindings for three critical use cases: the interactive Python console in the GUI, Python extension scripts, and the entire functional test framework (186 test cases in `util/test/`).

**Why SWIG (not pybind11/ctypes)**: SWIG auto-generates wrappers from `.i` interface files that reference existing C++ headers. This avoids maintaining a separate binding layer — when C++ interfaces change, regenerating SWIG output picks up the changes. A custom SWIG fork (`renderdoc-modified-7`) handles RenderDoc-specific needs.

**How it works**: The SWIG interface files (`renderdoc.i`, `qrenderdoc.i`) reference C++ headers from `renderdoc/api/replay/`. SWIG generates `.cxx` wrapper files that compile into `.pyd/.so` Python modules. The `TypeConversion<T>` template system in `pyconversion.h` provides bidirectional conversion for all C++ types.

**Key architectural decisions**:
- **Dual modules**: `renderdoc` (standalone, core API) and `qrenderdoc` (GUI, imports renderdoc). This separation allows headless Python usage without Qt.
- **Template-based conversion**: `TypeConversion<T>` specializations handle every type uniformly, making it easy to add new types
- **External reference counting**: `SDChunk/SDObject/SDFile` tree structures need cross-language lifetime management via `obj2py<T>` mapping tables
- **GIL-aware callbacks**: `ConvertFunc<>` + `ScopedFuncCall` ensure Python callables can safely be invoked from C++ threads

**Modification impact**: Adding a new C++ type to Python requires: (1) `DOCUMENT()` macro for docstrings, (2) include in `renderdoc.i`, (3) `TypeConversion<T>` if custom conversion needed, (4) `TEMPLATE_ARRAY_INSTANTIATE` if used in arrays. Forgetting step 4 causes runtime crashes.
