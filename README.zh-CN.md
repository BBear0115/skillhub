# SkillHub

面向个人空间与团队空间的开源 Skill 管理平台与 MCP 网关。

[English](README.md) | [简体中文](README.zh-CN.md)

SkillHub 现在采用三层职责分离模型：

- 超级管理员只负责审核 `SkillVersion`，决定某个版本是否允许进入 MCP
- 团队管理员只负责配置“团队空间里哪些已审核 Skill 对本团队开放”
- 所有团队成员都可以把 ZIP 上传并安装到团队空间，形成候选版本

超级管理员还可以对候选版本启动 review workbench。这个动作会在服务器侧准备审核/部署工作目录，同时保留 ZIP 快照下载入口和部署元信息。

Skill 真正进入 MCP 需要同时满足两个条件：

- 该 Skill 存在 `current_approved_version_id`
- 该团队空间对该 Skill 的 exposure 已启用

## 核心模型

- `Skill`：工作区内稳定的 Skill 身份
- `SkillVersion`：每次 ZIP 上传形成的版本快照，状态固定为 `uploaded`、`approved`、`rejected`、`archived`
- `WorkspaceSkillExposure`：团队空间维度的开放开关

上传 ZIP 只会生成候选版本，不会自动进入 MCP。

## 仓库结构

```text
skillhub/
|-- backend/
|   |-- app/
|   |-- alembic/
|   `-- pyproject.toml
|-- frontend/
|   |-- public/
|   |-- src/
|   `-- package.json
|-- examples/
|-- scripts/
|-- .env.example
|-- README.md
`-- README.zh-CN.md
```

## 快速开始

### 前置依赖

- Python 3.12+
- Node.js 和 npm
- 如果需要用辅助脚本启动，使用 PowerShell

### 后端安装

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### 前端安装

```powershell
cd frontend
npm install
```

### 同时启动前后端

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

默认地址：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`
- OpenAPI：`http://127.0.0.1:8000/docs`

本地持久化目录：

- SQLite 元数据：`backend/data/skillhub.db`
- ZIP 包与解压内容：`backend/storage/`

## 环境变量

参考 [.env.example](.env.example)：

```env
DATABASE_URL=sqlite:///./data/skillhub.db
SECRET_KEY=dev-secret-key-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
FRONTEND_URL=http://localhost:5173
STORAGE_ROOT=./storage
SUPER_ADMIN_ACCOUNT=
VITE_API_BASE_URL=/api
```

将 `SUPER_ADMIN_ACCOUNT` 设置为需要拥有版本审核权限的账号名。

## ZIP 导入规则

- ZIP manifest 必须包含 `name`
- ZIP manifest 必须包含 `version`
- 上传入口：`POST /workspaces/{workspace_id}/skills/upload`
- 如果是只包含 `SKILL.md + scripts/` 的隐式 skill 包，ZIP 内可能没有 manifest version；此时需要在上传表单里额外填写 `version`

导入后的结果只会是以下两种：

- 如果 Skill 不存在，创建一个新的 `Skill` 和一个 `uploaded` 版本
- 如果 Skill 已存在，在同名 Skill 下新增一个 `uploaded` 版本

## 审核工作台

- `POST /skill-versions/{version_id}/start-review`
- 在后端存储目录中准备 review workbench
- 把上传的 ZIP 快照和解压后的包内容复制到 workbench
- 向超级管理员返回部署所需的 handler 信息、manifest 和下载入口

## 主要接口

- `GET /workspaces/{workspace_id}/skills`
- `GET /skills/{skill_id}`
- `GET /skills/{skill_id}/versions`
- `GET /skill-versions/{version_id}`
- `GET /skill-versions/{version_id}/download`
- `POST /skill-versions/{version_id}/approve`
- `POST /skill-versions/{version_id}/reject`
- `POST /skills/{skill_id}/clear-approved-version`
- `GET /workspaces/{workspace_id}/approved-skills`
- `GET /workspaces/{workspace_id}/skill-exposure`
- `PUT /workspaces/{workspace_id}/skill-exposure`

## MCP 入口

单 Skill 入口：

```text
/mcp/{workspace_id}/{skill_id}
```

工作区聚合入口：

```text
/mcp/workspaces/{workspace_id}
```

这两个入口都只读取“当前审核通过版本”的快照。未审核、已拒绝、已归档或未开放的版本都不会对 MCP 可见。

workspace MCP 当前会暴露：

- `global_upload_audio_files`、`global_download_processed_artifacts` 等全局传输工具
- 通过 `skills_list` 做业务 skill 发现
- 通过 `skill_call` 做嵌套业务工具调用
- 每个已批准 skill 的 alias tool 入口

## 文件处理闭环

SkillHub 现在支持面向文件处理 skill 的服务端闭环 artifact 流程：

1. 先通过 `global_upload_audio_files` 上传一个或多个音频文件
2. agent 通过 workspace MCP 发现目标 skill
3. 调用业务 skill 时直接传上传后的 artifact ID
4. 当 skill 需要本地文件目录时，SkillHub 会自动把这些 artifact 物化成临时输入目录
5. 处理结果，例如 CSV 报表或筛选后的音频，会被重新注册为可下载 artifact
6. 再通过 `global_download_processed_artifacts` 下载处理结果
7. 最后通过 `global_delete_uploaded_artifacts` 清理输入和输出 artifact

对于 DNSMOS 这类 skill，agent 现在可以直接传 `input_artifact_ids`，不需要再手工把上传文件改写成本地 `input_dir`。SkillHub 会负责把上传 artifact 桥接到临时执行目录，并把输出重新发布到 artifact 存储中。

## Agent 说明

- 超管审核/部署流程仍然需要填写明确的 `mcp_endpoint_url`，但 agent 可以通过 skill 详情接口返回的 `mcp_endpoint` 自动推导：`server_base_url + mcp_endpoint`
- 如果 skill 包没有内置版本号，agent 需要在上传时自动生成并提交一个稳定的表单 `version`
- 对于需要同时发现多个业务 skill 的 agent，推荐优先走 workspace MCP；对于只调用单个 skill 的客户端，deploy 后仍然可以直接使用 skill MCP URL
- agent 访问统一使用 `Authorization: Bearer <access_token>`，不再支持基于 API key 的 MCP 调用

## 示例 Skill 包

- `examples/echo-skill`
- `examples/server-transfer-skill`

其中 `server-transfer-skill` 包含三个工具：

- `stream_audio_to_server`
- `stream_text_to_server`
- `delete_server_streams`
