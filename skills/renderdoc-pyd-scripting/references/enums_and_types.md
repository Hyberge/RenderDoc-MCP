# 枚举值和数据类型速查表

## ShaderStage

| 值 | 名称 | 用途 |
|----|------|------|
| `rd.ShaderStage.Vertex` | Vertex | 顶点着色器 |
| `rd.ShaderStage.Hull` | Hull / TCS | 外壳着色器 |
| `rd.ShaderStage.Domain` | Domain / TES | 域着色器 |
| `rd.ShaderStage.Geometry` | Geometry | 几何着色器 |
| `rd.ShaderStage.Pixel` | Pixel / Fragment | 像素着色器 |
| `rd.ShaderStage.Compute` | Compute | 计算着色器 |

## ActionFlags（位掩码）

| 标志 | 说明 | 判断方式 |
|------|------|---------|
| `Drawcall` | Draw call | `flags & rd.ActionFlags.Drawcall` |
| `Clear` | 清除操作 | `flags & rd.ActionFlags.Clear` |
| `Dispatch` | Compute dispatch | `flags & rd.ActionFlags.Dispatch` |
| `Copy` | 资源复制 | `flags & rd.ActionFlags.Copy` |
| `Resolve` | MSAA resolve | `flags & rd.ActionFlags.Resolve` |
| `Indexed` | 使用 Index Buffer | `flags & rd.ActionFlags.Indexed` |
| `Instanced` | 实例化绘制 | `flags & rd.ActionFlags.Instanced` |
| `Indirect` | Indirect 调用 | `flags & rd.ActionFlags.Indirect` |

## ResultCode

| 值 | 含义 |
|----|------|
| `rd.ResultCode.Succeeded` | 成功 |
| `rd.ResultCode.InternalError` | 内部错误 |
| `rd.ResultCode.FileNotFound` | 文件不存在 |
| `rd.ResultCode.ImageUnsupported` | 不支持的截帧格式 |
| `rd.ResultCode.FileCorrupted` | 文件损坏 |
| `rd.ResultCode.APIUnsupported` | 不支持的 API |

## FileType（纹理导出格式）

| 枚举值 | 格式 | HDR 支持 |
|--------|------|---------|
| `rd.FileType.PNG` | PNG | 否（8-bit） |
| `rd.FileType.JPG` | JPEG | 否 |
| `rd.FileType.BMP` | BMP | 否 |
| `rd.FileType.TGA` | TGA | 否 |
| `rd.FileType.HDR` | Radiance HDR | 是（32-bit float） |
| `rd.FileType.EXR` | OpenEXR | 是（16/32-bit float） |
| `rd.FileType.DDS` | DirectDraw Surface | 原始格式保留 |

## MeshDataStage

| 枚举值 | 说明 |
|--------|------|
| `rd.MeshDataStage.VSIn` | 顶点着色器输入 |
| `rd.MeshDataStage.VSOut` | 顶点着色器输出 |
| `rd.MeshDataStage.GSOut` | 几何着色器输出 |

## CompType

| 常用值 | 说明 |
|--------|------|
| `rd.CompType.Typeless` | 通用/自动推断类型（PickPixel/GetMinMax 常用） |
| `rd.CompType.Float` | 浮点 |
| `rd.CompType.UInt` | 无符号整数 |
| `rd.CompType.SInt` | 有符号整数 |
| `rd.CompType.UNorm` | 归一化无符号 |
| `rd.CompType.SNorm` | 归一化有符号 |

## VarType（ShaderVariable.type）

| int 值 | 名称 | 值访问器 |
|--------|------|---------|
| 0 | Float | `var.value.f32v[]` |
| 1 | Double | `var.value.f64v[]` |
| 2 | Half | `var.value.f32v[]` (promoted) |
| 3 | SInt | `var.value.s32v[]` |
| 4 | UInt | `var.value.u32v[]` |
| 5 | SShort | `var.value.s32v[]` |
| 6 | UShort | `var.value.u32v[]` |
| 7 | SLong | `var.value.s32v[]` |
| 8 | ULong | `var.value.u32v[]` |
| 9 | SByte | `var.value.s32v[]` |
| 10 | UByte | `var.value.u32v[]` |
| 11 | Bool | `var.value.u32v[]` |
| 14 | ConstantBlock | — |
| 15 | Struct | 查 `var.members` |

## Topology

| int 值 | 名称 |
|--------|------|
| 0 | Unknown |
| 1 | PointList |
| 2 | LineList |
| 3 | LineStrip |
| 4 | TriangleList |
| 5 | TriangleStrip |
| 6 | TriangleFan |
| 11 | PatchList |

## TextureDim

| int 值 | 名称 |
|--------|------|
| 0 | Unknown |
| 1 | Buffer |
| 2 | Texture1D |
| 3 | Texture1DArray |
| 4 | Texture2D |
| 5 | Texture2DArray |
| 6 | Texture2DMS |
| 7 | Texture2DMSArray |
| 8 | Texture3D |
| 9 | TextureCube |
| 10 | TextureCubeArray |

## BlendFactor

| int 值 | 名称 |
|--------|------|
| 0 | Zero |
| 1 | One |
| 2 | SrcColor |
| 3 | InvSrcColor |
| 4 | DstColor |
| 5 | InvDstColor |
| 6 | SrcAlpha |
| 7 | InvSrcAlpha |
| 8 | DstAlpha |
| 9 | InvDstAlpha |
| 10 | SrcAlphaSat |
| 11 | BlendFactor |
| 12 | InvBlendFactor |

## BlendOp

| int 值 | 名称 |
|--------|------|
| 0 | Add |
| 1 | Subtract |
| 2 | RevSubtract |
| 3 | Min |
| 4 | Max |

## CompareFunction

| int 值 | 名称 |
|--------|------|
| 1 | Never |
| 2 | Less |
| 3 | LessEqual |
| 4 | Greater |
| 5 | GreaterEqual |
| 6 | Equal |
| 7 | NotEqual |
| 8 | Always |

## StencilOp

| int 值 | 名称 |
|--------|------|
| 0 | Keep |
| 1 | Zero |
| 2 | Replace |
| 3 | IncrSat |
| 4 | DecrSat |
| 5 | Invert |
| 6 | IncrWrap |
| 7 | DecrWrap |

## CullMode

| int 值 | 名称 |
|--------|------|
| 0 | None |
| 1 | Front |
| 2 | Back |
| 3 | FrontAndBack |

## FillMode

| int 值 | 名称 |
|--------|------|
| 0 | Solid |
| 1 | Wireframe |
| 2 | Point |

## ResourceUsage

| int 值 | 名称 | | int 值 | 名称 |
|--------|------|-|--------|------|
| 1 | VertexBuffer | | 2 | IndexBuffer |
| 3-11 | VS/HS/DS/GS/PS/CS/..._Constants | | 12 | StreamOut |
| 13-21 | VS/HS/DS/GS/PS/CS/..._Resource | | 22-30 | VS/.../CS_RWResource |
| 31 | InputTarget | | 32 | ColorTarget |
| 33 | DepthStencilTarget | | 34 | Indirect |
| 35 | Clear | | 41-43 | Copy/CopySrc/CopyDst |

## 配置类型

### TextureSave

```python
texsave = rd.TextureSave()
texsave.resourceId = texId          # 要保存的纹理 ResourceId
texsave.destType = rd.FileType.PNG  # 目标格式
texsave.mip = 0                     # Mip 级别
texsave.slice.sliceIndex = 0        # Array slice / 3D slice
texsave.alpha = rd.AlphaMapping.Preserve  # Alpha 处理方式
# AlphaMapping: .Discard, .BlendToColor, .BlendToCheckerboard, .Preserve
```

### Subresource

```python
sub = rd.Subresource(mip, slice, sample)
# mip: mipmap 级别 (0 = 最大)
# slice: array slice 索引
# sample: MSAA 采样索引 (通常 0)
```

### ReplayOptions

```python
opts = rd.ReplayOptions()
# 通常使用默认值即可
# opts.optimisation = rd.ReplayOptimisationLevel.Full  # 优化级别
```

### DebugPixelInputs

```python
inputs = rd.DebugPixelInputs()
# 通常使用默认值
# inputs.sample = 0           # MSAA sample
# inputs.primitive = 0xFFFFFFFF  # 自动选择 primitive
```

### ResourceId

```python
rid = rd.ResourceId.Null()     # 空 ResourceId
int(rid) == 0                   # 判断是否为空
str(rid)                        # 字符串表示
```
