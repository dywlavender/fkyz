# AGENTS.md — 风控 AI 评审平台开发约定

## 1. 适用范围与项目边界

本文件适用于 `risk_review_web/` 目录及其运行产物。该目录是风控 AI 评审平台，不属于知识库内容生产流程。

处理平台代码、提示词、规则、接口文档、验证集或运行产物时，不得套用知识库的 INGEST / QUERY / LINT / REFLECT / MERGE / ADD-QUESTION 流程。

## 2. 主要文件与职责

- 后端入口：`risk_review_web/server_v2.py`
- 全局任务池：`risk_review_web/task_pool.py`
- 定时任务调度与持久化：`risk_review_web/scheduled_tasks.py`
- 标准与报告校验：`risk_review_web/artifacts.py`
- Prompt 装配：`risk_review_web/prompt_builder.py`
- 路径定义：`risk_review_web/paths.py`
- Phase 1/Phase 2/Reviewer Prompt：`risk_review_web/prompts/`
- 规则引擎 Skill：`risk_review_web/prompts/risk-review-skill/`
- React 前端源码：`risk_review_web/web/src/`
- React 前端构建配置：`risk_review_web/web/vite.config.ts`
- React 前端构建产物：`risk_review_web/static/dist/`
- 原有功能台页面：`risk_review_web/static/index.html`
- 原有功能台逻辑：`risk_review_web/static/app.js`
- 原有功能台样式：`risk_review_web/static/style.css`
- 公共业务规则：`risk_review_web/data/business_rules.json`
- 验证集案例：`risk_review_web/data/examples.json`
- 定时任务配置：`risk_review_web/data/scheduled_tasks.json`
- 定时任务运行记录：`risk_review_web/data/scheduled_runs.json`

不要在 `server_v2.py` 中复制一套与 `artifacts.py`、`prompt_builder.py` 或 `task_pool.py` 重复的规则。

## 3. 输出与数据目录

平台生成的标准加工规范、结构化摘要和评审报告统一保存到项目根目录：

```text
outputs/
```

禁止把平台运行产物写入 `wiki/outputs/`。`wiki/outputs/` 或 `validation/outputs/` 只可作为历史验证集输入的候选来源，不是平台结果目录。

文件保存必须由 Python 后端完成。Claude 只能向 stdout 输出正文，不得自行创建、修改、移动或删除文件。

## 4. 当前标准契约（仅 v4）

- 结构化摘要 schema 固定为 `risk_review_standard_json_v4`。
- 不保留 v2/v3 兼容分支、降级解析或旧 Prompt 契约；旧摘要必须重新执行 Phase 1。
- Phase 1 生成唯一、最可能、可执行的标准主链，不生成多套候选方案。
- D1-D5、D8-D9 进入标准的规则全部为 `hard`。
- 原始输入存在多种合理解释时，必须选择最可能口径，并使用 `selected_interpretation`、`SEL-xxx` 和 `selection_reason` 记录。
- 主链不得使用 `unclear`、待确认、疑问或建议口径代替确定逻辑。
- 因子加工输入字段只列实际参与加工的叶子字段；纯数组、列表、对象容器不单列，通过叶子字段完整路径表达层级。
- “因子名”只是展示和文件命名元数据，不得传给模型参与准出生成或评审判断。
- 不存在“因子输出字段”评审维度；规则编号 D6 后直接使用 D8、D9。
- 默认值只校验统一语义：“字段缺失、格式错误或数据异常时返回默认值”；具体返回值不进入标准，也不参与评审。

契约变更必须完整修改生成、结构化摘要、校验、Reviewer、前端展示、Mock 和回归链路，不得通过新增兼容限制保留旧逻辑。

## 5. 阶段与运行开关

### Phase 1

只接收需求内容、指定接口文档和公共业务规则，生成标准加工规范及 v4 结构化摘要。不得读取待评审准出内容。

### Phase 2

只依据 Phase 1 的 v4 结构化摘要、待评审准出内容和允许使用的公共业务规则评审。不得重新读取接口文档、重新推导标准或改变 Phase 1 的选定解释。

### Reviewer

Reviewer 只审核候选输出是否遵守当前阶段 Skill，不重写正文。开启“跳过 Reviewer”时，Phase 1 和 Phase 2 都不得调用 Reviewer。

### 阶段校验

- 关闭“跳过阶段校验”：强制执行 schema、正文语义和 checksum 校验。
- 开启“跳过阶段校验”：允许未完成 checksum/完整语义校验的 v4 摘要继续，但仍拒绝非 v4 schema，也不降低 hard 规则约束。
- P2-A1/P2-A2 和相关提示词必须与开关语义保持一致。

## 6. Claude 调用约束

- Claude 使用 `-p --output-format text` 管道模式。
- stdout 必须是完整 Markdown 正文；不得只输出摘要、路径、保存提示、计划或检查过程。
- Claude 不得自行保存标准或报告；后端负责规范化、校验和落盘。
- 调用目录必须保证读取到本项目的 `CLAUDE.md`，不得误读其他知识库运行契约。
- Phase 1、Phase 2 和 Reviewer 只能读取当前阶段明确允许的 Skill 和输入。
- 不得把因子名、内部任务 ID、客户端 ID 或线程池信息拼入模型业务输入。
- Phase 1 必须在按任务创建的临时隔离工作目录中运行；目录内只复制 Phase 1 Skill、当次指定接口文档和公共业务规则。
- Phase 1 Claude 调用只允许在隔离目录内使用只读 `Read`、`Glob`、`Grep`，必须禁用 Bash、写文件工具、Slash Commands、项目自定义配置和外部 MCP；任务结束后立即清理隔离目录。
- 不得把允许读取的文件全文一次性拼入 Prompt 代替文件隔离。

## 7. 全局任务池与并发

所有会触发因子加工或 Claude 调用的入口必须受 `GLOBAL_TASK_POOL` 控制，包括：

- 单因子 Phase 1
- 单因子 Phase 2
- 批量评审的每条因子流水线
- 验证集/回归案例执行
- 流水线内部的 Reviewer 与重试
- 定时任务的每条因子流水线

约束如下：

- 全局并发由 `RISK_REVIEW_GLOBAL_CONCURRENCY` 控制，默认 3。
- 批量页面的并发数只是本批次上限，不能突破全局上限。
- 不得在接口处理器、批量任务或验证任务中新增 `ThreadPoolExecutor`、裸 `threading.Thread` 或其他私有执行池。
- 固定 Worker 只能在 `task_pool.py` 中创建。
- `scheduled_tasks.py` 只允许保留一个不执行业务加工的计时扫描线程；到期因子必须提交到 `GLOBAL_TASK_POOL`，不得在调度线程内调用 Claude。
- 批量任务必须按父任务公平轮询，不能一次占满全局队列导致其他登录用户长期饥饿。
- 已排队任务取消时从全局队列移除；运行中任务取消时终止对应 Claude 进程组，并停止后续阶段。
- 不得从全局 Worker 内同步提交并等待同一个全局池的子任务，避免线程池死锁。批量/案例流水线应在已占用的 Worker 内顺序执行 Phase 1、Phase 2 和 Reviewer。

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
- 定时任务并发数是单次运行的组内上限，仍受 `RISK_REVIEW_GLOBAL_CONCURRENCY` 全局上限约束；同一任务不得重入执行。
- 业务契约不采用兼容分支；前端能力保留不等于允许 v2/v3 Schema、因子输出字段或旧 Prompt 重新进入 v4 主链。

## 10. 验证要求

修改后至少执行：

```bash
python3 -m py_compile risk_review_web/server_v2.py risk_review_web/task_pool.py risk_review_web/artifacts.py risk_review_web/prompt_builder.py
node --check risk_review_web/static/app.js
cd risk_review_web/web && npm run typecheck && npm run build
```

并完成：

- Mock 模式 Phase 1 冒烟
- Mock 模式 Phase 2 冒烟
- 涉及任务调度时，验证全局并发上限、排队、公平调度和取消
- 涉及定时任务时，至少验证一次手动触发和一次到期自动触发，并在结束后清理测试任务、运行记录和产物
- 涉及批量时，至少执行一组多行批量验证
- 涉及验证集时，至少执行一条回归案例
- 涉及 Prompt/Skill 时，重新生成或核对派生 Prompt，确保源文件与产物一致
- 检查 `outputs/`，清理本次测试产物
