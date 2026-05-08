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
  &signal=BUY                   # 单一信号精确匹配
  &signal_group=buy             # 聚合：buy=BUY+STRONG_BUY, sell=SELL+STRONG_SELL
  &min_fundamental=60           # 基本面最低分
  &min_composite=70             # 综合分最低
  &page=1&limit=50              # 分页（limit ≤ 200）
```

> `signal` 和 `signal_group` 同时传时 `signal_group` 优先级更高。

### 单只详情
```
GET /api/stocks/{code}
```
返回完整字段：基本面子分、估值百分位、信号 reason、行业上下文、近 30 条新闻摘要等。

### 强制刷新单只信号
```
POST /api/stocks/{code}/signal
```

### 刷新所有 5196 只信号
```
POST /api/signals/refresh-all
```
约 80 秒（纯 DB 计算，不打外部 API）。

---

## 3. 行业 `/api/industries`

| 方法 | 路径 | 用途 |
|---|---|---|
| GET  | `/industries?min_score=0` | 列出 216 个一级行业（按总分排序） |
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
