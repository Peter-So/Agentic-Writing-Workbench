# Agentic Writing Workbench

[English](README.md) | **中文**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Agentic Writing Workbench 是一个本地优先的智能创作工作台，面向长篇小说、电影短片脚本和灵感随想等长期创作项目。它不是简单的聊天窗口，而是把一次开放式创作请求转成可审计的工作流：理解意图、根据项目结构路由、精确组装材料、生成或收集候选稿、审查融合、等待用户确认，最后才写回项目文件。

项目基于 FastAPI、LangGraph、LangChain 兼容模型、可选网页 AI provider、项目 Wiki、可恢复任务状态和公共写作技能库构建。

![架构总图](docs/images/architecture-overview.png)

## 解决的问题

- 长期创作很容易在多轮对话、多个文件和多次修订之间丢失上下文。
- LLM 经常基于不完整或无关材料作答，导致内容偏题或污染项目文件。
- 网页 AI provider 有价值，但结果难以统一抓取、对比、融合和归档。
- 生成内容不应该静默覆盖大纲、正文、剧本、设定或项目规则。
- 参考小说、写作技法、项目记忆和审查标准需要稳定进入创作流程。

这个项目把创作当成一条可确认、可恢复、可复盘的生产流水线，而不是一次性问答。

## 界面预览

| 创作驾驶舱 | 项目 Wiki |
|---|---|
| ![创作驾驶舱](docs/images/ui-writing.jpg) | ![项目 Wiki](docs/images/ui-project-wiki.jpg) |

| Provider 材料收集 | 诊断检查 |
|---|---|
| ![Provider 聊天](docs/images/ui-provider-chat.jpg) | ![诊断检查](docs/images/ui-diagnostics.jpg) |

| 项目类型 | 正文创作 |
|---|---|
| ![项目类型](docs/images/ui-project-types.jpg) | ![正文创作](docs/images/ui-prose-creation.png) |

## 核心思想

- **项目结构优先**：每个项目都有 `维基/project-structure.json`。路由、归档和恢复逻辑先读结构，再决定文件路径。
- **先理解再执行**：用户提问先经过 LLM 意图分析，再进入合适的流程节点。
- **材料精确组装**：按章节、人物、情节、风格、记忆、参考资料和技法库选择材料，而不是整文件塞进 prompt。
- **人工确认门禁**：生成稿、provider 材料、文件改写、归档写回都需要用户确认。
- **任务可恢复**：未完成任务、状态栏耗时、provider 结果和确认状态支持刷新或重启后恢复。
- **本地数据归属**：密钥、浏览器会话、私有小说、生成产物、日志和记忆都保存在本地并被 Git 忽略。

## 总体框架

```text
Web UI
  -> FastAPI 接口 / SSE 流式事件
  -> LangGraph 创作工作流
  -> 项目 Wiki + SOP + pending intent 记忆
  -> 材料组装 + RAG/五维库/参考资料检索
  -> 本地 LLM 角色与可选网页 provider
  -> 审查、融合、定稿
  -> 用户确认
  -> 归档写回 + 记忆/Wiki 更新
```

当前支持三类项目：

| 类型 | 用途 |
|---|---|
| 小说 | 设定、人物、大纲、章节正文、连续性记忆、审查与归档 |
| 电影脚本 | 概念、节拍表、剧本、分镜提示词、角色视觉一致性、生图 |
| 随想 | 随想记录、灵感、草稿和参考材料 |

## 流程与工程看板

| 创作流程图 | 工程思想 |
|---|---|
| ![创作流程图](docs/images/creation-workflow.png) | ![工程思想](docs/images/engineering-principles.png) |

| 落地方案矩阵 |
|---|
| ![落地方案矩阵](docs/images/implementation-matrix.png) |

## 技术路线

- **后端**：FastAPI + SSE，负责流式输出、状态流转和任务恢复。
- **工作流**：LangGraph StateGraph，编排意图分析、路由、材料装配、provider fanout、审查、定稿和归档。
- **模型层**：通过 `.env.shared` 注册 OpenAI 兼容文本模型和生图模型。
- **Provider 层**：可选 Playwright 浏览器自动化，接入网页 AI provider，并在融合前等待用户确认。
- **记忆与恢复**：pending intent、invocation 日志、项目 Wiki、workflow status 快照共同支撑中断恢复。
- **知识层**：项目 Wiki、LLM Wiki、写作技法知识库、公共技能库、可选 Chroma/Embedding sidecar 和本地 TF-IDF 兜底。
- **前端**：静态 HTML/CSS/JS，包含项目卡片、文件树、只读 Wiki、模型选择、任务状态栏和确认按钮。

## 仓库结构

```text
app/                         FastAPI 应用、LangGraph 工作流、模型客户端、写作模块
app/static-writing/          默认 Web UI
docs/images/                 README 图示与截图
projects/writing/            Writing 工作区
projects/writing/novels/     三个已初始化的空项目
projects/writing/data/       公共写作技法知识库
projects/writing/novel-skill-suite/
projects/writing/short-film-skill-suite/
projects/writing/novel-acquisition/
scripts/validate-writing-project.py
```

干净发布版不包含私有密钥、provider 会话、浏览器资料、参考小说原文、Chroma 数据、任务日志、生成产物或项目私有内容。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.shared.example .env.shared
notepad .env.shared
.\.venv\Scripts\python.exe -m uvicorn app.writing_web:app --host 127.0.0.1 --port 7861
```

访问：

```text
http://127.0.0.1:7861/
```

更多说明见 [QUICK-START.md](QUICK-START.md)。

## 配置

`.env.shared.example` 提供模型注册格式：

- `LLM_KEYS`：文本模型列表。
- `LLM_ROLE_CHAT`：默认聊天模型。
- `LLM_ROLE_WRITING`：默认创作模型。
- `LLM_ROLE_REVIEW`：默认审查模型。
- `IMAGE_LLM_KEYS`：生图模型列表。
- `IMAGE_LLM_ROLE_IMAGE`：默认生图模型。

生图默认参数为 `16:9`、`1K`、`1536x1024`。请在本地填入自己的模型地址和密钥。

## 可选向量 Sidecar

ChromaDB 和 Embedding 服务是可选能力。保持以下字段为空即可禁用向量检索：

```dotenv
CHROMA_URL=
EMBEDDING_URL=
```

禁用后，工作台仍可运行：

- 用户确认后的产出会写入 `projects/writing/novel-acquisition/outputs-corpus/confirmed_outputs.json`。
- 参考资料检索可使用本地 TF-IDF / 五维精确检索。
- 诊断模块可能提示向量 sidecar 不可用，但创作流程应降级继续，而不是阻断。

如需启用远程向量服务，可先建立 SSH 隧道，再填写本地端点：

```powershell
ssh -L 8000:127.0.0.1:8000 -L 8001:127.0.0.1:8001 <user@your-sidecar-host>
# ChromaDB http://127.0.0.1:8000 | Embedding http://127.0.0.1:8001/embed
```

```dotenv
CHROMA_URL=http://127.0.0.1:8000
EMBEDDING_URL=http://127.0.0.1:8001/embed
CHROMA_TENANT=default_tenant
CHROMA_DATABASE=default_database
```

## 校验

```powershell
.\.venv\Scripts\python.exe scripts\validate-writing-project.py
node --check app\static-writing\app.js
```

## 许可证与商标

代码和文档文字使用 [MIT License](LICENSE) 发布。

项目名称、README 图示、截图和视觉呈现属于品牌资产，详见 [TRADEMARKS.md](TRADEMARKS.md)。

该仓库是一个干净框架导出版，仅保留可复用代码、模板、公共技能文件、公共写作技法知识库、项目图片和空项目骨架。

## 友情链接

<a href="https://linux.do/">
  <img src="docs/images/linux-do-logo.svg" alt="linux.do" width="25" height="25" align="absmiddle">
  linux.do
</a>
