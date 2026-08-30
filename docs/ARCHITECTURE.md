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
│  signal_engine：综合分 + 8 道门槛 → 5 等级长期信号          │
│  short_signal_engine：8 维反转打分 → 5 等级短期信号        │
│  price_position：上市以来总收益分位（描述性指标）           │
│  paper_trade：账户管理 + 买卖业务 + 持仓估值                │
│  auto_follow：短期信号 → 系统账户自动买卖                   │
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

系统里**并行跑着两套互不干扰的信号**，各自有独立的评分维度、权重和阈值，写在
`stocks` 表的不同列上：

| | 长期信号 | 短期信号 |
|---|---|---|
| 引擎 | `engines/signal_engine.py` | `engines/short_signal_engine.py` |
| 落库列 | `signal` / `composite_score` | `short_signal` / `short_composite_score` |
| 持有周期 | 6-12 个月 | 1-2 周（波段） |
| 思路 | 价值投资：好公司 + 便宜 | 反转：超卖反弹 |
| 章节 | §1.1 - §1.4 | §1.5 |

### 1.1 综合分计算（长期）

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

### 1.5 短期信号引擎（v200 → v202g）

`engines/short_signal_engine.py`，全项目最大的单文件。与长期信号完全独立：独立权重、
独立阈值、独立回测窗口，互不读写对方的列。

#### 1.5.1 为什么是"反转"而不是"追涨"

v200 最初是追涨模型，回测 **IC = -0.114**（评分越高表现越差）。v201 直接把方向翻过来：

- **动量**：跌且超卖加分，涨且过热扣分
- **量价**：涨且放量 = 顶部出货扣分，跌且放量 = 恐慌底加分（诊断 IC = -0.122，最强反向项）
- **科技板块**：v200 的"白名单 +40 起步"是负 IC 主因，v201 拿掉，改为行业评分直接线性映射
- **宏观**：诊断里唯一正 IC 的维度（BUY 子集 +0.116）→ 大幅加权

#### 1.5.2 8 个维度与当前权重

权重全在 `config.py` 的 `SHORT_*_WEIGHT`，启动时 `_validate_weights()` 校验总和 = 1.0
（不等于 1.0 只打 warning，不阻断启动）。

| 维度 | 权重 | 打分逻辑 |
|---|---|---|
| 量价 `volprice` | **0.40** | 涨放量扣分 / 跌放量加分 / 涨缩量扣分 / 跌缩量加分 |
| 宏观 `macro` | **0.30** | 复用 `signal_engine.score_macro()`，归一化到 0-100 |
| 行业相对 `industry_relative` | **0.25** | 跑输同行业越多越加分（剥离系统性 + 行业 beta 的反转） |
| 科技板块 `tech` | **0.05** | 行业评分线性映射；白名单 `TECH_INDUSTRIES` 仅作标记不再加分 |
| 动量 `momentum` | 0.00 | 5/20/60 日收益、相对 MA20/60、RSI(14) |
| 舆情 `news_heat` | 0.00 | 热度激增 × 事件加权情感 + 板块联动 + 强负面事件 veto |
| 换手率 `turnover` | 0.00 | 激增倍数（5日均/60日均）+ 极值水平（>25% 过热 / <0.5% 流动性差） |
| 定价权 `pricing_power` | 0.00 | 毛利率水平/稳定性/趋势 vs 行业均值 |

> **`momentum` 权重为 0 但仍在算**：它产出的 `ret_5d` 被 5 日急跌 veto 用到（见 1.5.4）。
> 权重为 0 且价格数据不足时用中性占位（`_neutral_momentum()`，`ret_5d=None`），
> 此时 veto 不触发 —— 这是有意行为：拿不到价格就无法验证是否急跌，不应拦截。

> **`pricing_power` 权重为 0 时整段跳过**（不只是乘 0）。它按股查 `FinancialData`，
> 跳过后回测从 ~80min 回到 ~13min。0 权重的 hook 保留供未来重设计。

#### 1.5.3 观察模式（observe mode）

`news_heat` 和 `turnover` 权重虽为 0，但由 `SHORT_NEWS_OBSERVE` / `SHORT_TURNOVER_OBSERVE`
（默认都是 `True`）控制**照常计算并写库**，只是乘 0 不影响买卖决策。

这是给新维度转正准备的：`scripts/forward_test_check.py` 每周一 04:00 记快照积累样本，
等前向测试证明该维度与未来收益正相关，再从权重池里给它匀出实权重。

回测路径显式传 `_skip_news_observe=True` / `_skip_turnover_observe=True` 跳过，
避免每只 × 每检查日多跑一堆不影响结果的查询。

#### 1.5.4 5 等级判定（`classify_short_signal`）

判定顺序**有先后**，第一条是硬 veto，跑在所有阈值之前：

```
1. ret_5d ≤ -10%              → STRONG_SELL   （急跌 veto，直接返回）
2. composite ≥ 73             → STRONG_BUY
3. composite ≥ 72.5           → BUY
4. composite ≥ 38             → HOLD
5. composite ≥ 28             → SELL
6. 其余                        → STRONG_SELL
7. 若 2/3 命中但大盘趋势不通过 → 降级为 HOLD
```

> ⚠️ 配置项命名与含义有错位，改之前先看清：`SHORT_SELL_THRESHOLD = 38` 实际是
> **HOLD 的下界**，`SHORT_STRONG_SELL_THRESHOLD = 28` 实际是 **SELL 的下界**。
>
> ⚠️ `STRONG_BUY`(73) 与 `BUY`(72.5) 只差 0.5 分，这是 run 66 精度分析校准的结果
> （≥72.5 胜率 86.7%，≥73 胜率 89%+），不是笔误。

#### 1.5.5 大盘趋势门槛（fail-closed）

`score_market_trend()` 看沪深300（`price_data` 里的 `IDX_000300`）的 20 日涨幅：

- 涨幅 ≥ `SHORT_MARKET_TREND_MIN_20D`（当前 **2.4%**）才放行 BUY / STRONG_BUY
- **基准 K 线不足 21 根时返回 `pass=False`** —— fail-closed，宁可漏也不在弱市里接刀
- 这个维度**不进 composite**，只作为 BUY 的风控闸门

> 2.4% 是 2026-05 手动下调的值（为了放更多候选进来），**低于**原先 3.0% 那个有回测
> 支撑的数值（run#44：沪深300 20 日涨幅 ≥3% 时 BUY 胜率 85.7%, n=63）。
> 改回或再调之前建议重跑短期回测确认胜率没劣化。

#### 1.5.6 观察候选桶（watch）

被大盘趋势降级成 HOLD、但 composite 本身够 BUY 线的高分票，不该在前端消失。
`GET /api/stocks?short_signal_group=watch` 把它们单独捞出来：

```
short_signal == "HOLD"
  且 short_composite_score >= SHORT_BUY_THRESHOLD
  且 reason 里含 "市场短线趋势未达反转买入门槛"
```

`watch` / `candidate` / `trend_blocked` 三个值等价，都走同一个过滤器。

#### 1.5.7 截面排名（默认关闭）

`SHORT_USE_CROSS_SECTIONAL_RANKS = False`。v203 实验过把原始分换成截面排名，结果
IC 从 0.108 **降到** 0.069，桶 5 超额从 +1.56% 降到 +0.92%。原因是当前的 raw scoring
已经精调过非线性奖励，把"极端事件给极端分"拉平成均匀分布反而丢了 alpha 来源。

框架代码保留，未来若加入正交因子（盈利惊喜 / 内幕交易 / 期权偏度等）可重新打开。

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

**行业分类的来源**：`data/fetcher.py::fetch_industries` 调 `ak.stock_board_industry_name_em`，
即**东方财富行业板块**（代码形如 `BK0475`），不是申万分类。`industries` 表里约 500 行，
其中约 216 行有评分 —— 数据不足的行业 `total_score` 留 `None`，不参与排序和门槛。

> ⚠️ **待财务/维护者确认**：`fetch_industries` 的 docstring 写的是"从申万行业分类拉取
> 一级行业列表"，与它实际调用的东财接口对不上。本文档按**代码实际行为**记录。
> 如果当初的意图确实是用申万分类，那这是 `fetcher.py` 的 bug 而不是文档的问题，
> 需要单独改代码 —— 本轮文档更新没有动 `fetcher.py`。

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

### 3.4 自动跟单（`engines/auto_follow.py`，v202g）

把短期信号接到模拟盘上，用真实的手续费和成交价长期记录策略表现 —— 不是回测，是前向验证。

**账户**：系统级 `PaperAccount`（`user_id IS NULL`），名为 `v202g 自动跟单`，初始 100 万。
所有交易在 `PaperTransaction.note` 上打 `v202g-auto` 标签便于绩效分析。

**每天 cron 在短期信号刷新之后跑一次**，两步：

```
Step 1 卖出：持仓自然日 ≥ AUTO_FOLLOW_HOLD_DAYS（15 天）的票全部平仓
Step 2 买入：short_signal ∈ (BUY, STRONG_BUY) 且 composite ≥ SHORT_BUY_THRESHOLD
             排序 = STRONG_BUY 优先，再按 composite 降序
             每只固定 AUTO_FOLLOW_POSITION_YUAN（5 万元）整手买入
```

**跳过保护**（都记进 `skipped` 不报错）：已达并发持仓上限 20 只 / 当日买入已达
`SHORT_MAX_BUY_PER_CHECK_DATE`（5 只）/ 已持仓 / 当日已卖（A 股 T+1 隐含约束）/
拿不到报价 / 股价太高凑不满 1 手 / 现金不足。

**时间口径**：`opened_at`、`trade_time` 入库都是 `datetime.utcnow()`，比较持有天数时
也用 `utcnow().date()`，避免本地时间（UTC+8）与 UTC 混用导致 `held_days` 偏差 1 天。
持有期按**自然日**算，与回测 `exit_date = entry + timedelta(days=N)` 的口径一致。

**并发安全**：

| 场景 | 保护 |
|---|---|
| 同进程多线程同时建账户 | `_auto_account_lock`（threading.Lock） |
| 跨进程同时建账户 | DB 部分唯一索引 `ix_paper_account_system_name`（`auto_migrate` 建），INSERT 冲突后重查 |
| cron / refresh-all / 管理员手动触发并发 | `_auto_follow_run_lock` 非阻塞锁，已在跑则返回 `already_running` |

> ⚠️ 运行级锁**仅进程内有效**。多 Gunicorn worker 的跨进程并发尚未防护
> （需要 DB advisory lock）。当前单进程部署下不是问题。

---

## 4. 数据更新策略

### 4.1 定时任务（APScheduler）

全部在 `main.py` 的 `lifespan` 里注册（AsyncIOScheduler）。

| 任务 | 时间 | 内容 |
|---|---|---|
| `_daily_data_update` | 每日 02:00 | 见下方明细 |
| `_weekly_rescore` | 周日 03:00 | 重算全部行业评分 |
| `_weekly_news_snapshot` | 周一 04:00 | 舆情前向测试快照（`scripts/forward_test_check.py`） |
| `_weekday_refresh_news` | 工作日 09:00 | 刷新新闻 + 情感分析（范围见下） |
| `_refresh_paper_price_cache` (am) | 工作日 9-11 每 8 分钟 | 模拟盘持仓实时价 |
| `_refresh_paper_price_cache` (pm) | 工作日 13-15 每 8 分钟 | 同上 |

**`_daily_data_update` 的完整链路**（每一步都单独 try/except，一步失败不阻断后面）：

```
1a. 价格增量        fetch_all_price_data(mode="incremental")
1b. 沪深300 基准    ensure_benchmark_data()      ← 短期信号的大盘趋势门槛依赖它
1c. 今日换手率      fetch_turnover_today()       ← sina 不返回，单次东财快照补齐
1d. 上市以来分位    compute_price_pctile_life()  ← 描述性指标，随最新价刷新
2.  宏观            fetch_macro_data()
3.  财报            fetch_all_financial_data(limit=200)
4.  情感分析        analyze_all_news()
5.  长期信号        generate_all_signals()
6.  短期信号        generate_all_short_signals()
7.  自动跟单        run_v202g_auto_follow()      ← 必须排在信号刷新之后
```

**`_weekday_refresh_news` 的范围**（去重后约 500-700 只，每只节流 0.5s，总耗时 ~5-10 分钟）：

1. 所有用户的自选股
2. 全部科技板块 active 股票（`TECH_INDUSTRIES`，~450 只）
3. 当前 BUY / STRONG_BUY 的短期信号候选（~30 只）

> 注：午休 12:00-12:59 与 16:00 之后不跑实时价预热，避免无谓打 sina。
> 早盘段覆盖 9:00-11:59（含集合竞价前 30 分钟），午后段覆盖 13:00-15:59（含收盘后 1 小时盘后价）。

### 4.2 手动触发（API）

| 端点 | 用途 |
|---|---|
| `POST /api/data/refresh-all` | 一键：宏观 + 行业评分 + 信号刷新 |
| `POST /api/data/update-prices?mode=incremental` | 增量补齐价格（仅已有数据的股票） |
| `POST /api/data/update-prices?mode=init-missing` | 仅给新股做首次全量历史拉取 |
| `POST /api/data/update-prices?mode=full` | 全量（耗时数小时，慎用） |
| `POST /api/data/update-financials?limit=200` | 拉财报 |
| `POST /api/data/update-macro` | 拉宏观（PMI/CPI/M2/北向） |
| `POST /api/signals/refresh-all` | 仅重算**长期**信号（不拉数据，约 80s） |
| `POST /api/signals/refresh-short` | 仅重算**短期**信号（约 1-2 分钟） |
| `POST /api/stocks/{code}/short-signal` | 重算单只短期信号 |
| `POST /api/paper/auto-follow/run` | 手动触发一次自动跟单（仅管理员） |
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
| 自动化测试覆盖窄 | 只有短期信号风控 + 价格分位两块有单测；长期信号引擎、模拟盘买卖、fetcher 重试仍是 0 覆盖 | 这些区域重构靠人审 + 冒烟，改动需小心（详见 DEVELOPMENT.md §测试） |
| SQLite 单文件 | 单进程读 / 多进程写不安全 | 单机部署 OK，分布式不行 |
| 自动跟单跨进程无锁 | 运行级锁仅进程内（见 §3.4） | 多 worker 部署会重复买入，当前单进程无影响 |
