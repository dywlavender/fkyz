# 复杂因子加工案例 06：多频度时间序列重采样 + 技术指标计算 + 信号融合

## 案例编号
CF-006

## 因子名称
企业经营现金流多频度技术特征因子

## 业务场景
中小企业授信中，需对企业近 2 年日级/周级/月级现金流序列进行多频度重采样，计算技术分析指标（趋势、波动、动量、周期性），融合多频度信号输出经营稳定性评分。

## 因子加工输入字段

| 字段完整路径 | 加工用途 |
|---|---|
| `cashFlowResponse.dailyFlows[]` | 日级现金流明细数组（近 730 天） |
| `cashFlowResponse.dailyFlows[].date` | 日期（YYYY-MM-DD） |
| `cashFlowResponse.dailyFlows[].inflow` | 当日流入总额（元） |
| `cashFlowResponse.dailyFlows[].outflow` | 当日流出总额（元） |
| `cashFlowResponse.dailyFlows[].netFlow` | 当日净流入（元）= inflow - outflow |
| `cashFlowResponse.dailyFlows[].txnCount` | 当日交易笔数 |
| `cashFlowResponse.dailyFlows[].maxSingleInflow` | 当日最大单笔流入 |
| `cashFlowResponse.dailyFlows[].maxSingleOutflow` | 当日最大单笔流出 |
| `cashFlowResponse.accountInfo.openDate` | 账户开户日期 |
| `cashFlowResponse.accountInfo.currency` | 账户币种（仅支持 CNY） |
| `configResponse.techIndicatorsConfig` | 技术指标参数配置（周期、阈值、权重） |

## 标准加工逻辑

| 步骤 | 逻辑类型 | 标准逻辑 |
|---|---|---|
| 1 | time_filter | 以 `referenceDate` 为基准，筛选近 730 天（2 年）数据。补全缺失交易日：若某日期无记录，插入 inflow=0、outflow=0、netFlow=0、txnCount=0 的记录。确保序列连续无间隙。 |
| 2 | derivation | **多频度重采样**（三个频度并行）：<br>• **周频（周五收盘）**：按 ISO 周聚合，周净流入 = Σ日净流入，周流入/流出 = Σ对应值，周笔数 = Σ笔数，周最大单笔 = MAX(日最大单笔)<br>• **月频（月末）**：按自然月聚合，同理计算月度指标<br>• **季频（季末）**：按自然季度聚合，同理计算季度指标<br>所有聚合金额保留两位小数，笔数为整数。 |
| 3 | derivation | **日频技术指标**（基于日级 netFlow 序列）：<br>• MA5/MA20/MA60：5/20/60 日简单移动平均<br>• EMA12/EMA26：12/26 日指数移动平均，α=2/(N+1)<br>• MACD：DIF=EMA12-EMA26，DEA=DIF 的 9 日 EMA，MACD=2×(DIF-DEA)<br>• 布林带：中轨=MA20，上轨=MA20+2×σ20，下轨=MA20-2×σ20，带宽=(上轨-下轨)/中轨<br>• RSI14：14 日相对强弱指标，基于净流入正/负变化<br>• 动量 MOM10 = 当日净流入 - 10 日前净流入<br>• 波动率 STD20 = 20 日净流入标准差<br>• 连续正/负天数：当前连续净流入>0 或 <0 的天数 |
| 4 | derivation | **周频技术指标**（基于周级 netFlow）：<br>• 周 MA4/MA12/MA26、周 MACD(12,26,9)、周 RSI14<br>• 周趋势斜率：最近 12 周线性回归斜率（元/周）<br>• 周波动率：12 周滚动标准差<br>• 周周期性：自相关系数 ACF(13) 检测年度周期 |
| 5 | derivation | **月频技术指标**（基于月级 netFlow）：<br>• 月 MA3/MA6/MA12、月 MACD(6,12,9)、月 RSI14<br>• 月趋势斜率：最近 12 月线性回归斜率<br>• 月度季节性分解：STL 分解提取趋势/季节/残差，季节性强度 = 季节成分方差/总方差 |
| 6 | aggregation | **多频度信号融合评分**：<br>每频度产出独立评分（0-100）：<br>• 趋势得分：MA 多头排列(短>中>长) + MACD 金叉 + 斜率>0 → 加分；空头排列+死叉+斜率<0 → 扣分<br>• 动量得分：RSI ∈ [40,60] 得高分，>70 或 <30 扣分；MOM 为正加分<br>• 波动得分：带宽/波动率越小越稳定，分位数映射<br>• 周期得分：季节性强度高且规律 → 加分<br>• 连续性得分：连续正流入天数/周数/月数越长越好<br>三频度加权融合：日频 0.3、周频 0.4、月频 0.3 → 综合技术评分（保留两位小数） |
| 7 | branch | **异常状态标识**：<br>• 日频：连续 10 日净流出且累计流出 > 近 60 日平均日流入 × 10 → "持续大额流出"<br>• 周频：MACD 连续 4 周死叉且 DIF < 0 → "周趋势确认下行"<br>• 月频：月净流入创 12 个月新低且趋势斜率 < -50000 → "月度结构性恶化"<br>任一触发 → 综合评分 × 0.7 惩罚 |
| 8 | derivation | **最终输出指标**：综合技术评分、各频度分项评分、关键技术指标当前值、异常标识、稳定性等级（≥80 优、60-79 良、40-59 中、<40 差）。 |

## 默认值兜底
因子加工输入字段缺失、格式错误或数据异常导致无法完成正常加工时，返回配置的默认值。

---

## 预期输出字段示例

```json
{
  "factorCode": "CF_006_MULTIFREQ_TECH_FEATURES",
  "factorName": "企业经营现金流多频度技术特征因子",
  "referenceDate": "2024-12-31",
  "dataCoverageDays": 728,
  "dailyIndicators": {
    "ma5": 125000.00,
    "ma20": 118000.00,
    "ma60": 110000.00,
    "macd": {"dif": 8500.00, "dea": 6200.00, "macd": 4600.00},
    "bollinger": {"upper": 185000.00, "middle": 118000.00, "lower": 51000.00, "width": 1.1356},
    "rsi14": 58.45,
    "mom10": 12000.00,
    "std20": 45000.00,
    "consecutivePositiveDays": 7
  },
  "weeklyIndicators": {
    "trendSlope": 25000.00,
    "volatility": 180000.00,
    "seasonalityACF": 0.23,
    "score": 72.50
  },
  "monthlyIndicators": {
    "trendSlope": 85000.00,
    "seasonalityStrength": 0.34,
    "score": 78.20
  },
  "frequencyScores": {"daily": 68.30, "weekly": 72.50, "monthly": 78.20},
  "compositeScore": 73.41,
  "anomalyFlags": ["持续大额流出"],
  "stabilityGrade": "良"
}
```