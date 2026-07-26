# 风控 AI 评审平台

## 评审主链

平台使用两阶段评审：

1. Phase 1 根据需求、指定接口文档、公共业务规则和 Phase 1《因子加工开发规范》，生成唯一的标准加工规范。
2. Phase 2 直接以冻结的标准加工规范为业务基准，按 Phase 2《因子加工评审规范》比较待评审加工逻辑是否具有相同结果语义。

因子名只用于页面展示和文件命名，不传给模型参与业务判断，也不进入标准缓存键。

### Phase 1 输出

标准加工规范只包含：

- 因子加工输入字段
- 标准加工逻辑
- 默认值兜底

加工输入字段只列实际参与加工的叶子字段。标准加工逻辑使用固定列：

`步骤 | 逻辑类型 | 标准逻辑`

逻辑类型限定为 `time_filter`、`condition_filter`、`iteration`、`aggregation`、`derivation`、`branch`。字段选择和业务概念映射统一在“因子加工输入字段”中表达，不作为加工步骤。

### Phase 2 输出

评审报告只包含：

- 总体结论
- 汇总问题清单

总体结论限定为通过、不通过、无法评审。每条问题引用稳定的开发规范编号，并同时展示需求原文、标准加工逻辑、待评审原文、影响和修改建议。

## 阶段规则来源

- Phase 1 开发规范：`prompts/risk-review-skill/spec/phase1_processing_spec.json`
- Phase 2 评审规范：`prompts/risk-review-skill/spec/phase2_review_spec.json`
- Phase 1 Skill：`prompts/risk-review-skill/phase/phase1_standard.md`
- Phase 2 Skill：`prompts/risk-review-skill/phase/phase2_review.md`
- Prompt 模板：`prompts/phase1.md`、`prompts/phase2.md`
- 派生 Prompt：`prompts/risk-review-skill/output/`
- 产物与最低技术校验：`artifacts.py`

修改开发规范或 Skill 后，运行：

```bash
python3 risk_review_web/prompts/risk-review-skill/build/build_prompt.py
```

## 隔离与数据边界

- Phase 1 临时目录只包含 Phase 1 Skill、Phase 1 开发规范、当次指定接口文档和公共业务规则。
- Phase 2 临时目录只包含 Phase 2 Skill 和 Phase 2 评审规范；冻结标准、原始需求和待评审逻辑通过 Prompt 提供。
- 两阶段均只允许只读文件工具，不允许 Bash、写文件工具、外部 MCP 或项目自定义配置。
- Phase 2 不读取接口文档和公共业务规则，不重新推导标准。
- Markdown 正文由模型输出，Python 后端负责校验、加展示元数据并写入根目录 `outputs/`。

## 页面

- 工作台：今日评审指标和常用入口
- 单因子评审：填写输入、一键或分阶段执行、查看标准加工规范和评审报告
- 批量评审：模板下载、CSV/XLSX 导入、并发执行、任务恢复和历史记录
- 定时任务：任务 CRUD、启停、导入预览、立即执行、取消和运行详情
- 验证集：案例维护、批量回归、期望结论与问题模式校验
- 评审模式对比：直接评审和两阶段评审实验
- 评审历史：查看或删除标准与报告产物
- 原有功能台：保留渐进迁移期间尚未进入 React 页面的入口

评审结果页面直接渲染评审 Markdown，不再维护旧式规则检查、步骤检查或 review JSON 展示。

## 并发模型

单因子、批量、验证集、对比实验和定时任务均提交到 `GLOBAL_TASK_POOL`。全局并发由 `RISK_REVIEW_GLOBAL_CONCURRENCY` 控制，默认 3；批量和定时任务的并发值只是各自组内上限。

当前任务池只适用于单 Python 后端进程。禁止同时启动多个后端进程共享 `data/` 和 `outputs/`；多进程或多机部署必须改用集中式队列和分布式锁。

## 本地验证

```bash
python3 -m py_compile \
  risk_review_web/server_v2.py \
  risk_review_web/task_pool.py \
  risk_review_web/artifacts.py \
  risk_review_web/prompt_builder.py \
  risk_review_web/scheduled_tasks.py

node --check risk_review_web/static/app.js

cd risk_review_web/web
npm run typecheck
npm run build
```

Mock 模式：

```bash
PORT=8765 RISK_REVIEW_MOCK=1 python3 -m risk_review_web.server_v2
```
