# 后端镜像（Python 3.11 + FastAPI）
# 构建上下文 = 项目根（需要同时拷贝 backend/ + docs/ + README.md）
# 因此本 Dockerfile 假定 docker build 时 -f backend/Dockerfile 且 context 为 .
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 系统依赖：
#   - gcc / libffi-dev：编译 bcrypt（passlib[bcrypt] 依赖）
#   - tzdata：让容器内时区一致（APScheduler 的 cron 表达式按本地时区跑）
#   - curl：HEALTHCHECK 用
#   - libxml2-dev / libxslt-dev：akshare 解析网页时偶尔用 lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libffi-dev tzdata curl libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

WORKDIR /app

# 先拷依赖文件，让 docker layer cache 最大化命中
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip \
    && pip install -r /app/backend/requirements.txt

# 拷应用代码 + 文档（main.py 里 `_PROJECT_ROOT = parent.parent` → /app）
# 所以 docs/ 必须在 /app/docs，这样 /project-docs 路由才能找到 .md
COPY backend /app/backend
COPY docs /app/docs
COPY README.md /app/README.md

WORKDIR /app/backend

# 数据卷挂载点（SQLite 数据库 + WAL/SHM 文件）
# DATABASE_URL 通过 env 覆盖到 sqlite:////data/stock_screener.db
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 默认命令：生产模式启动（不带 --reload）
# 开发模式如需热重载，docker compose run 时覆盖 command
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
