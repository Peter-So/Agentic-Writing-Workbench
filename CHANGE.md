# 更新日志

## Agentic-Writing-Workbench-v0.1.3

本版本在 v0.1.2 的 CLI 升级能力基础上，补齐 Web UI 检查新版、用户确认升级、后台备份更新、失败回滚与服务重启闭环。

### 新增

- Web UI 顶部新增“更新”入口，可检查 GitHub 最新 Release、展示新版内容，并由用户确认升级。
- 新增 `/api/app-upgrade/check`、`/api/app-upgrade/status`、`/api/app-upgrade/apply`，支持 Web 端检查、执行升级和轮询状态。
- 新增 `app/app_upgrade.py`，统一管理版本检查、后台升级、状态持久化、失败回滚和重启协调。
- 新增 `scripts/restart-workbench.py`，升级或回滚后可等待旧服务退出并重新启动 Web 服务。
- 升级前新增后端任务门禁，检查 provider job、未完成 invocation、pending intent；存在运行中或待确认/归档任务时阻止升级。

### 优化

- `scripts/upgrade-to-latest.py` 在复制框架文件失败时会基于本次备份清单自动回滚。
- Web UI 升级流程接入顶部状态流，展示检查版本、下载新版、创建备份、更新框架、失败回滚、重启服务。

## Agentic-Writing-Workbench-v0.1.2

本版本新增一键升级、自动备份和回滚能力，方便用户在保留本地项目资产的前提下更新到 GitHub 最新发布版本。

### 新增

- 新增 `scripts/upgrade-to-latest.py`，支持一键从 GitHub Release 升级框架代码。
- 新增升级前自动备份与 `--rollback` 回滚能力。
- 新增 `upgrade-manifest.json`，明确框架更新清单与用户数据保护清单。
- 新增 `scripts/test-upgrade-workflow.py`，用临时项目验证 dry-run、升级、备份、回滚和用户数据保护。

### 保护策略

- 升级默认不覆盖 `.env.shared`、`.env.local`、`projects/`、`data/`、`logs/`、`tmp/`、`backups/`。
- 用户项目、知识、技能、配置和创作资产不参与框架覆盖。

## Agentic-Writing-Workbench-v0.1.1

本版本聚焦 Web UI 的项目流程图可视化能力，让不同项目类型展示符合自身创作流程的节点图，而不是共用同一张通用图。

### 新增

- 左侧项目卡片新增绿色“流程”按钮，可直接打开当前项目类型的流程图。
- 新增 LangGraph 流程可视化页面，展示节点、边、条件分支、审查回环和项目类型说明。
- 后端新增 `/api/writing/graph-view` 接口，根据项目类型返回对应流程图配置。

### 优化

- 小说项目展示完整创作链路，包括意图理解、材料装配、生成、规则预审、模型审查、定稿确认。
- 电影脚本项目补充分镜与生图相关链路，包括分镜提示词、生图参数、生成画面、影像归档。
- 随想项目展示更轻量的灵感创作链路，包括灵感材料、随想成稿、确认沉淀。
- 流程图默认放大显示，顶部对齐、左右居中，打开后更容易阅读。
- 流程图画布支持鼠标左键拖拽，并限制在流程图边界内，避免拖出视野。
- 右上角新增悬浮 `+` / `-` 缩放按钮，按钮样式更轻量。

### 修复

- 修复小说、电影脚本、随想三类项目打开流程图时显示同一张图的问题。
- 修复流程图画布滚动条影响查看体验的问题，改为拖拽移动。
