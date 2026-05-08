#!/bin/bash
set -e

echo "=============================="
echo " 长期价值股票筛选系统 启动脚本"
echo "=============================="

# ── 检测 Python 命令 ──
if command -v python3 &>/dev/null; then
  PY=python3
elif command -v python &>/dev/null; then
  PY=python
else
  echo "❌ 未找到 Python，请先安装 Python 3.10+"
  exit 1
fi
echo "✓ 使用 Python: $($PY --version)"

# ── 后端 ──
cd "$(dirname "$0")/backend"

# 创建/激活虚拟环境
if [ ! -d ".venv" ]; then
  echo "[1/4] 创建虚拟环境 .venv ..."
  $PY -m venv .venv
else
  echo "[1/4] 使用已有虚拟环境 .venv"
fi

source .venv/bin/activate
echo "✓ 虚拟环境已激活"

echo "      安装后端依赖（首次较慢，请耐心等待）..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "✓ 后端依赖安装完毕"

# 首次运行才做初始化
if [ ! -f "stock_screener.db" ]; then
  echo "[2/4] 首次运行，初始化数据库..."
  python scripts/init_data.py
else
  echo "[2/4] 数据库已存在，跳过初始化"
fi

echo "[3/4] 启动后端服务（端口 8000）..."
# 释放已占用的端口
if lsof -ti tcp:8000 &>/dev/null; then
  echo "      ⚠ 端口 8000 被占用，正在强制释放..."
  lsof -ti tcp:8000 | xargs kill -9 2>/dev/null || true
  sleep 2
fi
# 不使用 --reload（后台任务期间热重载会打断正在运行的数据抓取）
# 如需开发模式热重载，改为：uvicorn main:app --port 8000 --reload
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --log-level warning &
BACKEND_PID=$!

# ── 前端 ──
echo "[4/4] 安装并启动前端（端口 5173）..."
cd ../frontend

# 检测 Node/npm
if ! command -v npm &>/dev/null; then
  echo "❌ 未找到 npm，请先安装 Node.js 18+"
  kill $BACKEND_PID 2>/dev/null
  exit 1
fi
echo "✓ 使用 Node: $(node --version)"

npm install --silent
# 释放已占用的前端端口
if lsof -ti tcp:5173 &>/dev/null; then
  echo "      ⚠ 端口 5173 被占用，正在强制释放..."
  lsof -ti tcp:5173 | xargs kill -9 2>/dev/null || true
  sleep 2
fi
npm run dev &
FRONTEND_PID=$!

echo ""
echo "=============================="
echo " 后端 API: http://localhost:8000"
echo " 前端 UI:  http://localhost:5173"
echo " API文档:  http://localhost:8000/docs"
echo "=============================="
echo " 按 Ctrl+C 停止所有服务"

trap "echo '正在停止服务...'; kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; deactivate 2>/dev/null" EXIT INT TERM
wait
