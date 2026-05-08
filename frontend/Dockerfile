# 前端镜像（多阶段：Node 构建 → nginx 服务静态资源）
# 这套设计让最终镜像只有 ~25 MB（nginx:alpine + dist 构建产物）

# ─── Stage 1: 构建静态资源 ───
FROM node:20-alpine AS builder

WORKDIR /app

# 先装依赖（package*.json 改动才会触发重装）
COPY package*.json ./
RUN npm ci

# 拷源码 + 构建
COPY . .
RUN npm run build

# ─── Stage 2: nginx 服务 ───
FROM nginx:alpine

# 移除默认配置，用我们自己的（含 SPA fallback + /api 反代）
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 把构建产物拷到 nginx 默认 web 根目录
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost/ || exit 1

# nginx:alpine 默认 CMD 即 ["nginx", "-g", "daemon off;"]，无需覆盖
