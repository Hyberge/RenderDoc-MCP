# RenderDoc MCP 工具完整参考

## 工具总览（56 个）

| 分类 | 数量 | 工具列表 |
|------|------|----------|
| Session Management | 4 | open_capture, close_capture, get_capture_info, get_frame_overview |
| Event Navigation | 7 | list_actions, get_action, set_event, search_actions, find_draws, get_api_calls_by_action, get_api_calls_range |
| Pipeline Inspection | 4 | get_pipeline_state, get_shader_bindings, get_vertex_inputs, get_draw_call_state |
| Resource Analysis | 4 | list_textures, list_buffers, list_resources, get_resource_usage |
| Data Extraction | 9 | save_texture, get_buffer_data, pick_pixel, get_texture_stats, read_texture_pixels, export_draw_textures, save_render_target, export_mesh, **get_texture_histogram** |
| Shader Analysis | 3 | disassemble_shader, get_shader_reflection, get_cbuffer_contents |
| Advanced | 10 | pixel_history, get_post_vs_data, diff_draw_calls, analyze_render_passes, **search_passes**, sample_pixel_region, **find_pixels**, debug_shader_at_pixel, **debug_vertex**, **debug_compute_thread** |
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
- **filter 深度搜索**: 有 filter/filter_flags/event_type 时，递归搜索**所有深度**（不受 max_depth 限制），但超过 max_depth 的匹配节点以 `matched_children_count` + `matched_children`（平铺列表）方式展示而非展开
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
- **新增 action_type 分类**: 每个结果携带 `action_type` 字段（`"draw"`/`"dispatch"`/`"clear"`/`"copy"`/`"resolve"`/`"marker"`/`"other"`）
- **marker 子节点 EID**: `action_type="marker"` 的结果会附加 `child_execution_eids` 列表（含每个子 draw/dispatch/clear 的 `event_id` 和 `type`），避免误用 marker EID 做 debug/dispatch 操作
- **常见陷阱**: 搜索 shader/pass 名时可能命中 debug marker，应读 `action_type` 并用 `child_execution_eids` 中的实际 dispatch/draw EID

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
- **参数**: `resource_id`, `offset=0`, `length=256`, `format="hex"/"floats"/"uint"/"auto"`, `event_id?`
- **限制**: 最大 64KB
- **format 选项**:
  - `"hex"`: 十六进制转储（默认）
  - `"floats"`: 解析为 float32 数组
  - `"uint"`: 解析为 uint32 数组（适合存储 uint/count/index 的 UAV buffer）
  - `"auto"`: 自动推断 — 扫描 shader 反射中的资源名称判断类型（名称含 uint/int → uint，含 float → floats，CBV → floats），无法推断时返回 hex + uint + float 三种并存解读
- **常见陷阱**: `format="floats"` 读 uint buffer 时全部得到 0（因为 uint 0x3B9ACA00 = float 0.005）— 应使用 `format="uint"` 或 `format="auto"`
- **子段自动检测**: 提供 `event_id` 时，自动扫描当前 pipeline state 中所有 stage（VS/HS/DS/GS/PS/CS）的 CBV/SRV/UAV 绑定，若该 buffer 以非零 `byteOffset` 绑定为子段（如 D3D12 Root CBV / Descriptor Table），则将 `byteOffset` 叠加到用户传入的 `offset` 上，使读取起点对准 shader 实际看到的数据
- **返回额外字段**: `absolute_offset`（实际读取偏移）、`binding`（检测到子段时含 stage/type/byteOffset/byteSize）、`format_note`（auto 模式的推断说明）

### pick_pixel
- **参数**: `resource_id`, `x`, `y`, `mip_level=0`, `array_slice=0`, `sample_index=0`, `raw_hex=False`, `event_id?`
- **功能**: 读取指定坐标像素值，根据纹理实际格式（compType/compCount）智能返回正确的通道和数据类型
- **参数校验**: 对 x/y 坐标（考虑 mip 缩放）、mip_level、array_slice、sample_index 均做越界检查
- **返回**: `value`（仅实际通道，如 depth 纹理只有 depth/stencil）、`data_type`（float/uint/sint）、`format_name`、`raw`（完整 float/uint/int 4 通道原始数据）、`texture_info`（元信息）
- **raw_hex**: 传 `true` 时额外返回 `raw_hex` 字段 —— PixelValue union 的 16 字节小端十六进制字符串（格式：`"00 00 80 3f ..."`，共 16 字节）。调用者可按任意格式自由解释，如 `struct.unpack("<4f/4I/2d", ...)`、R10G10B10A2 等自定义打包格式
- **辅助函数**: `_format_pixel_value(val, tex_desc)` — 模块级函数，可被其他工具导入复用
- **RenderDoc API**: `controller.PickPixel(tex_id, x, y, Subresource(mip, slice, sample), CompType.Typeless)`

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

### find_pixels ⭐ 像素搜索工具
- **参数**: `resource_id`, `filters?`, `expression?`, `region?`, `mip_level=0`, `array_slice=0`, `array_slice_end?`, `max_results=100`, `full_scan_threshold=65536`, `sample_count=1024`, `event_id?`
- **功能**: 在纹理中搜索满足条件的像素，支持：
  - **内置过滤器**（`filters` dict）：`min_r/max_r/min_g/...`（float 范围）、`bitmask_r/...`（uint 位掩码 AND）、`bitmask_value_r/...`（掩码期望值，省略则匹配非零）、`has_nan/has_inf/has_negative`（异常检测）
  - **自定义表达式**（`expression` str，最长500字符）：Python 表达式，变量 `r,g,b,a`（float）、`ri,gi,bi,ai`（uint）、`si_r,si_g,si_b,si_a`（sint）、`math`。支持位操作 `& | ^ ~ << >>`。例：`"(ri >> 24) & 0xFF == 128"`
  - **切片范围搜索**：`array_slice` + `array_slice_end` 指定搜索的 slice 闭区间，适用于 3D 纹理/数组纹理/cubemap
- **自适应搜索策略**（按单切片像素数判断）：
  - `≤ full_scan_threshold`（默认 65536）：全量逐像素扫描
  - `> full_scan_threshold`：两阶段搜索（Phase 1 均匀网格采样 `sample_count` 个点，Phase 2 对命中单元精确扫描）
- **返回**: 匹配像素列表（含 x/y/slice/value/data_type）、search_mode、pixels_scanned、stopped_early、slices_searched
- **限制**: max_results 上限 500，sample_count 上限 4096
- **RenderDoc API**: `controller.PickPixel(tex_id, x, y, Subresource(mip, slice, 0), CompType.Typeless)`，格式化复用 `data_tools._format_pixel_value()`

### debug_shader_at_pixel
- **参数**: `event_id`, `pixel_x`, `pixel_y`, `stage="pixel"`, `mode="summary"`, `watch_variables?`, `run_to_step?`, `run_to_line?`
- **功能**: 在指定像素执行 shader 调试，支持 4 种模式：
  - `summary`（默认）: 紧凑概览——inputs/outputs/anomalies/call chain；`final_visible_variables` 包含每个源码变量的**实际运行时值**（对应 GUI High-level Variables 面板）
  - `watch`: 追踪指定变量的值变化历史；额外返回 `not_found_variables`（未找到的变量名列表）和 `available_variables`（当前帧末可用的所有源码变量名，供参考）
  - `anomaly`: 定位第一个 NaN/Inf 出现的步骤和上下文
  - `step`: 运行到指定步骤/源码行；`visible_source_variables` 包含每个源码变量的**实际运行时值**；额外返回 `execution_context`（前后各 5 步的 `{step, instruction, line}` 序列）和可能的 `compiler_reorder_warning`（检测到行号非线性跳转时出现）
- **变量值解析机制**: 复刻 GUI `GetDebugVariable()`，根据 `DebugVariableReference.type` 分派到 6 个池（Variable→累积 changes 快照, Input→trace.inputs, Constant→trace.constantBlocks, ReadOnlyResource→trace.readOnlyResources, ReadWriteResource→trace.readWriteResources, Sampler→trace.samplers）；值以 `value` 字段返回，`status="undefined"` 表示未定义，`status="not_resolved"` 表示未找到对应调试变量
- **watch 变量查找**: watch 模式只在变量**发生 change 时**记录，若变量存在但整个 trace 没有变化则不会出现在 history 中（可用 `available_variables` 查看实际可 watch 的变量）
- **编译器重排警告**: `compiler_reorder_warning` 表示 DXIL 指令顺序不等于源码执行顺序，行号跳变是指令调度导致的，**不代表 branch taken**
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

### search_passes ⭐ 模糊搜索工具
- **参数**: `name?`, `min_draws?`, `max_draws?`, `adjacent_format?`, `rt?`（dict）, `depth?`（dict）, `stencil?`（dict）, `shader?`（dict）
- **功能**: 按 RT 属性、管线状态、相邻 pass 模式多维模糊搜索渲染 pass。采用两层过滤设计：
  - **第一层（零 set_event）**: name / min_draws / max_draws / adjacent_format / rt{format,count,width,height} 纯缓存过滤
  - **第二层（逐 draw 检查）**: depth{enabled,write,func} / stencil{enabled,func,pass_op} / shader{has_vs,has_ps,has_cs} 对候选 pass 内每个 draw 做 set_event + GetPipelineState
- **rt.format**: 支持 str（任意 RT 匹配）或 list[str]（MRT 按位置匹配，null 跳过槽位）
- **返回**: matches 数组（每个 pass 含 render_target_info + prev_pass/next_pass 上下文 + matched_draws 列表）, count, total_passes
- **内部实现**: 调用 `_detect_passes(session)` 获取 pass 列表，两层过滤后恢复原始 event 位置
- **典型用法**:
  - `search_passes(rt={"format": "R11G11B10"})` — 找灯光 pass
  - `search_passes(rt={"format": "R11G11B10"}, adjacent_format="B8G8R8A8")` — 找投影灯（前有 shadow mask）
  - `search_passes(depth={"enabled": true, "func": "Less"}, stencil={"enabled": true})` — 按管线状态搜索

---

## Performance Tools (performance_tools.py)

### get_pass_timing
- **参数**: `max_depth=2`, `parent_eid?`, `min_ms=0.0`
- **功能**: 火焰图式层级耗时分析。沿 action 树返回 GPU Duration，支持逐层向下钻取
- **工作流**: 先 `get_pass_timing()` 看总览 → 对最耗时节点调 `get_pass_timing(parent_eid=X)` 钻入 → 重复直至叶子 draw call
- **GPU 计时**: 优先 `EnumerateCounters()` + `FetchCounters()` 获取真实 GPU Duration（单位：秒→自动转 ms），无 counter 时回退到三角形数量启发式估算
- **层级感知**: 有 debug marker 的截帧（如 UE5）展示引擎渲染阶段层级；无 marker 的截帧展示为扁平列表
- **RenderDoc API**: `controller.EnumerateCounters()`, `controller.FetchCounters()`, `controller.GetRootActions()` (action tree)

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
- **功能**: 顶点着色器调试，支持与 `debug_shader_at_pixel` 相同的 4 种模式（summary/watch/anomaly/step），包含所有相同的增强：变量值解析（High-level Variables）、watch 的 not_found_variables/available_variables、step 的 execution_context 和 compiler_reorder_warning
- **回退**: 调试不支持时返回 input/output signature + 建议
- **RenderDoc API**: `controller.DebugVertex(vertid, instid, idx, view)` → `ContinueDebug` → `FreeTrace`

### debug_compute_thread
- **参数**: `event_id`, `group_id=[0,0,0]`, `thread_id=[0,0,0]`, `mode="summary"`, `watch_variables?`, `run_to_step?`, `run_to_line?`
- **功能**: Compute Shader 线程调试，支持与 `debug_shader_at_pixel` 相同的 4 种模式（summary/watch/anomaly/step），包含所有相同的增强：变量值解析、watch 的 not_found_variables/available_variables、step 的 execution_context 和 compiler_reorder_warning
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
  get_frame_overview → list_actions → analyze_render_passes → search_passes → search_actions

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
