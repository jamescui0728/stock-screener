# REST API 参考

> 完整 OpenAPI 规范见 **http://localhost:8000/docs**（FastAPI 自动生成，附 schema + Try-it-out）。
> 这里只列高频 endpoint + 关键参数。

所有路径前缀：`/api`
认证方式：`Authorization: Bearer <jwt_token>`（除 `/auth/login` `/auth/register`）

---

## 1. 认证 `/api/auth/*`

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/auth/register` | 手机号 + 密码注册 |
| POST | `/auth/login` | 登录，返回 `{ token, user }` |
| GET  | `/auth/me` | 当前用户信息 |
| POST | `/auth/change-password` | 改自己的密码（旧密码 + 新密码） |
| POST | `/auth/logout` | 后端清 session（前端清 localStorage） |

### 管理员专用

| 方法 | 路径 | 用途 |
|---|---|---|
| GET    | `/auth/users` | 列出所有用户 |
| POST   | `/auth/users` | 创建用户 |
| PATCH  | `/auth/users/{id}` | 改 name / is_admin / is_active |
| POST   | `/auth/users/{id}/reset-password` | 重置密码 |
| DELETE | `/auth/users/{id}` | 删除用户 |

### Login 请求示例
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13812345678","password":"abc123"}'
```
返回：
```json
{
  "message": "登录成功",
  "token": "eyJhbGc...",
  "user": { "id": 1, "phone": "...", "name": "...", "is_admin": true, "is_active": true }
}
```

---

## 2. 公司筛选 `/api/stocks`

### 列表
```
GET /api/stocks
  ?keyword=招商银行              # 模糊搜索（代码 / 名称，支持空格容忍）
  &industry_code=BK0475         # 行业代码精确过滤
  &signal=BUY                   # 长期信号，单一等级精确匹配（大小写不敏感）
  &signal_group=buy             # 长期聚合：buy=BUY+STRONG_BUY, sell=SELL+STRONG_SELL
  &short_signal=BUY             # 短期信号，语义同 signal
  &short_signal_group=buy       # 短期聚合：buy / sell / watch
  &min_fundamental=60           # 基本面最低分
  &min_composite=70             # 综合分最低
  &page=1&limit=50              # 分页（limit ≤ 200）
```

**优先级与组合规则：**

- `signal` 和 `signal_group` 同时传时 `signal_group` 优先（`signal` 被忽略），短期同理
- **长期过滤与短期过滤是 AND 关系**，可以同时生效（例如「长期 BUY 且短期 BUY」）
- `signal_group` / `short_signal_group` 传了 `buy` / `sell` / `watch` 之外的值 → **400**

**排序会跟着切换**：只要传了 `short_signal` 或 `short_signal_group`，结果按
`short_composite_score` 降序；否则按 `composite_score` 降序（两者都是 nulls last）。

**`watch` 观察候选桶**：捞出「composite 够 BUY 线、但被大盘趋势门槛降级成 HOLD」的高分票，
避免这些机会在前端消失。`watch` / `candidate` / `trend_blocked` 三个值等价：

```
GET /api/stocks?short_signal_group=watch
→ short_signal == "HOLD"
  且 short_composite_score >= SHORT_BUY_THRESHOLD
  且 reason 含 "市场短线趋势未达反转买入门槛"
```

### 单只详情
```
GET /api/stocks/{code}
```
返回 4 块：`info`（同列表项，含长/短期信号与 reason、行业上下文）、
`financials`（最近 10 期财报）、`prices`（最近 252 个交易日 K 线）、`news`（最近 20 条）。
股票不存在 → **404**。

### 强制刷新单只信号

```
POST /api/stocks/{code}/signal          # 长期信号；数据不足 → 422
POST /api/stocks/{code}/short-signal    # 短期信号；价格 < 21 个交易日 → 422
```

### 批量刷新信号

```
POST /api/signals/refresh-all      # 长期，全市场，约 80 秒
POST /api/signals/refresh-short    # 短期，全市场，约 1-2 分钟
```

两个都是**后台异步**：立刻返回 `{"message": "..."}`，实际进度通过 `data.task_tracker`
记录。纯 DB 计算，不打外部 API。

---

## 3. 行业 `/api/industries`

| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/industries?min_score=0` | 列出东财行业板块（按总分排序，未评分的排在最后） |
| POST | `/industries/{code}/score` | 重算单个行业评分 |
| POST | `/industries/rescore-all` | 重算全部行业（约 30 秒） |

---

## 4. 自选股 `/api/watchlist`

| 方法 | 路径 | 用途 |
|---|---|---|
| GET    | `/watchlist` | 当前用户的自选股 |
| POST   | `/watchlist` | `{ stock_code, note }` 加入 |
| DELETE | `/watchlist/{code}` | 移除 |
| GET    | `/watchlist/news?days=3&limit=50` | 自选股近 N 天新闻聚合（含情感标签） |
| POST   | `/watchlist/news/refresh` | 立即刷一次（不等 09:00 cron） |

---

## 5. 模拟盘 `/api/paper`

### 5.1 账户管理（多账户）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET    | `/paper/accounts` | 当前用户的所有账户 |
| POST   | `/paper/accounts` | `{ name, initial_cash? }` 新建（默认 initial_cash 从 settings 读） |
| PUT    | `/paper/accounts/{id}` | `{ name }` 重命名 |
| DELETE | `/paper/accounts/{id}` | 删除（至少保留 1 个） |

### 5.2 账户快照 / 流水 / 交易

所有以下接口都接受 `account_id` 参数（不传 = 用户的"默认账户"，即最早创建的）：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/paper/account?account_id=1` | 账户总览（现金/持仓/盈亏） |
| GET  | `/paper/transactions?account_id=1&limit=200` | 交易流水 |
| POST | `/paper/buy` | `{ account_id, stock_code, shares, price?, note? }` 买入 |
| POST | `/paper/sell` | 同上，卖出 |
| POST | `/paper/reset` | `{ account_id, initial_cash? }` 清仓重置 |

### 5.3 实时价缓存

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/paper/cache/warmup` | 强制刷新当前用户所有持仓的实时价（节流：5 秒/user） |

返回示例：
```json
{
  "refreshed": 14,
  "skipped": 0,
  "stale_before": 14,
  "message": "已刷新 14 / 14 只持仓股的实时价"
}
```

### 5.4 规则参数

```
GET /paper/rules
→ { "init_cash": 1000000, "fee_rate": 0.0003, "min_fee": 5.0, "lot_size": 100 }
```

### 5.5 报价

```
GET /paper/quote/{code}
→ { "code", "name", "signal", "composite_score", "close", "trade_date" }
```

### 5.6 自动跟单（v202g）

系统级账户（`user_id IS NULL`），跟着短期信号自动买卖。机制见
[ARCHITECTURE.md §3.4](ARCHITECTURE.md)。

| 方法 | 路径 | 权限 | 用途 |
|---|---|---|---|
| GET  | `/paper/auto-follow/performance` | 需登录 | 绩效快照（NAV / 胜率 / 持仓） |
| GET  | `/paper/auto-follow/transactions?limit=500` | 需登录 | 逐笔流水（只返回带 `v202g-auto` 标签的） |
| POST | `/paper/auto-follow/run` | **仅管理员** | 手动触发一次（凌晨 cron 之外的补救入口） |

- `transactions` 的 `limit` 服务端强制夹在 **1-1000**（防超大查询拉爆内存），默认 500
- 账户尚未建立时 `transactions` 返回 `[]`，**不会**顺手把账户建出来
- `run` 带非阻塞运行级锁：已在跑时返回 `{"skipped_reason": "already_running", ...}`
  而不是报错，也不会重复买入

---

## 6. 数据管理 `/api/data`

| 方法 | 路径 | 用途 | 耗时 |
|---|---|---|---|
| POST | `/data/refresh-all` | 一键：宏观 + 行业评分 + 信号 | ~3 分钟 |
| POST | `/data/update-prices?mode=incremental` | 增量补齐价格（默认） | ~1 分钟 |
| POST | `/data/update-prices?mode=init-missing` | 仅给新股拉历史 | 数小时 |
| POST | `/data/update-prices?mode=full` | 全量重拉 | 数小时 |
| POST | `/data/update-financials?limit=200` | 拉财报 | ~10 分钟 |
| POST | `/data/update-macro` | 拉宏观（PMI/CPI/M2/北向） | ~30 秒 |
| POST | `/data/update-news/{code}` | 单只股新闻 | ~5 秒 |
| GET  | `/data/refresh-progress` | 查 refresh-all 进度 | 即时 |
| GET  | `/status/financial-count` | 财报数据状态 | 即时 |

---

## 7. 回测 `/api/backtest`

| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/backtest/run` | 触发回测（参数 = 评分权重 / 阈值，body 可覆盖默认） |
| GET  | `/backtest/runs` | 历史 run 列表 |
| GET  | `/backtest/runs/{id}` | 报告详情（指标、记录、TopN 案例） |
| POST | `/backtest/optimize` | 贝叶斯参数优化（推荐 30+ iterations） |
| GET  | `/backtest/progress` | SSE 进度流（前端用 EventSource） |
| GET  | `/backtest/progress/snapshot` | 当前进度快照（JSON） |

---

## 8. 设置 `/api/settings`

| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/settings` | 当前所有可配置参数 |
| PUT  | `/settings` | 写 `user_settings.json`，热更新生效 |
| POST | `/settings/reset` | 清 user_settings.json，回到 config.py 默认 |

---

## 错误响应

所有 4xx / 5xx 错误统一格式：

```json
{ "detail": "错误信息（中文友好）" }
```

- **401**：token 无效 / 过期 → 前端自动清登录态
- **403**：权限不足（非管理员访问 admin 端点 / 跨用户访问账户）
- **404**：资源不存在（账户 ID / 股票代码）
- **429**：节流命中（仅 `/paper/cache/warmup`）
- **500**：后端异常（应只在 bug 时出现）
