# RenderDoc Python 脚本 Recipe

每个 Recipe 都是完整可运行的 Python 脚本。使用前请替换 `PYMODULE_DIR` 和 `RDC_FILE`。

---

## Recipe 1: 打开截帧并获取基本信息

```python
"""打开 .rdc 截帧文件，打印 API 类型、action 数量、纹理/缓冲区列表等基本信息。"""
import os, sys, json

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded, f"OpenFile failed: {result}"
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded, f"OpenCapture failed: {result}"

try:
    textures = controller.GetTextures()
    buffers = controller.GetBuffers()
    root_actions = controller.GetRootActions()

    # 递归计数所有 action
    def count_actions(actions):
        total = 0
        for a in actions:
            total += 1
            if len(a.children) > 0:
                total += count_actions(a.children)
        return total

    info = {
        "filepath": RDC_FILE,
        "api": cap.DriverName(),
        "total_actions": count_actions(root_actions),
        "root_actions": len(root_actions),
        "textures": len(textures),
        "buffers": len(buffers),
    }

    # 帧信息
    frame = controller.GetFrameInfo()
    info["frame_number"] = frame.frameNumber
    info["compressed_size_mb"] = round(frame.compressedFileSize / 1048576, 2)

    print(json.dumps(info, indent=2))

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 2: 遍历所有 Draw Call 并统计

```python
"""遍历所有 action，按类型（draw/dispatch/clear/copy）统计数量。"""
import os, sys, json

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    sf = controller.GetStructuredFile()
    stats = {"draw": 0, "dispatch": 0, "clear": 0, "copy": 0, "resolve": 0, "other": 0}
    draws = []

    def walk_actions(actions):
        for a in actions:
            flags = int(a.flags)
            if flags & int(rd.ActionFlags.Drawcall):
                stats["draw"] += 1
                draws.append({
                    "eid": a.eventId,
                    "name": a.GetName(sf),
                    "indices": a.numIndices,
                    "instances": a.numInstances,
                })
            elif flags & int(rd.ActionFlags.Dispatch):
                stats["dispatch"] += 1
            elif flags & int(rd.ActionFlags.Clear):
                stats["clear"] += 1
            elif flags & int(rd.ActionFlags.Copy):
                stats["copy"] += 1
            elif flags & int(rd.ActionFlags.Resolve):
                stats["resolve"] += 1

            if len(a.children) > 0:
                walk_actions(a.children)

    walk_actions(controller.GetRootActions())

    print(f"Action 统计: {json.dumps(stats)}")
    print(f"\n前 10 个 draw call:")
    for d in draws[:10]:
        print(f"  EID {d['eid']}: {d['name']} ({d['indices']} indices, {d['instances']} instances)")

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 3: 查询特定 Event 的完整管线状态

```python
"""查询指定 event 的管线状态：拓扑、视口、混合、深度、光栅化、Shader 绑定等。"""
import os, sys, json

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"
EVENT_ID = 100  # 替换为目标 event ID

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    controller.SetFrameEvent(EVENT_ID, True)
    state = controller.GetPipelineState()
    api = cap.DriverName()
    info = {"event_id": EVENT_ID, "api": api}

    # 拓扑
    info["topology"] = str(state.GetPrimitiveTopology())

    # 视口（逐个遍历）
    viewports = []
    for i in range(16):
        try:
            vp = state.GetViewport(i)
            if vp.width == 0 and vp.height == 0:
                break
            viewports.append({"x": vp.x, "y": vp.y, "w": vp.width, "h": vp.height})
        except:
            break
    info["viewports"] = viewports

    # 混合状态
    blends = state.GetColorBlends()
    info["blend_count"] = len(blends)
    if blends:
        b = blends[0]
        info["blend_0_enabled"] = b.enabled

    # 深度/光栅化状态（API-specific）
    if "D3D12" in api:
        pipe = controller.GetD3D12PipelineState()
        ds = pipe.outputMerger.depthStencilState
        rs = pipe.rasterizer.state
    elif "D3D11" in api:
        pipe = controller.GetD3D11PipelineState()
        ds = pipe.outputMerger.depthStencilState
        rs = pipe.rasterizer.state
    elif "Vulkan" in api:
        pipe = controller.GetVulkanPipelineState()
        ds = pipe.depthStencil
        rs = pipe.rasterizer
    elif "OpenGL" in api:
        pipe = controller.GetGLPipelineState()
        ds = pipe.depthState
        rs = pipe.rasterizer.state
    else:
        ds, rs = None, None

    if ds:
        depth_enable = getattr(ds, "depthEnable", getattr(ds, "depthTestEnable", None))
        info["depth_enable"] = depth_enable
        info["depth_func"] = str(ds.depthFunction)
    if rs:
        info["cull_mode"] = str(rs.cullMode)
        info["fill_mode"] = str(rs.fillMode)

    # 输出目标
    outputs = state.GetOutputTargets()
    info["render_targets"] = [str(o.resource) for o in outputs if int(o.resource) != 0]
    dt = state.GetDepthTarget()
    if int(dt.resource) != 0:
        info["depth_target"] = str(dt.resource)

    # 绑定的 Shader
    stages = {
        "vertex": rd.ShaderStage.Vertex, "pixel": rd.ShaderStage.Pixel,
        "hull": rd.ShaderStage.Hull, "domain": rd.ShaderStage.Domain,
        "geometry": rd.ShaderStage.Geometry, "compute": rd.ShaderStage.Compute,
    }
    shaders = {}
    for name, stage in stages.items():
        refl = state.GetShaderReflection(stage)
        if refl:
            shaders[name] = {"id": str(refl.resourceId), "entry": state.GetShaderEntryPoint(stage)}
    info["shaders"] = shaders

    print(json.dumps(info, indent=2))

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 4: 导出指定纹理为图片文件

```python
"""将指定 ResourceId 的纹理导出为 PNG 文件。"""
import os, sys

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"
EVENT_ID = 100        # 需要先导航到使用该纹理的 event
OUTPUT_DIR = r"."     # 输出目录

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    controller.SetFrameEvent(EVENT_ID, True)

    # 列出所有纹理，找到目标
    textures = controller.GetTextures()
    for tex in textures:
        # 可以按名称或尺寸过滤
        # if "SceneColor" not in tex.name: continue

        texsave = rd.TextureSave()
        texsave.resourceId = tex.resourceId
        texsave.destType = rd.FileType.PNG  # PNG/JPG/HDR/EXR/DDS
        texsave.mip = 0
        texsave.slice.sliceIndex = 0
        texsave.alpha = rd.AlphaMapping.Preserve

        name = tex.name if tex.name else str(tex.resourceId)
        # 清理文件名
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
        path = os.path.join(OUTPUT_DIR, f"{safe_name}_{tex.width}x{tex.height}.png")
        controller.SaveTexture(texsave, path)
        print(f"Saved: {path} ({tex.width}x{tex.height}, {tex.format.Name()})")

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 5: 读取 CBuffer 常量值

```python
"""读取指定 event 下 Pixel Shader 的所有常量缓冲区变量值。"""
import os, sys, json

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"
EVENT_ID = 100
STAGE = rd.ShaderStage.Pixel  # 可改为 Vertex/Compute 等

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    controller.SetFrameEvent(EVENT_ID, True)
    state = controller.GetPipelineState()
    refl = state.GetShaderReflection(STAGE)
    assert refl is not None, "No shader bound at this stage"

    pipeline = state.GetGraphicsPipelineObject()

    for idx, cb_refl in enumerate(refl.constantBlocks):
        cb_bind = state.GetConstantBlock(STAGE, idx, 0)
        # 必须用 descriptor 的 byteOffset/byteSize（D3D12 上传 0,0 会全零）
        buf_res = cb_bind.descriptor.resource
        offset = int(cb_bind.descriptor.byteOffset)
        size = int(cb_bind.descriptor.byteSize)

        if int(buf_res) == 0:
            continue

        vars = controller.GetCBufferVariableContents(
            pipeline, refl.resourceId, STAGE, refl.entryPoint,
            idx, buf_res, offset, size
        )

        print(f"\n=== CBuffer[{idx}]: {cb_refl.name} (offset={offset}, size={size}) ===")
        for var in vars:
            if len(var.members) > 0:
                print(f"  {var.name}: (struct, {len(var.members)} members)")
                for m in var.members:
                    vals = [var.value.f32v[r * m.columns + c]
                            for r in range(m.rows) for c in range(m.columns)]
                    print(f"    .{m.name} = {vals}")
            else:
                vals = [var.value.f32v[r * var.columns + c]
                        for r in range(var.rows) for c in range(var.columns)]
                print(f"  {var.name} = {vals}")

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 6: 像素历史追踪

```python
"""追踪指定像素的所有修改历史（哪些 draw call 写入了该像素）。"""
import os, sys, json

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"
TARGET_X, TARGET_Y = 640, 360      # 像素坐标
TARGET_RT = None                     # 指定 RT 的 ResourceId 字符串，None 则自动选择最后一个 RT

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    # 先检查是否支持 pixel history
    props = controller.GetAPIProperties()
    if not props.pixelHistory:
        print("WARNING: Pixel history not supported on this API/driver")

    # 找到目标 RT
    if TARGET_RT:
        # 从资源缓存查找
        for tex in controller.GetTextures():
            if str(tex.resourceId) == TARGET_RT:
                tex_id = tex.resourceId
                break
    else:
        # 导航到最后一个 root action，获取其输出 RT
        root_actions = controller.GetRootActions()
        last_action = root_actions[-1]
        # 找到最后一个实际 draw
        def find_last_draw(actions):
            for a in reversed(actions):
                if len(a.children) > 0:
                    r = find_last_draw(a.children)
                    if r: return r
                if int(a.flags) & int(rd.ActionFlags.Drawcall):
                    return a
            return None
        last_draw = find_last_draw(root_actions)
        if last_draw:
            controller.SetFrameEvent(last_draw.eventId, True)
            state = controller.GetPipelineState()
            outputs = state.GetOutputTargets()
            for o in outputs:
                if int(o.resource) != 0:
                    tex_id = o.resource
                    break

    history = controller.PixelHistory(
        tex_id, TARGET_X, TARGET_Y,
        rd.Subresource(0, 0, 0), rd.CompType.Typeless
    )

    print(f"Pixel ({TARGET_X}, {TARGET_Y}) history on {tex_id}: {len(history)} modifications\n")

    for mod in history:
        passed = mod.Passed()
        pre = [mod.preMod.col.floatValue[i] for i in range(4)]
        post = [mod.postMod.col.floatValue[i] for i in range(4)]
        status = "PASS" if passed else "FAIL"
        fail_reason = ""
        if not passed:
            if mod.backfaceCulled: fail_reason = " (backface culled)"
            elif mod.depthTestFailed: fail_reason = " (depth test failed)"
            elif mod.stencilTestFailed: fail_reason = " (stencil test failed)"
            elif mod.scissorClipped: fail_reason = " (scissor clipped)"

        print(f"  EID {mod.eventId}: [{status}{fail_reason}]")
        print(f"    pre:  RGBA={[round(v,4) for v in pre]}")
        print(f"    post: RGBA={[round(v,4) for v in post]}")

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 7: Shader 调试（Pixel Shader）

```python
"""调试指定像素的 Pixel Shader，收集所有执行步骤和变量变化。"""
import os, sys, json

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"
EVENT_ID = 100
PIXEL_X, PIXEL_Y = 640, 360

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    controller.SetFrameEvent(EVENT_ID, True)

    # 检查是否支持 shader 调试
    props = controller.GetAPIProperties()
    if not props.shaderDebugging:
        print("WARNING: Shader debugging not supported")

    trace = controller.DebugPixel(PIXEL_X, PIXEL_Y, rd.DebugPixelInputs())
    if trace is None or trace.debugger is None:
        print("Failed to start shader debug")
    else:
        # 收集所有执行状态
        all_states = []
        while True:
            new_states = controller.ContinueDebug(trace.debugger)
            if not new_states:
                break
            all_states.extend(new_states)

        print(f"Shader debug: {len(all_states)} execution steps")

        # 累积变量快照
        var_snapshot = {}  # name → ShaderVariable
        for i, state in enumerate(all_states):
            for change in state.changes:
                var_snapshot[change.after.name] = change.after

            # 打印每步的变化
            if state.changes:
                changes_str = ", ".join(
                    f"{c.after.name}={list(c.after.value.f32v[:c.after.columns])}"
                    for c in state.changes
                )
                print(f"  Step {state.stepIndex}: {changes_str}")

        # 最终变量状态
        print(f"\nFinal variable snapshot ({len(var_snapshot)} variables):")
        for name, var in sorted(var_snapshot.items()):
            if var.rows == 1 and var.columns <= 4:
                vals = list(var.value.f32v[:var.columns])
                print(f"  {name} = {vals}")

        controller.FreeTrace(trace)  # 必须释放！

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 8: 性能计数器采集

```python
"""采集 GPU 性能计数器（如 GPU Duration），输出每个 pass 的耗时。"""
import os, sys, json

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    # 枚举可用计数器
    counters = controller.EnumerateCounters()
    print(f"Available counters: {len(counters)}")

    # 找 GPU Duration 计数器
    gpu_duration = None
    for c in counters:
        desc = controller.DescribeCounter(c)
        if "duration" in desc.name.lower() or "gpu" in desc.name.lower():
            print(f"  {desc.name} (unit={desc.unit})")
            if gpu_duration is None:
                gpu_duration = c

    if gpu_duration is None:
        print("No GPU duration counter found (may not be supported on this GPU)")
    else:
        # 采集计时
        results = controller.FetchCounters([gpu_duration])

        # 结果只含叶子 action，按 eventId 索引
        timing = {}
        for r in results:
            timing[r.eventId] = r.value.d  # 单位：秒

        # 聚合到 root action 级别
        sf = controller.GetStructuredFile()
        root_actions = controller.GetRootActions()

        def sum_timing(action):
            if action.eventId in timing:
                return timing[action.eventId]
            total = 0.0
            for child in action.children:
                total += sum_timing(child)
            return total

        print(f"\nPass timing (top-level):")
        for action in root_actions:
            t = sum_timing(action)
            if t > 0:
                print(f"  EID {action.eventId}: {action.GetName(sf)} = {t*1000:.3f} ms")

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 9: 批量扫描异常像素（NaN/Inf/负值）

```python
"""扫描所有 float 格式的 Render Target，检测 NaN、Inf 和负值像素。"""
import os, sys, json, math

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"
SAMPLE_SIZE = 10  # 每个 RT 采样的像素行列数

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    # 找到所有 draw call，逐个扫描其 RT
    root_actions = controller.GetRootActions()
    sf = controller.GetStructuredFile()
    textures = {str(t.resourceId): t for t in controller.GetTextures()}
    anomalies = []

    def scan_actions(actions):
        for a in actions:
            if int(a.flags) & int(rd.ActionFlags.Drawcall):
                controller.SetFrameEvent(a.eventId, True)
                state = controller.GetPipelineState()
                outputs = state.GetOutputTargets()

                for o in outputs:
                    if int(o.resource) == 0:
                        continue
                    rid = str(o.resource)
                    tex = textures.get(rid)
                    if tex is None:
                        continue
                    fmt = tex.format.Name()
                    # 只检查 float 格式
                    if "FLOAT" not in fmt.upper() and "16_" not in fmt and "32_" not in fmt:
                        continue

                    # 采样检查
                    sub = rd.Subresource(0, 0, 0)
                    w, h = tex.width, tex.height
                    step_x = max(1, w // SAMPLE_SIZE)
                    step_y = max(1, h // SAMPLE_SIZE)

                    for sy in range(0, h, step_y):
                        for sx in range(0, w, step_x):
                            val = controller.PickPixel(tex.resourceId, sx, sy, sub, rd.CompType.Typeless)
                            for ch in range(4):
                                v = val.floatValue[ch]
                                if math.isnan(v) or math.isinf(v) or v < 0:
                                    anomalies.append({
                                        "eid": a.eventId,
                                        "name": a.GetName(sf),
                                        "rt": rid,
                                        "pixel": (sx, sy),
                                        "channel": ch,
                                        "value": str(v),
                                    })
                                    break
                            else:
                                continue
                            break  # 找到一个异常就跳到下一个 RT

            if len(a.children) > 0:
                scan_actions(a.children)

    scan_actions(root_actions)

    print(f"Anomaly scan complete: {len(anomalies)} anomalies found")
    for a in anomalies[:20]:
        print(f"  EID {a['eid']}: {a['name']} @ pixel {a['pixel']} ch={a['channel']} val={a['value']}")

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```

---

## Recipe 10: 导出 Mesh 顶点数据

```python
"""导出指定 draw call 的顶点数据（Post-VS）为可读格式。"""
import os, sys, json, struct

PYMODULE_DIR = r"<SKILL_DIR>/pymodule"
sys.path.insert(0, PYMODULE_DIR)
if sys.platform == "win32":
    os.add_dll_directory(PYMODULE_DIR)
import renderdoc as rd

RDC_FILE = r"<path-to-capture>.rdc"
EVENT_ID = 100  # 目标 draw call 的 event ID

rd.InitialiseReplay(rd.GlobalEnvironment(), [])
cap = rd.OpenCaptureFile()
result = cap.OpenFile(RDC_FILE, "", None)
assert result == rd.ResultCode.Succeeded
result, controller = cap.OpenCapture(rd.ReplayOptions(), None)
assert result == rd.ResultCode.Succeeded

try:
    controller.SetFrameEvent(EVENT_ID, True)

    # 获取 Post-VS 数据
    postvs = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)

    if postvs.vertexResourceId == rd.ResourceId.Null():
        print("No post-VS data available for this event")
    else:
        num_verts = postvs.numIndices
        stride = postvs.vertexByteStride
        print(f"Vertices: {num_verts}, stride: {stride} bytes")

        # 获取输出签名（属性布局）
        state = controller.GetPipelineState()
        vs_refl = state.GetShaderReflection(rd.ShaderStage.Vertex)
        if vs_refl:
            print("Output signature:")
            for sig in vs_refl.outputSignature:
                name = sig.semanticName or sig.varName or "?"
                print(f"  {name}{sig.semanticIndex}: {sig.compCount} components at reg {sig.regIndex}")

        # 读取顶点 buffer 数据
        data = controller.GetBufferData(
            postvs.vertexResourceId,
            postvs.vertexByteOffset,
            num_verts * stride
        )

        # 解析顶点（假设全 float）
        floats_per_vert = stride // 4
        print(f"\nFirst 10 vertices ({floats_per_vert} floats each):")
        for i in range(min(10, num_verts)):
            off = i * stride
            vfloats = list(struct.unpack_from(f"{floats_per_vert}f", data, off))
            print(f"  v[{i}]: {[round(f, 4) for f in vfloats]}")

        # 如果有索引 buffer，也读取
        if postvs.indexResourceId != rd.ResourceId.Null():
            idx_stride = postvs.indexByteStride  # 2 or 4
            idx_fmt = "H" if idx_stride == 2 else "I"
            idx_data = controller.GetBufferData(
                postvs.indexResourceId,
                postvs.indexByteOffset,
                num_verts * idx_stride
            )
            indices = list(struct.unpack_from(f"{num_verts}{idx_fmt}", idx_data))
            print(f"\nFirst 30 indices: {indices[:30]}")

finally:
    controller.Shutdown()
    cap.Shutdown()
    rd.ShutdownReplay()
```
