# 风控 AI 评审平台（Risk Review Web）

基于 Claude Code 的风控因子加工规范生成与准出内容评审平台。输入因子需求与接口文档，由 Claude 两阶段产出「标准加工规范」并对「待评审准出内容」做对比评审，给出不符合 / 待确认 / 建议三级结论。

> 本仓库根目录同时是「知识库」工作区，但 `risk_review_web/` 属于风控评审平台代码，不参与知识库的 INGEST / QUERY / LINT / REFLECT / MERGE 流程。详见 `AGENTS.md`。

## 能力概览

- **Phase 1 · 标准加工规范**：根据因子名 + 需求内容 + 关联接口文档，生成含加工字段、字段溯源、过滤条件、计算逻辑、默认值兜底的标准规范。
- **Phase 2 · 评审报告**：读取 Phase 1 规范 + 待评审准出内容（因子加工输入字段 / 加工逻辑 / 默认值），按多维度对比评审，输出总体结论与汇总问题清单。
- **一键评审**：自动串联 Phase 1 → Phase 2，一次性完成完整评审。
- **批量评审**：CSV / Excel 批量导入多条因子。
- **验证集工作台**：基于 `data/examples.json` 做回归验证。
- **接口文档库**：浏览 `raw/api/` 下的接口文档（本系统接口 / 外部接口）。
- **评审历史**：本地存储历史记录，支持详情回看与勾选删除。

## 目录结构

```text
.
├── start_v2.sh                      # 启动脚本（端口 / Claude 命令探测 / 释放占用端口）
├── CLAUDE.md                        # Claude 调用运行契约（只输出 Markdown，不写文件）
├── AGENTS.md                        # 平台开发约定与验证要求
├── risk_review_web/                 # 平台主代码
│   ├── server_v2.py                 # 后端入口（HTTP 服务 + Claude 调用 + 文件落盘）
│   ├── paths.py                     # 路径常量
│   ├── prompt_builder.py            # Prompt 组装
│   ├── artifacts.py                 # 产物校验 / checksum / 缓存
│   ├── business_rules.py            # 业务规则读写
│   ├── api_docs.py                  # 接口文档加载与缓存
│   ├── utils.py
│   ├── static/                      # 前端（index.html / app.js / style.css + vendor）
│   ├── data/
│   │   ├── business_rules.json      # 业务规则数据
│   │   └── examples.json            # 验证集数据
│   ├── prompts/
│   │   ├── phase1.md / phase2.md / core_rules.md / reviewer.md / review_feedback.md
│   │   └── risk-review-skill/       # 风控评审 Skill（phase / core / template / output / build）
│   └── README.md                    # 前端功能详细说明
├── raw/api/                         # 接口文档源（本系统接口 / 外部接口）
└── outputs/                         # 运行产物统一落盘目录（由后端写入）
```

## 快速开始

### 1. 环境依赖

- Python 3（`python` 或 `python3`）
- Claude Code CLI（`claude` 或 `claude.cmd`）—— Phase 1 / Phase 2 实际调用
- macOS / Linux / Git Bash（Windows）

### 2. 启动服务

```bash
./start_v2.sh           # 默认端口 8765
./start_v2.sh 9000      # 指定端口
```

启动脚本会：

1. 检查关键文件（Skill、`server_v2.py`、`index.html`）是否就位；
2. 创建 `outputs/` 目录；
3. 释放被占用的目标端口；
4. 探测 Claude CLI 命令（可用 `RISK_REVIEW_CLAUDE_CMD` 覆盖）；
5. 以 `python -m risk_review_web.server_v2` 启动 HTTP 服务。

启动后访问：<http://127.0.0.1:8765>

### 3. Mock 模式（无需 Claude）

```bash
RISK_REVIEW_MOCK=1 ./start_v2.sh
```

用于前端联调与冒烟测试，不实际调用 Claude。

## 配置项

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `PORT` | `8765` | HTTP 服务端口 |
| `RISK_REVIEW_MOCK` | — | 设为 `1` 启用 Mock 模式 |
| `RISK_REVIEW_CLAUDE_CMD` | `claude` / `claude.cmd` | Claude CLI 命令 |
| `RISK_REVIEW_CLAUDE_TIMEOUT` | `600` | Claude 调用超时（秒） |
| `RISK_REVIEW_REQUIRE_CLAUDE_HEALTH` | — | 设为 `1` 启动时检查 Claude 可用性 |

## 数据流

```
用户输入 → 前端验证 → 后端 API → Claude Code → 结果返回 → 前端渲染
```

- **Phase 1**：`POST /api/review/phase1` → 后端组装 `risk-review-skill/phase/phase1_standard.md` + 接口文档 → 调用 `claude -p` → 输出写入 `outputs/standard-<slug>.md` → 返回 `standardFileId` + checksum。
- **Phase 2**：`POST /api/review/phase2` → 后端读取 Phase 1 产物 + 待评审内容 → 组装 `phase2_review.md` Prompt → 调用 `claude -p` → 返回评审报告。
- **一键评审**：前端 `runFullReview()` 顺序调用 Phase 1 → Phase 2。

详细的前端页面布局、按钮语义与交互约定见 [`risk_review_web/README.md`](risk_review_web/README.md)。

## 开发约定

- **Claude 调用契约**（见 `CLAUDE.md`）：Claude 只根据 Prompt 输出 Markdown 正文，**不得自行创建 / 修改 / 删除任何文件**；所有文件落盘由 Python 后端统一完成。
- **输出目录**：运行产物统一写入项目根目录 `outputs/`，不要写入 `wiki/outputs/`。
- **后端入口**：`risk_review_web/server_v2.py`；**前端**：`static/index.html` / `static/app.js` / `static/style.css`。
- **修改后至少执行**：Python 语法检查、前端 JS 语法检查、Mock 模式 Phase 1 / Phase 2 冒烟；如涉及验证集再跑一条回归验证。
