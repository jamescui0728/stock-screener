# 系统架构

## 数据流总览

```
┌─────────────────────────────────────────────────────────┐
│                     数据源（外部）                         │
│  akshare → A 股财报 / 行情 / 个股新闻 / 北向资金            │
│  东方财富 JSON API → PMI / CPI / M2 月度宏观数据            │
│  Sina 财经 → 实时不复权报价（模拟盘）                       │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                     data/fetcher.py                      │
│  - _retry：3 次重试 + 90s 超时（宏观）/ 45s（其他）          │
│  - fetch_macro_data：东方财富主源 → akshare fallback        │
│  - fetch_all_price_data：incremental / full / init-missing │
│  - fetch_stock_news + sentiment.py：jieba 分词 + 关键词权重 │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                  SQLite（stock_screener.db）              │
│  industries / stocks / financial_data / price_data /      │
│  macro_data / news_items / watchlist / paper_account /    │
│  paper_positions / paper_transactions / users /           │
│  backtest_runs / backtest_records                          │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   engines/（评分核心）                     │
│  industry_scorer：营收/利润/抗周期/竞争 → 行业评分 0-100   │
│  company_scorer：ROE/利润增长/现金流/财务/估值 → 公司分     │
│  signal_engine：综合分 + 8 道门槛 → 5 等级信号             │
│  paper_trade：账户管理 + 买卖业务 + 持仓估值                │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   FastAPI 路由层                           │
│  api/routes.py：业务 endpoint（公司 / 行业 / 自选 / 模拟盘）│
│  api/auth_routes.py：登录 / 注册 / 用户管理                 │
│  + APScheduler 定时任务（数据更新 / 价格预热）              │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│                   Vue 3 前端                              │
│  views/ 10 个页面 ← axios → REST API                      │
│  Pinia auth store + JWT localStorage                      │
└─────────────────────────────────────────────────────────┘
```

---

## 1. 信号引擎（核心）

### 1.1 综合分计算

```
composite_score = (
    fundamental_100 * W_fund     # 基本面 0-100，权重 0.40
  + valuation_100   * W_val      # 估值 0-100，权重 0.25
  + sentiment_100   * W_sent     # 舆情 0-100，权重 0.15
  + macro_100       * W_macro    # 宏观 0-100，权重 0.20
)                                # 总和恒为 1.0
```

权重在 `config.py` 中可配置，回测优化器（贝叶斯）可自动搜索最优权重组合。

### 1.2 5 等级信号映射

| 等级 | 字符串 | 触发条件 |
|---|---|---|
| 🟢🟢 必买 | `STRONG_BUY` | composite ≥ 80 **且** 8 门槛全过 |
| 🟢 买入 | `BUY` | composite ≥ 64 **且** 8 门槛全过 |
| 🟡 持有 | `HOLD` | 50 ≤ composite < 64，**或** 综合分够但被门槛降级 |
| 🔴 卖出 | `SELL` | 30 ≤ composite < 50 |
| 🔴🔴 必卖 | `STRONG_SELL` | composite < 30 **或** veto 触发（财务造假/退市风险） |

> ⚠️ 阈值非对称：`STRONG_BUY_TH=80` 是"上界达到触发"，`STRONG_SELL_TH=30` 是"下界跌破触发"。

### 1.3 8 道门槛（必买 + 买入共用）

只有**全部通过**才能进入 BUY / STRONG_BUY，否则降级为 HOLD：

| # | 门槛 | 默认阈值 | 设计依据 |
|---|---|---|---|
| 1 | **质量** `quality_gate` | `score_roe_quality ≥ 15`（满分 25） | run#25：composite 60-64 + ROE<15 胜率 36% |
| 2 | **行业** `industry_gate` | 行业评分 ≥ 50 | run#26：行业 <50 胜率 48.4%, ≥50 胜率 70.1% |
| 3 | **宏观** `macro_gate` | macro_pct ≥ 55 | run#28：macro <55 胜率 55%, ≥55 胜率 76% |
| 4 | **估值** `valuation_gate` | val_pct ≥ 70 | run#31：估值偏贵 BUY 胜率显著低 |
| 5 | **金融业宏观** `fin_macro_gate` | 金融股 macro_pct ≥ 65 | run#32：银行对 LPR 利率敏感，需更严宏观环境 |
| 6 | **动量** `momentum_block` | 不在 60 日 -10% ~ 0% 区间 | run#29："接下跌的刀"区间胜率 50% |
| 7 | **veto**（一票否决） | 财务造假 / 退市风险 / 长期亏损等红牌 | 直接判 STRONG_SELL |
| 8 | （隐含）综合分阈值 | composite ≥ 64（BUY）或 ≥ 80（STRONG_BUY） | 同上 |

### 1.4 阈值在哪里改

- 默认值：`backend/config.py`
- 运行时覆盖：`backend/user_settings.json`（前端 `参数设置` 页面热更新会写入这里）
- 单次回测覆盖：`POST /api/backtest/run` 的请求 body 里传 `params: {...}`，覆盖该次回测使用的阈值

---

## 2. 评分体系

### 2.1 公司评分（`engines/company_scorer.py`）

| 维度 | 子分 | 数据来源 |
|---|---|---|
| ROE 质地 | 0-25 | 财报 7 年 ROE 序列：均值 / 稳定性 / 趋势 |
| 利润增长 | 0-15 | 5 年净利润复合增长率 |
| 现金流 | 0-15 | 5 年 FCF/Net Profit ≥ 0.8 比例 |
| 财务健康 | 0-15 | 资产负债率 + 利息保障倍数 |
| 估值（独立） | 0-20 | PE / PB 历史百分位 |
| **基本面合计** | **0-80** | （ROE + 利润增长 + 现金流 + 财务健康） |
| **估值合计** | **0-20** | （独立维度，不计入 fundamental_score） |

### 2.2 行业评分（`engines/industry_scorer.py`）

| 维度 | 子分 | 含义 |
|---|---|---|
| 营收稳定性 | 0-25 | 行业整体营收 5 年标准差 / 均值的反向打分 |
| 利润稳定性 | 0-25 | 同上，对利润 |
| 抗周期性 | 0-25 | 行业利润与 GDP 增速的相关性反向打分 |
| 竞争格局 | 0-25 | 头部 5 家市占率（CR5） |
| **合计** | **0-100** | |

适合长期持有的稳定行业（消费、医药、公用事业）评分高；强周期（钢铁、煤炭、地产）评分低。

### 2.3 宏观评分（`signal_engine.py:score_macro`）

```
基准 = 7.5（满分 15）
+ PMI 加分：(PMI - 50) * 0.6（限 ±3）
+ CPI 加分：1.0-3.0% 区间 +2，>4% 或 <0% -2
+ 北向资金：净流入按 2e9 元为标尺加分（限 ±2.5）
```

`macro_pct = macro_score / 15 * 100`，进入综合分前归一化到 0-100。

### 2.4 舆情评分（`data/sentiment.py`）

- jieba 分词 + 关键词权重词典（正面 / 负面 / 中性）
- 单条新闻 → `sentiment_score: 0-1`，`sentiment_label: positive/neutral/negative`
- 个股近 30 条新闻加权平均 → `0-20` 进入综合分

---

## 3. 模拟盘（`engines/paper_trade.py`）

### 3.1 多账户模型

```
User (1) ─────── (N) PaperAccount
                       │
                       ├── (N) PaperPosition  持仓
                       └── (N) PaperTransaction 流水
```

- 一个用户可有多个 `PaperAccount`（"早期账户"、"低估值蓝筹仓"、"试验仓"等）
- 删除账户时级联删除持仓和流水（`cascade="all, delete-orphan"`）
- 安全限制：每用户至少保留 1 个账户

### 3.2 交易规则

- 手续费：万分之三（买卖均收），最低 5 元
- 最小买入单位：100 股（A 股标准）
- 卖出可全部清仓（不要求 100 整数倍）
- 估值用 Sina 实时不复权价（与真实市场价一致）

### 3.3 实时价缓存（`_PRICE_CACHE`）

```
模块级 dict，跨用户共享
TTL: 10 分钟
预热策略:
  - 启动时:    daemon 线程并发拉所有持仓股（不阻塞 startup）
  - 工作日交易时段（9-11 / 13-15）: 每 8 分钟 cron 刷新
  - 用户手动: POST /paper/cache/warmup（5 秒/user 节流）
```

并行执行用 `concurrent.futures.ThreadPoolExecutor(max_workers=16)`，单只 8s 超时，整体 14s hard cap（防止 sina 抖动拖死请求）。

---

## 4. 数据更新策略

### 4.1 定时任务（APScheduler）

| 任务 | 时间 | 内容 |
|---|---|---|
| `_daily_data_update` | 每日 02:00 | 宏观 + 财报（200 只增量） + 情感分析 + 信号刷新 |
| `_weekday_refresh_watchlist_news` | 工作日 09:00 | 自选股新闻 + 情感分析 |
| `_weekly_rescore` | 周日 03:00 | 行业评分（216 个） |
| `_refresh_paper_price_cache` (am) | 工作日 9-11 每 8 分钟 | 模拟盘持仓实时价 |
| `_refresh_paper_price_cache` (pm) | 工作日 13-15 每 8 分钟 | 同上 |

### 4.2 手动触发（API）

| 端点 | 用途 |
|---|---|
| `POST /api/data/refresh-all` | 一键：宏观 + 行业评分 + 信号刷新 |
| `POST /api/data/update-prices?mode=incremental` | 增量补齐价格（仅已有数据的股票） |
| `POST /api/data/update-prices?mode=init-missing` | 仅给新股做首次全量历史拉取 |
| `POST /api/data/update-prices?mode=full` | 全量（耗时数小时，慎用） |
| `POST /api/data/update-financials?limit=200` | 拉财报 |
| `POST /api/data/update-macro` | 拉宏观（PMI/CPI/M2/北向） |
| `POST /api/signals/refresh-all` | 仅重算信号（不拉数据，约 80s） |
| `POST /api/paper/cache/warmup` | 当前用户持仓的实时价缓存 |

---

## 5. 前端架构

### 5.1 状态管理

- **认证**：`stores/auth.js`（Pinia），JWT 存 `localStorage`
- **页面状态**：每个 view 用 Composition API 自管，少量跨页面状态（如 currentAccountId）放 `localStorage`

### 5.2 路由守卫（`router/index.js`）

```
未登录 + 访问业务页 → 跳 /login
已登录 + 访问 /login → 跳 /industries
访问 /users 但非管理员 → 跳 /industries
```

### 5.3 与后端约定

- 所有 API 路径前缀 `/api`
- axios 自动注入 `Authorization: Bearer <token>`
- 401 响应自动清登录态 + 跳登录页
- 请求 timeout 默认 30s（warmup 单独 60s）

---

## 6. 关键性能优化

### 6.1 N+1 查询消除

- `fetch_all_price_data`：用 GROUP BY 一次拿全部 stock_code 的 max(trade_date)，避免 5196 次单查
- `account_snapshot`：用 IN 一次拿全部持仓的 Stock 行，避免 14 次单查

### 6.2 sina 实时价缓存

- 跨用户共享（同一只股票被 100 个用户持有也只打 1 次 sina）
- 8 分钟 cron 预热 + 启动时预热
- 14s hard cap 防止 sina 抖动拖死请求

### 6.3 fast-skip

`fetch_all_price_data(mode="incremental")` 启动时：
1. 一次 `MAX(trade_date) FROM price_data` 拿全表最新交易日
2. 任何股票如已到这个日期 → skip，免一次 sina 调用

---

## 7. 已知限制 / 设计权衡

| 问题 | 现状 | 影响 |
|---|---|---|
| `_retry` worker 线程不可中断 | timeout 后线程仍在跑，pool 满会 block 新请求 | sina 大面积抖动时偶发卡死，需重启 |
| akshare 宏观接口 2025-08 停更 | 已切到东方财富主源，akshare 仅 fallback | ✓ 已修复 |
| 历史数据混源（同月双值） | dedupe 脚本只删完全同值的，不同口径的保留 | score_macro 用 MAX(date)，正确 |
| 没有自动化测试 | 0 测试 | 重构靠人审 + 冒烟，重大改动需小心 |
| SQLite 单文件 | 单进程读 / 多进程写不安全 | 单机部署 OK，分布式不行 |
