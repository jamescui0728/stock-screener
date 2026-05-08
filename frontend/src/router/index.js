import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const routes = [
  {
    path: '/',
    redirect: '/industries',
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/Login.vue'),
    meta: { title: '登录', public: true, layout: 'blank' },
  },
  {
    path: '/register',
    name: 'Register',
    component: () => import('@/views/Register.vue'),
    meta: { title: '注册', public: true, layout: 'blank' },
  },
  {
    path: '/industries',
    name: 'IndustryMap',
    component: () => import('@/views/IndustryMap.vue'),
    meta: { title: '行业全景' },
  },
  {
    path: '/stocks',
    name: 'CompanyScreener',
    component: () => import('@/views/CompanyScreener.vue'),
    meta: { title: '公司筛选' },
  },
  {
    path: '/stocks/:code',
    name: 'CompanyDetail',
    component: () => import('@/views/CompanyDetail.vue'),
    meta: { title: '公司详情' },
  },
  {
    path: '/watchlist',
    name: 'Watchlist',
    component: () => import('@/views/Watchlist.vue'),
    meta: { title: '自选股' },
  },
  {
    path: '/paper',
    name: 'PaperTrade',
    component: () => import('@/views/PaperTrade.vue'),
    meta: { title: '模拟盘' },
  },
  {
    path: '/backtest',
    name: 'BacktestReport',
    component: () => import('@/views/BacktestReport.vue'),
    meta: { title: '回测中心' },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/views/Settings.vue'),
    meta: { title: '参数设置' },
  },
  {
    path: '/users',
    name: 'Users',
    component: () => import('@/views/Users.vue'),
    meta: { title: '用户管理', adminOnly: true },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to) => {
  document.title = `${to.meta.title || ''} — 长期价值股票筛选`

  // 未登录时：除公共页面外，一律跳登录
  const auth = useAuthStore()
  if (!to.meta.public && !auth.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  // 已登录再访问登录/注册页 → 回首页
  if (to.meta.public && auth.isLoggedIn) {
    return { path: '/industries' }
  }
  // 管理员专属页面：非管理员踢回首页
  if (to.meta.adminOnly && !auth.user?.is_admin) {
    return { path: '/industries' }
  }
})

export default router
