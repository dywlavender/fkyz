# 复杂因子加工案例 01：多维时间窗口交叉聚合因子

## 案例编号
CF-001

## 因子名称
近 3/6/12 个月多维交叉聚合资金流向因子

## 业务场景
信贷审批中评估企业近 3/6/12 个月的资金流向健康度，需同时计算多时间窗口下的流入/流出/净流入聚合指标，并进行跨窗口趋势对比。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `bankStatementResponse.accounts[].accountId` | 账户唯一标识，用于关联主账户与关联账户 |
| `bankStatementResponse.accounts[].accountType` | 账户类型（主账户/关联账户），用于筛选主账户交易 |
| `bankStatementResponse.accounts[].transactions[]` | 交易明细集合，包含所有流水记录 |
| `bankStatementResponse.accounts[].transactions[].transId` | 交易唯一标识，用于去重 |
| `bankStatementResponse.accounts[].transactions[].transTime` | 交易时间戳（毫秒级），用于时间窗口筛选 |
| `bankStatementResponse.accounts[].transactions[].transType` | 交易类型（收入/支出/转账/退款），用于分类聚合 |
| `bankStatementResponse.accounts[].transactions[].amount` | 交易金额（元，保留两位小数），聚合计算核心字段 |
| `bankStatementResponse.accounts[].transactions[].counterpartyType` | 对手方类型（企业/个人/银行/平台），用于对手方维度聚合 |
| `bankStatementResponse.accounts[].transactions[].channel` | 交易渠道（网银/柜台/第三方/内部），用于渠道维度聚合 |
| `bankStatementResponse.accounts[].transactions[].status` | 交易状态（成功/失败/处理中/退回），仅统计成功交易 |
| `bankStatementResponse.referenceDate` | 参考日期（日期字符串 YYYY-MM-DD），作为时间窗口的参照基准日 |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | time_filter | 以 `bankStatementResponse.referenceDate` 为基准日，构建三个时间窗口：近 3 个月 [referenceDate - 3 months, referenceDate]、近 6 个月 [referenceDate - 6 months, referenceDate]、近 12 个月 [referenceDate - 12 months, referenceDate]；时间边界包含起止日。将 `bankStatementResponse.accounts[].transactions[].transTime` 从毫秒时间戳转换为日期（YYYY-MM-DD），仅保留落在任一窗口内的交易记录。 |
| 2 | condition_filter | 从步骤 1 筛选结果中，仅保留 `status == "成功"` 且 `accountType == "主账户"` 的交易记录。 |
| 3 | iteration | 遍历步骤 2 筛选后的交易明细集合，对每条记录执行：提取 `transTime` 所属窗口标识（3M/6M/12M，一条记录可同时属于多个窗口）、`transType` 分类标识（收入/支出/转账/退款）、`counterpartyType`、 `channel`。 |
| 4 | aggregation | 按窗口标识 × 交易类型 × 对手方类型 × 渠道 四维分组聚合：计算各组 `amount` 总和（SUM）、笔数（COUNT）、最大单笔（MAX）、最小单笔（MIN）、平均单笔（AVG，保留两位小数，四舍五入）。同时计算各窗口下收入总和、支出总和、净流入（收入 - 支出）。 |
| 5 | derivation | 计算跨窗口趋势指标：<br>• 3M/6M 净流入环比 = (3M净流入 - 6M前3M净流入) / |6M前3M净流入|，6M前3M净流入 = 6M净流入 - 3M净流入，分母为 0 时返回默认值；<br>• 6M/12M 收入增长率 = (6M收入 - 12M前6M收入) / |12M前6M收入|，12M前6M收入 = 12M收入 - 6M收入，分母为 0 时返回默认值；<br>• 单笔均值漂移率 = (3M平均单笔 - 12M平均单笔) / |12M平均单笔|，分母为 0 时返回默认值。<br>所有比率保留四位小数，四舍五入。 |
| 6 | branch | 若任一时间窗口下的成功交易笔数为 0，则该窗口下所有聚合指标及衍生指标均返回默认值。若 12M 窗口收入总和 ≤ 0，则所有增长率指标返回默认值。 |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_001_MULTI_WINDOW_CROSS_AGG",
  "factorName": "近3/6/12个月多维交叉聚合资金流向因子",
  "referenceDate": "2024-12-31",
  "windows": {
    "3M": {
      "incomeSum": 1250000.00,
      "expenseSum": 980000.00,
      "netFlow": 270000.00,
      "incomeCount": 45,
      "expenseCount": 120,
      "maxSingle": 150000.00,
      "minSingle": 10.00,
      "avgSingle": 27777.78,
      "byCounterparty": {"企业": 800000, "个人": 300000, "银行": 150000},
      "byChannel": {"网银": 900000, "第三方": 350000}
    },
    "6M": {
      "incomeSum": 2400000.00,
      "expenseSum": 1950000.00,
      "netFlow": 450000.00,
      "incomeCount": 88,
      "expenseCount": 235,
      "maxSingle": 200000.00,
      "minSingle": 5.00,
      "avgSingle": 27272.73
    },
    "12M": {
      "incomeSum": 4500000.00,
      "expenseSum": 3800000.00,
      "netFlow": 700000.00,
      "incomeCount": 165,
      "expenseCount": 450,
      "maxSingle": 300000.00,
      "minSingle": 2.00,
      "avgSingle": 27272.73
    }
  },
  "trendIndicators": {
    "netFlow_MoM_3M_vs_6M_3M": 0.1250,
    "income_YoY_6M_vs_12M_6M": 0.0833,
    "avgSingle_Drift_3M_vs_12M": 0.0185
  }
}
```