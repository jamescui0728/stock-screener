/**
 * 认证 Store：持久化 token + 当前用户
 * - token/user 存 localStorage，刷新页面也能保持登录
 * - axios 请求拦截器从这里取 token 注入 Authorization 头
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

const TOKEN_KEY = 'ss_auth_token'
const USER_KEY  = 'ss_auth_user'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem(TOKEN_KEY) || '')
  const user  = ref(JSON.parse(localStorage.getItem(USER_KEY) || 'null'))

  const isLoggedIn = computed(() => !!token.value)
  const displayName = computed(() =>
    user.value?.name || user.value?.phone || '访客',
  )

  function setAuth(t, u) {
    token.value = t || ''
    user.value  = u || null
    if (t) localStorage.setItem(TOKEN_KEY, t)
    else   localStorage.removeItem(TOKEN_KEY)
    if (u) localStorage.setItem(USER_KEY, JSON.stringify(u))
    else   localStorage.removeItem(USER_KEY)
  }

  function logout() {
    setAuth('', null)
  }

  return { token, user, isLoggedIn, displayName, setAuth, logout }
})
