# PyRenderDoc Reference

This document provides a comprehensive reference for the **pyrenderdoc** module — the Python binding subsystem that exposes RenderDoc's C++ replay API and GUI extension API to Python. It covers the SWIG interface export mechanism, type conversion infrastructure, container handling, reference counting, build system, and dependencies.

## Module Architecture Overview

**Directory**: `qrenderdoc/Code/pyrenderdoc/`

PyRenderDoc produces **two separate Python modules** from C++ headers using a custom SWIG fork:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     SWIG Code Generation                            │
│                                                                     │
│  renderdoc.i ──SWIG──→ renderdoc_module_python.cxx ──→ renderdoc.pyd│
│  (Core Replay API)        (generated C++ wrapper)     (Python module)│
│                                                                     │
│  qrenderdoc.i ──SWIG──→ qrenderdoc_module_python.cxx──→qrenderdoc.pyd│
│  (GUI Extension API)      (generated C++ wrapper)     (Python module)│
│  [%import "renderdoc.i"]                                            │
└─────────────────────────────────────────────────────────────────────┘
```

| Module | SWIG Interface | Output | Purpose | Standalone? |
|--------|---------------|--------|---------|-------------|
| `renderdoc` | `renderdoc.i` | `renderdoc.pyd/.so` | Core replay API: `IReplayController`, data types, enums | Yes |
| `qrenderdoc` | `qrenderdoc.i` | `qrenderdoc.pyd/.so` | GUI extension API: `ICaptureContext`, `IMainWindow`, extensions | Depends on `renderdoc` via `%import` |

## File Inventory (24 Files)

| File | Size | Purpose |
|------|------|---------|
| **SWIG Interface Files** | | |
| `renderdoc.i` | 21.65 KB | Core module interface: naming rules, typemaps, 22 C++ header includes, ~90 template instantiations |
| `qrenderdoc.i` | 8.18 KB | GUI module interface: imports `renderdoc.i`, wraps Qt-facing interfaces |
| `pyconversion.i` | 2.27 KB | SWIG typemap definitions for simple types, `std::function`, `rdcpair` |
| `container_handling.i` | 15.07 KB | SWIG macros for `rdcarray<T>` ↔ Python list mapping |
| `ext_refcounts.i` | 3.51 KB | SWIG macros for refcounted types (`SDChunk`, `SDObject`, `SDFile`) |
| `cosmetics.i` | 5.20 KB | `__str__`, `__repr__`, `__eq__`, `__hash__`, `__int__`, `__lt__` for ~30 types |
| **C++ Type Conversion** | | |
| `pyconversion.h` | 21.53 KB | Core `TypeConversion<T>` template system for C++ ↔ Python |
| `container_handling.h` | 17.32 KB | C++ implementations of array operations (list semantics) |
| `function_conversion.h` | 9.85 KB | Python callable → C++ `std::function<>` conversion |
| `structured_conversion.h` | 17.30 KB | Specialized conversion for `SDChunk*`, `SDObject*`, `SDFile*` with external refcounting |
| `ext_refcounts.h` | 2.95 KB | `MakeFromArgsTuple<T>` factory specializations |
| `interface_check.h` | 12.32 KB | Runtime naming/docstring validation (debug builds only) |
| **Python Runtime** | | |
| `PythonContext.h` | 6.47 KB | `PythonContext` class declaration (QObject-based) |
| `PythonContext.cpp` | 40.53 KB | Embedded interpreter: GIL management, module init, stdout redirect, PySide2 integration |
| **Stub Files** | | |
| `pyrenderdoc_stub.cpp` | 2.13 KB | No-op stubs for standalone `renderdoc.pyd` (no GUI context) |
| `qrenderdoc_stub.cpp` | 5.81 KB | No-op stubs for standalone `qrenderdoc.pyd` (docs generation) |
| **Build System** | | |
| `pyrenderdoc_module.vcxproj` | 8.06 KB | MSVC project for `renderdoc.pyd` |
| `qrenderdoc_module.vcxproj` | 8.48 KB | MSVC project for `qrenderdoc.pyd` |
| `CMakeLists.txt` | 3.08 KB | Linux/macOS build for both modules |
| `python.props` | 9.93 KB | MSBuild property sheet for Python version detection |
| **Utilities** | | |
| `python_sheet.xml` | 1.25 KB | Visual Studio property page for custom Python path |
| `va_stdafx.h` | 128 B | Visual Assist compatibility macro |

---

## SWIG Interface Export Mechanism

### Custom SWIG Fork

PyRenderDoc uses a **custom fork** of SWIG: `renderdoc-modified-7`, hosted at `https://github.com/baldurk/swig/archive/renderdoc-modified-7.zip`. The fork includes modifications specific to RenderDoc's needs.

**On Linux/macOS**: SWIG is built from source via CMake `ExternalProject`:
```cmake
# qrenderdoc/CMakeLists.txt (lines 186-194)
ExternalProject_Add(custom_swig
  URL https://github.com/baldurk/swig/archive/renderdoc-modified-7.zip
  DEPENDS pcre_build
  CMAKE_ARGS -DPCRE_INCLUDE_DIR=... -DPCRE_LIBRARY=...
)
```

**On Windows**: Pre-built SWIG binary from `qrenderdoc/3rdparty/swig/`.

### renderdoc.i — Core Module Interface

**File**: `qrenderdoc/Code/pyrenderdoc/renderdoc.i`

This is the primary SWIG interface file defining the `renderdoc` Python module. Key sections:

#### 1. DOCUMENT() Macro for Docstrings

```cpp
// Maps C++ documentation macros to Python docstrings
#define DOCUMENT(text) %feature("docstring") text
```

All C++ interfaces use `DOCUMENT()` to provide Python-visible docstrings.

#### 2. Naming Conventions via %rename

```swig
%rename("%(strip:[RENDERDOC_])s") "";           // Strip RENDERDOC_ prefix
%rename("%(regex:/^I([A-Z].*)/\\1/)s", %$isclass) ""; // Strip I prefix from interfaces
// Flatten namespace prefixes:
//   D3D11Pipe::Shader → D3D11Shader
//   GLPipe::Shader    → GLShader
```

#### 3. C++ Header Includes

The `.i` file wraps **22 C++ headers** from `renderdoc/api/replay/`:
- `renderdoc_replay.h` (8 core interfaces, 107KB)
- `data_types.h`, `shader_types.h`, `common_pipestate.h`, `pipestate.h`
- `replay_enums.h` (67 enumerations)
- `resourceid.h`, `stringise.h`, `rdcarray.h`, `rdcpair.h`, `rdcstr.h`, `rdcdatetime.h`

#### 4. Template Array Instantiations

~90 explicit instantiations mapping C++ container types to Python:
```swig
TEMPLATE_ARRAY_INSTANTIATE(ActionDescription, rdcarray<ActionDescription>)
TEMPLATE_ARRAY_INSTANTIATE(TextureDescription, rdcarray<TextureDescription>)
TEMPLATE_FIXEDARRAY_INSTANTIATE(ShaderStage, rdcfixedarray<ShaderStage, 6>)
// ... ~87 more
```

#### 5. Special Wrapper Functions

```cpp
%inline %{
  // Transfer ownership of Python-created objects to C++ side
  void PassObjectToPython(PyObject *obj) { ... }
  
  // Debug dump structured data
  rdcstr RENDERDOC_DumpObject(SDObject *obj) { ... }
%}
```

### qrenderdoc.i — GUI Extension Module Interface

**File**: `qrenderdoc/Code/pyrenderdoc/qrenderdoc.i`

Imports the core module and adds GUI-specific bindings:

```swig
%import "renderdoc.i"     // Share types with core module
```

Key additions:
- **`PythonCaptureViewer`**: Wrapper class implementing `ICaptureViewer` for Python extensions
- **`BlockInvoke`**: Wrapper that releases GIL during `ReplayManager::BlockInvoke()` execution
- **QWidget conversion**: `WrapBareQWidget()` / `UnwrapBareQWidget()` for PySide2 interop
- Wraps: `QRDInterface.h`, `PersistantConfig.h`, `RemoteHost.h`, `Extensions.h`

---

## Type Conversion System

### TypeConversion<T> Template

**File**: `qrenderdoc/Code/pyrenderdoc/pyconversion.h`

The core infrastructure for converting between C++ types and Python objects. Uses C++ template specialization to handle every type:

```
TypeConversion<T>
├── Specializations for primitives:
│   ├── bool, uint8_t, int8_t, uint16_t, int16_t
│   ├── uint32_t, int32_t, uint64_t, int64_t
│   ├── float, double, rdhalf
│   └── PyObject* (passthrough)
├── Specializations for string types:
│   ├── rdcstr (mutable string → Python str)
│   └── rdcinflexiblestr (immutable → Python str)
├── Specializations for compound types:
│   ├── rdcdatetime → Python datetime
│   ├── rdcpair<A,B> → Python tuple
│   ├── bytebuf → Python bytes
│   ├── rdcarray<U> → Python list (via container_handling)
│   └── rdcfixedarray<U,N> → Python tuple
├── Enum specialization (via underlying integer type)
└── Pointer specialization (SWIG-wrapped object)
```

Each specialization provides:
- `ConvertFromPy(PyObject *obj, T &out)` — Python → C++
- `ConvertToPy(const T &in)` → `PyObject*` — C++ → Python

### SWIG Typemaps

**File**: `qrenderdoc/Code/pyrenderdoc/pyconversion.i`

The `SIMPLE_TYPEMAPS(type)` macro generates SWIG `%typemap(in)`, `%typemap(out)`, `%typemap(argout)` directives that route through `TypeConversion<T>`:

```swig
%define SIMPLE_TYPEMAPS(type)
%typemap(in) type {
  if(!TypeConversion<type>::ConvertFromPy($input, $1)) SWIG_fail;
}
%typemap(out) type {
  $result = TypeConversion<type>::ConvertToPy($1);
}
%enddef
```

Applied to: `bool`, `int8_t`-`int64_t`, `uint8_t`-`uint64_t`, `float`, `double`, `rdcstr`, `rdcdatetime`, `bytebuf`, `ResourceId`, all `rdcpair<>` instantiations.

---

## Container Handling

### rdcarray<T> → Python List Semantics

**Files**: `container_handling.i` (SWIG macros), `container_handling.h` (C++ implementations)

`rdcarray<T>` is mapped to full Python list semantics via a set of SWIG macros:

#### Macro Pipeline

```
TEMPLATE_ARRAY_DECLARE(elemType)     → Declares %template for rdcarray<elemType>
TEMPLATE_ARRAY_INSTANTIATE(name, type) → Instantiates + extends with list methods
```

#### Python List Operations Implemented

| Python Operation | C++ Implementation Function | Notes |
|-----------------|---------------------------|-------|
| `len(arr)` | `array_len()` | |
| `arr[i]` | `array_getitem()` | Supports negative indexing |
| `arr[i] = v` | `array_setitem()` | Supports negative indexing |
| `arr[i:j]` | `array_getsubscript()` | Full slice support |
| `arr[i:j] = [...]` | `array_setsubscript()` | Slice assignment |
| `arr.append(v)` | `array_append()` | |
| `arr.insert(i, v)` | `array_insert()` | |
| `arr.pop([i])` | `array_pop()` | Optional index argument |
| `arr.sort()` | `array_sort()` | |
| `arr.reverse()` | `array_reverse()` | |
| `arr.copy()` | `array_copy()` | |
| `arr.extend(other)` | `array_selfconcat()` | |
| `arr.index(v)` | `array_indexOf()` | |
| `arr.count(v)` | `array_countOf()` | |
| `arr.remove(v)` | `array_removeOne()` | |
| `arr + other` | `array_concat()` | Returns new array |
| `arr * n` | `array_repeat()` | Returns new array |
| `repr(arr)` | `array_repr()` | |
| `arr.clear()` | `array_clear()` | |

#### Special Arrays (Non-template)

Three structured data arrays use `NON_TEMPLATE_ARRAY_INSTANTIATE` because they require external reference counting:
- `StructuredChunkList` (contains `SDChunk*`)
- `StructuredObjectList` (contains `SDObject*`)
- `StructuredBufferList` (contains `SDFile*`)

---

## External Reference Counting

### Problem

`SDChunk`, `SDObject`, and `SDFile` are tree-structured C++ objects owned by C++. When Python holds a reference to a child node, the parent might be deleted by C++, leaving a dangling pointer.

### Solution: ActiveRefcounter<T> + obj2py<T>

**Files**: `structured_conversion.h`, `ext_refcounts.h`, `ext_refcounts.i`

```
┌──────────────────────────────────────────────┐
│         External Reference Counting          │
│                                              │
│  obj2py<SDObject>: C++ ptr → PyObject* map   │
│                                              │
│  ExtRefcount<SDObject>::AddRef(ptr)          │
│    → Creates or finds PyObject in map        │
│    → Increments Python refcount              │
│                                              │
│  ExtRefcount<SDObject>::Release(ptr)         │
│    → Decrements Python refcount              │
│    → If refcount → 0: removes from map       │
│                                              │
│  Custom tp_dealloc:                          │
│    → Recursively releases child references   │
│    → Cleans up map entries                   │
└──────────────────────────────────────────────┘
```

**Key components**:
- `obj2py<T>()` — global mapping tables from C++ pointer to Python wrapper
- `ActiveRefcounter<T>` — template managing reference count operations
- `ExtRefcount<T>` — specializations with custom `DelPyObject` that handles child cleanup
- `RefcountConverter<T>` — SWIG conversion that participates in external refcounting
- `REFCOUNTED_TYPE(typeName)` macro — defines custom `tp_init`/`tp_dealloc` slots
- `MakeFromArgsTuple<T>` — factory specializations for constructing objects from Python args

---

## Function/Callback Conversion

### Python Callable → C++ std::function<>

**File**: `qrenderdoc/Code/pyrenderdoc/function_conversion.h`

When C++ APIs accept callbacks (e.g., `ReplayManager::AsyncInvoke`), Python callables must be safely converted:

```
ConvertFunc<funcType>(PyObject *callback)
  → Creates varfunc<rettype, paramTypes...> object
  → varfunc captures PyObject* with reference counting
  → When C++ invokes the std::function:
      1. ScopedFuncCall acquires GIL
      2. Converts C++ args → Python via TypeConversion<T>
      3. PyObject_Call(callback, args)
      4. Converts result back to C++
      5. ScopedFuncCall releases GIL
```

**Key safety mechanisms**:
- `PyObjectRefCounter` — prevents premature Python object destruction during C++ invocation
- `ScopedFuncCall` — RAII for GIL acquire/release + exception handling
- `ExceptionHandler` / `StackExceptionHandler` — cross-thread exception propagation
- `HandleCallbackFailure()` — different behavior for sync (raise immediately) vs async (queue for later) callbacks
- `varfunc<rettype, paramTypes...>` — variadic template for argument packing/unpacking

---

## PythonContext Runtime

### Embedded Python Interpreter Management

**Files**: `PythonContext.h`, `PythonContext.cpp`

`PythonContext` is a QObject-based class that manages the embedded Python interpreter within the qrenderdoc GUI.

### Initialization Flow

```
GlobalInit() [called once at application startup]:
  1. PyImport_AppendInittab("renderdoc", &PyInit__renderdoc)
  2. PyImport_AppendInittab("qrenderdoc", &PyInit__qrenderdoc)
  3. Py_Initialize()
  4. Import both modules
  5. Redirect sys.stdout / sys.stderr → OutputRedirector
  6. Detect PySide2/Shiboken2 availability
  7. PyEval_SaveThread() → release GIL for GUI thread
```

### Per-Context Setup

```
PythonContext() constructor:
  1. Copy main_dict (isolated namespace)
  2. Set up rlcompleter.Completer for code completion
  3. Ready for executeString() / executeFile() calls
```

### Code Execution

```
executeString(source):
  1. Py_CompileString(source, "<input>", Py_file_input)
  2. Set trace function (for line-by-line debugging)
  3. PyEval_EvalCode(compiled, context_dict, context_dict)
  4. If exception: fetch traceback → emit exception signal
```

### Extension Loading

```
LoadExtension(path):
  1. Import/reload module from path
  2. Call module.register(version, pyrenderdoc)
  3. Extension receives pyrenderdoc object (ICaptureContext)
  4. Extension can register menus, panels, event handlers
```

### PySide2/Shiboken Integration

Optional Qt ↔ Python interop for QWidget objects:

```
QWidgetToPy(QWidget *widget):
  if PySide2 available:
    → Shiboken::wrapInstance(widget, QWidget_type) → native PySide2 wrapper
  else:
    → SWIG opaque wrapper (limited functionality)

QWidgetFromPy(PyObject *widget):
  if PySide2 available:
    → Shiboken::getCppPointer(widget) → QWidget*
  else:
    → SWIG unwrap
```

### Thread Safety

- **GIL Management**: GUI thread releases GIL after initialization; Python execution acquires GIL
- **OutputRedirector**: Custom `sys.stdout`/`sys.stderr` that routes `write()` calls to Qt signals → GUI log panel
- **`PythonContextHandle`**: RAII wrapper that acquires/releases GIL for safe Python access from non-Python threads

---

## Interface Validation (Debug Builds)

**File**: `qrenderdoc/Code/pyrenderdoc/interface_check.h`

In debug builds, `check_interface()` validates the Python bindings at startup:

| Check | Rule | Purpose |
|-------|------|---------|
| Naming conventions | Types = PascalCase, members = camelCase, enum values allow underscores | Consistent API surface |
| Docstring uniqueness | No duplicate docstrings | Catches copy-paste errors |
| Enum documentation | All enum values must have docstrings | Complete documentation |
| Member `:type:` annotations | All documented members must have type annotations | Python type hints |

---

## Cosmetic Enhancements

**File**: `qrenderdoc/Code/pyrenderdoc/cosmetics.i`

Adds Pythonic special methods to ~30 types:

- `ResourceId`: `__int__`, `__hash__`, `__lt__`, `__str__`, `__repr__`
- `ResultDetails`: `__str__`, `__repr__`
- `DEFINE_SAFE_EQUALITY` macro: Safe `__eq__`/`__ne__` that handles `None` comparison without crash
- Applied to: `ShaderReflection`, `TextureDescription`, `BufferDescription`, `ActionDescription`, `PipeState`, and ~25 more

---

## Build System

### Windows Build (MSVC)

**Files**: `pyrenderdoc_module.vcxproj`, `qrenderdoc_module.vcxproj`, `python.props`

#### renderdoc.pyd Build

```
pyrenderdoc_module.vcxproj:
  1. CustomBuild: swig.exe -python -c++ -o renderdoc_module_python.cxx renderdoc.i
  2. Compile: pyrenderdoc_stub.cpp + renderdoc_module_python.cxx (generated)
  3. Link: renderdoc.lib (core library) + pythonXY.lib
  4. Output: $(Platform)/$(Configuration)/pymodules/renderdoc.pyd
```

#### qrenderdoc.pyd Build

```
qrenderdoc_module.vcxproj:
  1. CustomBuild: swig.exe on BOTH renderdoc.i AND qrenderdoc.i
  2. Compile: both stub files + both generated .cxx files
  3. Link: renderdoc.lib + pythonXY.lib
  4. Output: $(Platform)/$(Configuration)/pymodules/qrenderdoc.pyd
```

#### Python Version Detection (python.props)

Cascading detection supporting Python 3.4 through 3.15:

```
Priority:
  1. VSPythonOverridePath (user override)
  2. RENDERDOC_PYTHON_PREFIX32/64 (environment variable)
  3. Built-in 3rdparty/python (Python 3.6 bundled)

Detection checks for each version:
  - Python.h exists
  - pythonXY.zip exists
  - pythonXY.lib exists
```

### Linux/macOS Build (CMake)

**File**: `qrenderdoc/Code/pyrenderdoc/CMakeLists.txt`

```cmake
# Build standalone renderdoc.so
add_library(_renderdoc MODULE
  renderdoc_python.cxx      # Generated by SWIG
  pyrenderdoc_stub.cpp       # No-op stubs (no GUI context)
)
target_link_libraries(_renderdoc PRIVATE renderdoc)
set_target_properties(_renderdoc PROPERTIES
  PREFIX ""                   # Remove "lib" prefix
  OUTPUT_NAME "renderdoc"     # → renderdoc.so
)

# Build standalone qrenderdoc.so
add_library(_qrenderdoc MODULE
  renderdoc_python.cxx
  qrenderdoc_python.cxx      # Generated by SWIG
  pyrenderdoc_stub.cpp
  qrenderdoc_stub.cpp
)
target_link_libraries(_qrenderdoc PRIVATE renderdoc)
set_target_properties(_qrenderdoc PROPERTIES
  PREFIX ""
  OUTPUT_NAME "qrenderdoc"    # → qrenderdoc.so
)
```

Both targets depend on the `swig-bindings` target (which runs SWIG code generation).

#### SWIG Code Generation (Linux/macOS)

From `qrenderdoc/CMakeLists.txt` (lines 346-372):
```cmake
add_custom_command(
  OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/renderdoc_python.cxx
  COMMAND custom_swig -python -c++ -threads
    -I${CMAKE_CURRENT_SOURCE_DIR}/Code/pyrenderdoc
    -I${RDOC_SOURCE_DIR}/api/replay
    -o ${CMAKE_CURRENT_BINARY_DIR}/renderdoc_python.cxx
    -outdir ${CMAKE_CURRENT_BINARY_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/Code/pyrenderdoc/renderdoc.i
  DEPENDS custom_swig renderdoc.i pyconversion.i container_handling.i ...
)
```

---

## Dependencies

### Required Dependencies

| Dependency | Version | Purpose |
|-----------|---------|---------|
| **librenderdoc** | (same build) | Core replay API library that Python modules wrap |
| **Python** | 3.4–3.15 | Python interpreter and development libraries (`Python.h`, `pythonXY.lib`) |
| **SWIG** | `renderdoc-modified-7` (custom fork) | Generates C++ → Python wrapper code from `.i` interface files |

### Build-time Dependencies

| Dependency | Platform | Purpose |
|-----------|----------|---------|
| **PCRE** | Linux/macOS | Required by SWIG for regex pattern matching |
| **CMake ExternalProject** | Linux/macOS | Builds SWIG from source |
| **Visual Studio** | Windows | MSVC compiler for `.vcxproj` builds |

### Optional Dependencies

| Dependency | Purpose | Fallback |
|-----------|---------|----------|
| **PySide2** | Native Qt QWidget ↔ Python conversion | Opaque SWIG wrapping (limited functionality) |
| **Shiboken2** | PySide2's binding generator (provides `wrapInstance`/`getCppPointer`) | N/A (paired with PySide2) |

### Dependency Graph

```
renderdoc.pyd
  ├── librenderdoc.dll/so (core replay library)
  ├── pythonXY.dll/so (Python runtime)
  └── [built with] SWIG renderdoc-modified-7

qrenderdoc.pyd
  ├── librenderdoc.dll/so (core replay library)
  ├── pythonXY.dll/so (Python runtime)
  ├── [type sharing] renderdoc module (via %import)
  └── [built with] SWIG renderdoc-modified-7

PythonContext (embedded in qrenderdoc GUI)
  ├── renderdoc module (imported at init)
  ├── qrenderdoc module (imported at init)
  ├── Python interpreter (embedded via Py_Initialize)
  └── [optional] PySide2 + Shiboken2 (for QWidget interop)
```

---

## Development Workflows

### Adding a New C++ Type to Python Bindings

1. **Define the type** in the appropriate C++ header (e.g., `data_types.h`)
2. **Add DOCUMENT() macro** for Python docstrings
3. **Add to `renderdoc.i`**: Include the header, add any needed `%rename` or `%ignore` directives
4. **If the type uses `rdcarray<T>`**: Add `TEMPLATE_ARRAY_INSTANTIATE(TypeName, rdcarray<TypeName>)` in `renderdoc.i`
5. **If the type needs custom conversion**: Add `TypeConversion<T>` specialization in `pyconversion.h`
6. **If the type needs cosmetic methods**: Add `__str__`, `__eq__`, etc. in `cosmetics.i`
7. **Rebuild**: SWIG re-generates the wrapper; compile and link

### Adding a New Python-Accessible Method to an Existing Interface

1. **Add the method** to the C++ interface (e.g., `IReplayController`)
2. **Ensure DOCUMENT() macro** is present for the method
3. **If new parameter types are used**: Ensure `TypeConversion<T>` exists for them
4. **SWIG will auto-wrap** the method if the interface is already included in `renderdoc.i`
5. **Test**: The method should appear in Python with correct docstring and type conversion

### Adding a New Callback/std::function Parameter

1. **Define the callback signature** in C++ (e.g., `std::function<void(float)>`)
2. **Add a `SIMPLE_TYPEMAPS` entry** in `pyconversion.i` if the callback type is new
3. **SWIG typemap for `std::function`** in `pyconversion.i` handles the conversion via `ConvertFunc<>()`
4. **Test**: Python callables should be automatically converted to C++ std::function at call sites

### Debugging Python Binding Issues

- **`interface_check.h`**: Enable debug builds to catch naming/docstring issues at startup
- **SWIG `-debug-typemap` flag**: Add to SWIG command to trace typemap resolution
- **`cap2json` + Python console**: Use structured export + interactive Python to test bindings
- **Check stub files**: If a function works in GUI but not standalone, check if the stub provides a no-op that masks the real behavior

### Common Pitfalls

- ❌ **NEVER** forget `TEMPLATE_ARRAY_INSTANTIATE` for new `rdcarray<T>` types — SWIG will generate a broken wrapper that crashes at runtime
- ❌ **NEVER** modify the SWIG `.i` files without regenerating — stale generated `.cxx` files cause mysterious compilation or runtime errors
- ❌ **NEVER** hold the GIL while calling back into C++ replay functions — this causes deadlocks with the replay thread
- ❌ **NEVER** assume PySide2 is available — always check and fall back to opaque wrapping
- ❌ **NEVER** reorder chunk enum values in `renderdoc.i` `%rename` directives — this changes the Python-visible API surface
