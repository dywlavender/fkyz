# 风控 AI 评审平台（Risk Review Web）

基于 LangChain Agent 的风控因子加工逻辑评审平台。输入需求与接口文档后，Phase 1 生成唯一的「标准加工规范」，Phase 2 将待评审加工逻辑与该标准进行结果语义比较，输出通过、不通过或无法评审。

> 本仓库根目录同时是「知识库」工作区，但 `risk_review_web/` 属于风控评审平台代码，不参与知识库的 INGEST / QUERY / LINT / REFLECT / MERGE 流程。详见 `AGENTS.md`。

## 能力概览

- **Phase 1 · 标准加工规范**：依据需求、指定接口文档、公共业务规则和 Phase 1《因子加工开发规范》，生成加工输入字段、标准加工逻辑、默认值兜底三部分内容。
- **Phase 2 · 评审报告**：直接以冻结的 Phase 1 Markdown 为唯一业务标准，结合原始需求引用证据，评审待评审加工逻辑。
- **阶段规则隔离**：Phase 1 标准生成规则维护在 `phase1_processing_spec.json`，Phase 2 评审规则维护在 `phase2_review_spec.json`，两个阶段只读取各自规则。
- **一键评审**：自动串联 Phase 1 → Phase 2，一次性完成完整评审。
- **批量评审**：CSV / Excel 批量导入多条因子。
- **验证集工作台**：基于 `data/examples.json` 做回归验证。
- **接口文档库**：浏览 `raw/api/` 下的接口文档（本系统接口 / 外部接口）。
- **评审历史**：本地存储历史记录，支持详情回看与勾选删除。

## 目录结构

```text
.
├── start_v2.sh                      # 启动脚本（端口 / 释放占用端口）
├── AGENTS.md                        # 平台开发约定与验证要求
├── risk_review_web/                 # 平台主代码
│   ├── server_v2.py                 # 后端入口（HTTP Server 启动）
│   ├── app_context.py               # 应用依赖、任务与工作流编排
│   ├── paths.py                     # 路径常量
│   ├── prompt_builder.py            # Prompt 组装
│   ├── artifacts.py                 # 产物校验 / checksum / 缓存
│   ├── business_rules.py            # 业务规则读写
│   ├── api_docs.py                  # 接口文档加载与缓存
│   ├── utils.py
│   ├── task_pool.py                 # 单后端进程全局任务池
│   ├── routes/                      # HTTP 路由与 Handler
│   ├── scheduling/                  # 定时任务模型、解析、仓储、服务与扫描器
│   ├── web/src/                     # React 前端源码
│   ├── static/                      # React 构建产物及 Markdown 查看器共享资源
│   ├── data/
│   │   ├── business_rules.json      # 业务规则数据
│   │   └── examples.json            # 验证集数据
│   ├── prompts/
│   │   ├── phase1.md / phase2.md / direct_review.md
│   │   └── risk-review-skill/       # v5 Skill、开发规范、模板与派生 Prompt
│   └── README.md                    # 前端功能详细说明
├── raw/api/                         # 接口文档源（本系统接口 / 外部接口）
└── outputs/                         # 运行产物统一落盘目录（由后端写入）
```

## 快速开始

### 1. 环境依赖

- Python 3（`python` 或 `python3`）
- 使用 Agent 时需要 conda 环境 `langchain1.2`（默认 Python 路径 `/opt/miniconda3/envs/langchain1.2/bin/python`）及对应模型 API 凭据
- macOS / Linux / Git Bash（Windows）

### 2. 启动服务

```bash
./start_v2.sh           # 默认端口 8765
./start_v2.sh 9000      # 指定端口
```

启动脚本会：

1. 检查关键文件（Skill、`server_v2.py`、`static/dist/index.html`）是否就位；
2. 创建 `outputs/` 目录；
3. 释放被占用的目标端口；
4. 以 `/opt/miniconda3/envs/langchain1.2/bin/python -m risk_review_web.server_v2` 启动 HTTP 服务。

目标机 Python 路径不同时，直接修改 `start_v2.sh` 最后一行。

启动后访问：<http://127.0.0.1:8765>

### 3. Mock 模式（无需模型服务）

```bash
RISK_REVIEW_MOCK=1 ./start_v2.sh
```

用于前端联调与冒烟测试，不实际调用模型。

### 4. Agent 配置

复制 `.env.example` 为 `.env`，配置 Agent Provider、模型名和凭据。所有模型任务由全局任务池线程直接调用 Agent。

Agent 的最终响应正文直接作为 Markdown 结果交给现有校验与落盘链路；中间模型输入、模型输出、工具调用和工具结果写入每日运行日志，不混入业务结果。

## 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `PORT` | `8765` | HTTP 服务端口 |
| `RISK_REVIEW_MOCK` | — | 设为 `1` 启用 Mock 模式 |
| `RISK_REVIEW_AGENT_PROVIDER` | `anthropic` | `anthropic` 或 OpenAI 兼容 Provider |
| `RISK_REVIEW_AGENT_MODEL` | — | Agent 模型名（在 `.env` 中配置） |
| `RISK_REVIEW_GLOBAL_CONCURRENCY` | `3` | 单后端进程内全局模型任务并发上限 |

## 数据流

```
用户输入 → 前端验证 → 后端 API → 全局线程池 → LangChain Agent → 结果返回 → 前端渲染
```

- **Phase 1**：`POST /api/review/phase1` → 在临时只读隔离目录中提供 Phase 1 Skill、Phase 1 开发规范、指定接口文档和公共业务规则 → 调用所选模型后端 → 写入标准 Markdown 与 `.metadata.json`。
- **Phase 2**：`POST /api/review/phase2` → 在独立只读隔离目录中提供 Phase 2 Skill 和 Phase 2 评审规范，并通过 Prompt 提供冻结标准、原始需求和待评审逻辑 → 返回仅含总体结论和问题清单的 Markdown 报告。
- **一键评审**：前端 `runFullReview()` 顺序调用 Phase 1 → Phase 2。

因子名仅用于页面展示和文件命名，不进入模型业务判断或标准缓存键。当前全局任务池只覆盖单个 Python 后端进程；不要同时启动多个后端实例共享同一 `data/` 目录。

详细的前端页面布局、按钮语义与交互约定见 [`risk_review_web/README.md`](risk_review_web/README.md)。

## 开发约定

- **输出目录**：运行产物统一写入项目根目录 `outputs/`，不要写入 `wiki/outputs/`。
- **后端入口**：`risk_review_web/server_v2.py`；**React 前端**：`risk_review_web/web/src/`；**构建产物**：`risk_review_web/static/dist/`。
- **修改后至少执行**：Python 编译、React typecheck/build、Mock 模式 Phase 1 / Phase 2 冒烟；如涉及验证集再跑一条回归验证。
