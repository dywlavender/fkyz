# AGENTS.md — 风控 AI 评审平台开发约定

## 1. 适用范围与项目边界

本文件适用于 `risk_review_web/` 目录及其运行产物。该目录是风控 AI 评审平台，不属于知识库内容生产流程。

处理平台代码、提示词、规则、接口文档、验证集或运行产物时，不得套用知识库的 INGEST / QUERY / LINT / REFLECT / MERGE / ADD-QUESTION 流程。

## 2. 主要文件与职责

- 后端入口：`risk_review_web/server_v2.py`
- HTTP 路由分组：`risk_review_web/routes/`
- 无状态解析、持久化与领域服务：`risk_review_web/services/`
- 全局任务池：`risk_review_web/task_pool.py`
- 定时任务调度与持久化：`risk_review_web/scheduled_tasks.py`
- 标准与报告校验：`risk_review_web/artifacts.py`
- 统一任务错误分类：`risk_review_web/error_classification.py`
- Prompt 装配：`risk_review_web/prompt_builder.py`
- 路径定义：`risk_review_web/paths.py`
- Phase 1/Phase 2 Prompt：`risk_review_web/prompts/`
- 阶段 Skill：`risk_review_web/prompts/risk-review-skill/phase/`
- Phase 1 因子加工开发规范：`risk_review_web/prompts/risk-review-skill/spec/phase1_processing_spec.json`
- Phase 2 因子加工评审规范：`risk_review_web/prompts/risk-review-skill/spec/phase2_review_spec.json`
- React 前端源码：`risk_review_web/web/src/`
- 单因子评审功能模块：`risk_review_web/web/src/features/single-review/`
- 验证集功能模块：`risk_review_web/web/src/features/validation-set/`
- React 前端构建配置：`risk_review_web/web/vite.config.ts`
- React 前端构建产物：`risk_review_web/static/dist/`
- 原有功能台页面：`risk_review_web/static/index.html`
- 原有功能台逻辑：`risk_review_web/static/app.js`
- 原有功能台样式：`risk_review_web/static/style.css`
- 公共业务规则：`risk_review_web/data/business_rules.json`
- 验证集案例：`risk_review_web/data/examples.json`
- 本地运行状态：`risk_review_web/.local/state/`
- 定时任务配置：`risk_review_web/.local/state/scheduled_tasks.json`
- 定时任务运行记录：`risk_review_web/.local/state/scheduled_runs.json`
- 统一并发设置：`risk_review_web/.local/state/runtime_settings.json`
- 验证运行与评审记录：`risk_review_web/.local/state/validation_runs.json`、`review_records.json`

不要在 `server_v2.py` 中复制一套与 `artifacts.py`、`prompt_builder.py` 或 `task_pool.py` 重复的规则。

## 3. 输出与数据目录

平台生成的标准加工规范、v5 元数据和评审报告统一保存到项目根目录：

```text
outputs/
```

禁止把平台运行产物写入 `wiki/outputs/`。`wiki/outputs/` 或 `validation/outputs/` 只可作为历史验证集输入的候选来源，不是平台结果目录。

文件保存必须由 Python 后端完成。Agent 最终响应只能包含正文，不得自行创建、修改、移动或删除文件。

## 4. 当前业务契约

- 标准 schema 为 `risk_review_standard_v5`，元数据 schema 为 `risk_review_standard_metadata_v5`。
- Phase 1 规则的唯一来源是 `risk_review_web/prompts/risk-review-skill/spec/phase1_processing_spec.json`。
- Phase 2 规则的唯一来源是 `risk_review_web/prompts/risk-review-skill/spec/phase2_review_spec.json`。
- 两个阶段不得读取或复制对方规则；阶段 Skill 只维护角色、处理流程和输出格式，不复制规则正文。
- 业务规则不得在 Prompt、Skill、`AGENTS.md` 或校验代码中作为独立事实来源手工重复维护；由构建脚本从规则 JSON 生成的派生产物除外。
- 因子名只用于页面展示和文件命名，不得进入模型业务输入、评审判断或业务缓存键。
- Phase 2 直接使用冻结的标准 Markdown 作为业务判定基准，不读取或重建结构化业务摘要。
- 不保留 v2/v3/v4 兼容分支、旧摘要解析或旧 Prompt 契约；旧标准必须重新执行 Phase 1。
- 契约变更必须同步修改生成、校验、前端展示、Mock 和回归链路，不得通过新增兼容限制保留旧逻辑。

## 5. 阶段数据边界

### Phase 1

输入包括需求内容、指定接口文档、公共业务规则、Phase 1 Skill 和 Phase 1 规则 JSON。不得读取待评审加工逻辑、Phase 2 Skill 或 Phase 2 规则。

### Phase 2

输入包括冻结的标准加工规范、原始需求、待评审加工逻辑、Phase 2 Skill 和 Phase 2 规则 JSON。原始需求只作为证据来源；不得读取接口文档、公共业务规则、Phase 1 Skill 或 Phase 1 规则。

当前只执行最小技术防护：输出非空且必要 Markdown 章节可识别。严格完整性校验尚未启用，前后端不得保留无效校验开关。不得使用固定业务措辞或时间窗口表格项正则作为阶段阻断条件。

## 6. Agent 调用约束

- LangChain Agent 在 Web 服务进程的 `GLOBAL_TASK_POOL` Worker 线程中直接调用模型，不得另启 Worker 子进程，不得通过 stdin、stdout 或 stderr 传递业务输入、模型结果或事件。
- Agent 最终响应必须是完整 Markdown 正文；不得只输出摘要、路径、保存提示、计划或检查过程。
- Agent 不得自行保存标准或报告；后端负责规范化、校验和落盘。
- Phase 1、Phase 2 只能读取当前阶段明确允许的 Skill 和输入。
- 不得把因子名、内部任务 ID、客户端 ID 或线程池信息拼入模型业务输入。
- Phase 1 必须在按任务创建的临时隔离工作目录中运行；目录内只复制 Phase 1 Skill、Phase 1 因子加工开发规范、当次指定接口文档和公共业务规则。
- Phase 1 Agent 只允许使用后端提供的隔离目录只读 `read_file`、`glob_files`、`grep_files` 工具；不得提供 Bash、写文件工具或外部 MCP；任务结束后立即清理隔离目录。
- Phase 2 必须在按任务创建的临时隔离工作目录中运行；目录内只复制 Phase 2 Skill 和 Phase 2 因子加工评审规范，冻结标准、原始需求和待评审逻辑通过 Prompt 提供。
- Phase 2 Agent 使用与 Phase 1 相同的只读工具和隔离限制，任务结束后立即清理隔离目录。
- 不得把允许读取的文件全文一次性拼入 Prompt 代替文件隔离。

## 7. 全局任务池与并发

所有会触发因子加工或 Agent 调用的入口必须受 `GLOBAL_TASK_POOL` 控制，包括：

- 单因子 Phase 1
- 单因子 Phase 2
- 批量评审的每条因子流水线
- 验证集/回归案例执行
- 流水线内部的阶段调用与重试
- 定时任务的每条因子流水线

约束如下：

- 全局、批量评审和定时任务并发数统一由“系统管理 → 运行设置”管理，并持久化到 `runtime_settings.json`。
- `RISK_REVIEW_GLOBAL_CONCURRENCY` 只是首次缺省全局并发数，默认 3；持久化设置存在时以设置为准。
- 批量评审和定时任务的并发上限不得超过全局上限；业务页面不得另行提供并发输入。
- 不得在接口处理器、批量任务或验证任务中新增 `ThreadPoolExecutor`、裸 `threading.Thread` 或其他私有执行池。
- 固定 Worker 只能在 `task_pool.py` 中创建。
- `scheduled_tasks.py` 只允许保留一个不执行业务加工的计时扫描线程；到期因子必须提交到 `GLOBAL_TASK_POOL`，不得在调度线程内调用 Agent。
- 批量任务必须按父任务公平轮询，不能一次占满全局队列导致其他登录用户长期饥饿。
- 已排队任务取消时从全局队列移除；运行中任务设置取消标记，在当前模型网络请求返回后停止后续轮次和阶段。Python 线程不得通过非安全手段强杀。
- 不得从全局 Worker 内同步提交并等待同一个全局池的子任务，避免线程池死锁。批量/案例流水线应在已占用的 Worker 内顺序执行 Phase 1 和 Phase 2。

当前全局池是单 Python 进程内全局。若部署多个后端进程或多台服务器，必须改用 Redis 等集中式队列和分布式锁，不能宣称进程内线程池实现了跨节点全局并发。

## 8. 任务状态与共享访问

统一使用：`queued`、`running`、`cancelling`、`cancelled`、`done`、`error`。

任务应记录 `taskType`、`clientId`、父任务、创建/更新时间、本任务并发上限、全局排队和运行信息。多地点访问同一后端时，共享同一个任务池和 `/api/tasks` 任务视图。

更新 `TASKS`、`RUNNING_PROCS`、案例或业务规则时必须使用现有锁。避免形成 `TASK_LOCK` 与任务池内部锁的反向加锁顺序。

## 9. 修改原则

- 优先修改单一事实来源，避免 Prompt、Skill、Mock、校验器各自维护互相漂移的规则。
- 删除功能时执行完整链路删除，不遗留隐藏字段、旧默认值、兼容解析或无效提示词。
- 保留用户已有的无关改动，不覆盖或重置脏工作区。
- 不主动删除历史运行产物；若明确要求“不保留旧版”，应先识别旧 schema 标准及其关联报告，再成组清理。
- 运行测试生成的临时标准和报告必须在交付前从 `outputs/` 清理。
- 当前前端采用渐进式迁移：新 React 页面缺少的原有页面、按钮、开关和调试入口必须继续通过“原有功能台”可访问。
- 定时任务及其类型、API 和组件不得删除。CRUD、启停、导入预览、立即执行、取消未执行项、运行记录和自动调度必须保持前后端契约一致。
- 定时任务并发数是统一运行设置中的单次运行组内上限；同一任务不得重入执行。
- 业务契约不采用兼容分支；前端能力保留不等于允许 v2/v3/v4 Schema、因子输出字段或旧 Prompt 重新进入 v5 主链。

## 10. 验证要求

根据修改范围执行对应验证：

| 修改范围 | 必须验证 |
|---|---|
| Python 后端 | 对相关模块执行 `python3 -m py_compile` |
| 原有功能台 | 执行 `node --check risk_review_web/static/app.js` |
| React 前端源码、依赖或构建配置 | 在 `risk_review_web/web/` 执行 `npm run typecheck` 和 `npm run build` |
| Phase 1 Prompt、Skill 或规则 | 重新生成或核对派生规范展示 Markdown，执行 Phase 1 Mock 冒烟 |
| Phase 2 Prompt、Skill 或规则 | 重新生成或核对派生规范展示 Markdown，执行 Phase 2 Mock 冒烟 |
| 全局任务池 | 验证并发上限、排队、公平调度和取消 |
| 定时任务 | 至少验证一次手动触发和一次到期自动触发 |
| 批量评审 | 至少执行一组多行批量验证 |
| 验证集 | 至少执行一条回归案例 |

只修改 Python、Prompt、Skill、规则 JSON、派生规范 Markdown 或数据文件时，不得运行无关的前端构建，也不得因此更新 `risk_review_web/static/dist/`。只有 `risk_review_web/web/src/`、前端依赖、Vite 配置或其他实际参与前端打包的输入发生变化时，才重新生成 React 构建产物。

测试生成的临时任务、运行记录和 `outputs/` 产物必须在交付前清理。
