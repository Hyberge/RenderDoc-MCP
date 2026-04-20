## 技能文档

### 基本信息
- 技能名: `renderdoc-pyd-scripting`
- 创建人: @brainfkli (brainfkli@tencent.com)
- 版本: v1.0.0
- 更新时间: 2026-04-20

### 适用场景
指导 LLM 直接编写使用 renderdoc.pyd 的 Python 脚本来分析 GPU 帧捕获（.rdc 文件），无需通过 MCP Server，大幅减少 token 消耗。适用于：
- 打开 RenderDoc 截帧查看基本信息
- 遍历/搜索 draw call
- 查看管线状态、Shader 绑定
- 导出纹理、Buffer 数据
- 像素历史追踪、Shader 调试
- 性能计数器采集
- 批量扫描异常像素

### 前置条件
- Python 版本必须与 `pymodule/VERSION.txt` 中记录的版本一致（当前为 Python 3.14）
- Windows x64 系统
- `pymodule/` 目录下的二进制文件需通过 `build_and_install.ps1` 编译生成

### 使用示例
```
帮我分析这个 RenderDoc 截帧文件 D:/captures/test.rdc，查看所有 draw call 的管线状态
```

### 注意事项
⚠️ renderdoc.pyd 与编译时的 Python 版本强绑定，版本不匹配会导致 import 失败
⚠️ pymodule/ 下的二进制文件不入 git，需要本地编译或从构建产物复制
⚠️ PipeState API 有诸多陷阱（GetViewport(i) 而非 GetViewports()），详见 SKILL.md

### 已知问题
- [ ] pymodule/ 二进制文件需手动从 build/x64 复制，未集成到构建脚本自动部署

### 相关技能
- `renderdoc-mcp-analyzer`: MCP 服务器开发 Skill，侧重工具开发
- `renderdoc-source-analyzer`: RenderDoc C++ 源码 Skill，侧重源码理解
