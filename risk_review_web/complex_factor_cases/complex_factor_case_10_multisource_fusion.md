# 复杂因子加工案例 10：多源异构数据融合 + 实体对齐 + 动态权重自适应评分

## 案例编号
CF-010

## 因子名称
企业全景画像多源自适应融合评分因子

## 业务场景
企业授信全流程决策：融合工商、税务、银行、征信、司法、电商、社保、海关、知识产权、环保、社交舆情 11 大类异构数据源，通过实体对齐、数据质量评估、动态权重自适应、冲突证据消解，输出统一、可解释、鲁棒的综合信用评分。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `fusionResponse.entityAlignment` | 实体对齐结果：主体 ID 映射表、置信度、冲突标记 |
| `fusionResponse.entityAlignment.mappings[]` | 映射数组：sourceId、targetId、sourceType、targetType、confidence、conflictFlags[] |
| `fusionResponse.sources.*.rawData` | 各数据源原始数据（结构不一，含结构化/半结构化/非结构化） |
| `fusionResponse.sources.*.metadata` | 数据源元数据：采集时间、覆盖范围、已知质量问题、更新频率 |
| `fusionResponse.sources.*.qualityMetrics` | 数据质量指标：完整性、一致性、时效性、准确性评分（0-1） |
| `configResponse.fusionConfig` | 融合配置：实体对齐阈值、质量权重、动态权重学习参数、冲突消解规则 |
| `configResponse.dimensionWeights` | 11 大维度基础权重（和为 1，作为先验） |
| `configResponse.indicatorRegistry` | 指标注册表：每个指标的定义、数据源、计算口径、业务方向（正/负/中性）、缺失处理策略 |
| `industryBenchmarkResponse.industryProfiles` | 行业画像基准：各行业各维度分布统计、相关性矩阵 |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | iteration | **实体对齐与冲突检测**：遍历 `entityAlignment.mappings[]`，按 `confidence` 分层：≥0.9 高置信自动合并、0.7-0.9 待人工复核、<0.7 丢弃。对高置信映射：合并同一实体的多源数据，记录字段级冲突（同字段多源取值不一致）。冲突字段标记 `conflictFlags`。 |
| 2 | derivation | **数据源质量评分计算**：对每个数据源，计算综合质量分：<br>`sourceQuality = 0.3×完整性 + 0.25×一致性 + 0.25×时效性 + 0.2×准确性`<br>时效性按（采集时间 - referenceDate）天数衰减：≤7天=1.0、≤30天=0.8、≤90天=0.6、>90天=0.3。<br>质量分作为该源后续权重的基础乘子。 |
| 3 | iteration | **指标级提取与标准化**（按 `indicatorRegistry` 并行）：<br>• 从各源 `rawData` 按指标定义提取原始值，处理单位差异（万元/元/分、百分比/基点、绝对值/同比/环比）<br>• 缺失值处理：按注册表策略——行业中位数插补/均值插补/前向填充/标记缺失/剔除<br>• 异常值 Winsorize：按行业分布 P1/P99 截尾<br>• 方向统一：负向指标取反（如负债率→偿债能力=1-负债率）<br>• 分位数标准化：按行业基准映射到 [0,100]，同案例 07 逻辑 |
| 4 | derivation | **动态权重自适应学习**（EM 算法思想简化版）：<br>• 初始权重 = `dimensionWeights` × 对应维度平均源质量分<br>• 迭代 3 轮：<br>  1) 计算当前加权综合分<br>  2) 计算各指标与综合分的秩相关（Spearman）<br>  3) 相关性显著（p<0.05）且方向一致的指标权重 × 1.1，不显著或方向相反 × 0.9<br>  4) 权重重新归一化到和为 1<br>• 收敛条件：权重变化 L1 范数 < 0.01 或达最大迭代数<br>• 最终动态权重 = 归一化后权重，保留四位小数 |
| 5 | branch | **冲突证据消解**（针对步骤 1 标记的冲突字段）：<br>• 同一指标多源取值冲突：按源质量分加权平均，权重 = sourceQuality × 动态权重<br>• 语义冲突（如：税务显示正常经营、电商显示店铺关闭）：引入冲突消解规则表，按业务优先级裁决（税务>银行>征信>工商>电商>社保>海关>知识产权>环保>舆情），低优先级来源该指标权重 × 0.5 |
| 6 | aggregation | **维度级聚合**：11 个维度内，按动态权重加权平均得维度得分（0-100，保留两位小数）。计算维度内指标一致性：标准差 / 平均值，一致性低（CV>0.5）标记"维度内部离散"。 |
| 7 | derivation | **综合评分与不确定性量化**：<br>• 综合评分 = Σ(维度得分 × 维度动态权重)，保留两位小数<br>• 评分不确定度 = Σ(维度动态权重 × 维度内部离散度 × 源质量倒数)，保留四位小数（越小越确定）<br>• 有效维度数 = COUNT(维度指标数 ≥ 3 且 维度得分非空) |
| 8 | branch | **专家规则硬约束（最高优先级，不可学习覆盖）**：<br>• 一票否决：任一维度触发否决规则（如司法失信、税务 D 级、征信恶意逃废债、环保重大违法、海关高风险） → 最终评分 = 0，等级 "E-拒绝"<br>• 硬性上限：若核心维度（财务/税务/银行/征信）任一得分 < 30 → 综合评分上限 = 60<br>• 硬性下限：若核心维度全部 ≥ 70 且 无否决 → 综合评分下限 = 55 |
| 9 | branch | **最终等级映射**（含不确定度调整）：<br>调整后评分 = 综合评分 - 不确定度 × 10（惩罚不确定性）<br>• ≥ 80 → "AAA-优质"<br>• 70-79 → "AA-良好"<br>• 60-69 → "A-合格"<br>• 50-59 → "BBB-关注"<br>• 40-49 → "BB-预警"<br>• < 40 → "B-高风险"<br>• 否决触发 → "E-拒绝" |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_010_MULTISOURCE_ADAPTIVE_FUSION",
  "factorName": "企业全景画像多源自适应融合评分因子",
  "referenceDate": "2024-12-31",
  "enterpriseId": "ENT_001234",
  "entityAlignment": {
    "totalMappings": 47,
    "highConfidence": 42,
    "conflictsDetected": 8,
    "conflictsResolved": 8
  },
  "sourceQuality": {
    "tax": 0.95,
    "banking": 0.88,
    "credit": 0.92,
    "judicial": 0.90,
    "businessReg": 0.96,
    "ecommerce": 0.72,
    "socialSecurity": 0.85,
    "customs": 0.78,
    "ip": 0.65,
    "environmental": 0.70,
    "sentiment": 0.55
  },
  "dynamicWeights": {
    "tax": 0.1842,
    "banking": 0.1625,
    "credit": 0.1487,
    "judicial": 0.1123,
    "businessReg": 0.1056,
    "financial": 0.0984,
    "socialSecurity": 0.0732,
    "customs": 0.0451,
    "ecommerce": 0.0345,
    "ip": 0.0218,
    "environmental": 0.0137
  },
  "dimensionScores": {
    "tax": {"score": 92.5, "indicators": 6, "consistency": 0.08},
    "banking": {"score": 78.3, "indicators": 8, "consistency": 0.15},
    "credit": {"score": 85.0, "indicators": 6, "consistency": 0.11},
    "judicial": {"score": 88.0, "indicators": 5, "consistency": 0.05},
    "businessReg": {"score": 71.2, "indicators": 7, "consistency": 0.22},
    "financial": {"score": 65.8, "indicators": 10, "consistency": 0.18},
    "socialSecurity": {"score": 81.5, "indicators": 6, "consistency": 0.12},
    "customs": {"score": 69.0, "indicators": 4, "consistency": 0.25},
    "ecommerce": {"score": 73.0, "indicators": 7, "consistency": 0.31},
    "ip": {"score": 58.0, "indicators": 5, "consistency": 0.40},
    "environmental": {"score": 95.0, "indicators": 3, "consistency": 0.03}
  },
  "compositeScore": 81.37,
  "uncertainty": 0.0234,
  "adjustedScore": 81.14,
  "effectiveDimensions": 11,
  "hardConstraints": {"vetoTriggered": false, "coreFloorActive": true, "coreCeilingActive": false},
  "finalGrade": "AAA-优质",
  "explainability": {
    "topPositiveDrivers": ["税务合规优", "征信良好", "司法无风险", "环保优秀"],
    "topNegativeDrivers": ["财务杠杆偏高", "工商经营异常历史", "知识产权布局弱"],
    "conflictResolutions": [
      {"indicator": "经营状态", "sources": ["tax:正常", "ecommerce:停业"], "resolution": "采信税务，电商权重减半"}
    ]
  }
}
```