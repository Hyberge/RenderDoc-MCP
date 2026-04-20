# RenderDoc Python API 参考

本文档是 `renderdoc.pyd` 模块的 API 参考，面向独立 Python 脚本编写。

## 初始化与全局函数

```python
rd.InitialiseReplay(rd.GlobalEnvironment(), [])  # 初始化回放系统（进程级，调一次）
rd.ShutdownReplay()                                # 关闭回放系统（进程退出前调）
rd.OpenCaptureFile()                               # 创建 CaptureFile 句柄
rd.SetConfigSetting("DXBC.Debug.SearchDirPaths")   # 获取/设置配置项（如 shader PDB 路径）
```

## CaptureFile

```python
cap = rd.OpenCaptureFile()
result = cap.OpenFile(filepath, "", None)       # ResultCode
cap.LocalReplaySupport()                         # bool - 本机是否支持回放
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)  # (ResultCode, ReplayController)
cap.DriverName()                                  # str - API 名称 ("D3D12", "Vulkan" 等)
cap.Shutdown()                                    # 释放文件句柄
```

## ReplayController

核心类，所有帧分析都通过它进行。

### 帧导航

```python
controller.SetFrameEvent(eventId, True)    # 导航到指定事件（True = 强制刷新）
controller.GetRootActions()                # List[ActionDescription] - 根级 action 树
controller.GetStructuredFile()             # StructuredFile - 用于 action.GetName(sf)
```

### 管线状态（PipeState — API-agnostic）

```python
state = controller.GetPipelineState()

# 拓扑
state.GetPrimitiveTopology()            # Topology 枚举

# 视口 — 逐索引遍历，没有 GetViewports() 方法！
state.GetViewport(0)                    # Viewport: .x, .y, .width, .height, .minDepth, .maxDepth

# 裁剪 — 逐索引遍历，没有 GetScissors() 方法！
state.GetScissor(0)                     # Scissor: .x, .y, .width, .height

# 混合 — 返回列表，没有 GetColorBlend() 单数方法！
state.GetColorBlends()                  # List[ColorBlend]: .enabled, .colorBlend.source/dest/operation, .alphaBlend...

# 模板 — 返回元组，没有 GetStencilState() 方法！
front, back = state.GetStencilFaces()   # StencilFace: .passOperation, .failOperation, .depthFailOperation, .function

# 输出目标
state.GetOutputTargets()                # List[Descriptor] - RT 绑定
state.GetDepthTarget()                  # Descriptor - 深度目标

# 顶点输入
state.GetIBuffer()                      # BoundVBuffer - Index Buffer
state.GetVBuffers()                     # List[BoundVBuffer] - Vertex Buffers
state.GetVertexInputs()                 # List[VertexInputAttribute]

# API 类型检测
state.IsCaptureD3D11()                  # bool
state.IsCaptureD3D12()                  # bool
state.IsCaptureVK()                     # bool
state.IsCaptureGL()                     # bool

# Shader 信息
state.GetShaderReflection(stage)        # ShaderReflection
state.GetShaderEntryPoint(stage)        # str
state.GetGraphicsPipelineObject()       # ResourceId
state.GetComputePipelineObject()        # ResourceId

# 绑定查询
state.GetConstantBlock(stage, idx, 0)   # Bindpoint → .descriptor: ShaderBindpointMapping
state.GetReadOnlyResources(stage)       # List[BoundResourceArray] - SRV
state.GetReadWriteResources(stage)      # List[BoundResourceArray] - UAV
state.GetSamplers(stage)                # List[BoundResourceArray]
```

### API-Specific Pipeline State（深度/光栅化）

PipeState 不暴露 depth/rasterizer，需根据 API 类型获取：

```python
# D3D12
pipe = controller.GetD3D12PipelineState()
ds = pipe.outputMerger.depthStencilState  # .depthEnable, .depthFunction, .depthWrites, .stencilEnable
rs = pipe.rasterizer.state                 # .fillMode, .cullMode, .frontCCW, .depthBias, .depthClip

# D3D11
pipe = controller.GetD3D11PipelineState()
ds = pipe.outputMerger.depthStencilState
rs = pipe.rasterizer.state

# Vulkan
pipe = controller.GetVulkanPipelineState()
ds = pipe.depthStencil                     # .depthTestEnable, .depthFunction, .depthWriteEnable
rs = pipe.rasterizer                       # .fillMode, .cullMode, .frontCCW, .depthBiasEnable

# OpenGL
pipe = controller.GetGLPipelineState()
ds = pipe.depthState                       # .depthEnable, .depthFunction, .depthWrites
rs = pipe.rasterizer.state                 # .fillMode, .cullMode
```

### 资源查询

```python
controller.GetTextures()                # List[TextureDescription]
controller.GetBuffers()                 # List[BufferDescription]
controller.GetResources()               # List[ResourceDescription]
controller.GetUsage(resourceId)         # List[EventUsage] - 资源使用历史
```

### 数据读取

```python
# 读取 buffer 原始字节
data = controller.GetBufferData(bufferId, offset, length)  # bytes

# 读取单个像素
pixel = controller.PickPixel(texId, x, y,
    rd.Subresource(mip, slice, sample),
    rd.CompType.Typeless)  # PixelValue: .floatValue[4]

# 纹理统计
minval, maxval = controller.GetMinMax(texId,
    rd.Subresource(mip, slice, sample),
    rd.CompType.Typeless)  # (PixelValue, PixelValue)

# 保存纹理到文件
texsave = rd.TextureSave()
texsave.resourceId = texId
texsave.destType = rd.FileType.PNG
texsave.mip = 0
texsave.slice.sliceIndex = 0
texsave.alpha = rd.AlphaMapping.Preserve
controller.SaveTexture(texsave, outputPath)  # bool - 成功/失败

# 直方图
histogram = controller.GetHistogram(texId,
    rd.Subresource(0, 0, 0), rd.CompType.Typeless,
    minVal, maxVal,
    (True, True, True, False))  # channels 是 tuple(R,G,B,A)，不是 4 个独立参数！
# 返回 List[int]，256 个 bucket
```

### 顶点数据

```python
postvs = controller.GetPostVSData(instId=0, viewId=0,
    stage=rd.MeshDataStage.VSOut)
# postvs.vertexResourceId   - 顶点数据 buffer 的 ResourceId
# postvs.vertexByteOffset   - 字节偏移
# postvs.vertexByteStride   - 每顶点步长
# postvs.numIndices          - 顶点/索引数量
# postvs.indexResourceId     - 索引 buffer ResourceId（如果有）
# postvs.indexByteOffset     - 索引 buffer 偏移
# postvs.indexByteStride     - 索引大小（2 或 4 字节）
```

### Shader 分析

```python
# 反汇编
targets = controller.GetDisassemblyTargets(True)  # List[str] - 可用目标
disasm = controller.DisassembleShader(pipeline, refl, target)  # str - 反汇编代码

# 读取常量缓冲区变量
cb_bind = state.GetConstantBlock(stage, cbufferIdx, 0)
vars = controller.GetCBufferVariableContents(
    pipeline, shaderId, stage, entryPoint,
    cbufferIdx,
    cb_bind.descriptor.resource,
    cb_bind.descriptor.byteOffset,    # 必须从绑定获取！
    cb_bind.descriptor.byteSize       # 传 0,0 在 D3D12 会全零
)  # List[ShaderVariable]

# HLSL 源码（需要 shader PDB）
refl = state.GetShaderReflection(stage)
if refl.debugInfo and len(refl.debugInfo.files) > 0:
    for f in refl.debugInfo.files:
        print(f.filename, f.contents)
```

### 像素历史与调试

```python
# 像素修改历史
history = controller.PixelHistory(texId, x, y,
    rd.Subresource(0, 0, 0), rd.CompType.Typeless)
# List[PixelModification]:
#   mod.eventId, mod.directShaderWrite, mod.unboundPS
#   mod.Passed() → bool
#   mod.preMod, mod.postMod → ModificationValue: .col.floatValue[4]
#   mod.shaderOut → ModificationValue
#   mod.backfaceCulled, mod.depthTestFailed, mod.stencilTestFailed, mod.scissorClipped

# Shader 调试
trace = controller.DebugPixel(x, y, rd.DebugPixelInputs())
trace = controller.DebugVertex(vertid, instid, idx, view)
trace = controller.DebugThread((gx,gy,gz), (tx,ty,tz))
# 返回 ShaderDebugTrace:
#   trace.debugger → debugger handle
#   trace.inputs → List[ShaderVariable]
#   trace.sourceVars → List[SourceVariableMapping]
#   trace.instInfo → List[InstructionSourceInfo]

# 执行循环（必须！trace 不含预填充状态）
states = []
while True:
    new_states = controller.ContinueDebug(trace.debugger)
    if not new_states:
        break
    states.extend(new_states)
# ShaderDebugState:
#   state.stepIndex, state.nextInstruction
#   state.changes → List[ShaderVariableChange]
#     change.after → ShaderVariable

controller.FreeTrace(trace)  # 必须释放！
```

### 性能计数器

```python
counters = controller.EnumerateCounters()     # List[GPUCounter]
info = controller.DescribeCounter(counter)    # CounterDescription
# info.name, info.unit (CounterUnit), info.resultType, info.resultByteWidth

results = controller.FetchCounters([counter]) # List[CounterResult]
# result.eventId, result.value.d (double)
# GPU Duration 单位是秒，需 ×1000 转 ms
# 仅叶子 action 有数据，父节点需递归求和
```

### 调试消息

```python
messages = controller.GetDebugMessages()       # List[DebugMessage]
# msg.eventId, msg.severity, msg.category, msg.source, msg.description
```

### API 属性

```python
props = controller.GetAPIProperties()          # APIProperties
# props.pipelineType  → GraphicsAPI
# props.vendor        → GPUVendor
# props.pixelHistory  → bool（是否支持 pixel history）
# props.shaderDebugging → bool（是否支持 shader 调试）
# props.degraded      → bool
```

### 帧信息

```python
frame = controller.GetFrameInfo()   # 单个 FrameDescription（不是列表！）
# frame.frameNumber, frame.captureTime
# frame.compressedFileSize, frame.uncompressedFileSize
# frame.stats (FrameStatistics) — D3D12/Vulkan/GL 的 stats.recorded=False
```

### 描述符访问

```python
accesses = controller.GetDescriptorAccess()    # List[DescriptorAccess]
# acc.stage, acc.type, acc.index, acc.arrayElement
# acc.staticallyUnused, acc.descriptorStore, acc.byteOffset
```

## 关键数据结构

### ActionDescription
| 属性 | 类型 | 说明 |
|------|------|------|
| `eventId` | int | 事件 ID |
| `GetName(sf)` | str | 名称（需 StructuredFile） |
| `flags` | int | ActionFlags 位掩码 |
| `numIndices` | int | 索引/顶点数量 |
| `numInstances` | int | 实例数量 |
| `outputs` | List[ResourceId] | 输出 RT |
| `depthOut` | ResourceId | 深度输出 |
| `children` | List[ActionDescription] | 子 action |
| `parent`/`previous`/`next` | ActionDescription | 导航指针 |
| `events` | List[APIEvent] | 关联的 API 事件 |

### TextureDescription
| 属性 | 类型 | 说明 |
|------|------|------|
| `resourceId` | ResourceId | 资源 ID |
| `name` | str | 名称 |
| `width`/`height`/`depth` | int | 尺寸 |
| `arraysize`/`mips` | int | 数组/Mip 层数 |
| `format.Name()` | str | 格式名 |
| `dimension` | TextureDim | 维度枚举 |
| `byteSize` | int | 精确字节大小 |

### BufferDescription
| 属性 | 类型 | 说明 |
|------|------|------|
| `resourceId` | ResourceId | 资源 ID |
| `name` | str | 名称 |
| `length` | int | 字节长度 |
| `creationFlags` | int | BufferCategory 标志 |

### ShaderReflection
| 属性 | 类型 | 说明 |
|------|------|------|
| `resourceId` | ResourceId | Shader ID |
| `entryPoint` | str | 入口点 |
| `inputSignature` | List[SigParameter] | 输入签名 |
| `outputSignature` | List[SigParameter] | 输出签名 |
| `constantBlocks` | List[ConstantBlock] | 常量块 |
| `readOnlyResources` | List[ShaderResource] | SRV |
| `readWriteResources` | List[ShaderResource] | UAV |
| `samplers` | List[ShaderSampler] | 采样器 |
| `debugInfo.files` | List | HLSL 源文件（需 PDB） |

### ShaderVariable
| 属性 | 类型 | 说明 |
|------|------|------|
| `name` | str | 变量名 |
| `type` | VarType | 类型枚举 |
| `rows`/`columns` | int | 维度 |
| `value.f32v[]` | float[] | float 值 |
| `value.s32v[]` | int[] | int 值 |
| `value.u32v[]` | uint[] | uint 值 |
| `value.f64v[]` | double[] | double 值 |
| `members` | List[ShaderVariable] | 子成员 |
