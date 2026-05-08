# 贡献指南

感谢你对本项目感兴趣 🎉

## 在开始之前

- **阅读架构文档** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 了解信号引擎、评分体系、模拟盘的设计
- **阅读开发指南** [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) 了解约定、调试技巧、常见任务
- **看变更记录** [docs/CHANGELOG.md](docs/CHANGELOG.md) 了解最近做了什么

## 本地开发环境

```bash
# 选项 A：本地（适合改代码）
./start.sh

# 选项 B：Docker（适合验证部署链路）
cp .env.example .env
cp backend/user_settings.example.json backend/user_settings.json
docker compose up -d --build
```

后端 hot reload 默认**关闭**（cron 任务跑数据时被打断会损坏 SQLite WAL）。改后端代码后手动重启：

```bash
pkill -f "uvicorn main:app"
cd backend && nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info > /tmp/uvicorn.log 2>&1 &
```

前端 vite 自带 hot reload，保存即生效。

---

## 改代码的几条原则

### 1. 阈值调整必须有数据驱动依据

例：要改 `BUY_THRESHOLD` 从 64 到 70？**先跑回测看胜率**：

```bash
curl -X POST http://localhost:8000/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"description":"test threshold 70","params":{"buy_threshold":70}}'
```

新阈值的胜率不能比旧阈值低。如果低，commit 不要进 main。

### 2. 信号 / 评分的逻辑改动要附 v 版本号 + 注释

参考 `engines/signal_engine.py` 里现有的 v102 / v103 / ... 注释格式。每次加新门槛 / 改判定，都要写清楚：
- 第几版（vXXX）
- 改之前胜率
- 改之后胜率
- 数据样本量

### 3. 数据表 schema 变更走 migration

**不要**直接改 `models/models.py` 的字段类型 / 长度然后 push。SQLite 不会自动 ALTER。

正确做法：
- 在 `backend/scripts/` 写一个 dry-run + apply 双模式的 migration
- 在 [docs/CHANGELOG.md](docs/CHANGELOG.md) 记录为何要改
- 测：`migrate.py` 跑一次 + 数据无误 + 旧 API 仍兼容

### 4. 新增 endpoint 默认要登录

`api/routes.py` 的所有业务 endpoint 都用 `Depends(get_current_user)`，仅纯参数查询（如 `/paper/rules`）和健康检查（`/health`）无认证。

跨用户数据访问（如 `/paper/account?account_id=X`）必须在路由层校验所有权（参考 `_resolve_account` 函数）。

### 5. 注释优先中文

项目主要使用者是中文用户，业务注释用中文（变量名 / 函数名仍用英文）。

---

## Commit 信息约定

```
<type>: <subject>

<body 可选>
```

`<type>` 用：
- `feat` 新功能
- `fix` 修 bug
- `refactor` 重构
- `perf` 性能优化
- `docs` 文档
- `chore` 构建/工具

例：
```
feat: 5 等级买卖信号

将 BUY/HOLD/SELL 三档拓展为必买/买入/持有/卖出/必卖五档：
- composite >= 80 + 全门槛通过 → STRONG_BUY
- composite < 30 或 veto 触发 → STRONG_SELL
- 回测代码兼容：r.signal in ('BUY', 'STRONG_BUY')
```

---

## Pull Request 流程

1. **Fork 本仓库**到你的账号
2. **建分支**：`git checkout -b feature/my-thing` 或 `fix/some-bug`
3. **改代码 + commit**（合理拆分多个 commit，不要一个巨型 commit）
4. **本地验证**：`./start.sh` 跑起来，至少手动冒烟测试一遍受影响的页面
5. **如有数据/算法改动**：跑一次 `/api/backtest/run`，把胜率结果贴在 PR 描述里
6. **push 你的 fork**：`git push origin feature/my-thing`
7. **开 PR**到 `main` 分支，描述里说明：
   - 改了什么 / 为什么
   - 怎么测的
   - 有没有 breaking change
8. **等 GitHub Actions CI 跑完**（编译 + build + docker），全绿才合并
9. **合并方式**：默认 squash merge（保持 main 历史干净）

---

## 测试现状（实话实说）

**项目目前 0 自动化测试**，所有验证靠：
- 回测胜率指标（量化验证算法层）
- 手动冒烟测试（验证 UI / 业务流）
- GitHub Actions CI（仅验证编译能过）

如果你愿意补测试，**特别欢迎**。建议优先级：
1. `engines/signal_engine.py:generate_signal` 的 11 个分支
2. `engines/paper_trade.py` 的 buy/sell 流程
3. `data/fetcher.py:_retry` 的超时 + 重试行为

---

## 报 bug

GitHub Issues。请附：
- 你跑的命令 / 操作步骤
- 看到的错误（贴日志、截图）
- 期望的行为
- 环境（macOS / Linux，Python 版本，是否走 Docker）

---

## 不在范围内的功能

- ❌ **港股 / 美股**：当前仅 A 股，扩展需要重写大量数据接入层
- ❌ **下单交易**：模拟盘是模拟，**不会**接入真实券商 API
- ❌ **AI / LLM 加成**：本项目是规则驱动 + 数据驱动，不计划加 LLM 评分
- ❌ **移动端 app**：响应式 web 已经能用，原生 app 不在路线图

如果你想做以上功能，建议 fork 后自行实现 🙂
