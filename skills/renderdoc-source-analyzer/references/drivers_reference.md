# Drivers Reference

This document covers the **architectural differences** between RenderDoc's 5 graphics API drivers, explains **why** each driver works the way it does, and highlights **what you must know** when modifying driver code.

## Shared Pattern: Wrapped Object Architecture

All mature drivers (D3D11, D3D12, Vulkan, GL) follow the same interception pattern:

```
Application → Wrapped API Object → Real API Object
                    │
                    ├── ResourceManager (tracking + dirty detection)
                    ├── Serialiser (chunk recording during capture)
                    └── Pipeline state tracking (for replay inspection)
```

**Common driver file structure** (prefix varies per API):
- `*_device.h/.cpp` — Wrapped device (main entry point, largest file)
- `*_replay.h/.cpp` — `IReplayDriver` implementation
- `*_resources.h/.cpp` — ResourceManager configuration + resource records
- `*_renderstate.h/.cpp` — Pipeline state tracking struct
- `*_debug.h/.cpp` — Shader debugging + overlay rendering
- `*_hooks.cpp` — API function hooking
- `*_counters.cpp` — GPU performance counter collection

---

## D3D11 Driver

**Directory**: `renderdoc/driver/d3d11/`

**Interception mechanism**: COM interface wrapping. `WrappedID3D11Device` implements `ID3D11Device5` through multiple inheritance, forwarding to the real `m_pDevice`.

**Why COM wrapping works here**: D3D11 objects are COM interfaces with virtual function tables. By inheriting from the same interfaces and holding a pointer to the real object, we can transparently intercept every call.

**Key characteristics**:
- Single-threaded command submission (immediate context) — simplifies serialization
- `WrappedID3D11DeviceContext` handles both immediate and deferred contexts
- Map/Unmap tracking for dirty detection on mapped resources
- ~200+ chunk types covering all D3D11 API calls

**Modification tips**:
- New API calls: follow existing pattern in the wrapped device/context
- D3D11 is the simplest driver — good reference implementation for understanding the pattern
- Deferred contexts add complexity: chunks are recorded per-context and flattened at execute time

---

## D3D12 Driver

**Directory**: `renderdoc/driver/d3d12/`

**Interception mechanism**: COM wrapping, same as D3D11 but vastly more complex. `WrappedID3D12Device` implements up to `ID3D12Device14` — **15 interface versions**, the largest COM surface of any driver.

**Why thread-local serializers**: D3D12's explicit multi-threading model allows recording commands on multiple threads simultaneously. Each thread MUST have its own `WriteSerialiser` via `GetThreadSerialiser()` — shared serializers would cause data corruption. **Never add shared mutable state to D3D12 serialization code.**

**Key characteristics**:
- Thread-local serializers (`GetThreadSerialiser()`)
- Command list wrapping up to `ID3D12GraphicsCommandList10`
- Descriptor heap tracking with copy-on-write for descriptor tables
- Root signature tracking (root constants, root descriptors, descriptor tables)
- Resource barrier / state transition tracking for correct replay
- Raytracing support (acceleration structure build/trace)
- GPU sync via fences (`m_GPUSyncFence`)

**Modification tips**:
- Always use `GetThreadSerialiser()`, never a shared serializer
- New command list methods: must wrap in `WrappedID3D12GraphicsCommandList` (check which interface version added the method)
- Descriptor changes: be very careful with the copy-on-write descriptor table mechanism
- Resource states: D3D12 requires explicit resource barriers; the state tracker in `D3D12RenderState` must stay in sync

---

## Vulkan Driver

**Directory**: `renderdoc/driver/vulkan/`

**Interception mechanism**: Vulkan Layer. Instead of COM wrapping, RenderDoc registers as a Vulkan layer (`vk_layer.cpp`) that the loader inserts between the application and the ICD (GPU driver).

**Why a Layer (not wrapping)**: Vulkan's design includes an explicit layer mechanism for tools. It's the officially supported interception path — no hacking required.

**Dispatch table architecture**: Instead of virtual functions, Vulkan uses function pointer dispatch tables. `WrappedVulkan` maintains both `VkInstDispatchTable` (instance-level) and `VkDevDispatchTable` (device-level, ~300+ function pointers) pointing to the real ICD functions.

```
Application → vkCmdDraw() → Loader → RenderDoc Layer → ICD
                                        │
                                        └── WrappedVulkan records chunk,
                                            then calls m_DeviceDispatchTable.CmdDraw()
```

**Key characteristics**:
- **167KB header file** (`vk_core.h`) — NEVER read in full, always search
- Explicit object creation/binding (no global state)
- Command buffer recording tracked independently per command buffer
- Synchronization objects (fences, semaphores, events) must be correctly replayed
- Memory management tracking (VkDeviceMemory allocation + binding)
- Descriptor set model with layouts and pools
- Render passes with subpass dependencies
- Hundreds of extensions with varying support
- Sparse binding and raytracing support

**Modification tips**:
- New Vulkan functions: add to dispatch table, add hook in `vk_layer.cpp`, implement wrapper in `WrappedVulkan`
- Extensions: must check extension support before using functions; add capability tracking
- Command buffer state: each `BakedCmdBufferInfo` tracks independently — don't assume global state

---

## OpenGL Driver

**Directory**: `renderdoc/driver/gl/`

**Interception mechanism**: Function hooking via 4 platform-specific backends:
- `wgl_hooks.cpp` — Windows WGL
- `glx_hooks.cpp` — Linux GLX
- `egl_hooks.cpp` — EGL (Android, Linux Wayland)
- `gl_hooks.cpp` — Common GL function hooks

**Why global state is a challenge**: Unlike D3D/Vulkan, OpenGL has implicit global state. There are no explicit pipeline objects — state is set via `glEnable`, `glBindTexture`, etc. This means the driver must track **hundreds** of state variables to correctly capture and replay.

**Key characteristics**:
- **164KB header file** (`gl_driver.h`) — NEVER read in full, always search
- Name-based resources (`GLuint` integer names, not pointers)
- Context sharing complicates resource ownership tracking
- `GLRenderState` tracks everything: VAO, 192 texture binding points, 84 UBOs, 84 SSBOs, framebuffers, shaders, rasterization state, etc.
- Must handle both core and compatibility GL profiles
- DSA (Direct State Access) extensions bypass the bind-to-edit model

**Modification tips**:
- New GL functions: add hook in the appropriate `*_hooks.cpp`, implement wrapper in `WrappedOpenGL`
- State tracking: if a new state is set by the function, add tracking in `GLRenderState`
- Context sharing: resource creation/deletion may affect multiple contexts

---

## Metal Driver (Early Development)

**Directory**: `renderdoc/driver/metal/`

**Interception mechanism**: Objective-C method swizzling. Replaces method implementations at runtime.

**Current status**:
- `ENABLE_METAL` defaults to **OFF**
- Does **NOT** implement `IReplayDriver` — **cannot replay captures**
- `MetalReplay` class exists but only provides `GetResourceDesc()`
- Only basic capture-side hooking: device creation, command buffer wrapping, basic resource tracking
- No shader debugging, no pixel history, no overlay rendering

**Modification tips**: Metal is a work-in-progress. Don't assume any replay functionality exists. Check `metal_device.h` for current capabilities.

---

## DXGI Shared Infrastructure

**Directory**: `renderdoc/driver/dxgi/`

**Why it exists**: DXGI (DirectX Graphics Infrastructure) is shared between D3D11 and D3D12 for swap chain management. `WrappedIDXGISwapChain` intercepts `Present()` calls for frame capture boundaries; `WrappedIDXGIFactory` routes device creation through RenderDoc.

---

## Shader Processing Subsystem

**Directory**: `renderdoc/driver/shaders/`

Three independent modules for three shader bytecode formats:

| Module | Format | Used By | Key Capabilities |
|--------|--------|---------|-----------------|
| `dxbc/` | DirectX Bytecode (SM 4.0-5.1) | D3D11, D3D12 (legacy) | Container parsing, reflection, disassembly, software debugger |
| `dxil/` | DirectX IL (SM 6.0+) | D3D12 (modern) | LLVM bitcode parsing, reflection from metadata, DXIL debugging |
| `spirv/` | SPIR-V | Vulkan, OpenGL | Reflection, disassembly, software debugger, binary editing, cross-compilation |

**Shader debugging flow**:
```
DebugPixel(x, y) request
  → Driver identifies bound pixel shader at current event
  → Extract bytecode (DXBC / DXIL / SPIR-V)
  → Initialize software debug state:
      - Interpolated input variables (from vertices)
      - Constant buffer values (from GPU readback)
      - Texture/sampler bindings (software-emulated sampling)
  → Step through bytecode instruction by instruction
  → Record all intermediate variable values
  → Return ShaderDebugTrace with full execution history
```

**Shader hot-reload flow**:
```
User edits shader → Compile to bytecode (d3dcompiler/glslang/dxc)
  → driver.ReplaceResource(originalId, newBytecode)
  → Replay with modified shader → User sees updated rendering
```

**Modification tips**:
- Each format module is self-contained — changes to DXBC don't affect SPIR-V
- Adding new shader instructions: update both the disassembler and the software debugger
- SPIR-V has the richest tooling (editing, cross-compilation via SPIRV-Cross)

---

## IHV (Hardware Vendor) Integration

**Directory**: `renderdoc/driver/ihv/`

| Vendor | Directory | Key Features |
|--------|-----------|-------------|
| **AMD** | `ihv/amd/` | ISA disassembly (GCN/RDNA), GPU counters (GPUPerfAPI), RGP profiler integration, AGS features |
| **NVIDIA** | `ihv/nv/` | NVAPI for vendor D3D extensions, PerfKit counters, Aftermath crash analysis, vendor VK extensions |
| **Intel** | `ihv/intel/` | GPU counters via Metrics Discovery API |
| **ARM** | `ihv/arm/` | Mali GPU counters via HWCPipe, tile-based rendering metrics |

**Modification tips**: IHV code depends on vendor SDKs in `official/` subdirectories. These SDKs have their own licenses and version requirements. Feature availability varies by GPU generation.

---

## Driver Capability Matrix

| Feature | D3D11 | D3D12 | Vulkan | OpenGL | Metal |
|---------|-------|-------|--------|--------|-------|
| Frame capture | ✅ | ✅ | ✅ | ✅ | 🔨 |
| Replay | ✅ | ✅ | ✅ | ✅ | ❌ |
| Pipeline state | ✅ | ✅ | ✅ | ✅ | ❌ |
| Shader debug | ✅ | ✅ | ✅ | ✅ | ❌ |
| Pixel history | ✅ | ✅ | ✅ | ✅ | ❌ |
| Overlays | ✅ | ✅ | ✅ | ✅ | ❌ |
| GPU counters | ✅ | ✅ | ✅ | ✅ | ❌ |
| ISA disassembly | AMD/NV | AMD/NV | AMD/NV | AMD/NV | ❌ |
| Remote replay | ✅ | ✅ | ✅ | ✅ | ❌ |
| Multi-threaded | N/A | ✅ | ✅ | N/A | ❌ |
| Raytracing | N/A | ✅ | ✅ | N/A | ❌ |

## Driver Registration

Each driver self-registers during static initialization:
```cpp
// Simplified — each driver does this in its *_device.cpp:
static struct DriverRegistration {
  DriverRegistration() {
    RenderDoc::Inst().RegisterDriver(RDCDriver::D3D11, "D3D11");
    RenderDoc::Inst().SetReplayDriverCreator(RDCDriver::D3D11, &D3D11Replay::Create);
  }
} registration;
```

Replay driver creation:
```
CaptureFile::OpenCapture()
  → Read RDCDriver enum from .rdc header
  → RenderDoc::Inst().CreateReplayDriver(type, rdc, &driver)
  → Returns the registered factory's IReplayDriver*
  → Wrapped in ReplayController for public API
```
