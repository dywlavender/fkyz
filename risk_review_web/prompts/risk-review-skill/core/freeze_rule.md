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
