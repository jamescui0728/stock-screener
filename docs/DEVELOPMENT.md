# 开发指南

## 启动开发环境

```bash
# 一键启动（首次会装依赖 + 初始化数据库）
./start.sh

# 或分别启动
cd backend && .venv/bin/uvicorn main:app --reload --port 8000
cd frontend && npm run dev
```

## 前端热重载 vs 后端

- **前端**（vite）：保存 .vue / .js 自动热替换，**多数情况不用刷页面**
- **后端**（uvicorn）：默认**不**带 `--reload`（会打断后台数据抓取）。改完 Python 代码要手动重启 uvicorn 才生效

> `start.sh` 默认不开 `--reload`。开发时如想自动重载，改 `start.sh` 里的 uvicorn 行，或单独跑 `uvicorn main:app --reload`。

## 重启后端的"快"做法

```bash
pkill -f "uvicorn main:app"; sleep 1
cd backend && nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info > /tmp/uvicorn.log 2>&1 &
```

---

## 项目约定

### 命名

- Python：`snake_case`，私有用 `_leading_underscore`
- 数据库表：`snake_case`，**单复数不统一**（历史遗留，见下）
- ORM 类：`PascalCase` 单数（`PaperAccount`）
- API 路径：kebab-case（`/data/update-prices`），参数 `snake_case`
- Vue 组件：`PascalCase.vue`
- Vue ref/computed：`camelCase`

> ⚠️ **表名单复数不一致，写 SQL 前先 `.tables` 确认**。多数表是复数
> （`stocks` / `industries` / `users` / `paper_positions` / `paper_transactions`），
> 但有两张是单数：**`paper_account`**（不是 `paper_accounts`）和 **`watchlist`**。
> 新表建议用复数，但**不要**为了统一去改这两张已有的表 —— 那需要迁移脚本 + 全量改引用。

### 注释

- **优先中文**（本项目使用者主要是中文用户）
- 关键阈值 / 业务规则必须有数据驱动的依据（"基于 run#X 分析"）
- 复杂的 if/elif 链每个 branch 加一行说明

### 异常 / 错误

- API 层：`raise HTTPException(status_code, detail)`，`detail` 必须是中文友好提示
- 引擎层：抛 `ValueError`（业务错误）/ `PermissionError`（权限）/ `Exception`（其他），由路由层捕获翻译
- 不要 silent except，用 `logger.warning` / `logger.debug` 记下

### 日志

```python
logger = logging.getLogger(__name__)

logger.debug("...")   # 详细，仅在排查时查看
logger.info("...")    # 关键节点（任务开始/完成、数据写入条数）
logger.warning("...") # 异常但可恢复（sina 超时重试、降级 fallback）
logger.error("...")   # 真错误，需要人介入
```

---

## 常见任务

### 添加一个新的 API endpoint

**1. 后端**（在 `api/routes.py` 或 `api/auth_routes.py`）：

```python
class MyRequest(BaseModel):
    foo: str
    bar: Optional[int] = None

@router.post("/my-endpoint")
def my_endpoint(
    body: MyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),  # 需要登录用这个；管理员用 get_current_admin
):
    """简短描述"""
    if not body.foo:
        raise HTTPException(400, "foo 不能为空")
    # 业务逻辑
    return {"result": ...}
```

**2. 前端**（在 `frontend/src/api/index.js`）：

```js
export const myApi = {
  doSomething: (foo, bar) => http.post('/my-endpoint', { foo, bar }),
}
```

**3. Vue 组件中调用**：

```vue
<script setup>
import { myApi } from '@/api'
const result = await myApi.doSomething('hello', 42)
</script>
```

后端 OpenAPI 文档自动更新到 http://localhost:8000/docs。

### 添加一个新的评分维度

1. 在 `engines/company_scorer.py` 加一个 `score_xxx(db, stock_code, ...)` 函数
2. 在 `signal_engine.py:generate_signal` 把它加入综合分计算
3. 在 `config.py` 加权重 `XXX_WEIGHT: float = 0.X`
4. 调整其他权重总和保持 = 1.0
5. 跑一次 `POST /backtest/run` 验证胜率不退化
6. 跑 `POST /signals/refresh-all` 让所有股票按新公式打分

### 调整阈值（不改代码）

**前端方式**：登录 → 参数设置页 → 改值 → 保存 → 立即生效（写到 `backend/user_settings.json`）

**API 方式**：
```bash
curl -X PUT http://localhost:8000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"BUY_THRESHOLD": 70.0, "MACRO_MIN_SCORE": 60.0}'
```

### 加新数据源

1. 在 `data/fetcher.py` 加 `fetch_xxx(db) -> dict` 函数（参考 `fetch_macro_data` 的"主源 + fallback"模式）
2. 在 `models/models.py` 如有新表则加 ORM 类，跑一次让 `init_db()` 创建表
3. 在 `api/routes.py` 加 `POST /data/update-xxx` endpoint
4. 在 `main.py` 加定时任务（如需要）

### 加新页面

1. `frontend/src/views/MyPage.vue` 新建
2. `frontend/src/router/index.js` 加路由：
   ```js
   { path: '/my-page', name: 'MyPage',
     component: () => import('@/views/MyPage.vue'),
     meta: { title: '我的页面' } }
   ```
3. `frontend/src/App.vue` 在侧边栏加菜单项：
   ```vue
   <el-menu-item index="/my-page">
     <el-icon><SomeIcon /></el-icon>
     <span>我的页面</span>
   </el-menu-item>
   ```
4. 仅管理员可见的页面加 `meta: { adminOnly: true }`，路由守卫会拦

### 写一次性维护脚本

放在 `backend/scripts/`，遵循 dry-run 优先的模式：

```python
"""
脚本目的的一句话说明。

使用：
  .venv/bin/python scripts/xxx.py            # dry-run
  .venv/bin/python scripts/xxx.py --apply    # 实际写库
"""
import sys
sys.path.insert(0, ".")
from database import SessionLocal

def main(apply: bool):
    db = SessionLocal()
    try:
        # 先扫描，打印将要做的事
        print("=== 计划 ===")
        ...
        if not apply:
            print("\n[dry-run] 加 --apply 实际执行")
            return
        # 实际写库
        ...
        db.commit()
        print("✓ 完成")
    finally:
        db.close()

if __name__ == "__main__":
    main("--apply" in sys.argv)
```

---

## 调试技巧

### 实时看后端日志

```bash
tail -f /tmp/uvicorn.log

# 或仅看 fetcher 活动（注意可能有 binary 字符，加 -a）
tail -f /tmp/uvicorn.log | grep -a "data.fetcher\|engines"
```

### 看 SQLite 数据

```bash
cd backend
.venv/bin/python -c "
from database import SessionLocal
from sqlalchemy import text
db = SessionLocal()
for r in db.execute(text('SELECT signal, COUNT(*) FROM stocks GROUP BY signal')).fetchall():
    print(r)
"
```

或用 `sqlite3` CLI：

```bash
sqlite3 backend/stock_screener.db
sqlite> .tables
sqlite> SELECT * FROM paper_account;    -- 注意：单数，不是 paper_accounts
sqlite> .quit
```

### 单独触发 BackgroundTask 失败的恢复

后端的 `BackgroundTasks.add_task(_run)` 如果 `_run` 卡死（典型：sina 抖动 + worker 池泄漏），整个 endpoint 仍会返回 200。但实际任务挂起。

判定方法：
- `ps aux | grep uvicorn` 看 CPU%。0.0% 但有任务在跑 = 卡死
- `tail /tmp/uvicorn.log` 看是否长时间没新日志
- `lsof -i -p <pid> | grep ESTABLISHED` 看是否有 sina 长连接挂着

恢复方法：
```bash
pkill -9 -f "uvicorn main:app"   # 强杀
# 重启
```

### 跑回测看胜率

```bash
curl -X POST http://localhost:8000/api/backtest/run \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "description": "test new threshold",
    "params": {
      "buy_threshold": 70,
      "macro_min_score": 60
    }
  }'

# 进度
curl http://localhost:8000/api/backtest/progress/snapshot

# 报告
curl http://localhost:8000/api/backtest/runs/{id}
```

---

## 测试

用标准库 `unittest`（**没有引入 pytest**，仓库里也没有 pytest 配置）。测试跑在内存
SQLite 上（`create_engine("sqlite:///:memory:")` + `Base.metadata.create_all`），
不碰真实的 `stock_screener.db`，也不打外部网络。

### 怎么跑

```bash
cd backend
python -m unittest discover -s tests -v      # 全部 164 条
```

单跑一个文件 / 一个用例：

```bash
python -m unittest tests.test_short_signal_risk -v
python -m unittest tests.test_short_signal_risk.TestClassifyShortSignal -v
```

### 现有覆盖

| 文件 | 条数 | 覆盖内容 |
|---|---|---|
| `tests/test_short_signal_risk.py` | 40 | `classify_short_signal` 各分支、5 日急跌 veto、大盘趋势 fail-closed、`score_news_heat`、`compute_industry_news_heat`、截面排名 |
| `tests/test_price_position.py` | 4 | `compute_price_pctile_life`（上市以来总收益分位） |
| `tests/test_watch_tag.py` | 49 | EMA20 / 量比数值、四档标签判定与优先级、前复权换算与同日守卫、成本价读取 |
| `tests/test_macd.py` | 27 | MACD(12/26/9) 数值、零轴上方回踩金叉四个必要条件、零轴下方金叉排除 |
| `tests/test_gap.py` | 44 | 跳空识别、突破/加油/衰竭/加仓四态、缺口回补判定、信号有效期 |

### CI 跑什么

`.github/workflows/ci.yml` 三个 job（push/PR 到 main 触发）：

1. **backend-syntax** — `py_compile` 全部 .py（跳过 `scripts/`）→ 跑
   `tests.test_short_signal_risk` → import 一遍 17 个顶层模块
2. **frontend-build** — `npm ci` + `npm run build`
3. **docker-build** — 前后端镜像构建（不 push）+ `docker compose config` 校验

> ⚠️ **CI 只跑 `test_short_signal_risk` 一个文件**，另外 4 个文件（124 条）都没纳入：
> `test_price_position` / `test_watch_tag` / `test_macd` / `test_gap`。
> 这是**有意的选择**，不是遗漏 —— 改坏了 CI 不会变红，改动这些模块后**必须本地自己跑**：
>
> ```bash
> cd backend && python -m unittest discover -s tests -v
> ```

### 仍然是 0 覆盖的地方

改到这些区域时，自动化测试帮不了你，得靠下面的手工验证：

- `engines/signal_engine.py:generate_signal` 的各分支（必买/买入/各种 HOLD 降级/卖出/必卖/veto）
- `engines/paper_trade.py` 的 buy/sell 流程（费用计算、加权平均成本、清仓边界）
- `engines/auto_follow.py` 的跟单买卖与跳过保护
- `data/fetcher.py:_retry` 的超时 + 重试行为
- `engines/company_scorer.py` 的 ROE / 利润增长各子分边界

### 改了信号引擎 / 评分逻辑 / 阈值之后

1. 跑一次 `/backtest/run` 看胜率不退化
2. `POST /signals/refresh-all`（长期）或 `POST /signals/refresh-short`（短期），
   看信号分布是否合理（必买/必卖不应突然占比剧增/暴减）
3. 手动开页面冒烟测试（公司筛选、自选股、模拟盘买卖）

---

## 依赖管理

### 后端

```bash
cd backend
source .venv/bin/activate
pip install xxx               # 装新包
pip freeze | grep xxx >> requirements.txt   # 锁版本
```

### 前端

```bash
cd frontend
npm install xxx               # 自动写 package.json
```

---

## 常见坑

| 坑 | 症状 | 解法 |
|---|---|---|
| f-string 用反斜杠转义双引号 | `SyntaxError: f-string expression part cannot include a backslash` | 换单引号 / `%` 格式化 / 用变量先存 |
| logger.info 日志看不见 | uvicorn 启动参数 `--log-level warning` | 改成 `--log-level info` |
| `tail` 时 grep 报 binary 错 | 日志里有 tqdm 的 `\r` 字符 | 加 `grep -a` 强制按文本处理 |
| 改了后端代码没生效 | uvicorn 没 --reload，模块缓存了旧代码 | `pkill uvicorn` 重启 |
| akshare 接口偶发 SSL 错误 | sina 抖动 | `_retry` 已带重试，看日志确认 |
| sina 长时间卡死 worker 池 | 14 只持仓但 endpoint 30+ 秒没返回 | `_warmup_prices_parallel` 已加 14s hard cap，重启可清池 |
