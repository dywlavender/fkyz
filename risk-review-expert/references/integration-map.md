# 专家能力集成映射

## 专家级配置

- 专家系统提示词：`risk-review-expert/EXPERT_PROMPT.md`
- 专家 Skill：`risk-review-expert/SKILL.md`
- 业务输入：通过 user 消息传入，不拼入专家系统提示词。

## Phase 1 加载项

- Skill：`risk_review_web/prompts/risk-review-skill/phase/phase1_standard.md`
- 唯一规则源：`risk_review_web/prompts/risk-review-skill/spec/phase1_processing_spec.json`
- 业务规则：`risk_review_web/data/business_rules.json`
- 业务证据：原始需求与当次指定的 `raw/api/` 接口文档。

不加载 Phase 2 Skill、Phase 2 规则、待评审逻辑、验证案例或历史产物。

## Phase 2 加载项

- Skill：`risk_review_web/prompts/risk-review-skill/phase/phase2_review.md`
- 唯一规则源：`risk_review_web/prompts/risk-review-skill/spec/phase2_review_spec.json`
- 业务证据：冻结标准加工规范、原始需求和待评审加工逻辑。

不加载接口文档、公共业务规则、Phase 1 Skill、Phase 1 规则、验证案例或历史产物。

## WorkBuddy 专家映射

- 将 `EXPERT_PROMPT.md` 配置为专家角色提示词。
- 将两个阶段 Skill 和对应规则 JSON 分别配置为专家的阶段能力。
- 通过任务路由只激活当前阶段能力；执行完整评审时先冻结 Phase 1 结果，再将其作为 Phase 2 输入。
- 若平台只支持单次上下文，仍应在调度层分两次调用，不要在一次模型请求中同时加载两个阶段规则。
