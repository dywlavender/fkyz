# 复杂因子加工案例 04：时序模式识别 + 滑动窗口异常检测 + 状态机分析

## 案例编号
CF-004

## 因子名称
账户交易行为时序模式异常识别因子

## 业务场景
反洗钱/反欺诈场景，需对账户近 180 天的交易时序进行模式识别：识别拆分交易、倒账循环、异常时段集中、金额阶梯试探等可疑模式，基于有限状态机分析交易序列状态转移，输出多维风险评分。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `amlResponse.account.transactions[]` | 交易流水数组，按 transTime 升序排列 |
| `amlResponse.account.transactions[].transId` | 交易唯一标识 |
| `amlResponse.account.transactions[].transTime` | 交易时间戳（毫秒级精度） |
| `amlResponse.account.transactions[].amount` | 交易金额（元，两位小数） |
| `amlResponse.account.transactions[].transType` | 交易类型（转入/转出/消费/提现/还款/退款） |
| `amlResponse.account.transactions[].counterpartyAccount` | 对手方账号（脱敏后） |
| `amlResponse.account.transactions[].counterpartyName` | 对手方姓名（脱敏后） |
| `amlResponse.account.transactions[].channel` | 交易渠道 |
| `amlResponse.account.transactions[].location` | 交易地理位置（省/市） |
| `amlResponse.account.transactions[].deviceId` | 交易设备指纹 |
| `amlResponse.account.transactions[].riskTags[]` | 上游风控系统打标标签数组 |
| `amlResponse.account.accountOpenDate` | 账户开户日期 |
| `amlResponse.account.kycInfo.riskLevel` | KYC 风险等级（低/中/高） |
| `amlConfigResponse.patternRules` | 模式识别规则配置（含阈值、时间窗、金额比例等） |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | time_filter | 以 `referenceDate` 为基准，筛选近 180 天交易：`transTime ∈ [referenceDate - 180 days, referenceDate]`。将毫秒时间戳转为日期时间（精确到秒）。 |
| 2 | iteration | 构建交易时序序列：按 `transTime` 升序排列，为每笔交易计算特征向量：<br>• `timeSinceLastTx` = 与上一笔交易时间差（秒），首笔为 null<br>• `amountRatio` = 当前金额 / 上一笔金额，首笔或上一笔为 0 时为 null<br>• `sameCounterparty` = 是否与上一笔同一对手方<br>• `sameChannel` = 是否同一渠道<br>• `sameLocation` = 是否同一地点<br>• `sameDevice` = 是否同一设备<br>• `hourOfDay` = 交易小时（0-23）<br>• `isNight` = hourOfDay ∈ [22, 23] ∪ [0, 5] |
| 3 | branch | **模式 1：拆分交易识别**（滑动窗口 10 分钟）<br>遍历序列，以每笔交易为窗口起点，向前扩展 10 分钟窗口：<br>• 窗口内转出交易笔数 ≥ 5 且 Σ金额 ≥ 50000 且 最大金额 / 最小金额 ≤ 1.5 → 标记为"疑似拆分转出"<br>• 窗口内转入交易笔数 ≥ 5 且 Σ金额 ≥ 50000 且 最大金额 / 最小金额 ≤ 1.5 → 标记为"疑似拆分转入"<br>• 同一对手方在窗口内出现 ≥ 3 次 → 权重 × 1.5 |
| 4 | branch | **模式 2：倒账循环识别**（配对匹配）<br>遍历转出交易，寻找 30 分钟内同一对手方的转入交易：<br>• 金额差异 ≤ 1% 且 方向相反 → 形成"疑似倒账对"<br>• 同一对手方形成 ≥ 3 个倒账对 → 标记为"疑似倒账循环"<br>• 循环链路长度 ≥ 4（A→B→C→D→A）→ 标记为"复杂倒账链" |
| 5 | branch | **模式 3：异常时段集中**（夜间交易占比）<br>计算：夜间交易笔数 / 总笔数、夜间交易金额 / 总金额。<br>• 夜间笔数占比 > 0.4 且 夜间金额占比 > 0.5 → "高度夜间集中"<br>• 夜间笔数占比 > 0.25 → "中度夜间集中" |
| 6 | branch | **模式 4：金额阶梯试探**（单向递增/递减序列）<br>提取同方向（均为转出或均为转入）连续子序列，检测：<br>• 连续 ≥ 5 笔，金额单调递增且每笔增幅 ∈ [5%, 20%] → "阶梯试探递增"<br>• 连续 ≥ 5 笔，金额单调递减且每笔降幅 ∈ [5%, 20%] → "阶梯试探递减"<br>• 首笔金额 < 1000 且 末笔金额 > 50000 → 权重 × 2 |
| 7 | branch | **模式 5：设备/地点异常切换**（状态机分析）<br>构建状态机：状态 = {设备指纹, 地点}，转移 = 相邻交易状态变化。<br>• 1 小时内设备切换 ≥ 3 次 → "高频设备切换"<br>• 1 小时内地点跨省 ≥ 2 次 → "异地跳跃"<br>• 新设备首现即大额（> 50000） → "新设备大额首现" |
| 8 | aggregation | 多维风险评分汇总：<br>• 拆分交易得分 = 疑似拆分笔数 × 2 + 疑似拆分金额 / 10000（上限 30）<br>• 倒账循环得分 = 倒账对数 × 3 + 复杂链路数 × 10（上限 25）<br>• 夜间集中得分 = 夜间笔数占比 × 40（上限 20）<br>• 阶梯试探得分 = 递增序列数 × 5 + 递减序列数 × 5（上限 15）<br>• 设备异常得分 = 设备切换次数 × 2 + 异地跳跃次数 × 5 + 新设备大额次数 × 8（上限 10）<br>• KYC 基础分 = 低=0、中=5、高=15<br>• 总分 = Σ各维度得分（上限 100，保留两位小数） |
| 9 | branch | 风险等级判定：≥ 70 → "高风险"、50-69 → "中风险"、30-49 → "低风险"、< 30 → "正常"。 |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_004_TEMPORAL_PATTERN_ANOMALY",
  "factorName": "账户交易行为时序模式异常识别因子",
  "referenceDate": "2024-12-31",
  "analysisWindowDays": 180,
  "totalTransactions": 342,
  "patternsDetected": {
    "splitTransactions": {"outCount": 12, "inCount": 8, "amount": 680000, "score": 22.40},
    "washTradeLoops": {"pairCount": 5, "complexChainCount": 1, "score": 25.00},
    "nightConcentration": {"txnRatio": 0.45, "amountRatio": 0.58, "level": "高度夜间集中", "score": 18.00},
    "ladderProbing": {"increasingSeq": 2, "decreasingSeq": 1, "score": 15.00},
    "deviceAnomaly": {"deviceSwitches": 4, "geoJumps": 2, "newDeviceLarge": 1, "score": 10.00}
  },
  "kycBaseScore": 5.00,
  "totalScore": 95.40,
  "riskLevel": "高风险",
  "topRiskFactors": ["倒账循环", "拆分交易", "夜间集中"]
}
```