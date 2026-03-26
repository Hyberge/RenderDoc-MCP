# RenderDoc Python API 参考（renderdoc 模块）

本文档总结 RenderDoc Python API 中被本 MCP 服务器使用的核心类和方法。

## 初始化与全局函数

```python
rd.InitialiseReplay(rd.GlobalEnvironment(), [])  # 初始化回放系统
rd.ShutdownReplay()                                # 关闭回放系统
rd.OpenCaptureFile()                               # 创建 CaptureFile 句柄
```

## CaptureFile 类

```python
cap = rd.OpenCaptureFile()
result = cap.OpenFile(filepath, "", None)       # 打开文件，返回 ResultCode
cap.LocalReplaySupport()                         # 检查本机回放支持
result, controller = cap.OpenCapture(            # 打开回放，返回 (ResultCode, ReplayController)
    rd.ReplayOptions(), None
)
cap.DriverName()                                  # 获取驱动/API 名称
cap.Shutdown()                                    # 关闭文件句柄
```

**ResultCode**: `rd.ResultCode.Succeeded` 表示成功

## ReplayController 类

这是最核心的类，所有帧分析都通过它进行。

### 帧导航
```python
controller.SetFrameEvent(eventId, force=True)  # 导航到指定事件
controller.GetRootActions()                     # 获取根级 action 列表
controller.GetStructuredFile()                  # 获取结构化文件（用于 action 名称）
```

### 管线状态
```python
state = controller.GetPipelineState()   # 获取通用 PipeState

# PipeState API-agnostic 方法：
state.GetPrimitiveTopology()            # 图元拓扑 → Topology
state.GetViewport(index)                # 单个视口 → Viewport（按 index 遍历，0..N）
state.GetScissor(index)                 # 单个裁剪矩形 → Scissor（按 index 遍历）
state.GetColorBlends()                  # 颜色混合状态列表 → List[ColorBlend]
state.GetStencilFaces()                 # 模板状态 → (front: StencilFace, back: StencilFace)
state.GetOutputTargets()                # 输出目标（RT）列表 → List[Descriptor]
state.GetDepthTarget()                  # 深度目标 → Descriptor
state.GetIBuffer()                      # Index Buffer → BoundVBuffer
state.GetVBuffers()                     # Vertex Buffers → List[BoundVBuffer]
state.GetVertexInputs()                 # 顶点输入属性 → List[VertexInputAttribute]
state.GetDescriptorAccess()             # 描述符访问列表 → List[DescriptorAccess]
state.IsCaptureD3D11() / IsCaptureD3D12() / IsCaptureVK() / IsCaptureGL()  # API 类型检测

# ⚠️ 重要：PipeState 没有以下方法（常见误用）：
#   GetViewports()    — 不存在！用 GetViewport(index) 逐个遍历
#   GetScissors()     — 不存在！用 GetScissor(index) 逐个遍历
#   GetColorBlend()   — 不存在！用 GetColorBlends()（复数）
#   GetDepthState()   — 不存在！需访问 API-specific state
#   GetRasterizer()   — 不存在！需访问 API-specific state
#   GetStencilState() — 不存在！用 GetStencilFaces()

# Shader 相关
state.GetShaderReflection(shaderStage)  # 获取 shader 反射信息
state.GetShaderEntryPoint(shaderStage)  # 获取入口点名称
state.GetGraphicsPipelineObject()       # 获取图形管线对象
state.GetComputePipelineObject()        # 获取计算管线对象
state.GetConstantBlock(stage, idx, 0)   # 获取常量块绑定
state.GetReadOnlyResources(stage)       # 获取只读资源绑定（SRV）
state.GetReadWriteResources(stage)      # 获取读写资源绑定（UAV）
state.GetSamplers(stage)                # 获取采样器绑定
```

### API-Specific Pipeline State（深度/光栅化状态）

PipeState 不暴露 depth/rasterizer 状态，需通过 API-specific state 获取：

```python
# D3D12
api_state = controller.GetD3D12PipelineState()
ds = api_state.outputMerger.depthStencilState  # .depthEnable, .depthFunction, .depthWrites, .stencilEnable
rs = api_state.rasterizer.state                 # .fillMode, .cullMode, .frontCCW, .depthBias, .depthClip

# D3D11
api_state = controller.GetD3D11PipelineState()
ds = api_state.outputMerger.depthStencilState  # same fields as D3D12
rs = api_state.rasterizer.state                 # same fields as D3D12

# Vulkan
api_state = controller.GetVulkanPipelineState()
ds = api_state.depthStencil                     # .depthTestEnable, .depthFunction, .depthWriteEnable, .stencilTestEnable
rs = api_state.rasterizer                       # .fillMode, .cullMode, .frontCCW, .depthBiasEnable

# OpenGL
api_state = controller.GetGLPipelineState()
ds = api_state.depthState                       # .depthEnable, .depthFunction, .depthWrites
rs = api_state.rasterizer.state                 # .fillMode, .cullMode
```

### 资源查询
```python
controller.GetTextures()    # 所有纹理 → [TextureDescription, ...]
controller.GetBuffers()     # 所有缓冲区 → [BufferDescription, ...]
controller.GetResources()   # 所有资源 → [ResourceDescription, ...]
controller.GetUsage(rid)    # 资源使用历史 → [EventUsage, ...]
```

### 数据读取
```python
controller.GetBufferData(buffId, offset, length)  # 读取 buffer 字节数据
controller.PickPixel(                               # 读取指定像素
    texId, x, y, rd.Subresource(mip, slice, 0), rd.CompType.Typeless
)
controller.GetMinMax(                               # 获取纹理 min/max 统计
    texId, rd.Subresource(mip, slice, 0), rd.CompType.Typeless
)
controller.SaveTexture(texSave, outputPath)         # 保存纹理到文件
```

### 顶点数据
```python
postvs = controller.GetPostVSData(                  # 获取变换后顶点数据
    instId=0, viewId=0, stage=rd.MeshDataStage.VSOut
)
# postvs.vertexResourceId, postvs.vertexByteOffset, postvs.vertexByteStride, postvs.numIndices
```

### Shader 分析
```python
targets = controller.GetDisassemblyTargets(True)    # 可用反汇编目标
disasm = controller.DisassembleShader(               # 反汇编 shader
    pipeline, refl, target
)
cbufferVars = controller.GetCBufferVariableContents( # 读取常量缓冲区变量值
    pipeline, shaderId, stage, entryPoint,
    cbufferIdx, bufResource, byteOffset, byteSize
)
# ⚠️ byteOffset 和 byteSize 必须从 descriptor 获取：
#   cb_bind = state.GetConstantBlock(stage, idx, 0)
#   byteOffset = cb_bind.descriptor.byteOffset
#   byteSize = cb_bind.descriptor.byteSize
# ⚠️ 传 0, 0 在 D3D12 中会导致读到全零！因为 cbuffer 通常绑定在大 buffer 的非零偏移处。
# 这与 RenderDoc GUI (BufferViewer.cpp:3745) 的行为一致。
```

### 像素历史与调试
```python
history = controller.PixelHistory(                   # 获取像素修改历史
    texId, x, y, rd.Subresource(0, 0, 0), rd.CompType.Typeless
)
# history → [PixelModification, ...]
# mod.eventId, mod.Passed(), mod.preMod, mod.postMod
# mod.backfaceCulled, mod.depthTestFailed, mod.stencilTestFailed, etc.

trace = controller.DebugPixel(                       # 像素 shader 调试
    x, y, rd.DebugPixelInputs()
)
# trace → ShaderDebugTrace
# trace.debugger → debugger handle (NOT pre-populated states!)
# trace.inputs → [ShaderVariable, ...] (shader inputs)
# trace.sourceVars → global source variable mappings
# trace.instInfo → [InstructionSourceInfo, ...] (per-instruction metadata)
#   instInfo[].instruction → instruction index
#   instInfo[].lineInfo → LineColumnInfo (.fileIndex, .lineStart, .lineEnd)
#   instInfo[].sourceVars → per-instruction source variable mappings

# ⚠️ 必须通过 ContinueDebug 循环获取执行状态：
all_states = []
while True:
    new_states = controller.ContinueDebug(trace.debugger)
    if not new_states:
        break
    all_states.extend(new_states)
# all_states → [ShaderDebugState, ...]
# state.stepIndex → 执行步骤索引
# state.nextInstruction → 下一条指令索引
# state.callstack → 调用栈
# state.changes → [ShaderVariableChange, ...]  (⚠️ 不是 .locals 或 .states!)
#   change.after → ShaderVariable (变更后的值)

# ⚠️ 用完必须释放调试器资源：
controller.FreeTrace(trace)

# Source variable mapping:
# trace.sourceVars (全局) 和 instInfo[].sourceVars (per-instruction)
# 包含 SourceVariableMapping 对象:
#   svm.name → HLSL 源码变量名 (如 "TileCoord.x")
#   svm.variables → [DebugVariableReference, ...]
#     ref.name → 调试寄存器名 (如 "_238")
#     ref.component → 分量索引
#   svm.rows, svm.columns → 变量维度
#   svm.undefinedValue → 值是否未定义
```

### 性能计数器
```python
counters = controller.EnumerateCounters()            # 枚举可用计数器
info = controller.DescribeCounter(counter)           # 描述计数器
results = controller.FetchCounters([counter])        # 获取计数器值
# result.eventId, result.value.d (double)
```

### 调试消息（新增工具使用）
```python
messages = controller.GetDebugMessages()             # 获取 API 验证/驱动诊断消息
# msg.eventId, msg.severity (MessageSeverity), msg.category (MessageCategory)
# msg.source (MessageSource), msg.description (str)
```

### API 属性（新增工具使用）
```python
props = controller.GetAPIProperties()                # 查询 API 能力
# props.pipelineType (GraphicsAPI)    — 捕获的 API 类型
# props.localRenderer (GraphicsAPI)   — 本地渲染器
# props.vendor (GPUVendor)            — GPU 厂商
# props.pixelHistory (bool)           — 是否支持 pixel history
# props.shaderDebugging (bool)        — 是否支持 shader 调试
# props.degraded (bool)               — 是否降级模式
# props.rgpCapture (bool)             — 是否支持 RGP 捕获
```

### 帧信息
```python
frame = controller.GetFrameInfo()               # 获取单个 FrameDescription（非列表！）
# frame.frameNumber, frame.captureTime (int, UTC)
# frame.compressedFileSize, frame.uncompressedFileSize (int)
# frame.stats (FrameStatistics):
#   frame.stats.recorded (bool) — 是否收集了统计，仅 D3D11 会设为 True
#   frame.stats.draws (DrawcallStats)     — SWIG 子对象，需提取 .calls/.instanced/.indirect 等
#   frame.stats.dispatches (DispatchStats) — SWIG 子对象
#   frame.stats.indices (IndexBindStats)   — SWIG 子对象
#   frame.stats.vertices (VertexBindStats) — SWIG 子对象
# frame.debugMessages — 非事件绑定的调试消息
#
# ⚠️ 注意：GetFrameInfo() 返回单个 FrameDescription 对象，不是列表！
# ⚠️ D3D12/Vulkan/GL 的 frame.stats.recorded=False，统计数据全为 0
```

### 描述符访问（新增工具使用）
```python
accesses = controller.GetDescriptorAccess()          # 当前事件的描述符访问
# acc.stage (ShaderStage), acc.type (DescriptorType)
# acc.index (int), acc.arrayElement (int)
# acc.staticallyUnused (bool)
# acc.descriptorStore (ResourceId), acc.byteOffset (int)
```

### 直方图
```python
histogram = controller.GetHistogram(                 # 获取纹理直方图
    texId, rd.Subresource(0, 0, 0), rd.CompType.Typeless,
    minVal, maxVal,
    (includeR, includeG, includeB, includeA),        # channels 为 tuple(bool,bool,bool,bool)
)
# ⚠️ channels 参数是一个 4-bool tuple，不是 4 个独立 bool！
# 正确: GetHistogram(texId, sub, comp, min, max, (True, True, True, False))  — 6 个参数
# 错误: GetHistogram(texId, sub, comp, min, max, True, True, True, False)    — 9 个参数，会报错
# 返回 256 个 int 值的列表，每个代表一个亮度 bucket 的像素数
```

### 顶点/Compute 调试
```python
trace = controller.DebugVertex(                      # 顶点着色器调试
    vertid, instid, idx, view
)
trace = controller.DebugThread(                      # Compute shader 线程调试
    (gx, gy, gz), (tx, ty, tz)
)
# 返回与 DebugPixel 相同的 ShaderDebugTrace 结构
# 同样需要 ContinueDebug 循环获取状态，用完调用 FreeTrace 释放
```

### Shader Debug Info (HLSL 源码)
```python
refl = state.GetShaderReflection(stage)
debug_info = refl.debugInfo
# debug_info.files → shader 源码文件列表
#   file.filename → 文件名
#   file.contents → 源码内容 (HLSL)
# 需要通过 open_capture 的 shader_debug_paths 参数设置 PDB 搜索路径
# 底层调用: rd.SetConfigSetting("DXBC.Debug.SearchDirPaths")
# ⚠️ 必须在 cap.OpenCapture() 之前设置，因为 DXBCContainer 在构造时加载 debug info
```

## 关键数据结构

### ActionDescription
```python
action.eventId          # 事件 ID
action.GetName(sf)      # 获取名称（需要 StructuredFile）
action.flags            # ActionFlags 位掩码
action.numIndices       # 索引/顶点数量
action.numInstances     # 实例数量
action.outputs          # 输出 RT 的 ResourceId 列表
action.depthOut         # 深度输出 ResourceId
action.children         # 子 action 列表
action.parent           # 父 action
action.previous/next    # 前/后 action
```

### TextureDescription
```python
tex.resourceId          # ResourceId
tex.name                # 名称
tex.width, tex.height, tex.depth
tex.arraysize, tex.mips
tex.format.Name()       # 格式名称字符串
tex.dimension           # 纹理维度枚举
tex.msQual, tex.msSamp  # 多重采样
tex.creationFlags       # 创建标志（含 TextureCategory）
tex.byteSize            # 精确字节大小（新增使用）
tex.cubemap             # 是否为 cubemap（新增使用）
```

### BufferDescription
```python
buf.resourceId          # ResourceId
buf.name                # 名称
buf.length              # 字节长度
buf.creationFlags       # BufferCategory 标志
buf.gpuAddress          # GPU 地址（新增使用）
```

### ResourceDescription（新增工具使用）
```python
res.resourceId          # ResourceId
res.name                # 名称
res.type                # ResourceType 枚举
res.autogeneratedName   # 名称是否自动生成
res.parentResources     # 父资源 ID 列表
res.derivedResources    # 派生资源（视图）ID 列表
res.initialisationChunks # 初始化块索引列表
```

### ShaderReflection
```python
refl.resourceId         # Shader ResourceId
refl.entryPoint         # 入口点名称
refl.inputSignature     # 输入签名 [SigParameter, ...]
refl.outputSignature    # 输出签名
refl.constantBlocks     # 常量块 [ConstantBlock, ...]
refl.readOnlyResources  # 只读资源
refl.readWriteResources # 读写资源
refl.samplers           # 采样器
```

### ShaderVariable
```python
var.name                # 变量名
var.type                # VarType 枚举
var.rows, var.columns   # 维度
var.value.f32v[]        # float 值数组
var.value.s32v[]        # int 值数组
var.value.u32v[]        # uint 值数组
var.value.f64v[]        # double 值数组
var.members             # 子成员（结构体/数组）
```

## 关键枚举

### ShaderStage
```python
rd.ShaderStage.Vertex, .Hull, .Domain, .Geometry, .Pixel, .Compute
```

### ActionFlags（位掩码）
```python
rd.ActionFlags.Drawcall   # draw call
rd.ActionFlags.Clear      # 清除操作
rd.ActionFlags.Dispatch   # compute dispatch
rd.ActionFlags.Copy       # 复制操作
rd.ActionFlags.Resolve    # MSAA resolve
```

### FileType
```python
rd.FileType.PNG, .JPG, .BMP, .TGA, .HDR, .EXR, .DDS
```

### MeshDataStage
```python
rd.MeshDataStage.VSIn, .VSOut, .GSOut
```

### TextureSave 配置
```python
texsave = rd.TextureSave()
texsave.resourceId = texId
texsave.destType = rd.FileType.PNG
texsave.mip = 0
texsave.slice.sliceIndex = 0
texsave.alpha = rd.AlphaMapping.Preserve
```
