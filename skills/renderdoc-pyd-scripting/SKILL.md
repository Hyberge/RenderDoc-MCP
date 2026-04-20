---
name: renderdoc-pyd-scripting
description: "指导 LLM 直接编写使用 renderdoc.pyd 的 Python 脚本来分析 GPU 帧捕获（.rdc 文件），无需通过 MCP Server。当用户要求分析 RenderDoc 截帧、调试渲染问题、导出纹理、查看管线状态、进行像素调试、Shader 调试或性能分析时使用此 Skill。Skill 目录内自包含 renderdoc.pyd 及所有依赖 DLL，生成的脚本可直接运行。"
---

# RenderDoc Python Scripting Guide

## Prerequisites

- **Python 版本**：必须与 `pymodule/VERSION.txt` 中记录的版本一致（当前为 **Python 3.14**）
- `pymodule/` 目录包含 `renderdoc.pyd`、`renderdoc.dll` 及所有运行时依赖
- 仅支持 **Windows x64**

## Core Directives

1. **所有脚本必须自包含**：包含完整的模块加载、初始化、业务逻辑、清理代码
2. **使用 try/finally 确保资源释放**：controller.Shutdown() → cap.Shutdown() → rd.ShutdownReplay()
3. **从 Skill 目录的 `pymodule/` 加载 renderdoc**：使用下方脚本模板
4. **输出使用 print + json**：便于用户阅读和后续处理
5. **路径参数化**：.rdc 文件路径通过变量或命令行参数传入，不要硬编码

## Script Template

所有脚本都基于此骨架。`PYMODULE_DIR` 指向 Skill 目录下的 `pymodule/`。

```python
"""<脚本用途描述>"""
import os, sys, json

# ── 加载 renderdoc 模块 ──
PYMODULE_DIR = r"<SKILL_DIR>/pymodule"  # 替换为 Skill 目录的绝对路径
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

# ── 配置 ──
RDC_FILE = r"<path-to-capture>.rdc"

# ── 初始化 ──
rd.InitialiseReplay(rd.GlobalEnvironment(), [])

cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded, f"OpenFile failed: {result}"

result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded, f"OpenCapture failed: {result}"

try:
    # ── 业务逻辑 ──
    # controller.SetFrameEvent(eid, True)
    # state = controller.GetPipelineState()
    # ...
    pass

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

**生成脚本时**，将 `PYMODULE_DIR` 替换为实际的 Skill 目录绝对路径（此 SKILL.md 所在目录 + `/pymodule`）。

## Task Routing

| 用户意图 | 对应 Recipe | 关键 API |
|---------|------------|---------|
| 打开截帧查看基本信息 | Recipe 1 | `GetRootActions()`, `GetTextures()`, `GetBuffers()` |
| 遍历/搜索 draw call | Recipe 2 | `GetRootActions()`, `ActionFlags` |
| 查看管线状态 | Recipe 3 | `GetPipelineState()`, API-specific state |
| 导出纹理为图片 | Recipe 4 | `SaveTexture()`, `TextureSave` |
| 读取 CBuffer 常量值 | Recipe 5 | `GetCBufferVariableContents()` |
| 像素历史追踪 | Recipe 6 | `PixelHistory()` |
| Shader 调试 | Recipe 7 | `DebugPixel()`/`DebugVertex()`, `ContinueDebug()`, `FreeTrace()` |
| 性能计数器采集 | Recipe 8 | `EnumerateCounters()`, `FetchCounters()` |
| 扫描异常像素 | Recipe 9 | `PickPixel()`, NaN/Inf 检测 |
| 导出 Mesh 数据 | Recipe 10 | `GetPostVSData()`, `GetBufferData()` |

完整 Recipe 代码见 [recipes.md](references/recipes.md)。

## Critical Pitfalls

### PipeState API（最常犯错）

`controller.GetPipelineState()` 返回 API-agnostic 的 `PipeState`，但接口有限：

| 错误用法（不存在） | 正确用法 | 说明 |
|-------------------|---------|------|
| `state.GetViewports()` | `state.GetViewport(i)` | 逐个索引遍历，i=0,1,... |
| `state.GetScissors()` | `state.GetScissor(i)` | 同上 |
| `state.GetColorBlend()` | `state.GetColorBlends()` | 返回 List[ColorBlend] |
| `state.GetStencilState()` | `state.GetStencilFaces()` | 返回 (front, back) 元组 |
| `state.GetDepthState()` | 需用 API-specific state | 见下方 |
| `state.GetRasterizer()` | 需用 API-specific state | 见下方 |

**获取 depth/rasterizer 状态**（必须根据 API 类型分发）：

```python
api = cap.DriverName()
if "D3D12" in api:
    pipe = controller.GetD3D12PipelineState()
    depth = pipe.outputMerger.depthStencilState
    raster = pipe.rasterizer.state
elif "D3D11" in api:
    pipe = controller.GetD3D11PipelineState()
    depth = pipe.outputMerger.depthStencilState
    raster = pipe.rasterizer.state
elif "Vulkan" in api:
    pipe = controller.GetVulkanPipelineState()
    depth = pipe.depthStencil
    raster = pipe.rasterizer
elif "OpenGL" in api:
    pipe = controller.GetGLPipelineState()
    depth = pipe.depthState
    raster = pipe.rasterizer
```

### Shader 调试生命周期

```python
trace = controller.DebugPixel(x, y, inputs)  # 返回 ShaderDebugTrace
# trace.debugger 是句柄，NOT 预填充的状态
states = []
while True:
    new_states = controller.ContinueDebug(trace.debugger)
    if not new_states:
        break
    states.extend(new_states)
# 每个 state 有 .changes (list[ShaderVariableChange])
# change.after 是 ShaderVariable，含 value
controller.FreeTrace(trace)  # 必须释放！
```

### 其他常见错误

- `GetFrameInfo()` 返回**单个** FrameDescription，不是列表
- `GetHistogram()` channels 参数是 **tuple** `(R,G,B,A)`，不是 4 个独立 bool
- SWIG 枚举用 `int(enum_value)` 转换比较，`str()` 不含类名前缀
- `ResourceId` 比较用 `int(rid) != 0` 判断是否为空
- `PickPixel` 需要 `rd.Subresource(mip, slice, sample)` 和 `rd.CompType.Typeless`
- `GetCBufferVariableContents()` 需要正确的 `byteOffset`/`byteSize`（从绑定信息获取）

## Reference Documents

按需加载详细参考：

- **[api_reference.md](references/api_reference.md)** — renderdoc.pyd 完整 API 参考（类、方法、参数、返回值）
- **[recipes.md](references/recipes.md)** — 10 个完整可运行的 Python 脚本 Recipe
- **[enums_and_types.md](references/enums_and_types.md)** — 枚举值和数据类型速查表
