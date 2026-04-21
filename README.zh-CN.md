# SkillHub

面向个人与团队的开源 Skill 管理平台与 MCP 网关。

[English](README.md) | [简体中文](README.zh-CN.md)

SkillHub 用来统一管理个人 Skill、团队 Skill 和可被 agent 调用的 MCP 接口。这个仓库现在采用标准的本地前后端开发形态：

- 后端：FastAPI + SQLModel + Uvicorn
- 前端：React + Vite
- 本地持久化：默认使用 SQLite

## 核心能力

- 个人空间与团队空间
- ZIP Skill 包上传
- 在控制台直接创建 Inline Skill
- 团队审核流与 Skill 开放控制
- 工作区级 API Key
- 单 Skill 和工作区级 MCP 接口

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
- 如果要使用启动脚本，需要 PowerShell

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

### 一键启动前后端

在仓库根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-skillhub.ps1
```

默认地址：

- 前端：`http://127.0.0.1:5173`
- 后端：`http://127.0.0.1:8000`
- OpenAPI：`http://127.0.0.1:8000/docs`

本地持久化数据位置：

- 账户和业务数据：`backend/data/skillhub.db`
- 上传的 Skill 包与解压内容：`backend/storage/`

### 手动启动

后端：

```powershell
cd backend
$env:DATABASE_URL="sqlite:///./data/skillhub.db"
$env:FRONTEND_URL="http://localhost:5173"
$env:STORAGE_ROOT="./storage"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Vite 开发服务器会把 `/api` 请求代理到 `http://localhost:8000`。

## 环境变量

参考 [.env.example](.env.example)：

```env
DATABASE_URL=sqlite:///./data/skillhub.db
SECRET_KEY=dev-secret-key-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=1440
ALGORITHM=HS256
FRONTEND_URL=http://localhost:5173
STORAGE_ROOT=./storage
VITE_API_BASE_URL=/api
```

## MCP 用法

### 单 Skill 入口

```text
/mcp/{workspace_id}/{skill_id}
```

### 工作区聚合入口

```text
/mcp/workspaces/{workspace_id}
```
