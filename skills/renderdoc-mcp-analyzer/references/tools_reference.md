# RenderDoc MCP 工具完整参考

## 工具总览（54 个）

| 分类 | 数量 | 工具列表 |
|------|------|----------|
| Session Management | 4 | open_capture, close_capture, get_capture_info, get_frame_overview |
| Event Navigation | 7 | list_actions, get_action, set_event, search_actions, find_draws, get_api_calls_by_action, get_api_calls_range |
| Pipeline Inspection | 4 | get_pipeline_state, get_shader_bindings, get_vertex_inputs, get_draw_call_state |
| Resource Analysis | 4 | list_textures, list_buffers, list_resources, get_resource_usage |
| Data Extraction | 9 | save_texture, get_buffer_data, pick_pixel, get_texture_stats, read_texture_pixels, export_draw_textures, save_render_target, export_mesh, **get_texture_histogram** |
| Shader Analysis | 3 | disassemble_shader, get_shader_reflection, get_cbuffer_contents |
| Advanced | 8 | pixel_history, get_post_vs_data, diff_draw_calls, analyze_render_passes, sample_pixel_region, debug_shader_at_pixel, **debug_vertex**, **debug_compute_thread** |
| Performance | 4 | get_pass_timing, analyze_overdraw, analyze_bandwidth, analyze_state_changes |
| Diagnostics | 4 | diagnose_negative_values, diagnose_precision_issues, diagnose_reflection_mismatch, diagnose_mobile_risks |
| Inspection | 7 | **get_debug_messages**, **get_api_properties**, **get_frame_info**, **get_diagnostic_overview**, **get_descriptor_access**, **get_resource_details**, **get_cached_message** |

---

## Session Management (session_tools.py)

### open_capture
- **参数**: `filepath: str` — .rdc 文件绝对路径; `shader_debug_paths: Optional[list[str]]` — shader PDB 搜索路径列表
- **功能**: 打开捕获文件，自动关闭之前的捕获。构建 action_map、event_map 和资源缓存。若提供 `shader_debug_paths`，在打开前配置 `DXBC.Debug.SearchDirPaths`，使 disassemble_shader 可返回 HLSL 源码
- **超时**: 60s（默认 10s 的特殊覆盖，因大截帧含 shader PDB 扫描耗时较长）
- **返回**: filepath, api, total_actions, root_actions, textures, buffers 数量, shader_debug_paths（若设置）
- **RenderDoc API**: `rd.OpenCaptureFile()` → `cap.OpenFile()` → `cap.OpenCapture()`; `rd.SetConfigSetting("DXBC.Debug.SearchDirPaths")` 设置 PDB 路径

### close_capture
- **参数**: 无
- **功能**: 关闭当前捕获，释放 controller 和 cap 资源
- **RenderDoc API**: `controller.Shutdown()`, `cap.Shutdown()`

### get_capture_info
- **参数**: 无
- **功能**: 获取捕获元数据，检测分辨率（从最常用 RT 推断），识别 GPU quirks
- **返回**: filepath, api, resolution, main_color_format, total_actions, textures/buffers 数量, current_event, known_gpu_quirks
- **GPU Quirks 检测**: 基于 `driver_name` 识别 Adreno/Mali/PowerVR/Apple/OpenGL ES 特定问题

### get_frame_overview
- **参数**: 无
- **功能**: 帧级统计概览
- **返回**: 按类型的 action 计数（draw/clear/dispatch）、纹理/Buffer 总内存（MB）、render targets 列表（含 draw count/size/format）、推断分辨率
- **内存估算**: 基于格式启发式计算（考虑 mip chain 1.33x 倍数）

---

## Event Navigation (event_tools.py)

### list_actions
- **参数**: `max_depth=2`, `filter_flags`, `filter`（名称子串）, `event_type`（draw/dispatch/clear/copy/resolve）
- **功能**: 列出 action 树，支持多种过滤。event_type 优先级高于 filter_flags
- **返回**: actions 数组 + total 数量

### get_action
- **参数**: `event_id: int`
- **功能**: 获取单个 action 的完整详情
- **返回**: event_id, name, flags, num_indices/instances, index/base/vertex/instance offsets, parent/prev/next IDs

### set_event
- **参数**: `event_id: int`
- **功能**: **关键工具**——导航到指定事件，后续 pipeline 查询反映该事件状态
- **RenderDoc API**: `controller.SetFrameEvent(eventId, True)`

### search_actions
- **参数**: `name_pattern`（正则）, `flags`（ActionFlags 名称列表）
- **功能**: 按名称正则和/或 flags 搜索所有 actions

### find_draws
- **参数**: `blend`, `min_vertices`, `texture_id`, `shader_id`, `render_target_id`, `max_results=50`
- **功能**: 按渲染状态搜索 draw calls（需逐个 set_event 检查 pipeline state，可能较慢）
- **注意**: 完成后恢复原始 event 位置

### get_api_calls_by_action
- **参数**: `event_id: int`, `include_params: bool = True`
- **功能**: 获取某个 action 关联的所有原始 API 调用（包括 BeginQuery、SetRootCBV、ResourceBarrier 等非 action 级调用），通过 `ActionDescription.events` + `SDFile.chunks[chunkIndex]` 访问
- **返回**: action_event_id, action_name, api_calls（每个包含 event_id, name, duration_us, thread_id, params）, total
- **RenderDoc API**: `action.events` → `APIEvent.chunkIndex` → `SDFile.chunks[]` → `SDChunk`（继承 SDObject），参数通过 `serialize_sd_object()` 递归序列化

### get_api_calls_range
- **参数**: `start_eid: int`, `end_eid: int`, `include_params: bool = True`, `name_filter: Optional[str] = None`
- **功能**: 按 EID 范围查询所有 API 调用，支持按函数名子串（大小写不敏感）过滤
- **返回**: range, api_calls, total, name_filter
- **RenderDoc API**: 通过 session.event_map（eid → chunkIndex 全局映射）查找，同上 SDChunk 序列化

---

## Pipeline Inspection (pipeline_tools.py)

### get_pipeline_state
- **参数**: `event_id?`
- **功能**: 获取完整管线状态——拓扑、视口、裁剪、光栅化、混合、深度、模板、已绑定 shader、输出目标
- **RenderDoc API**: `controller.GetPipelineState()` → `GetViewport(i)`, `GetScissor(i)`, `GetColorBlends()`, `GetStencilFaces()`；depth/rasterizer 通过 API-specific state（`GetD3D12PipelineState()` 等）获取
- **注意**: PipeState 没有 `GetViewports()`/`GetScissors()`/`GetColorBlend()`/`GetDepthState()`/`GetRasterizer()`/`GetStencilState()` 方法

### get_shader_bindings
- **参数**: `stage: str`, `event_id?`
- **功能**: 获取指定 shader 阶段的资源绑定——constant buffers, SRVs, UAVs, samplers
- **RenderDoc API**: `state.GetConstantBlock()`, `state.GetReadOnlyResources()`, `state.GetReadWriteResources()`, `state.GetSamplers()`

### get_vertex_inputs
- **参数**: `event_id?`
- **功能**: 获取顶点输入布局、vertex/index buffer 绑定
- **RenderDoc API**: `state.GetIBuffer()`, `state.GetVBuffers()`, `state.GetVertexInputs()`

### get_draw_call_state ⭐ 高级工具
- **参数**: `event_id: int`
- **功能**: **一站式 draw call 分析**——action info + blend formula + depth/stencil + rasterizer + textures (with sizes) + render targets + shader summaries
- **内部共享**: `_get_draw_state_dict()` 被 `diff_draw_calls` 复用

---

## Resource Analysis (resource_tools.py)

### list_textures
- **参数**: `filter_format?`, `min_width?`

### list_buffers
- **参数**: `min_size?`

### list_resources
- **参数**: `type_filter?`, `name_pattern?`（正则）

### get_resource_usage
- **参数**: `resource_id: str`
- **功能**: 查看资源在所有事件中的使用历史（读/写）
- **RenderDoc API**: `controller.GetUsage(rid)`

---

## Data Extraction (data_tools.py)

### save_texture
- **参数**: `resource_id`, `output_path`, `file_type="png"`, `mip=0`, `event_id?`
- **支持格式**: PNG, JPG, BMP, TGA, HDR, EXR, DDS
- **RenderDoc API**: `controller.SaveTexture(texsave, path)`

### get_buffer_data
- **参数**: `resource_id`, `offset=0`, `length=256`, `format="hex"/"floats"`
- **限制**: 最大 64KB

### pick_pixel
- **参数**: `resource_id`, `x`, `y`, `event_id?`
- **返回**: float RGBA + uint RGBA
- **RenderDoc API**: `controller.PickPixel(tex_id, x, y, Subresource, CompType.Typeless)`

### get_texture_stats
- **参数**: `resource_id`, `event_id?`, `all_slices=False`
- **功能**: Min/Max 统计 + NaN/Inf/负值异常自动检测
- **Cubemap 支持**: all_slices=True 时返回每个面的统计（+X/-X/+Y/-Y/+Z/-Z）
- **RenderDoc API**: `controller.GetMinMax(tex_id, Subresource, CompType.Typeless)`

### read_texture_pixels
- **参数**: `resource_id`, `x`, `y`, `width`, `height`, `mip_level=0`, `array_slice=0`, `event_id?`
- **限制**: 区域上限 64×64
- **功能**: 逐像素读取 + 自动标注异常（NaN/Inf/负值）

### export_draw_textures ⭐ 批量工具
- **参数**: `event_id`, `output_dir`, `skip_small=True`
- **功能**: 批量导出 draw call pixel shader 绑定的所有纹理（跳过 4×4 以下占位纹理）

### save_render_target
- **参数**: `event_id`, `output_path`, `save_depth=False`
- **功能**: 保存指定事件的 RT 快照（颜色 + 可选深度）

### export_mesh
- **参数**: `event_id`, `output_path`
- **功能**: 导出 post-VS mesh 为 OBJ 格式（含位置/法线/UV），支持 TriangleList/Strip/Fan 拓扑
- **RenderDoc API**: `controller.GetPostVSData(0, 0, MeshDataStage.VSOut)`

---

## Shader Analysis (shader_tools.py)

### disassemble_shader
- **参数**: `stage`, `target?`, `event_id?`, `line_range?`, `search?`
- **功能**: 反汇编 shader。若 target 省略且有 HLSL 源码（通过 shader PDB），自动返回 HLSL 源码；`target="hlsl"` 强制请求源码。支持自动回退链（尝试所有目标）、行范围过滤、关键字搜索（带 5 行上下文）
- **HLSL 源码**: 通过 `refl.debugInfo.files` 检测，需在 `open_capture` 时设置 `shader_debug_paths`
- **回退**: 所有反汇编失败时返回 reflection 信息

### get_shader_reflection
- **参数**: `stage`, `event_id?`
- **返回**: input/output signatures, constant blocks, read-only/read-write resources, samplers

### get_cbuffer_contents
- **参数**: `stage`, `cbuffer_index`, `event_id?`, `filter?`
- **功能**: 读取 constant buffer 的实际变量值（递归序列化），支持变量名过滤
- **返回**: stage, cbuffer_index, cbuffer_name, resource_id, byte_offset, byte_size, filter, variables, variable_count
- **RenderDoc API**: `controller.GetCBufferVariableContents(pipe, shaderId, stage, entry, cbIdx, resource, byteOffset, byteSize)`
- **关键实现细节**: 必须从 `cb_bind.descriptor.byteOffset` 和 `cb_bind.descriptor.byteSize` 获取偏移和大小传给 API（与 RenderDoc GUI BufferViewer.cpp:3745 一致）。传 `0, 0` 在 D3D12 中会导致读到全零，因为 cbuffer 通常绑定在大 buffer 的非零偏移处（通过 Root CBV / Descriptor Table）

---

## Advanced Tools (advanced_tools.py)

### sample_pixel_region ⭐ 诊断工具
- **参数**: `event_id?`, `resource_id?`, `region?`, `sample_count=256`, `anomaly_threshold=10.0`
- **功能**: 均匀网格采样 RT，自动检测 NaN/Inf/负值/极亮像素，生成统计和诊断提示
- **限制**: 最大 1024 采样点

### debug_shader_at_pixel
- **参数**: `event_id`, `pixel_x`, `pixel_y`, `stage="pixel"`, `mode="summary"`, `watch_variables?`, `run_to_step?`, `run_to_line?`
- **功能**: 在指定像素执行 shader 调试，支持 4 种模式：
  - `summary`（默认）: 紧凑概览——inputs/outputs/anomalies/call chain
  - `watch`: 追踪指定变量的值变化历史
  - `anomaly`: 定位第一个 NaN/Inf 出现的步骤和上下文
  - `step`: 运行到指定步骤/源码行，显示所有可见源码变量
- **run_to_line 容错**: 若目标行被编译器优化掉，自动 snap 到最近可达行；若无可达行，返回 nearby_executed_lines 提示
- **回退**: 调试不支持时返回像素值 + shader 信息 + 建议
- **RenderDoc API**: `controller.DebugPixel(x, y, DebugPixelInputs())` → `ContinueDebug(trace.debugger)` 循环 → `FreeTrace(trace)`

### pixel_history
- **参数**: `resource_id`, `x`, `y`, `event_id?`
- **功能**: 获取像素完整修改历史——每个事件的 pre/post 值、通过/失败状态（深度/模板/裁剪/discard）
- **RenderDoc API**: `controller.PixelHistory(tex_id, x, y, ...)`

### get_post_vs_data
- **参数**: `stage="vsout"`, `max_vertices=100`, `event_id?`
- **功能**: 获取变换后的顶点数据（vsin/vsout/gsout）

### diff_draw_calls ⭐ 对比工具
- **参数**: `eid1`, `eid2`
- **功能**: 对比两个 draw call 的完整状态差异，带人类可读的 implication 注释
- **中文 Implications**: blend/depth/stencil/rasterizer/topology 变化的含义解释

### analyze_render_passes
- **参数**: 无
- **功能**: 自动检测 render pass 边界（通过 Clear/RT 切换），汇总每个 pass 的 draw count/RT info

---

## Performance Tools (performance_tools.py)

### get_pass_timing
- **参数**: `granularity="pass"/"draw_call"`, `top_n=20`
- **功能**: 尝试 GPU 计数器获取真实时间，回退到三角形数量启发式估算
- **RenderDoc API**: `controller.EnumerateCounters()`, `controller.FetchCounters()`

### analyze_overdraw
- **参数**: `pass_name?`, `region?`, `sample_count=64`
- **功能**: 估算 overdraw——按 RT 分组统计 draw count 作为代理

### analyze_bandwidth
- **参数**: `breakdown_by="pass"/"resource_type"/"operation"`
- **功能**: 估算 GPU 内存带宽消耗，识别 tile load/store 操作

### analyze_state_changes
- **参数**: `pass_name?`, `change_types?`
- **功能**: 分析连续 draw call 间的状态变化（shader/blend/depth/cull/render_target），识别 batching 机会
- **限制**: 最多分析 200 个 draw call

---

## Diagnostic Tools (diagnostic_tools.py)

### diagnose_negative_values ⭐ 核心诊断
- **参数**: `check_targets?`, `trace_depth=5`
- **功能**: 扫描所有浮点 RT 的负值/NaN/Inf，追踪首次引入的事件，检测 TAA 累积放大
- **根因分析**: 自动识别 IBL/SH/TAA/Temporal 相关的根因候选

### diagnose_precision_issues
- **参数**: `focus="all"/"color_precision"/"depth_precision"/"format_risks"`, `threshold=0.01`
- **检测项**: R11G11B10 无符号位、D16 深度精度不足、SRGB/linear 不一致

### diagnose_reflection_mismatch
- **参数**: `reflection_pass_hint?`, `object_hint?`
- **功能**: 自动配对正常/反射 draw call，对比 shader/blend/RT 格式差异，量化亮度比例

### diagnose_mobile_risks ⭐ 综合风险检查
- **参数**: `check_categories?`, `severity_filter="all"/"medium"/"high"`
- **检查类别**: precision (R11G11B10/D16), performance (全屏 pass/tile), compatibility (纹理尺寸), gpu_specific (Adreno/Mali)
- **输出**: 按严重性排序的风险列表 + 修复建议

---

## Inspection Tools (inspection_tools.py) — 新增

### get_debug_messages
- **参数**: 无
- **功能**: 获取 API 验证层和驱动的调试消息，按 severity 排序（High 在前）
- **返回**: messages 数组（event_id, severity, category, source, description）+ count + 按严重性的 summary
- **RenderDoc API**: `controller.GetDebugMessages()`

### get_api_properties
- **参数**: 无
- **功能**: 查询 API 能力元信息，让 LLM 在调用 pixel_history 或 debug 工具前知道是否支持
- **返回**: pipeline_type, local_renderer, vendor, pixel_history_supported, shader_debugging_supported, degraded, rgp_capture_supported, hints
- **RenderDoc API**: `controller.GetAPIProperties()`

### get_frame_info
- **参数**: 无
- **功能**: 获取 FrameDescription 中的精确帧信息
- **返回**: frame_number, compressed/uncompressed_file_size, capture_time, statistics
- **统计数据**: `statistics.recorded` 标识是否收集了帧统计。仅 D3D11 capture 会返回有效统计数据；D3D12/Vulkan/GL 的 `recorded=false`，会返回提示而非空数据
- **RenderDoc API**: `controller.GetFrameInfo()` — 返回单个 FrameDescription（非列表）

### get_diagnostic_overview ⭐ 一站式诊断入口
- **参数**: 无
- **功能**: **聚合工具**——一次调用收集 API properties + debug messages 摘要 + precision 格式风险 + 帧统计 + GPU quirks + 分辨率，提供 suggested_next_steps
- **用途**: LLM 打开捕获后的"第一步"，快速了解是否有明显问题

### get_descriptor_access
- **参数**: `event_id?`
- **功能**: 获取当前事件的描述符访问详情（shader stage, type, index, array_element, statically_unused）
- **主要用于**: D3D12/Vulkan 的现代绑定模型
- **RenderDoc API**: `controller.GetDescriptorAccess()`

### get_resource_details
- **参数**: `resource_id: str`
- **功能**: 获取资源完整信息：type, name, parentResources, derivedResources, annotations, texture_info（含 byte_size/cubemap）, buffer_info（含 gpu_address）, usage_summary
- **RenderDoc API**: `controller.GetResources()` + `controller.GetUsage()`

### get_cached_message
- **参数**: `message_id: str`, `page=1`, `page_size=50`, `search?`, `collection?`, `context_lines=2`
- **功能**: 查询 server 端缓存的大结果，支持分页、切换 collection 视图、全文搜索
- **适用场景**: `list_actions` / `list_textures` / `disassemble_shader` 等返回内容过长时的二次查询
- **返回**: available_views, selected_view, items/lines/matches, pagination

---


## Data Extraction 新增工具

### get_texture_histogram
- **参数**: `resource_id`, `min_val=0.0`, `max_val=1.0`, `channels="rgb"`, `event_id?`
- **功能**: 获取纹理 256 bucket 直方图，附带分布特征分析（过曝/欠曝/clamp 检测）
- **RenderDoc API**: `controller.GetHistogram(texId, sub, compType, minVal, maxVal, (r,g,b,a))` — channels 为 4-bool tuple，共 6 个参数

---

## Advanced Tools 新增工具

### debug_vertex
- **参数**: `event_id`, `vertex_id`, `instance_id=0`, `index=0`, `view=0`, `mode="summary"`, `watch_variables?`, `run_to_step?`, `run_to_line?`
- **功能**: 顶点着色器调试，支持与 `debug_shader_at_pixel` 相同的 4 种模式（summary/watch/anomaly/step）
- **回退**: 调试不支持时返回 input/output signature + 建议
- **RenderDoc API**: `controller.DebugVertex(vertid, instid, idx, view)` → `ContinueDebug` → `FreeTrace`

### debug_compute_thread
- **参数**: `event_id`, `group_id=[0,0,0]`, `thread_id=[0,0,0]`, `mode="summary"`, `watch_variables?`, `run_to_step?`, `run_to_line?`
- **功能**: Compute Shader 线程调试，支持与 `debug_shader_at_pixel` 相同的 4 种模式（summary/watch/anomaly/step）
- **回退**: 调试不支持时返回 constant_blocks + rw_resources + 建议
- **RenderDoc API**: `controller.DebugThread(groupid, threadid)` → `ContinueDebug` → `FreeTrace`

---

## LLM 分析层次设计

```
Layer 0 — 打开 & 元信息:
  open_capture → get_capture_info → get_api_properties → get_frame_info

Layer 1 — 帧级诊断入口:
  get_diagnostic_overview → get_debug_messages

Layer 2 — 帧结构导航:
  get_frame_overview → list_actions → analyze_render_passes → search_actions

Layer 3 — 事件级分析:
  set_event → get_draw_call_state → get_pipeline_state → get_shader_bindings
  → get_descriptor_access → get_vertex_inputs
  → get_api_calls_by_action → get_api_calls_range (raw API calls with params)

Layer 4 — 数据深挖:
  disassemble_shader → get_cbuffer_contents → get_texture_histogram
  → pixel_history → debug_shader_at_pixel → debug_vertex → debug_compute_thread

Layer 5 — 专项诊断:
  diagnose_negative_values → diagnose_precision_issues → diagnose_mobile_risks
```

---

## 3 个内置 Prompt

### debug_draw_call(event_id)
引导深入分析单个 draw call：pipeline → shader bindings → cbuffers → 输出

### find_rendering_issue(description)
从用户描述出发系统排查：capture info → actions → pipeline → shaders → pixel analysis → diagnostics

### analyze_performance()
帧级性能分析流程：info → overview → timing → overdraw → bandwidth → state changes → mobile risks
