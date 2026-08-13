# 风控 AI 评审平台 Python 重构方案

## 1. 文档目的

本方案针对当前 Python 后端的六项结构性问题制定可分阶段实施的改造路径：

1. 删除重复健康检查和重复表格解析。
2. 将 Agent 返回值从 `stdout`、`stderr`、`returncode` 迁移为统一结果对象。
3. 提取统一阶段执行器，合并 Phase 1、Phase 2、需求评审的公共执行流程。
4. 引入类型化 Task 和统一状态迁移。
5. 将历史记录改为单一事实来源。
6. 拆分 `server_v2.py` 和 `scheduled_tasks.py`。

本次重构的目标是降低状态分支、重复校验和跨模块隐式约定，不改变 Phase 1/Phase 2 业务边界、全局任务池规则、定时任务能力及现有 HTTP 对外功能。

## 2. 当前问题与事实依据

### 2.1 入口文件承担过多职责

`server_v2.py` 同时包含：

- HTTP 路由分发；
- 任务创建、状态更新、取消和回收；
- 历史记录同步、合并和删除；
- Agent 调用适配；
- Phase 1、Phase 2、需求评审执行；
- 批量评审、验证集和实验任务编排；
- 健康检查和服务启动。

`scheduled_tasks.py` 同时包含：

- 调度表达式计算；
- CSV/XLSX 导入；
- 定时任务和运行记录持久化；
- 定时任务业务校验；
- 扫描线程；
- 到期任务提交和运行编排。

因此，当前复杂度主要来自职责聚合，而不是单个算法过于复杂。

### 2.2 结果契约仍然模拟子进程

Agent 已改为进程内调用，但业务流程仍使用以下字段表达结果：

```text
stdout
stderr
returncode
```

`model_response_content()` 还同时兼容 `content` 和 `stdout`。这使 Agent、阶段执行、任务结果和 HTTP 响应共享一个含义不清的字典，并保留了已经不存在的“进程退出码”概念。

### 2.3 任务状态缺少统一入口

`TASKS` 的值是无固定结构的字典。`queued`、`running`、`done`、`error`、`cancelled` 等状态及 `result`、`updated`、`outputs` 字段由多个函数直接修改。

由此产生的补偿逻辑包括：

- 后台 Future 完成后检查业务函数是否写入终态；
- 查询时重新计算 progress；
- 多处重复检查取消状态；
- 父子任务分别拼接结果字典；
- 调用方必须了解任务字典的内部字段。

### 2.4 历史记录存在多个事实来源

当前历史记录会合并：

- 内存中的活动任务；
- `review_records.json`；
- 从 `outputs/` 反向扫描出的孤立产物；
- 删除 tombstone。

而且历史查询会触发终态任务持久化，造成读接口隐藏写入。多事实来源是历史模块大量去重、兼容和引用检查的根因。

## 3. 设计原则

### 3.1 必须保留的约束

- Phase 1 和 Phase 2 的输入边界及隔离工作目录不变。
- Agent 只允许使用规定的只读工具。
- 后端负责输出规范化、校验和落盘。
- 所有业务加工继续通过 `GLOBAL_TASK_POOL`。
- 全局公平调度、分组并发、排队取消和运行取消语义不变。
- 定时任务的 CRUD、启停、导入预览、立即执行、取消、运行记录和自动扫描能力不删除。
- 仍按单 Python 进程设计；不宣称支持跨进程全局并发。
- 不为旧结果字典长期保留双轨兼容分支。

### 3.2 重构策略

- 先统一数据契约和状态迁移，再拆文件。
- 每个阶段都保持可运行、可测试、可回滚。
- 搬迁与行为修改分开提交，避免无法判断失败来自路径变化还是逻辑变化。
- 历史记录先以 `review_records.json` 为唯一持久化事实来源。本轮不同时引入 SQLite，避免扩大验证范围。
- `outputs/` 只保存产物，不再承担任务索引或历史记录恢复职责。

## 4. 目标架构

```text
risk_review_web/
├── server_v2.py                  # 仅保留依赖组装、HTTP Server 和 main
├── app_context.py                # 服务依赖及启动/关闭生命周期
├── domain/
│   ├── agent_result.py           # AgentResult、AgentStatus
│   ├── task.py                   # Task、TaskStatus、TaskResult、状态迁移
│   └── review_record.py          # 历史记录模型
├── repositories/
│   ├── task_repository.py        # 内存任务状态的唯一访问入口
│   └── review_record_repository.py # review_records.json 唯一持久化入口
├── workflows/
│   ├── stage_executor.py         # 公共阶段执行模板
│   ├── phase1.py                 # Phase 1 特有逻辑
│   ├── phase2.py                 # Phase 2 特有逻辑
│   ├── requirement_review.py     # 需求评审特有逻辑
│   ├── batch_review.py           # 批量流水线
│   ├── regression.py             # 验证集流水线
│   └── experiment.py             # 对比实验流水线
├── routes/
│   ├── tasks.py
│   ├── reviews.py
│   ├── history.py
│   ├── imports.py
│   ├── business_rules.py
│   └── ...                       # 保留现有 route 模块
├── services/
│   ├── health.py
│   ├── tabular_import.py         # 所有 CSV/XLSX 解析唯一实现
│   └── model_runtime/
└── scheduling/
    ├── models.py
    ├── schedule_parser.py
    ├── repository.py
    ├── service.py
    └── scheduler.py
```

这里的目录是目标职责边界，不要求在一个提交中一次建完。

## 5. 核心数据契约

### 5.1 Agent 统一结果对象

```python
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AgentStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AgentResult:
    status: AgentStatus
    content: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

约束：

- Agent 层不再返回 `stdout`、`stderr`、`returncode`。
- `content` 只表示模型最终正文。
- 工具调用日志通过事件回调进入任务日志，不塞入 `content`。
- 模型异常在 Agent 边界转换为 `FAILED`；取消转换为 `CANCELLED`。
- Agent 层不保存业务产物，也不附加 `standardFileId`、`reviewFileId`。

HTTP 层如果短期仍需要旧字段，应在路由响应序列化器中一次性转换；业务内部不得继续消费旧字段。兼容转换在前后端完成同步迁移后删除。

### 5.2 阶段执行结果

```python
@dataclass(frozen=True, slots=True)
class StageArtifacts:
    content: str
    file_ids: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StageOutcome:
    status: TaskStatus
    artifacts: StageArtifacts | None = None
    error: TaskError | None = None
```

阶段结果负责业务产物和业务错误；它与纯模型 `AgentResult` 分离。

### 5.3 类型化 Task

```python
class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    DONE = "done"
    ERROR = "error"


@dataclass(slots=True)
class Task:
    id: str
    task_type: str
    status: TaskStatus
    client_id: str
    parent_task_id: str | None
    group_concurrency: int | None
    context: dict[str, Any]
    result: StageOutcome | dict[str, Any] | None
    output_ids: list[str]
    cancel_requested: bool
    created_at: float
    updated_at: float
    logs: list[dict[str, Any]]
```

禁止业务代码直接执行：

```python
TASKS[task_id]["status"] = "done"
```

必须通过 `TaskRepository` 或 `TaskService`：

```python
tasks.create(task)
tasks.start(task_id)
tasks.request_cancel(task_id)
tasks.complete(task_id, outcome)
tasks.fail(task_id, error)
tasks.cancel(task_id, reason)
```

合法状态迁移：

```text
queued -> running
queued -> cancelled
running -> cancelling
running -> done
running -> error
running -> cancelled
cancelling -> cancelled
cancelling -> error
```

对非法迁移应抛出领域异常，不能静默覆盖终态。

## 6. 统一阶段执行器设计

### 6.1 公共模板

统一执行器只承载各阶段真实共有的生命周期：

```text
检查任务取消
→ 标记任务 running
→ 构建阶段上下文
→ 创建隔离工作目录
→ 执行 Mock 或 Agent
→ 检查 AgentResult
→ 规范化模型正文
→ 校验业务输出
→ 保存业务产物
→ 返回 StageOutcome
→ 清理隔离目录
→ 统一写入任务终态
→ 持久化历史记录
```

建议接口：

```python
class StageDefinition(Protocol):
    name: str

    def prepare(self, request: object, task: Task) -> PreparedStage: ...
    def build_prompt(self, prepared: PreparedStage) -> ModelPrompt: ...
    def mock(self, prepared: PreparedStage) -> str: ...
    def normalize(self, content: str, prepared: PreparedStage) -> str: ...
    def validate(self, content: str, prepared: PreparedStage) -> None: ...
    def persist(self, content: str, prepared: PreparedStage) -> StageArtifacts: ...
```

`StageExecutor` 不知道 Phase 1/Phase 2 的业务字段，也不拼接任何业务 Markdown。

### 6.2 阶段差异保留位置

| 阶段 | 保留的特有行为 |
|---|---|
| Phase 1 | checksum、标准缓存、接口文档和业务规则隔离、标准及元数据保存 |
| Phase 2 | 冻结标准加载、原始需求和待评审逻辑输入、报告保存 |
| 需求评审 | 指定接口文档隔离、需求评审报告及证据快照保存 |

Phase 1 缓存命中可以由 `prepare()` 返回预计算 `StageArtifacts`，执行器直接完成任务，但仍必须经过统一终态和历史持久化入口。

### 6.3 取消检查收敛

保留以下取消点：

- 从全局队列取出后、阶段启动前；
- 每轮 Agent 调用前；
- 每个工具调用前；
- Agent 返回后、产物保存前；
- 流水线进入下一阶段前。

删除 `run_generation()` 和阶段函数对同一结果的重复空响应、取消检查。空正文只在 Agent 边界或执行器边界检查一次。

## 7. 历史记录单一事实来源

### 7.1 最终规则

- `review_records.json` 是历史记录的唯一持久化事实来源。
- `TASKS`/`TaskRepository` 只表示当前进程中的活动任务和近期终态任务。
- `outputs/` 是产物存储，不参与普通历史查询。
- 任务进入 `done`、`error` 或 `cancelled` 时，在同一个终态提交路径中 upsert 历史记录。
- `/api/review-history` 只读取 `ReviewRecordRepository`，不触发同步、GC 或文件扫描。
- 活动任务通过 `/api/tasks` 查询，不混入历史接口。

### 7.2 一次性迁移

上线新结构前执行一次迁移命令：

```text
现有 review_records.json
+ 当前 outputs/ 可识别的孤立产物
→ 去重、校验
→ 生成新的 review_records.json
→ 输出迁移报告
```

迁移完成后删除运行期的：

- `artifact_review_history_records()` 合并分支；
- `sync_terminal_tasks_to_review_records()` 查询时同步；
- 删除 tombstone 参与多来源屏蔽的逻辑；
- `content`/`stdout` 等旧记录形态的兼容读取。

删除记录时，Repository 应先检查其他记录是否引用同一产物，再决定是否删除文件。这个安全检查应保留，但不应散落在路由和历史拼装函数中。

### 7.3 原子性边界

JSON 文件不能提供真正数据库事务，因此采用以下顺序：

1. 先保存并校验产物；
2. 原子 upsert 历史记录；
3. 更新内存任务为终态；
4. 如果第 2 步失败，任务进入 `error`，保留产物并记录“未索引产物”；
5. 由显式修复命令处理未索引产物，不在查询时自动修补。

如果未来出现多进程部署、历史记录规模过大或事务要求提高，再将 Repository 后端替换为 SQLite；这不属于本轮六项重构。

## 8. `server_v2.py` 拆分方案

### 8.1 第一轮：纯搬迁，不改变行为

| 当前职责 | 目标模块 |
|---|---|
| 健康检查 | `services/health.py` |
| Agent 调用适配 | `services/model_runtime/agent_runner.py` |
| 任务创建、查询、取消、回收 | `repositories/task_repository.py`、`services/task_service.py` |
| 历史记录 CRUD | `repositories/review_record_repository.py`、`routes/history.py` |
| Phase 1/2/需求评审 | `workflows/` 对应模块 |
| 批量评审 | `workflows/batch_review.py` |
| 回归和实验 | `workflows/regression.py`、`workflows/experiment.py` |
| 剩余 HTTP 分支 | 对应 `routes/*.py` |

第一轮搬迁后，`server_v2.py` 只允许保留：

- 依赖实例化；
- Handler 组合；
- Server 创建；
- `main()`；
- 为过渡期保留的少量导入别名。

### 8.2 第二轮：删除过渡导入

测试和业务调用全部改为从目标模块导入后，删除 `server_v2.py` 的兼容导出。禁止长期把 `server_v2.py` 当作公共 API 聚合文件。

### 8.3 路由约束

路由层只负责：

- 读取和解析 HTTP 输入；
- 调用 service/workflow；
- 将领域对象序列化为 HTTP 响应；
- 把已分类异常映射为 HTTP 状态码。

路由层不得直接：

- 修改任务字典；
- 写 JSON 状态文件；
- 保存产物；
- 构建 Prompt；
- 调用 Agent。

## 9. `scheduled_tasks.py` 拆分方案

### 9.1 模块边界

| 目标模块 | 职责 |
|---|---|
| `scheduling/models.py` | ScheduledTask、ScheduledRun、枚举和校验错误 |
| `scheduling/schedule_parser.py` | once/daily/weekly/monthly/cron 的下一次运行时间计算 |
| `scheduling/repository.py` | scheduled_tasks.json、scheduled_runs.json 的原子读写 |
| `scheduling/service.py` | CRUD、启停、导入预览、立即执行、取消、运行查询 |
| `scheduling/scheduler.py` | 唯一扫描线程、到期检测、防重入、提交全局任务池 |

### 9.2 必须消除的重复

- 删除 `scheduled_tasks.py` 内部的 `parse_csv_rows()` 和 `parse_xlsx_rows()`。
- 所有普通导入、验证集导入和定时任务导入统一调用 `services/tabular_import.py`。
- 编码策略统一为：BOM UTF-8 → UTF-8 → GB18030，解析错误必须显式返回，不允许 XLSX 异常直接变成空列表。
- 表头别名应按业务场景分组，但底层 CSV/XLSX 单元格解析只能有一份。

### 9.3 调度线程边界

扫描线程只允许：

```text
读取到期任务
→ 原子声明运行
→ 提交 GLOBAL_TASK_POOL
→ 更新下一运行时间
```

扫描线程不得执行 Phase 1、Phase 2 或批量业务。业务执行器由全局任务池 Worker 调用。

## 10. 分阶段实施计划

### 阶段 A：低风险去重

改动：

1. `system_health()` 将 outputs、API docs、模型健康结果各计算一次并复用。
2. 定时任务删除本地 CSV/XLSX 解析，统一使用 `services/tabular_import.py`。
3. 补充 CSV UTF-8、UTF-8 BOM、GB18030、XLSX、损坏文件测试。

验收：

- 每次健康请求只执行一次输出目录探针和一次接口文档扫描。
- 全仓库只有一套底层 CSV/XLSX 解析函数。
- 三类导入入口对相同文件产生一致的行数据或一致错误。

### 阶段 B：结果对象迁移

改动：

1. 新增 `AgentResult`、`AgentStatus`。
2. `run_agent_call()` 和 `run_agent_prompt()` 返回 `AgentResult`。
3. 阶段流程只读取 `result.content`、`result.error`、`result.status`。
4. HTTP 序列化器输出新契约。
5. 同步修改 React 类型、Mock、脚本和测试。
6. 删除 `model_response_content()` 及内部 `stdout/stderr/returncode`。

验收：

```bash
rg -n 'model_response_content|"stdout"|"stderr"|"returncode"' risk_review_web --glob '*.py'
```

结果中不得再出现模型或阶段结果的旧字段；若其他非 Agent 场景确有同名字段，必须逐项说明。

### 阶段 C：统一阶段执行器

改动：

1. 提取 `StageExecutor` 和三个 `StageDefinition`。
2. 将隔离目录、Mock/Agent 分支、规范化、校验、保存和清理接入统一模板。
3. 缓存作为 Phase 1 定义的特有能力保留。
4. 删除三个旧的大型阶段函数和重复取消/空响应检查。

验收：

- Phase 1、Phase 2、需求评审分别通过 Mock 冒烟。
- 三个阶段都能覆盖成功、模型失败、校验失败、取消、清理隔离目录。
- Phase 1/2 阶段文件边界测试继续通过。
- 业务规则和 Prompt 内容没有被抽入公共执行器。

### 阶段 D：类型化任务和状态机

改动：

1. 新增 `Task`、`TaskStatus`、`TaskResult`。
2. `TaskRepository` 封装锁和内存字典。
3. 所有任务创建、开始、取消、完成、失败通过统一方法。
4. Future 回调只负责将未处理异常交给 `TaskService.fail()`。
5. progress 由类型化任务/批量结果生成，不再由查询函数猜测字段。

验收：

```bash
rg -n 'TASKS\[[^]]+\]\[|TASKS\[[^]]+\]\.update' risk_review_web --glob '*.py'
```

业务模块不得直接修改任务内部字段。

必须新增状态机测试：

- 所有合法迁移；
- 终态不可被覆盖；
- queued 取消；
- running 取消；
- 父任务取消后子任务不启动；
- Future 异常进入 error；
- 全局池公平调度、分组上限和取消保持原行为。

### 阶段 E：历史记录单一事实来源

改动：

1. 新增 `ReviewRecordRepository`。
2. 任务终态提交时立即持久化历史记录。
3. 实现并执行一次性历史迁移脚本。
4. 历史查询只读 Repository。
5. 删除运行时产物扫描、查询时同步和 tombstone 合并。

验收：

- 读取历史接口不会修改任何文件。
- 删除 `outputs/` 中未被记录引用的文件不会改变历史列表。
- 新终态任务只生成一条历史记录。
- 服务重启后历史记录一致。
- 删除/归档记录不会影响其他记录共享的产物。

### 阶段 F：拆分两个大文件

改动：

1. 先搬迁 `server_v2.py` 的 workflow、task、history、health。
2. 再搬迁剩余 routes。
3. 拆分 `scheduled_tasks.py` 为 scheduling 包。
4. 清理过渡导入和死代码。
5. 更新 AGENTS.md、README 和离线打包清单中的模块路径。

验收：

- `server_v2.py` 只负责组装和启动，目标控制在 300 行以内。
- 原 `scheduled_tasks.py` 删除或只保留不超过 100 行的兼容入口，并在同一次迁移中最终删除兼容入口。
- 生产代码不再从 `server_v2.py` 导入领域或工作流函数。
- 路由不直接访问 `TASKS`、状态 JSON 或 `OUTPUTS_DIR`。

## 11. 测试与验证矩阵

每个阶段至少执行：

```bash
python3 -m py_compile <本阶段修改的 Python 文件>
python3 -m unittest discover -s risk_review_web/tests
```

按影响范围额外执行：

| 范围 | 验证 |
|---|---|
| Agent 结果对象 | Agent 成功、空响应、工具调用、工具错误、超轮数、取消 |
| 阶段执行器 | 三阶段 Mock 冒烟、三阶段异常和取消、隔离目录清理 |
| Task 状态机 | 状态迁移、并发更新、父子取消、Future 异常 |
| 历史记录 | 终态持久化、重启恢复、查询无写入、删除与归档 |
| 全局任务池 | 并发上限、父任务公平轮询、排队取消、运行取消 |
| 定时任务 | CRUD、启停、导入预览、手动触发、到期触发、防重入、取消 |
| 批量评审 | 多行任务、部分失败、取消、组内并发限制 |
| 验证集 | 至少一条回归案例 |
| React 契约变化 | `npm run typecheck`、`npm run build` |

测试产生的状态、任务记录和 `outputs/` 产物必须清理。

## 12. 提交和回滚策略

建议按以下边界提交，不做一个巨型重构提交：

1. `health/import dedup`
2. `agent result contract`
3. `stage executor`
4. `typed task state machine`
5. `review history repository and migration`
6. `server module extraction`
7. `scheduler module extraction`
8. `remove compatibility and dead code`

每个提交必须满足：

- 测试独立通过；
- 没有同时包含无关 UI 或 Prompt 修改；
- 能通过回退该提交恢复上一阶段结构；
- 数据迁移提交在执行前自动备份 `review_records.json`，但运行期不保留双写。

## 13. 主要风险和控制措施

| 风险 | 控制措施 |
|---|---|
| 结果契约修改导致前后端字段不一致 | 同一阶段同步修改 Python 序列化器、React 类型、Mock 和测试 |
| 阶段抽象泄漏业务规则 | 公共执行器只处理生命周期，业务 Prompt/校验/保存留在阶段定义 |
| Task 状态机导致取消行为回归 | 先锁定现有取消测试，再替换写入口，覆盖 queued/running/parent-child |
| 历史迁移漏记录或重复 | 迁移前备份，dry-run 输出数量、冲突和孤立产物清单，校验后再替换 |
| 拆文件造成循环依赖 | domain/repository 不导入 routes/server；依赖只从外层指向内层 |
| 定时任务拆分后发生重入 | 防重入声明和下一次运行时间更新保留在 Repository/Service 的单一临界区 |
| JSON 单文件在多进程下不安全 | 明确维持单进程部署；多进程前必须迁移 SQLite/集中式存储与队列 |

## 14. 完成定义

只有同时满足以下条件，才视为本轮重构完成：

- 健康检查和表格解析没有重复实现或重复执行。
- Agent、阶段和任务内部不再使用子进程式结果字段。
- 三类阶段由同一个生命周期执行器驱动，业务边界保持独立。
- 所有 Task 更新都经过类型化状态迁移接口。
- 历史记录只有一个持久化事实来源，查询无写入、无运行时产物扫描。
- `server_v2.py` 和 `scheduled_tasks.py` 完成职责拆分，且没有长期兼容转发层。
- 后端、Agent、任务池、定时任务、批量、验证集和前端契约测试全部通过。
- 测试临时产物已清理，离线打包清单和运行文档已更新。

