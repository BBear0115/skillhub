# Skill 格式要求与自动化改造规范

本文档面向两类对象：

- `SkillHub` 使用者：需要知道什么样的 skill ZIP 能被系统导入、解析和调用
- `Codex`：当输入一个不合规 skill 时，需要依据本文档自动将其改造为可被 `SkillHub` 接收和执行的合规包

本文档描述的是当前仓库实现实际支持的结构，不是抽象理想规范。实现依据见：

- [backend/app/services/skill_packages.py](/Users/zhangxiaojiang/skillhub/backend/app/services/skill_packages.py:15)
- [backend/app/services/skill_runner.py](/Users/zhangxiaojiang/skillhub/backend/app/services/skill_runner.py:42)

## 1. 目标

一个可交付给 `SkillHub` 的合规 skill，至少要满足以下目标之一：

1. 作为单 skill ZIP，被直接导入并调用
2. 作为 skill 仓库 ZIP，被导入后枚举出多个工具
3. 对于脚本型 skill，脚本能在后端服务器上被正确定位并启动执行
4. 对于 docs-only skill，即使没有脚本，也能被正确解析并通过 MCP 返回文档内容

## 2. 当前支持的合规形态

### 2.1 单 skill 包

ZIP 根目录需包含：

- `skill.json` 或 `skillhub.json`

推荐同时包含：

- `SKILL.md`
- `scripts/` 或实际代码文件

最小示例：

```text
my-skill.zip
├── skill.json
└── main.py
```

最小 `skill.json` 示例：

```json
{
  "name": "echo-skill",
  "description": "Echo input",
  "tools": [
    {
      "name": "echo",
      "description": "Return input"
    }
  ],
  "handler": {
    "type": "python_package",
    "entrypoint": "main.py:handle_tool"
  }
}
```

### 2.2 `skills-index.json` 仓库包

仓库内支持：

- `.codex/skills-index.json`
- `skills-index.json`

最小示例：

```text
repo.zip
├── .codex/
│   └── skills-index.json
└── skills/
    └── demo/
        ├── SKILL.md
        └── scripts/
            └── main.py
```

导入后会被识别为 `skill_repo_exec`。

### 2.3 `marketplace.json` 仓库包

当前支持这些位置：

- `marketplace.json`
- `.claude-plugin/marketplace.json`
- `.codex-plugin/marketplace.json`
- `.cursor-plugin/marketplace.json`

最小示例：

```text
repo.zip
├── marketplace.json
└── plugins/
    └── repo-docs/
        ├── SKILL.md
        └── scripts/
            └── main.py
```

导入后会被识别为 `marketplace_repo`。

### 2.4 docs-first 仓库包

如果没有显式 manifest，但仓库内能递归发现有效 `SKILL.md` skill 目录，也可导入。

最小示例：

```text
repo.zip
└── skills/
    └── skill-creator/
        ├── SKILL.md
        └── scripts/
            └── generate_openai_yaml.py
```

导入后会被识别为 `docs_first_repo`。

## 3. `SKILL.md` 合规要求

`SKILL.md` 不是所有导入路径都强制要求，但强烈建议每个 skill 都提供。

推荐格式：

```md
---
name: skill-creator
description: Generate OpenAI agent yaml from a prompt
---

# skill-creator

Short description.

## Usage

Explain how the skill should be called.
```

建议至少具备：

- `name`
- `description`
- skill 做什么
- 如何调用
- 如有脚本，说明脚本前置条件和输入要求

## 4. `scripts/` 合规要求

如果 skill 需要在后端真实执行，推荐目录结构：

```text
my-skill/
├── SKILL.md
└── scripts/
    ├── main.py
    └── helper.sh
```

当前后端执行器稳定支持：

- Python 脚本
- Shell 脚本

当前不应假设直接支持：

- Node.js / TypeScript
- Deno
- Ruby
- Go
- 任意自定义二进制
- 自动依赖安装

## 5. 脚本参数约定

当前系统会用启发式方式推断脚本能力，因此 skill 作者和 `Codex` 都应尽量向这些参数约定收敛：

- 路径输入：`path` `project` `dir` `directory` `root`
- JSON 输入文件：`input-file` `input` `file` `data-file` `data` `json-file`
- 示例数据：`--sample`
- JSON 输出：`--json` 或 `--format json`

推荐写法：

```python
parser.add_argument("--path")
parser.add_argument("--json", action="store_true")
```

或：

```python
parser.add_argument("input_file")
```

不推荐：

- 无文档的自定义参数命名
- 必填参数没有在 `SKILL.md` 说明
- 脚本只能在特定 IDE / 容器上下文运行，但文档未声明

## 6. 运行前置条件要求

合规 skill 不仅要结构合法，还要显式声明运行前提。至少应在 `SKILL.md` 中说明：

- 需要的 Python 包
- 需要的系统 CLI
- 需要的环境变量或 API Token
- 需要的输入文件或目录
- 需要联网、Git 仓库、GPU、Docker 等额外条件时，也必须写明

推荐写法：

```md
## Requirements

- Python package: `huggingface_hub`
- CLI: `gh`
- Env: `OPENAI_API_KEY`
- Input: `--dataset <path>`
```

## 7. 不合规 skill 的典型表现

以下输入都应视为不合规或半合规，需要改造后再交给 `SkillHub`：

### 7.1 只有源码，没有 manifest，也没有 `SKILL.md`

示例：

```text
broken-skill/
└── tool.py
```

问题：

- 无法识别 skill 名称
- 无法枚举工具
- 无法生成 handler

### 7.2 有 `SKILL.md`，但没有明确 skill 边界

示例：

```text
repo/
├── README.md
├── docs/
│   └── SKILL.md
└── random.py
```

问题：

- `SKILL.md` 不在明确 skill 根目录
- `scripts/`、文档、资源文件混杂

### 7.3 有脚本，但没有统一入口

示例：

```text
skill/
├── SKILL.md
├── a.py
├── b.py
└── legacy.sh
```

问题：

- 无法确定默认脚本
- 参数契约不统一

### 7.4 依赖和环境要求未声明

示例：

- 脚本 import 第三方包，但未说明安装方式
- 脚本依赖 `gh`、`uv`、`docker`，但文档未写
- 脚本依赖 `OPENAI_API_KEY`，但文档未写

### 7.5 ZIP 根目录错误

示例：

```text
archive.zip
└── top-folder/
    └── nested-folder/
        └── skill.json
```

问题：

- manifest 被多层目录包裹
- 实际引用路径容易错位

## 8. Codex 自动化改造要求

当把一个不合规 skill 或 skill 仓库连同本文档一起交给 `Codex` 时，`Codex` 应按以下顺序自动改造。

### 8.1 第一步：识别输入类型

输入可能是：

- 单个 skill 文件夹
- 单 skill ZIP
- 多 skill 仓库
- 只有文档的 docs-first 仓库
- 混合目录，尚未组织成 skill

`Codex` 必须先判断它更接近哪一类，而不是直接硬套某一种格式。

### 8.2 第二步：建立 skill 边界

`Codex` 必须为每个 skill 建立清晰根目录，推荐结构：

```text
skill-name/
├── SKILL.md
├── scripts/
├── references/
└── assets/
```

最少要求：

- `SKILL.md`
- 可执行脚本放入 `scripts/`

### 8.3 第三步：补齐缺失元数据

如果不存在 `skill.json` 或 `skillhub.json`，`Codex` 应根据输入形态选择：

- 单 skill：生成 `skill.json`
- 多 skill 仓库：生成 `skills-index.json` 或 `marketplace.json`
- docs-first 仓库：至少保证每个 skill 目录具备 `SKILL.md`

`Codex` 生成的元数据必须反映真实文件位置，不能写出与目录不一致的 `source` 或 `entrypoint`。

### 8.4 第四步：统一脚本入口

如果存在多个脚本，`Codex` 应：

- 将真正用于 MCP 调用的脚本收敛到 `scripts/`
- 明确一个默认脚本
- 尽量统一为 Python 或 shell 入口
- 删除或隔离历史遗留脚本，避免枚举歧义

如果脚本过于复杂，至少要在 `SKILL.md` 中声明：

- 默认脚本名
- 必填参数
- 运行前置条件

### 8.5 第五步：显式化运行前提

`Codex` 必须把隐含前提补写进 `SKILL.md`，至少包括：

- Python 依赖
- 系统依赖
- 环境变量
- 输入文件要求
- 运行上下文要求

如果无法自动满足这些前提，也必须在文档中明确标注，不允许保持隐式状态。

### 8.6 第六步：输出可导入的最终产物

最终输出应满足以下之一：

1. 单 skill ZIP，根目录即 skill 根目录
2. skill 仓库 ZIP，根目录包含 `skills-index.json` 或 `marketplace.json`
3. docs-first 仓库 ZIP，skill 边界清晰，`SKILL.md` 可被递归发现

同时输出一份简短改造报告，至少包含：

- 输入类型判断
- 改了哪些目录和文件
- 新增了哪些 manifest
- 哪些脚本被指定为默认入口
- 哪些问题仍然无法自动修复

## 9. Codex 不得做的事情

把不合规 skill 自动化改造时，`Codex` 不应：

- 编造不存在的业务能力
- 随意删除用户源码而不保留可追溯结构
- 伪造可运行状态
- 在缺失依赖、token、CLI 时仍声称“脚本可完整跑通”
- 把 docs-only skill 伪装成 script-backed skill

## 10. 推荐交付模板

如果目标是生成一个最稳妥、最容易被 `SkillHub` 接收的 skill，推荐直接输出为：

```text
my-skill/
├── skill.json
├── SKILL.md
└── scripts/
    └── main.py
```

或仓库型：

```text
repo/
├── marketplace.json
└── plugins/
    └── my-skill/
        ├── SKILL.md
        └── scripts/
            └── main.py
```

## 11. 给 Codex 的执行指令

如果你把一个不合规 skill 和本文档一起交给 `Codex`，建议直接使用下面这段要求：

```md
请按 docs/skill-format-requirements.md 将这个 skill 或 skill 仓库改造成 SkillHub 可导入格式。

要求：
- 先判断它属于单 skill、skill 仓库还是 docs-first 仓库
- 为每个 skill 建立清晰根目录
- 补齐缺失的 SKILL.md、manifest、scripts 目录
- 尽量统一默认脚本入口
- 在 SKILL.md 中补写依赖、CLI、环境变量、输入参数要求
- 输出最终可导入 ZIP 所需的目录结构
- 额外给出一份“仍无法自动修复的问题”清单
```

这段要求的目标不是让 `Codex` 美化文档，而是让它产出一个可被 `SkillHub` 实际接收、解析、调用的最终结构。
