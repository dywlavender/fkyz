# Rule Engine v4

Phase 1 生成的是 Phase 2 唯一使用的可执行标准，不是歧义清单。

## Rule Level

所有进入标准正文的业务规则统一使用：

| Level | 含义 | Phase 1 | Phase 2 |
|---|---|---|---|
| hard | 已选定、可执行、必须遵守的标准口径 | 写入标准 | 可作为不符合依据 |

不得生成 `unclear`、`suggestion` 或“待确认后再定”的主链规则。原始材料存在多种实现时，Phase 1 必须选择最可能的一种，并以 `selected_interpretation` 记录选择理由。

## Basis Type

| basis_type | 含义 |
|---|---|
| requirement | 需求直接明确 |
| api_doc | 接口文档直接明确 |
| public_rule | 公共业务规则直接明确 |
| deterministic_inference | 可由输入唯一推导 |
| selected_interpretation | 存在多种实现，Phase 1 已选择最可能口径 |

## Rule JSON Contract

每条规则必须包含：

- `rule_id`
- `dimension_ref`
- `source_section`
- `standard_level`，固定为 `hard`
- `basis_type`
- `basis_text`
- `evidence_ids`
- `selection_reason`，非选定解释时为空字符串
- `content`

`basis_type = selected_interpretation` 时，必须引用 `SEL-xxx` 并提供非空 `selection_reason`。

---

# Evidence Engine v4

Evidence ID 按来源类型固定：

| Evidence 前缀 | 来源类型 |
|---|---|
| REQ | 需求内容 |
| API | 接口文档 |
| PUB | 公共业务知识规则 |
| INF | 确定性推导 |
| SEL | Phase 1 选定解释 |

排序规则：REQ、API、PUB、INF、SEL，同类型按原文或选择顺序编号。

每条 hard 规则必须引用至少一个 Evidence ID。

使用 `SEL-xxx` 时，必须在「选定解释」章节写明原始歧义、选定口径和选择理由。选择理由应说明为什么该方案最贴近需求语义和可用接口字段，不能只写“根据经验”。

---

# Freeze Rule

| 冻结对象 | 生成阶段 | 冻结后限制 |
|---|---|---|
| INPUT_SET | STATE0 | 不得新增用户未提供的输入 |
| PUBLIC_RULE_SET | STATE1 | 不得新增公共规则 |
| API_DOC_SET | STATE2 | 不得新增接口文档 |
| FIELD_SET | STATE3 | 不得新增字段 |
| EVIDENCE_SET | STATE4 | 不得新增证据 |
| STANDARD_RULE_SET | STATE4 | 不得新增标准规则 |
| STRUCTURED_STANDARD_JSON | Phase2 STATE2 | 不得改写标准 JSON |
| REVIEWABLE_RULE_SET | Phase2 STATE3 | 不得新增可评审规则 |
| DRAFT_PARSE_SET | Phase2 STATE4 | 不得重新解释待评审输入 |
| ISSUE_SET | Phase2 STATE5 | 不得新增未判定问题 |

冻结后如发现需要新增字段、规则、证据或问题，不得直接新增；必须回到对应生成状态重新选择并冻结，不得在最终输出阶段临时补充。

---

# Hallucination Guard

以下内容如果没有来自需求内容、接口文档、公共业务知识规则或结构化标准 JSON 的明确依据，一律不得新增：

1. 新字段
2. 新接口
3. 新接口调用步骤
4. 新过滤条件
5. 新状态码
6. 新成功码
7. 新枚举值
8. 新默认值
9. 新兜底分支
10. 新时间窗口
11. 新边界条件
12. 新单位换算
13. 新精度
14. 新四舍五入方式
15. 新排序规则
16. 新去重规则
17. 新取最新/取第一条规则
18. 新分组字段
19. 新条件分支
20. 新业务规则
21. 新 Java 实现约束

处理规则：

- 有明确依据：写入对应规则，并引用 Evidence ID。
- 存在多个、但均由现有需求和接口字段支持的加工解释：选择最可能的一种，登记为 `selected_interpretation`，不得发明新字段或新业务条件。
- 完全没有输入依据的内容：不得新增。
- 只是工程实现建议且不影响加工结果：不写入标准。

Phase2 不得基于本防护栏新增评审标准；本防护栏只用于阻止模型生成额外问题。

---

# Deterministic Output v4

相同输入必须输出相同章节、相同规则顺序和同一套最可能加工逻辑。

## Phase 1 章节顺序

1. `# 标准加工规范`
2. `## 输入解析`
3. `## 因子加工输入字段`
4. `## 字段溯源`
5. `## 过滤条件`
6. `## 列表/数组字段处理`
7. `## 计算逻辑`
8. `## 默认值兜底策略`
9. `## 比率/衍生因子计算`
10. `## 条件分支逻辑`
11. `## 选定解释`

## Phase 2 章节顺序

1. `# 评审报告`
2. `## 输入解析`
3. `## 总体结论`
4. `## 汇总问题清单`
5. `## 待确认项`

规则编号固定使用 D1-D6、D8-D9；D6 之后直接使用 D8、D9。选定解释使用 SEL-001 起连续编号。

不得输出主链“未明确”“待确认”“建议口径”或多套候选方案。必须选择一个最可能且可执行的标准答案。

---

# Selected Interpretation

原始材料存在多个可能实现时，不得停止在“未明确”状态。Phase 1 必须选定最可能的一套可执行口径，并登记：

| 选择编号 | 触发位置 | 原始歧义 | 选定口径 | 选择理由 | 证据锚点 |
|---|---|---|---|---|---|

选择编号使用 `SEL-001`、`SEL-002`。相关标准规则必须引用对应 SEL 编号，且仍统一标记为 `hard`。

如果不存在选定解释，保留表头并在表格后写「无」。
