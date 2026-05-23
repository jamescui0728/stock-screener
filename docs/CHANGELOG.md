# 版本变更记录

按时间倒序。最近的改动在最上面。

---

## 2026-05-23

### v202g 自动跟单引擎（信号 → 模拟盘）

新增 `backend/engines/auto_follow.py`：系统级账户（`user_id IS NULL`，初始 100 万）每天 cron 在短期信号刷新后自动执行：

- **卖出**：持仓达到 `AUTO_FOLLOW_HOLD_DAYS`（15 天，与回测持有期对齐）的票全部平仓
- **买入**：新出现的 BUY/STRONG_BUY 信号，STRONG_BUY 优先、按 composite 降序，每只固定 `AUTO_FOLLOW_POSITION_YUAN`（5 万）整手买入
- **跳过保护**：已持仓 / 当日已卖（T+1）/ 现金不足 / 无报价 / 超当日买入上限，均记入 `skipped` 不报错
- **交易标记**：`PaperTransaction.note` 打 `v202g-auto` 标签便于绩效分析

新增 API：
- `GET /paper/auto-follow/performance` — 账户绩效快照（NAV / 胜率 / 持仓，公开可查）
- `POST /paper/auto-follow/run` — 手动触发一次（仅管理员）
- `/data/refresh-all` 在短期信号刷新后顺带跑一次自动跟单，与凌晨 cron 行为一致

并发安全：`get_or_create_auto_account` 加进程内线程锁 + 提交失败重查；跨进程靠 `auto_migrate` 建立的部分唯一索引 `ix_paper_account_system_name`（`user_id IS NULL` 时 `name` 唯一）兜底。

### 短期信号风控加固

- **大盘趋势 fail-closed**：沪深300 基准 K 线不足 21 根时 `score_market_trend` 返回 `pass=False`，不再误放行 BUY（弱市接刀）。沪深300 20 日涨幅 ≥ `SHORT_MARKET_TREND_MIN_20D`（3%）才允许买入
- **5 日急跌 veto**：`ret_5d ≤ -10%` 时统一经 `classify_short_signal` 强制 `STRONG_SELL`
- **动量权重为 0**：缺动量数据时用中性占位，不再整只跳过
- **观察候选 bucket**：被市场过滤降级为 HOLD 但 composite ≥ 阈值的高分票，通过 `/api/stocks?short_signal_group=watch` 单独暴露，前端不再隐藏机会池

### v202g 权重 / 阈值重校准

`run 65` 诊断显示 pricing_power 几乎无区分度（IC=-0.019），拿掉它把 0.10 权重还给最强的 industry_relative：

| 因子 | 旧权重 | 新权重 |
|---|---|---|
| industry_relative | 0.15 | 0.25 |
| pricing_power | 0.10 | 0.00（保留 hook） |

阈值随分布右移重新校准（`run 66`）：`SHORT_BUY_THRESHOLD` 71 → 72.5（胜率 86.7%，n≈25/年）。

### 回测：消除 look-ahead / execution bias

- **成交价改用次日**：信号在 check_date 收盘后才可知，回测从下一交易日开盘价成交并计持有期，避免同一根 K 线既出信号又成交
- **财报可见性截断**：`compute_industry_avg_gm` / `score_pricing_power` 加 `as_of_date - REPORT_LAG_DAYS` 上界，回测不再看到未公开的财报
- **沪深300 基准增量更新**：`ensure_benchmark_data` 默认增量补到今天（之前只在首次插入，基准数据卡死）

### 回测：同日买入上限 + 日期集中度报告

- `SHORT_MAX_BUY_PER_CHECK_DATE`（5）：单个检查日最多纳入 5 个买入信号，避免一个市场事件日批量信号被当作独立样本霸屏（`_select_tradeable_short_results`，SELL 不限）
- 报告新增 `signal_date_concentration`，Top 案例每个信号日最多展示 3 条（`_pick_diversified_records`）

### 回测性能优化

- `compute_recent_price_cache` 批量预加载价格窗口，momentum / volprice / industry_relative 复用，省掉 per-stock 重复查询
- 每个检查日预计算 `market_trend`、`industry_gm`，pricing_power 权重为 0 时直接跳过 `industry_gm`（省 ~90% 时间）
- 新增索引 `ix_price_data_stock_date`、`ix_news_items_stock_pub_date`

### 前端：菜单点击无响应修复

**问题**：Docker 部署后侧边栏菜单点击完全无反应（URL/页面均不变）。

**根因**：nginx 对 `index.html` 未设 `Cache-Control`，浏览器缓存旧 `index.html` → 加载过期 JS bundle → 路由逻辑失效。

**解决**：
- `nginx.conf` 对 `/index.html` 加 `no-cache, no-store, must-revalidate`（哈希化的 JS/CSS 仍保留 1 年强缓存）
- `App.vue` 给 `.aside` 加 `position: relative; z-index: 1`，防御性确保侧边栏独立层叠上下文

### 测试 / 工程化

- 新增 `backend/tests/test_short_signal_risk.py`（22 个用例）：信号分类、市场趋势打分、同日买入限额与 STRONG_BUY 优先、整手计算、自动跟单跳过逻辑、auto_migrate 部分唯一索引
- 新增根目录 `CLAUDE.md`（8 条协作行为约束）
- Colima 配置中国镜像加速器（`docker.1panel.live` 等），解决 Docker Hub 拉取超时

---

## 2026-05-07

### 实时价缓存预热（消除模拟盘首次加载的 6-10 秒延迟）

**问题**：`/paper/account` 首次访问需要并行打 14 次 sina 拉实时价，sina 抖动时单次 60-600 秒，用户看不到数据。

**解决**：
- 启动时 daemon 线程异步预热（不阻塞 startup）
- 工作日交易时段（9-11 / 13-15）每 8 分钟 cron 刷新（`backend/main.py:_refresh_paper_price_cache`）
- 缓存 10 分钟 TTL > cron 8 分钟，**永远命中**
- 加 POST `/paper/cache/warmup` 让用户手动强刷（节流：5 秒/user，14s 后端 hard cap）
- 前端 `刷新行情` 按钮 + axios 60s timeout

**实测**：模拟盘首次访问从 6-10s → **0.018s**

### 5 等级买卖信号

`BUY / HOLD / SELL` 三档拓展为 `STRONG_BUY / BUY / HOLD / SELL / STRONG_SELL` 五档：

| 等级 | 触发 | 实测分布（5196 只） |
|---|---|---|
| 必买 | composite ≥ 80 + 全门槛 | 2 |
| 买入 | composite ≥ 64 + 全门槛 | 2 |
| 持有 | composite ≥ 50 或被门槛降级 | 2941 |
| 卖出 | 30 ≤ composite < 50 | 2063 |
| 必卖 | composite < 30 或 veto | 188 |

veto（财务造假/退市风险）从 SELL **升级为 STRONG_SELL**——是真正"必须避开"的红牌。

回测代码 `r.signal == "BUY"` 全改为 `r.signal in ("BUY", "STRONG_BUY")`，向后兼容。

### 东方财富宏观数据源

**问题**：akshare 的 `macro_china_pmi_yearly / cpi_yearly / m2_yearly` 数据停在 2025-08，**整整 9 个月没新数据**。

PMI = 49.4（2025-08）卡住所有股票的 `macro_gate`，导致全市场 0 个 BUY 信号。

**解决**：
- 直接打东方财富数据中心 JSON API（`RPT_ECONOMY_PMI / RPT_ECONOMY_CPI / RPT_ECONOMY_CURRENCY_SUPPLY`）
- 数据已恢复到 2026-04（PMI=50.3 扩张区间）
- akshare 旧接口保留为 fallback

**实测**：BUY 信号从 0 → 4（白酒龙头），symbol 链路全通。

### `signal_group` 聚合过滤

`/api/stocks?signal_group=buy` 一键拿 BUY+STRONG_BUY 的并集，sell 同理。
原 `?signal=BUY` 仍是精确匹配单一等级。OpenAPI doc 加完整 `description` 说明大小写处理。

### 多账户模拟盘

每个用户支持多个独立 `PaperAccount`：
- 多账户 CRUD：`GET/POST/PUT/DELETE /paper/accounts`
- 所有账户操作 endpoint 加 `account_id` 参数
- 前端账户切换器（顶部下拉），状态持久化到 localStorage
- 安全限制：每用户至少保留 1 个账户

数据库 schema：`PaperAccount.user_id` 改成 `nullable=True, index=True`（一对多关系，cascade delete-orphan）。

### N+1 查询消除

- `fetch_all_price_data`：5196 次 `MAX(trade_date)` 单查 → 1 次 GROUP BY（启动省 ~50s）
- `account_snapshot`：14 次 `Stock` 单查 → 1 次 IN（首次加载省 ~3s）

### `fetch_all_price_data` 增量模式

旧逻辑：`if existing > 100: skip`，导致 499 只老股**永远停在某天**不被增量更新。

新增 `mode` 参数：
- `incremental`（默认）：按 `max(trade_date)` 真增量补到今天
- `full`：所有股票从 2010 起重拉
- `init-missing`：仅给完全没价格数据的股票首次全量

### `_retry` 超时也重试

旧版超时直接 `return None`（不重试），导致 sina 偶发慢响应被立即放弃。
新版超时计入重试次数（最多 3 次），日志 WARNING 级别可见。

### 性能优化：`_warmup_prices_parallel`

并行预热持仓实时价，`ThreadPoolExecutor(max_workers=16)`，14s overall hard cap 防 sina 抖动拖死主线程。

---

## 2026-04-27（多账户改造前）

### 自选股新闻聚合

工作日 09:00 cron 自动抓取所有用户自选股的最新新闻 + 情感分析。
前端 `/watchlist` 顶部展示近 3 天新闻列表。

### 用户管理（管理员专用）

- 新增 `/users` 页面（仅管理员可见）
- 创建 / 重置密码 / 启用/禁用 / 删除用户

### `_user_public` 添加 `is_active` 字段

修复 UI 误显示"已禁用"的 bug。

---

## v100 → v108 信号引擎演进（数据驱动校准）

每个版本都有对应的回测 run 提供数据依据：

| 版本 | 改动 | 依据 |
|---|---|---|
| v100 | 基础三档信号（BUY/HOLD/SELL） | 综合分阈值 BUY=64 |
| v102 | 加 ROE 质地门槛 | run#25：ROE<15 时 BUY 胜率仅 36% |
| v103 | 加行业评分门槛（≥50） | run#26：行业 <50 胜率 48.4% / ≥50 胜率 70.1% |
| v104 | 加宏观环境门槛（≥55） | run#28：macro<55 胜率 55% / ≥55 胜率 76% |
| v105 | 加动量门槛（拒绝接下跌的刀） | run#29：60 日 -10%~0% 区间胜率仅 50% |
| v106 | 修复 PE/PB 估值百分位 bug | 估值分原本算反了 |
| v107 | 加估值门槛（≥70） | run#31：valuation_pct<50 vs ≥75 胜率 63.6% vs 82.6% |
| v108 | 加金融业宏观门槛（≥65） | run#32：银行误判全因 macro 在 60-64 边缘 |

每个门槛都是回测数据出现"明确分桶"后才加的，不是拍脑袋。

---

## 已知技术债

- ❌ **0 自动化测试**：所有验证靠回测胜率 + 手动冒烟
- ⚠️ **`_retry` worker 线程不可中断**：sina socket 卡死时 worker 仍跑，pool 满会 block 新请求。短期靠 14s hard cap 缓解，根治需换 `requests.Session` + socket-level timeout
- ⚠️ **SQLite 单文件**：单进程读写无锁问题，但分布式部署不行
- ⚠️ **历史数据混源**：sina 旧版 + 东方财富新版同月可能有两条记录，`score_macro` 用 `MAX(date)` 取自然偏好新源，但**回测复现历史时点的 macro 值**会有口径漂移
