# renderdoc-mcp

[RenderDoc](https://renderdoc.org/) 的 MCP 服务器 —— 让 AI 助手分析 GPU 帧捕获文件（`.rdc`），用于图形调试与性能分析。

基于 [Model Context Protocol](https://modelcontextprotocol.io/) 构建，支持 Claude Desktop、Cursor、VS Code 及所有兼容 MCP 的客户端。

## 特性

- **完全独立运行** —— 自带从源码编译的 `renderdoc.pyd`，**不依赖也不干扰**系统中已安装的 RenderDoc。你可以同时使用系统的 RenderDoc GUI 进行日常调试，两者互不影响
- **54 个工具**，覆盖完整的 RenderDoc 分析流程，按"从总体到细节"五层设计，契合 AI 分析模式
- **11 个高级工具**，一次调用即可完成分析（Draw Call 状态、帧概览、对比、批量导出、像素区域采样、像素条件搜索等）
- **7 个检查工具**，提供帧级诊断总览、API 属性查询、调试消息获取、描述符访问，以及缓存消息分页查询
- **统一超时保护** —— 每个 tool 默认带超时报错机制，避免长时间卡死无响应
- **超长结果自动缓存** —— 输出过大时自动返回 `message_id`，再通过 `get_cached_message` 分页或搜索
- **3 个内置提示模板**，引导 AI 进行调试

- **人类可读输出** —— 混合模式、深度函数、拓扑类型均以名称而非数字显示
- **GPU 特性检测** —— 根据驱动名称自动识别 Adreno/Mali/PowerVR/Apple 特有的问题
- **无头运行** —— 无需 GUI，完全通过 RenderDoc 的 Python Replay API 运行
- 支持 D3D11、D3D12、OpenGL、Vulkan、OpenGL ES 捕获文件

## 工作原理

```
AI 助手 <--MCP--> renderdoc-mcp server <--Python API--> renderdoc.pyd <--> GPU replay
```

服务器使用 RenderDoc 的无头回放 API（`renderdoc.pyd`）来：
1. 无需 GUI 即可打开 `.rdc` 捕获文件
2. 回放帧并查询任意事件处的管线状态
3. 提取纹理、缓冲区、Shader 数据和像素历史
4. 返回结构化 JSON 供 AI 进行推理分析

## 从源码构建

本仓库以 Git 子模块的形式包含了 RenderDoc 源码，并提供自动化构建脚本，一步完成 `renderdoc.pyd` 的编译和 MCP 服务器的安装。

### 前置条件

- **Python >= 3.6** —— 完整安装，需包含开发头文件和库文件
  - 从 [python.org](https://www.python.org/downloads/) 下载
  - 安装时务必勾选 **"Add Python to PATH"**
- **Visual Studio 2015 或更高版本** —— 需安装 **C++ 生成工具**（使用 C++ 的桌面开发）
  - 从 [visualstudio.microsoft.com](https://visualstudio.microsoft.com/downloads/) 下载

### 构建与安装

#### 方式 A：双击运行（最简单）

双击 `build_and_install.bat` —— 它会以正确的执行策略运行 PowerShell 脚本。

#### 方式 B：PowerShell

```powershell
# 如果执行策略阻止了脚本运行：
powershell -ExecutionPolicy Bypass -File .\build_and_install.ps1

# 执行干净构建：
powershell -ExecutionPolicy Bypass -File .\build_and_install.ps1 -Clean
```

#### 脚本参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-Configuration` | `Release` | 构建配置（`Release` / `Debug`） |
| `-Platform` | `x64` | 目标平台 |
| `-Clean` | 关闭 | 干净构建 —— 先执行 MSBuild Clean 并删除中间文件 |

#### 脚本执行步骤

1. **检测** Python 和 Visual Studio / MSBuild 环境
2. **修补** `python.props`，适配标准 Python 安装（移除 `.zip` 文件检查）
3. **编译** `renderdoc.pyd` 和 `renderdoc.dll`（通过 MSBuild）
4. **收集** 构建产物到 `build/x64/`
5. **安装** MCP 服务器（通过 `pip install -e`）
6. **生成** `mcp.json` 配置文件

构建成功后，产物位于：

```
build/x64/
├── renderdoc.pyd          # Python 模块
├── renderdoc.dll          # 核心库
└── ...
```

## 配置 MCP 客户端

构建脚本会生成 `mcp.json` 文件，将其内容复制到你的 MCP 客户端配置中：

<details>
<summary><b>Claude Desktop</b></summary>

编辑 `claude_desktop_config.json`（Settings -> Developer -> Edit Config）：

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

添加到 `~/.cursor/mcp.json`：

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

添加到 `.vscode/mcp.json`：

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

> **提示：** 请将路径替换为你实际的 Python 可执行文件路径和构建输出目录。脚本生成的 `mcp.json` 已包含适合你系统的正确路径。

## 使用示例

配置完成后，直接与 AI 助手对话即可：

> "打开 `frame.rdc`，告诉我这一帧里发生了什么"

> "找到渲染角色模型的 Draw Call，检查它的管线状态"

> "我的阴影贴图为什么是全黑的？检查一下 Depth Pass"

> "分析性能 —— 有没有冗余的 Draw Call？"

### 典型工具调用流程

工具按"从总体到细节"五层设计，引导 AI 逐步深入：

```
Layer 0 — 打开 & 元信息:
  open_capture → get_capture_info → get_api_properties → get_frame_info

Layer 1 — 帧级诊断入口:
  get_diagnostic_overview → get_debug_messages

Layer 2 — 帧结构导航:
  get_frame_overview → list_actions → analyze_render_passes → search_passes → search_actions

Layer 3 — 事件级分析:
  set_event → get_draw_call_state → get_pipeline_state → get_shader_bindings
  → get_descriptor_access → get_vertex_inputs

Layer 4 — 数据深挖:
  disassemble_shader → get_cbuffer_contents → get_texture_histogram
  → pixel_history → debug_shader_at_pixel → debug_vertex → debug_compute_thread

Layer 5 — 专项诊断:
  diagnose_negative_values → diagnose_precision_issues → diagnose_mobile_risks
```

```
open_capture("frame.rdc")                     # 加载捕获文件
├── get_capture_info()                         # API、GPU、已知 GPU 问题
├── get_frame_overview()                       # 帧级统计与渲染 Pass
├── get_draw_call_state(142)                   # 一次调用获取完整 Draw Call 状态
├── diff_draw_calls(140, 142)                  # 对比两个 Draw Call
├── export_draw_textures(142, "./tex/")        # 批量导出所有绑定纹理
├── save_render_target(142, "./rt.png")        # 保存渲染目标快照
├── analyze_render_passes()                    # 自动检测渲染 Pass 边界
├── sample_pixel_region(rt_id, 0,0,512,512)   # 扫描 RT 区域，检测 NaN/Inf/负值
├── pixel_history(id, 512, 384)               # 调试特定像素
├── export_mesh(142, "./mesh.obj")             # 导出网格为 OBJ
└── close_capture()                            # 释放资源
```

性能与诊断分析：

```
get_pass_timing()                         # 火焰图式层级耗时总览
get_pass_timing(parent_eid=145)           # 钻入最耗时的节点
analyze_overdraw()                        # Overdraw 估算
analyze_bandwidth()                       # 显存带宽估算
analyze_state_changes()                   # 合批优化机会
diagnose_negative_values()               # 查找 NaN/Inf/负值（爆闪）
diagnose_precision_issues()              # R11G11B10、D16、SRGB 精度问题
diagnose_reflection_mismatch()           # 反射伪影诊断
diagnose_mobile_risks()                  # 移动端 GPU 全面风险检查
```

## 工具列表

### 会话管理（4 个）

| 工具 | 说明 |
|------|------|
| `open_capture` | 打开 `.rdc` 文件（自动关闭之前的捕获） |
| `close_capture` | 关闭当前捕获并释放资源 |
| `get_capture_info` | 捕获元数据：API、Action 数量、分辨率、**已知 GPU 问题** |
| `get_frame_overview` | 帧级统计：按类型的 Action 数量、纹理/缓冲区显存、渲染目标 |

### 事件导航（5 个）

| 工具 | 说明 |
|------|------|
| `list_actions` | 列出 Draw Call / Action 树，支持 `filter` 和 `event_type` 筛选 |
| `get_action` | 获取单个 Action 的完整信息 |
| `set_event` | 导航到指定事件（管线查询前必须调用） |
| `search_actions` | 按名称模式和/或 Action 标志搜索 |
| `find_draws` | 按渲染状态搜索 Draw Call：混合、最小顶点数、纹理/Shader/RT 绑定 |

### 管线检查（4 个）

| 工具 | 说明 |
|------|------|
| `get_pipeline_state` | 完整状态：拓扑、视口、光栅化、混合、深度、模板 |
| `get_shader_bindings` | 指定 Shader 阶段的 CBV、SRV、UAV、采样器 |
| `get_vertex_inputs` | 顶点属性、顶点/索引缓冲区绑定 |
| `get_draw_call_state` | **一次调用完整分析**：Action 信息 + 混合公式 + 深度 + 模板 + 光栅化 + 纹理尺寸 + RT + Shader |

### 资源分析（4 个）

| 工具 | 说明 |
|------|------|
| `list_textures` | 所有纹理（可按格式、最小宽度筛选） |
| `list_buffers` | 所有缓冲区（可按最小大小筛选） |
| `list_resources` | 所有命名资源（可按类型、名称模式筛选） |
| `get_resource_usage` | 查询哪些事件读写了指定资源 |

### 数据提取（9 个）

| 工具 | 说明 |
|------|------|
| `save_texture` | 导出为 PNG、JPG、BMP、TGA、HDR、EXR 或 DDS |
| `get_buffer_data` | 读取缓冲区数据（十六进制或 float32 数组），自动检测 D3D12 子段绑定偏移 |
| `pick_pixel` | 获取指定坐标的像素值（支持 mip/array_slice/MSAA，根据纹理格式智能返回通道和数据类型） |
| `get_texture_stats` | 每通道最小/最大/平均值，带异常检测（NaN/Inf/负值） |
| `read_texture_pixels` | 读取矩形像素区域（最大 64x64） |
| `export_draw_textures` | 批量导出 Draw Call 绑定的所有纹理 |
| `save_render_target` | 保存指定事件处的 RT 快照（颜色 + 可选深度） |
| `export_mesh` | 导出网格为 OBJ（包含位置、法线、UV） |
| `get_texture_histogram` | **新增** 纹理直方图（256 bucket），快速判断 HDR 溢出/曝光分布 |

### Shader 分析（3 个）

| 工具 | 说明 |
|------|------|
| `disassemble_shader` | Shader 反汇编，支持自动回退链、`search` 关键字搜索和 `line_range` |
| `get_shader_reflection` | 输入/输出签名、资源绑定布局 |
| `get_cbuffer_contents` | 常量缓冲区变量的实际值，支持 `filter` 按名称筛选 |

### 高级功能（10 个）

| 工具 | 说明 |
|------|------|
| `pixel_history` | 完整的逐像素修改历史（跨所有事件） |
| `get_post_vs_data` | 变换后顶点数据（VS 输出 / GS 输出） |
| `diff_draw_calls` | 对比两个 Draw Call，显示差异及其含义 |
| `analyze_render_passes` | 自动检测渲染 Pass 边界 |
| `search_passes` | **新增** 按 RT 格式/尺寸/数量、名称、draw count、相邻 pass 格式、管线状态（depth/stencil/shader）多维模糊搜索渲染 Pass |
| `sample_pixel_region` | 均匀网格采样 RT 区域 —— 检测 NaN/Inf/负值/过曝 |
| `find_pixels` | **新增** 按条件搜索匹配像素（min/max/bitmask/NaN/Inf/负值 + 自定义 Python 表达式，支持位操作），支持 `array_slice`/`array_slice_end` 跨切片范围搜索，自适应全量/两阶段搜索 |
| `debug_shader_at_pixel` | 逐像素 Shader 调试，返回变量追踪信息 |
| `debug_vertex` | **新增** 顶点着色器调试，追踪顶点变换/蒙皮问题 |
| `debug_compute_thread` | **新增** Compute Shader 线程调试 |

### 性能分析（4 个）

| 工具 | 说明 |
|------|------|
| `get_pass_timing` | 火焰图式层级耗时分析（沿 action 树逐层钻取，优先使用 GPU 计数器） |
| `analyze_overdraw` | 每个 RT 分组的 Overdraw 估算 |
| `analyze_bandwidth` | 每个 RT 的读写带宽估算 |
| `analyze_state_changes` | 查找冗余状态切换和合批优化机会 |

### 诊断工具（4 个）

| 工具 | 说明 |
|------|------|
| `diagnose_negative_values` | 扫描所有浮点 RT 中的负值/NaN/Inf —— 定位首次产生的事件，检测 TAA 累积 |
| `diagnose_precision_issues` | 检查 R11G11B10 符号位丢失、浅深度缓冲区、SRGB/线性不匹配 |
| `diagnose_reflection_mismatch` | 对比反射 Pass 与主场景 Draw —— 查找 Shader/混合/格式差异 |
| `diagnose_mobile_risks` | 全面检查移动端风险（精度/性能/兼容性/GPU 特定问题） |

### 检查工具（7 个）

| 工具 | 说明 |
|------|------|
| `get_debug_messages` | 获取 API 验证层和驱动的调试/诊断消息，按严重性排序 |
| `get_api_properties` | 查询 API 能力：是否支持 pixel history、shader debugging、GPU 厂商等 |
| `get_frame_info` | 帧精确信息：捕获时间、文件大小、FrameStatistics |
| `get_diagnostic_overview` | **一站式帧诊断总览** —— 聚合调试消息、精度风险、GPU 特性、帧统计于一次调用 |
| `get_descriptor_access` | 当前事件的描述符访问详情（D3D12/Vulkan 现代绑定模型） |
| `get_resource_details` | 资源完整信息：父/派生资源关系、精确字节大小、使用摘要 |
| `get_cached_message` | 查询超长结果缓存：基于 `message_id` 分页、切换视图或全文搜索 |


## 提示模板

内置的提示模板，引导 AI 完成常见调试流程：

| 提示 | 说明 |
|------|------|
| `debug_draw_call` | 深入分析单个 Draw Call：管线 -> Shader -> CBV -> 输出 |
| `find_rendering_issue` | 根据问题描述进行系统性诊断 |
| `analyze_performance` | 全帧性能分析：Pass 耗时、Overdraw、带宽、状态切换 |

## 项目结构

```
renderdoc-mcp/
├── renderdoc/                  # RenderDoc 源码（Git 子模块）
├── mcpserver/                  # MCP 服务器 Python 包
│   ├── src/renderdoc_mcp/
│   │   ├── server.py           # FastMCP 服务器，提示模板定义
│   │   ├── session.py          # 捕获生命周期，资源缓存
│   │   ├── runtime.py          # 统一超时守卫、大结果缓存、分页查询
│   │   ├── util.py             # 序列化，枚举映射，模块加载器
│   │   └── tools/              # 10 个工具模块（共 54 个工具）

│   ├── tests/
│   └── pyproject.toml
├── build/                      # 构建产物（renderdoc.pyd, .dll）
├── build_and_install.ps1       # 自动化构建脚本（PowerShell）
├── build_and_install.bat       # 包装脚本 —— 双击即可构建
└── mcp.json                    # 生成的 MCP 客户端配置
```
