# 复杂因子加工案例 09：流水对账 + 异常模式识别 + 多账户资金归集链路追踪

## 案例编号
CF-009

## 因子名称
多账户资金归集链路追踪与异常模式因子

## 业务场景
企业集团多账户资金池管理监控：识别跨账户的资金归集/分拨链路、异常资金流向（如资金沉淀、空转、违规挪用）、关联交易对手方聚类，量化资金管理合规风险。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `fundPoolResponse.groupInfo.groupId` | 集团/企业群 ID |
| `fundPoolResponse.groupInfo.memberAccounts[]` | 成员账户列表 |
| `fundPoolResponse.groupInfo.memberAccounts[].accountId` | 账户唯一标识 |
| `fundPoolResponse.groupInfo.memberAccounts[].accountName` | 账户名称 |
| `fundPoolResponse.groupInfo.memberAccounts[].accountType` | 账户类型：基本户/一般户/专用户/临时户 |
| `fundPoolResponse.groupInfo.memberAccounts[].entityId` | 归属主体 ID（关联法人实体） |
| `fundPoolResponse.groupInfo.memberAccounts[].entityName` | 归属主体名称 |
| `fundPoolResponse.groupInfo.memberAccounts[].openBank` | 开户行 |
| `fundPoolResponse.transactions[]` | 全集团账户合并流水（近 90 天） |
| `fundPoolResponse.transactions[].transId` | 交易唯一标识 |
| `fundPoolResponse.transactions[].transTime` | 交易时间戳（毫秒） |
| `fundPoolResponse.transactions[].outAccountId` | 付款账户 ID |
| `fundPoolResponse.transactions[].inAccountId` | 收款账户 ID |
| `fundPoolResponse.transactions[].amount` | 交易金额（元） |
| `fundPoolResponse.transactions[].transType` | 交易类型：转账/汇款/代发/代扣/票据/存取现 |
| `fundPoolResponse.transactions[].counterpartyAccount` | 对手方账号（外部） |
| `fundPoolResponse.transactions[].counterpartyName` | 对手方名称（外部） |
| `fundPoolResponse.transactions[].counterpartyEntityId` | 对手方归属主体 ID（若可识别） |
| `fundPoolResponse.transactions[].purpose` | 交易用途/备注 |
| `fundPoolResponse.transactions[].channel` | 渠道：网银/柜台/大小额/超级网银/第三方 |
| `fundPoolResponse.dailyBalances[]` | 各账户日终余额序列 |
| `fundPoolResponse.dailyBalances[].accountId` | 账户 ID |
| `fundPoolResponse.dailyBalances[].date` | 日期 |
| `fundPoolResponse.dailyBalances[].balance` | 日终余额（元） |
| `configResponse.fundPoolConfig` | 资金池配置：归集阈值、分拨规则、预警参数、合规名单 |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | time_filter | 以 `referenceDate` 为基准，筛选近 90 天流水与余额。将毫秒时间戳转为日期时间（精确到秒）。 |
| 2 | iteration | **构建账户间资金流向有向图**：节点为成员账户，边为账户间转账交易。聚合同方向同账户对交易：`edgeWeight = Σamount`，`edgeCount = COUNT`，`avgAmount = edgeWeight/edgeCount`，`firstTime/lastTime`。计算每日账户净流入 = Σ(入账) - Σ(出账)。 |
| 3 | branch | **识别归集/分拨链路**（核心逻辑）：<br>• **归集链路**：存在"汇聚账户"（基本户/专用户），满足：入账账户数 ≥ 3、入账金额占该账户总入账 > 80%、入账频次高（日均 ≥ 2 笔）、资金快速流出（入账后 24h 内流出 > 90%）。标记为"资金归集账户"。<br>• **分拨链路**：从归集账户流出，流向 ≥ 3 个不同账户、分拨金额占流出 > 80%、分拨时间集中（2 小时内完成 ≥ 80% 金额）。标记为"资金分拨账户"。<br>• **链路完整性**：归集账户 → 分拨账户 形成完整链路，计算链路周转天数 = (分拨时间 - 最早归集时间) / 笔数。 |
| 4 | iteration | **外部关联交易对手聚类**：提取所有外部交易（对手方非集团内账户）。按 `counterpartyEntityId` 聚类（无 ID 则按账号+名称模糊匹配）。计算每簇：<br>• 簇总交易额、笔数、净流向（流入-流出）<br>• 交易账户数（涉及集团内几个账户）<br>• 时间集中度：最大单日金额 / 总金额<br>• 用途一致性：用途关键词 Jaccard 相似度<br>标记"疑似关联交易簇"：涉及账户 ≥ 3 且 时间集中度 > 0.5 且 用途一致性 > 0.7。 |
| 5 | branch | **异常模式识别**：<br>• **资金沉淀**：账户日均余额 > 近 90 日平均日流入 × 30 且 无大额流出（> 余额 10%），持续 ≥ 30 天 → "长期资沉资账户"<br>• **资金空转**：账户间 A→B→C→A 形成闭环，闭环金额相近（差异 < 5%）、时间跨度 < 24h、无外部对手方 → "空转闭环"<br>• **违规挪用**：资金流向非经营范围关联方（对手方非供应商/客户/员工/税务/社保），单笔 > 100 万或 累计 > 500 万 → "疑似违规挪用"<br>• **归集不及时**：基本户/专用户日终余额 < 应归集金额 × 0.3，持续 ≥ 5 个工作日 → "归集延迟"<br>• **大额拆分规避**：同一对手方、同一日、同一账户，拆分为 ≥ 5 笔、单笔 < 50 万、合计 > 200 万 → "拆分规避" |
| 6 | aggregation | **合规风险量化指标**：<br>• 归集账户数 / 总账户数、分拨账户数<br>• 平均归集周转天数、最长归集链路长度<br>• 疑似关联交易簇数、关联交易簇涉及金额占比<br>• 沉资账户数、沉资金额占总余额比<br>• 空转闭环数、空转金额占流水比<br>• 疑似违规挪用笔数/金额、拆分规避笔数/金额、归集延迟天数<br>• 资金池集中度：前 3 大账户余额和 / 总余额（HHI 指数）<br>• 跨行归集占比：跨行归集金额 / 总归集金额 |
| 7 | derivation | **综合合规评分**（0-100，越高越合规）：<br>基础分 100，各异常扣分：沉资 -15、空转 -20、违规挪用 -30、拆分规避 -10、归集延迟 -5/天（上限 -20）、关联交易簇 -8/个（上限 -24）。<br>加分：归集及时率 > 95% +5、无异常模式 +10、账户结构清晰（基本户≤1、一般户≤3）+5。<br>最终评分截断 [0,100]，保留两位小数。 |
| 8 | branch | **风险等级**：≥ 85 优、70-84 良、60-69 中、< 60 差。 |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_009_FUND_POOL_TRACKING",
  "factorName": "多账户资金归集链路追踪与异常模式因子",
  "referenceDate": "2024-12-31",
  "analysisWindowDays": 90,
  "groupId": "GRP_001",
  "accountStats": {"totalAccounts": 12, "basicAccounts": 1, "generalAccounts": 5, "specialAccounts": 4},
  "collectionDispersion": {
    "collectionAccounts": ["ACC_001", "ACC_005"],
    "dispersionAccounts": ["ACC_002", "ACC_003", "ACC_004", "ACC_006"],
    "avgCollectionCycleDays": 1.8,
    "maxChainLength": 3,
    "collectionEfficiency": 0.92
  },
  "externalCounterpartyClusters": {
    "clusterCount": 3,
    "totalAmount": 45000000.00,
    "suspiciousClusters": 1,
    "suspiciousAmount": 18000000.00
  },
  "anomalyPatterns": {
    "idleFundAccounts": 2,
    "idleFundAmount": 8500000.00,
    "idleFundRatio": 0.14,
    "emptyLoopCount": 1,
    "emptyLoopAmount": 3200000.00,
    "suspectedMisuseCount": 0,
    "splitAvoidanceCount": 4,
    "splitAvoidanceAmount": 9800000.00,
    "collectionDelayDays": 3
  },
  "complianceMetrics": {
    "concentrationHHI": 0.42,
    "crossBankCollectionRatio": 0.35,
    "collectionTimelinessRate": 0.94
  },
  "complianceScore": 71.50,
  "riskGrade": "中"
}
```