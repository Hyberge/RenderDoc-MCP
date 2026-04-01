# RenderDoc MCP Server — 架构参考文档

## 项目概况

RenderDoc MCP 是一个基于 Model Context Protocol (MCP) 的 Python 服务器，让 AI 助手能够分析 GPU 帧捕获文件 (`.rdc`) 进行图形调试和性能分析。它基于 RenderDoc 的无头 (headless) 回放 Python API (`renderdoc.pyd`/`renderdoc.so`)，不需要 GUI。

- **版本**: 0.2.0
- **Python 要求**: >= 3.10
- **依赖**: `mcp[cli]>=1.0.0`（FastMCP 框架）
- **传输方式**: stdio（通过 `python -m renderdoc_mcp` 启动）
- **支持的 API**: D3D11, D3D12, OpenGL, Vulkan, OpenGL ES

## 数据流架构

```
AI Assistant ←—MCP (stdio)—→ renderdoc-mcp server ←—Python API—→ renderdoc.pyd ←→ GPU replay
```

1. AI 客户端（Claude Desktop / Claude Code / CodeBuddy）通过 MCP 协议调用工具
2. FastMCP 服务器接收请求，路由到对应的 tool 函数
3. tool 函数通过 `RenderDocSession` 单例操作 `renderdoc` 模块
4. `renderdoc` 模块执行 GPU 回放并返回数据
5. 返回 JSON 格式结果给 AI

## 环境配置

关键环境变量：
- `RENDERDOC_MODULE_PATH`: 指向 `renderdoc.pyd`（Windows）或 `librenderdoc.so`（Linux）所在目录

模块加载机制（`util.py::load_renderdoc()`）：
1. 检查 `sys.modules` 是否已有 `renderdoc`
2. 从环境变量获取路径并添加到 `sys.path`
3. Windows 上调用 `os.add_dll_directory()` 添加 DLL 目录
4. `import renderdoc` 完成加载

## 项目文件结构

```
src/renderdoc_mcp/
├── __init__.py          # 包描述
├── __main__.py          # 入口点: python -m renderdoc_mcp
├── server.py            # FastMCP 服务器定义 + 3 个 Prompt + atexit 清理
├── session.py           # RenderDocSession 单例——捕获生命周期管理
├── util.py              # 模块加载、序列化、枚举映射、辅助函数
└── tools/
    ├── __init__.py
    ├── session_tools.py      # 4 工具: open/close/info/frame_overview
    ├── event_tools.py        # 7 工具: list/get/set/search actions + find_draws + get_api_calls_by_action + get_api_calls_range
    ├── pipeline_tools.py     # 4 工具: pipeline state, shader bindings, vertex inputs, draw_call_state
    ├── resource_tools.py     # 4 工具: list textures/buffers/resources, resource usage
    ├── data_tools.py         # 9 工具: save/read/pick/stats + read_pixels + export_draw_textures + save_rt + export_mesh + get_texture_histogram
    ├── shader_tools.py       # 3 工具: disassemble, reflection, cbuffer contents
    ├── advanced_tools.py     # 9 工具: pixel_history, post_vs, diff_draws, render_passes, search_passes, sample_region, debug_shader + debug_vertex + debug_compute_thread
    ├── performance_tools.py  # 4 工具: pass_timing, overdraw, bandwidth, state_changes
    ├── diagnostic_tools.py   # 4 工具: negative_values, precision, reflection_mismatch, mobile_risks
    └── inspection_tools.py   # 7 工具: debug_messages, api_properties, frame_info, diagnostic_overview, descriptor_access, resource_details, cached_message
```

## 核心组件

### 1. server.py — FastMCP 服务器

- 使用 `FastMCP("renderdoc-mcp")` 创建服务器实例
- 通过各模块的 `register(mcp)` 函数注册 55 个工具
- 定义 3 个 Prompt 模板：`debug_draw_call`、`find_rendering_issue`、`analyze_performance`
- `atexit` 注册清理函数确保资源释放

### 2. session.py — RenderDocSession 单例

核心类 `RenderDocSession` 管理单个捕获文件的完整生命周期：

**属性/缓存**：
- `_cap`: CaptureFile 句柄
- `_controller`: ReplayController 句柄
- `_filepath`: 当前文件路径
- `_current_event`: 当前导航到的 event ID
- `_action_map`: `dict[int, ActionDescription]` — eventId → action 的全局映射
- `_event_map`: `dict[int, int]` — eventId → chunkIndex 的全局映射（覆盖所有 API 事件，包括非 action 级调用）
- `_structured_file`: StructuredFile 引用（用于获取 action 名称）
- `_resource_id_cache`: `dict[str, ResourceId]` — 字符串 → ResourceId 对象映射
- `_texture_desc_cache`: `dict[str, TextureDescription]` — 纹理描述缓存

**关键方法**：
- `open(filepath)` → 打开 .rdc，初始化回放，构建 action_map、event_map 和资源缓存
- `close()` → 释放 controller 和 cap，清空所有缓存
- `set_event(event_id)` → 调用 `controller.SetFrameEvent()` 导航到指定事件
- `ensure_event(event_id)` → 如果提供 event_id 则 set_event，否则确保有当前事件
- `require_open()` → 检查是否有打开的捕获，返回错误或 None
- `resolve_resource_id(str)` → 将字符串 ID 解析为 ResourceId 对象
- `get_texture_desc(str)` → 获取纹理描述
- `shutdown()` → 完整关闭（close + deinitialize replay）

**单例模式**：
```python
_session: RenderDocSession | None = None

def get_session() -> RenderDocSession:
    global _session
    if _session is None:
        _session = RenderDocSession()
    return _session
```

### 3. util.py — 工具函数和映射

**模块加载**：`load_renderdoc()` → 全局 `rd = load_renderdoc()`

**枚举映射字典**（将 RenderDoc 整数枚举转为人类可读字符串）：
- `SHADER_STAGE_MAP`: str → enum（vertex/hull/domain/geometry/pixel/compute）
- `FILE_TYPE_MAP`: str → enum（png/jpg/bmp/tga/hdr/exr/dds）
- `MESH_DATA_STAGE_MAP`: str → enum（vsin/vsout/gsout）
- `BLEND_FACTOR_MAP`: int → str（0-16 混合因子名称）
- `BLEND_OP_MAP`: int → str（Add/Subtract/RevSubtract/Min/Max）
- `COMPARE_FUNC_MAP`: int → str（深度比较函数）
- `STENCIL_OP_MAP`: int → str（模板操作）
- `CULL_MODE_MAP`: int → str（None/Front/Back/FrontAndBack）
- `FILL_MODE_MAP`: int → str（Solid/Wireframe/Point）
- `TOPOLOGY_MAP`: int → str（图元拓扑类型）
- `VAR_TYPE_MAP`: int → str（变量类型）
- `SYSTEM_VALUE_MAP`: int → str（系统语义）
- `TEXTURE_DIM_MAP`: int → str（纹理维度）
- `RESOURCE_USAGE_MAP`: int → str（资源使用类型，0-44 连续编号）
- `GRAPHICS_API_MAP`: int → str（D3D11/D3D12/OpenGL/Vulkan）
- `GPU_VENDOR_MAP`: int → str（Unknown/ARM/AMD/.../Samsung）
- `SHADER_STAGE_REVERSE_MAP`: int → str（Vertex/Hull/.../Callable，枚举值→名称反查）
- `DESCRIPTOR_TYPE_MAP`: int → str（Unknown/ConstantBuffer/Sampler/.../AccelerationStructure）
- `RESOURCE_TYPE_MAP`: int → str（Unknown/Buffer/Texture/.../StateObject）

**序列化函数**：
- `serialize_action()` / `serialize_action_detail()` — Action 序列化
- `serialize_sd_object()` — SDObject/SDChunk 递归序列化（处理所有 SDBasic 类型：Struct/Array/Enum/Resource/GPUAddress 等）
- `serialize_texture_desc()` / `serialize_buffer_desc()` / `serialize_resource_desc()` — 资源描述序列化
- `serialize_shader_variable()` — 着色器变量递归序列化（支持不同类型访问器）
- `serialize_usage_entry()` / `serialize_sig_element()` — 用法和签名序列化
- `blend_formula()` — 生成人类可读的混合公式字符串

**辅助函数**：
- `enum_str(value, mapping)` — 枚举值到字符串的安全转换
- `flags_to_list(flags)` — ActionFlags 位掩码转名称列表
- `make_error(message, code)` — 标准化错误字典
- `to_json(obj)` — 紧凑 JSON 序列化

## 工具注册模式

所有 tool 模块都遵循统一模式：

```python
def register(mcp: FastMCP):
    @mcp.tool()
    def tool_name(param1: type, ...) -> str:
        """Tool description with Args docstring."""
        session = get_session()
        err = session.require_open()
        if err:
            return to_json(err)
        # ... 业务逻辑 ...
        return to_json(result)
```

关键模式：
1. 所有工具返回 JSON 字符串
2. 首先调用 `require_open()` 检查捕获状态
3. 需要事件上下文的工具调用 `ensure_event(event_id)`
4. 资源 ID 使用字符串表示，通过 `resolve_resource_id()` 解析
5. 错误通过 `make_error()` 返回标准格式
