import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// ── 请求拦截：自动注入 Authorization 头 ──
http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('ss_auth_token')
  if (token) cfg.headers = { ...(cfg.headers || {}), Authorization: `Bearer ${token}` }
  return cfg
})

// ── 响应拦截：统一错误提示 + 401 强制登出 ──
http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const status = err.response?.status
    const msg    = err.response?.data?.detail || err.message || '请求失败'

    if (status === 401) {
      // token 过期/无效 → 清空并跳回登录
      localStorage.removeItem('ss_auth_token')
      localStorage.removeItem('ss_auth_user')
      // 避免在登录/注册页重复提示
      const path = window.location.pathname
      if (path !== '/login' && path !== '/register') {
        ElMessage.warning('登录已过期，请重新登录')
        // 用 replace 避免生成历史记录；携带 redirect 便于登录后返回
        window.location.replace(`/login?redirect=${encodeURIComponent(path)}`)
      }
    } else {
      ElMessage.error(msg)
    }
    return Promise.reject(err)
  },
)

// ── 行业 ──
export const industryApi = {
  list: (minScore = 0)  => http.get('/industries', { params: { min_score: minScore } }),
  rescore: (code)       => http.post(`/industries/${code}/score`),
  rescoreAll: ()        => http.post('/industries/rescore-all'),
}

// ── 股票 ──
export const stockApi = {
  list: (params)            => http.get('/stocks', { params }),
  detail: (code)            => http.get(`/stocks/${code}`),
  refreshSignal: (code)     => http.post(`/stocks/${code}/signal`),
  refreshAllSignals: ()     => http.post('/signals/refresh-all'),
}

// ── 自选股 ──
export const watchlistApi = {
  get: ()                   => http.get('/watchlist'),
  add: (code, note = '')    => http.post('/watchlist', { stock_code: code, note }),
  remove: (code)            => http.delete(`/watchlist/${code}`),
  news:         (days = 7, limit = 50) =>
                    http.get('/watchlist/news', { params: { days, limit } }),
  refreshNews:  ()          => http.post('/watchlist/news/refresh'),
}

// ── 回测 ──
export const backtestApi = {
  run:      (body)   => http.post('/backtest/run', body),
  list:     ()       => http.get('/backtest/runs'),
  report:   (runId)  => http.get(`/backtest/runs/${runId}`),
  optimize: (body)   => http.post('/backtest/optimize', body),
  snapshot: ()       => http.get('/backtest/progress/snapshot'),

  /**
   * 订阅 SSE 进度流
   * @param {function} onMessage  每条进度回调 (progressObj) => void
   * @param {function} onDone     完成/出错回调 (progressObj) => void
   * @returns {EventSource}       调用 .close() 可手动断开
   */
  subscribeProgress(onMessage, onDone) {
    const es = new EventSource('/api/backtest/progress')
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        onMessage(data)
        if (data.status === 'done' || data.status === 'error') {
          es.close()
          onDone && onDone(data)
        }
      } catch (_) {}
    }
    es.onerror = () => {
      es.close()
      onDone && onDone({ status: 'error', error_msg: '连接中断' })
    }
    return es
  },
}

// ── 数据管理 ──
export const dataApi = {
  updateMacro: ()           => http.post('/data/update-macro'),
  updateFinancials: (limit) => http.post('/data/update-financials', null, { params: { limit } }),
  updatePrices: (limit = 0) => http.post('/data/update-prices', null, { params: { limit } }),
  updateNews: (code)        => http.post(`/data/update-news/${code}`),
  refreshAll: ()            => http.post('/data/refresh-all'),
  refreshProgress: ()       => http.get('/data/refresh-progress'),
  financialStatus: ()       => http.get('/status/financial-count'),
}

// ── 模拟盘 ──
// 多账户模式：所有"账户相关"接口都可携带 account_id；不传则后端用默认账户。
export const paperApi = {
  rules:        ()                      => http.get('/paper/rules'),
  quote:        (code)                  => http.get(`/paper/quote/${code}`),

  // 账户管理
  listAccounts:   ()                    => http.get('/paper/accounts'),
  createAccount:  (name, initial_cash)  => http.post('/paper/accounts', { name, initial_cash: initial_cash || null }),
  updateAccount:  (id, patch)           => http.put(`/paper/accounts/${id}`, patch),
  deleteAccount:  (id)                  => http.delete(`/paper/accounts/${id}`),

  // 账户快照 / 流水 / 交易（account_id 可选）
  account:      (account_id)            => http.get('/paper/account', { params: { account_id } }),
  transactions: (account_id, limit = 200) =>
                   http.get('/paper/transactions', { params: { account_id, limit } }),
  buy:          (account_id, stock_code, shares, price, note) =>
                   http.post('/paper/buy',  { account_id, stock_code, shares, price, note }),
  sell:         (account_id, stock_code, shares, price, note) =>
                   http.post('/paper/sell', { account_id, stock_code, shares, price, note }),
  reset:        (account_id, initial_cash) =>
                   http.post('/paper/reset', { account_id, initial_cash: initial_cash || null }),

  // 手动强制刷新当前用户所有账户持仓的实时价缓存（不等 cron）
  // 后端 hard cap ~14s（per_call 8s + 6s buffer），但 sina 抖动 + 多用户并发时
  // 仍可能逼近，axios 单请求 60s timeout 确保不被全局 30s 截断
  warmupCache:  ()                      => http.post('/paper/cache/warmup', null, { timeout: 60000 }),
}

// ── 用户认证 ──
export const authApi = {
  register:       (phone, password, name) =>
                    http.post('/auth/register', { phone, password, name }),
  login:          (phone, password) =>
                    http.post('/auth/login',    { phone, password }),
  me:             () => http.get('/auth/me'),
  changePassword: (old_password, new_password) =>
                    http.post('/auth/change-password', { old_password, new_password }),
  logout:         () => http.post('/auth/logout'),
}

// ── 用户管理（管理员专用） ──
export const usersApi = {
  list:          ()                  => http.get('/auth/users'),
  create:        (body)              => http.post('/auth/users', body),
  update:        (id, patch)         => http.patch(`/auth/users/${id}`, patch),
  resetPassword: (id, new_password)  =>
                    http.post(`/auth/users/${id}/reset-password`, { new_password }),
  remove:        (id)                => http.delete(`/auth/users/${id}`),
}

// ── 设置 ──
export const settingsApi = {
  get:   ()       => http.get('/settings'),
  save:  (body)   => http.put('/settings', body),
  reset: ()       => http.post('/settings/reset'),
}
