# SkillHub

面向个人 Skill、公开 Skill Market 和超管部署流程的开源 Skill 管理控制台与 MCP 网关。

[English](README.md) | [简体中文](README.zh-CN.md)

## 概览

SkillHub 支持用户上传 Skill ZIP 包，由超管审核、部署并开放为具体 MCP 端点，Agent 可以按提示词直接调用这些端点完成真实任务。

当前产品模型保持简化：

- 普通用户管理自己的 Skill。
- 普通用户可以从 Skill Market 将公开 Skill 同步到自己的个人工作区。
- 超管负责审核包、部署运行时、开放 MCP、配置提示词和维护市场 Skill。
- 后端仍保留团队/工作区兼容能力，但主界面聚焦个人 Skill 和公开市场。

## 核心功能

- 登录、注册和 Bearer Token 认证。
- 个人 Skill 上传与版本管理。
- 公开 Skill Market，支持查看详情、工具、ZIP 下载、提示词复制和同步到个人工作区。
- 超管部署工作台，支持准备审核材料、部署、开放 MCP、拒绝和提示词配置。
- 具体 Skill MCP 端点：`/mcp/{workspace_id}/{skill_id}`。
- 自动生成 Agent 提示词，包含 MCP 连接方式、认证方式、Skill 使用说明和全局 Artifact 传输工具。
- 全局 MCP 工具支持音频/文本上传、处理结果下载和临时文件清理。
- 可选清理脚本用于删除临时音频和压缩包 Artifact。

## 目录结构

```text
skillhub/
|-- backend/
|   |-- app/
|   |-- alembic/
|   |-- cleanup_audio_artifacts.py
|   `-- pyproject.toml
|-- examples/
|   |-- echo-skill/
|   `-- server-transfer-skill/
|-- frontend/
|   |-- src/
|   `-- package.json
|-- scripts/
|-- .env.example
|-- README.md
`-- README.zh-CN.md
```

运行期文件不会提交到 Git，包括 `.env`、SQLite 数据库、日志、存储目录、ZIP 包、前端构建产物以及本地/服务器同步临时目录。

## 快速开始

### 环境要求

- Python 3.12+
- Node.js 和 npm
- Windows 下使用启动脚本时需要 PowerShell

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### 前端

```powershell
cd frontend
npm install
npm run build
```

### 配置

本地开发时将 `.env.example` 复制为 `backend/.env`，并替换所有占位符：

```env
DATABASE_URL=sqlite:///./data/skillhub.db
SECRET_KEY=<generate-a-long-random-secret>
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
FRONTEND_URL=http://localhost:5173
STORAGE_ROOT=./storage
SUPER_ADMIN_ACCOUNT=<admin-account>
SUPER_ADMIN_PASSWORD=<admin-password>
VITE_API_BASE_URL=/api
```

不要提交真实 `.env`、数据库、日志、上传包、存储目录或部署生成物。

### 启动服务

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

默认本地地址：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`
- OpenAPI：`http://127.0.0.1:8000/docs`

## Skill 包规则

- 上传接口：`POST /workspaces/{workspace_id}/skills/upload`
- ZIP 包应包含 `skill.json` 或 `skillhub.json`。
- 如果包内 manifest 已包含版本号，上传表单中的 `version` 字段可以留空。
- 仅文档型 Skill 也可以通过包含 `SKILL.md` 的包导入。
- 仓库包可以通过 `skills-index.json` 或市场元数据暴露多个可执行 Skill。
- 上传只会生成待审核版本。只有超管部署并批准后，Skill 才能通过 MCP 调用。

## Skill Market 流程

用户可以在 Skill Market 浏览公开 Skill。点击添加时，SkillHub 会通过以下接口将选中的公开版本同步到该用户的个人工作区：

```text
POST /skill-versions/{version_id}/sync-to-workspace
```

同步后的 Skill 会作为普通工作区 Skill 保存，因此可以跨浏览器和设备使用，不再只是本地收藏。

## 超管流程

1. 用户上传 Skill ZIP。
2. 超管调用 `POST /skill-versions/{version_id}/start-review` 准备审核材料。
3. 超管部署该版本。
4. 运行时部署会将审核包复制到后端存储，并为版本创建独立虚拟环境。
5. 系统根据 `requirements.txt`、`pyproject.toml` 或运行时元数据安装依赖。
6. 超管批准版本并开放具体 MCP 端点。
7. 超管可以编辑 Skill 专属提示词和提示词拼接逻辑。

## MCP 调用方式

只使用具体 Skill MCP 端点：

```text
/mcp/{workspace_id}/{skill_id}
```

旧的工作区级 MCP 端点会返回明确错误，不应再用于工具调用。

运行时可见需要满足：

- 版本已批准
- 运行时已部署
- MCP 端点已发布
- 请求携带有效 `Authorization: Bearer <access_token>`
- 初始化后携带与该具体 Skill 端点匹配的 `Mcp-Session-Id`

生成的提示词会包含完整调用顺序：

1. 带 `Authorization` 请求头向具体 MCP 端点发送 `initialize`。
2. 读取响应头 `Mcp-Session-Id`，后续 MCP 请求都携带该会话 ID。
3. 调用 `tools/list` 获取业务工具和全局工具。
4. 如果存在资源，调用 `resources/list` 和 `resources/read`。
5. 调用 `tools/call`，在 `params.name` 中写工具名，在 `params.arguments` 中传 JSON 参数。
6. 对文件或音频任务，按“上传一个 Artifact、调用一次业务工具、下载输出、删除输入和输出 Artifact”的顺序循环处理。

## 全局 Artifact 工具

每个具体 Skill MCP 端点都会注入以下辅助工具：

- `global_upload_audio_files`
- `global_upload_text_files`
- `global_download_processed_artifacts`
- `global_download_processed_artifacts_and_cleanup`
- `global_delete_uploaded_artifacts`

这些工具按流式方式使用：

- 每次调用只上传一个文件。
- 每次业务工具调用只处理一个输入 Artifact。
- 每次调用只下载一个处理后 Artifact。
- 每次调用只删除一个 Artifact。

批量 Artifact ID 会在上传、处理、普通下载和删除路径上被拒绝，避免 Agent 创建不可观察的长时间批处理任务。

## Artifact HTTP API

- `POST /artifacts/audio`：使用 multipart 字段 `file` 上传一个本地音频文件
- `GET /artifacts/{artifact_id}`：读取 Artifact manifest
- `GET /artifacts/{artifact_id}/download`：下载一个 Artifact
- `DELETE /artifacts/{artifact_id}?mode=soft|hard`：删除一个 Artifact

Artifact 存储在 `STORAGE_ROOT` 下。Artifact ID 会被校验，路径穿越会被阻断。

## 清理

可使用脚本清理临时音频和压缩包 Artifact：

```bash
python backend/cleanup_audio_artifacts.py --older-than-hours 24 --mode hard
```

清理脚本只扫描 SkillHub Artifact 存储，不会删除 Skill 源包、已部署运行时、数据库、日志或服务器上的其他文件。

## 测试

后端测试：

```bash
backend/.venv/bin/python -m pytest -q
```

前端构建：

```powershell
cd frontend
npm run build
```

## 安全说明

- 上传的 Skill 包在审核前都应视为不可信。
- 上传压缩包和运行时路径会被校验，阻止路径穿越到 SkillHub 存储目录之外。
- 每个真实部署都应使用强随机 `SECRET_KEY`。
- `SUPER_ADMIN_ACCOUNT` 和 `SUPER_ADMIN_PASSWORD` 必须保存在 Git 之外。
- 不要提交 `.env`、`backend/data`、`backend/storage`、日志、上传 ZIP、前端 `dist` 或远程同步临时目录。
- 如果任何凭据曾写入本地临时文件，在发布仓库前应轮换。

## 示例 Skill 包

- `examples/echo-skill`
- `examples/server-transfer-skill`
