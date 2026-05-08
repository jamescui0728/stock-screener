# 长期价值股票筛选系统

[![CI](https://github.com/jamescui0728/stock-screener/actions/workflows/ci.yml/badge.svg)](https://github.com/jamescui0728/stock-screener/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3.4-4FC08D?logo=vue.js&logoColor=white)](https://vuejs.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](docker-compose.yml)

A 股长期价值投资量化筛选工具：**基本面 + 估值 + 行业 + 宏观 + 舆情** 五维评分，输出 5 等级买卖信号，附带模拟盘和回测功能。

> 这是个人使用工具，不构成投资建议。

---

## 主要功能

| 模块 | 功能 |
|---|---|
| **公司筛选** | 5196 只 A 股按综合分排序，可按信号 / 行业 / 基本面分过滤 |
| **行业全景** | 申万一级 216 个行业评分（营收稳定性、利润稳定性、抗周期性、竞争格局） |
| **自选股** | 关注列表 + 工作日 09:00 自动汇总最新公司新闻（含情感分析） |
| **模拟盘** | 多账户、实时报价（Sina）、买入/卖出、持仓估值、交易流水、信号联动 |
| **回测中心** | 按历史时点回放信号，分窗口胜率 / α / IC / 夏普比统计 |
| **参数设置** | 评分权重、阈值、买卖规则的热更新（无需重启） |
| **用户管理** | 多用户认证（手机号 + bcrypt + JWT），管理员可创建/重置密码 |

---

## 技术栈

**后端**：Python 3.10+ / FastAPI / SQLAlchemy / SQLite / APScheduler / akshare（A 股数据源）

**前端**：Vue 3 / Vite / Element Plus / Pinia / ECharts / axios

**数据源**：
- 行情、财报、新闻：[akshare](https://akshare.akfamily.xyz/)（封装东方财富、Sina、巨潮等公开数据）
- 宏观（PMI / CPI / M2）：东方财富数据中心 JSON API（直打）
- 实时报价：Sina 财经实时不复权收盘价

---

## 快速开始

两种部署方式，挑一个：

### 方式 A · Docker（推荐生产/服务器部署）

仅需 **Docker** + **Docker Compose**（macOS / Windows 装 Docker Desktop 即可）。

```bash
# 1. 准备环境变量（生成 JWT 强随机密钥）
cp .env.example .env
# 编辑 .env，把 JWT_SECRET 改成 `openssl rand -base64 48` 的输出

# 2. 准备运行时配置（参数热更新文件）
cp backend/user_settings.example.json backend/user_settings.json

# 3. 构建 + 启动两个服务（首次构建 ~5 分钟，后续秒起）
docker compose up -d --build

# 4. 首次部署需要初始化数据库（拉股票/行业/财务，约 30-60 分钟）
docker compose run --rm backend python scripts/init_data.py
```

完成后浏览器打开 **http://localhost:8080**（端口可在 `.env` 里改 `HTTP_PORT`）。

**常用命令：**

| 命令 | 用途 |
|---|---|
| `docker compose up -d` | 后台启动 |
| `docker compose down` | 停止并删除容器（数据保留在 volume 里） |
| `docker compose logs -f` | 看实时日志 |
| `docker compose logs -f backend` | 仅看后端日志 |
| `docker compose ps` | 看服务状态 |
| `docker compose restart backend` | 仅重启后端 |
| `docker compose run --rm backend python scripts/xxx.py` | 跑维护脚本 |
| `docker volume inspect stock-screener-data` | 看数据卷信息 |
| `docker compose up -d --build` | 改了代码后重新构建 |

**数据备份**（SQLite 数据库在 named volume `stock-screener-data`）：

```bash
# 备份
docker run --rm -v stock-screener-data:/data -v $(pwd):/backup alpine \
    tar czf /backup/db-backup-$(date +%Y%m%d).tar.gz -C /data .

# 恢复
docker compose down
docker run --rm -v stock-screener-data:/data -v $(pwd):/backup alpine \
    sh -c "rm -rf /data/* && tar xzf /backup/db-backup-XXXX.tar.gz -C /data"
docker compose up -d
```

### 方式 B · 本地开发（推荐开发场景）

```bash
./start.sh
```

脚本会自动：
1. 创建/激活 `.venv`，安装后端依赖
2. 首次运行初始化数据库（约 30-60 分钟）
3. 启动后端（端口 8000）
4. 安装前端依赖并启动 Vite dev server（端口 5173）

完成后浏览器打开 **http://localhost:5173**。

### 环境要求

- **Docker 方式**：Docker 20+ / Docker Compose 2+，~2 GB 磁盘
- **本地方式**：Python **3.10+**（推荐 3.11） + Node.js **18+**，~3 GB 磁盘

### 默认账号

首次启动会通过迁移脚本创建一个管理员账号。具体凭据见 `backend/scripts/migrate_add_users.py` 或登录页注册新用户。

---

## 项目目录

```
stock-screener/
├── start.sh                  # 本地一键启动
├── docker-compose.yml        # Docker 部署编排
├── .env.example              # Docker 环境变量模板
├── README.md                 # 本文
├── docs/                     # 详细文档
│   ├── ARCHITECTURE.md       # 系统设计 / 信号引擎 / 评分体系
│   ├── API.md                # REST 接口参考
│   ├── DEVELOPMENT.md        # 开发指南 / 约定 / 常见任务
│   └── CHANGELOG.md          # 版本变更记录
├── backend/
│   ├── Dockerfile            # Python 3.11-slim 镜像
│   ├── .dockerignore
│   ├── main.py               # FastAPI 入口 / 定时任务注册
│   ├── config.py             # 全局配置（评分权重、阈值等，可被 user_settings.json 覆盖）
│   ├── database.py           # SQLAlchemy 引擎 / Session
│   ├── auth.py               # JWT + bcrypt 鉴权
│   ├── api/
│   │   ├── routes.py         # 业务 endpoint（200+ 行）
│   │   └── auth_routes.py    # 登录 / 注册 / 用户管理
│   ├── models/models.py      # SQLAlchemy ORM 模型
│   ├── engines/              # 核心算法
│   │   ├── company_scorer.py # 公司基本面评分（0-80）+ 估值评分（0-20）
│   │   ├── industry_scorer.py# 行业景气度评分（0-100）
│   │   ├── signal_engine.py  # 综合信号引擎（5 等级 + 8 道门槛）
│   │   └── paper_trade.py    # 模拟盘业务逻辑
│   ├── data/
│   │   ├── fetcher.py        # akshare 数据抓取（财报 / 行情 / 宏观 / 新闻）
│   │   └── sentiment.py      # 新闻情感分析（jieba 词典 + 关键词权重）
│   ├── backtest/
│   │   ├── engine.py         # 滚动窗口回测引擎
│   │   ├── evaluator.py      # 报告生成
│   │   └── optimizer.py      # 贝叶斯参数优化
│   ├── scripts/              # 一次性维护脚本（迁移、数据修复等）
│   └── stock_screener.db     # SQLite 数据库
└── frontend/
    ├── Dockerfile            # multi-stage：Node 构建 → nginx 服务
    ├── nginx.conf            # SPA fallback + /api 反代到 backend 容器
    ├── .dockerignore
    ├── package.json
    └── src/
        ├── views/            # 页面（10 个）
        ├── components/       # 共享组件（SignalBadge、ScoreBar 等）
        ├── api/index.js      # axios 封装 + endpoint 集合
        ├── router/           # Vue Router + 路由守卫
        ├── stores/           # Pinia auth store
        └── App.vue           # 主框架（侧边栏 + 路由 outlet）
```

---

## 进一步阅读

| 文档 | 内容 |
|---|---|
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | 信号引擎设计、5 等级阈值、8 道门槛、评分体系、模拟盘多账户、缓存策略 |
| **[docs/API.md](docs/API.md)** | 全部 REST 接口、参数说明、请求/响应示例 |
| **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)** | 添加新功能 / 改阈值 / 加新数据源 / 调试技巧 |
| **[docs/CHANGELOG.md](docs/CHANGELOG.md)** | 版本演进（v100 → v108 + 多账户重构、东方财富宏观源接入等） |

后端 OpenAPI 文档自动生成：**http://localhost:8000/docs**

---

## 关键设计决策（一句话总结）

- **数据驱动校准**：所有阈值（综合分门槛、行业 / 宏观 / 估值门槛）都是基于历史回测分桶分析得出，不是拍脑袋
- **5 等级信号**：必买 / 买入 / 持有 / 卖出 / 必卖。必买/必卖是综合分极端 + 全门槛通过的稀有信号
- **模拟盘多账户**：每个用户可有多个独立账户，互不干扰，方便分策略试验
- **数据陈旧降级**：宏观接口（akshare-sina）2025-08 后停更，已切换主源到东方财富 JSON API，akshare 留作 fallback
- **实时价缓存**：跨用户共享、10 分钟 TTL，工作日交易时段每 8 分钟自动后台预热，用户打开模拟盘秒开
