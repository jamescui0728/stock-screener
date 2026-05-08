<template>
  <!-- 登录/注册页使用空白布局（无侧边栏/顶栏） -->
  <router-view v-if="$route.meta.layout === 'blank'" />

  <el-container v-else class="app-layout">
    <!-- 侧边导航 -->
    <el-aside width="220px" class="aside">
      <div class="logo">
        <el-icon size="22"><TrendCharts /></el-icon>
        <span>长期价值筛选</span>
      </div>
      <el-menu
        :default-active="$route.path"
        router
        background-color="#001529"
        text-color="#ffffffa0"
        active-text-color="#ffffff"
      >
        <el-menu-item index="/industries">
          <el-icon><PieChart /></el-icon>
          <span>行业全景</span>
        </el-menu-item>
        <el-menu-item index="/stocks">
          <el-icon><DataAnalysis /></el-icon>
          <span>公司筛选</span>
        </el-menu-item>
        <el-menu-item index="/watchlist">
          <el-icon><Star /></el-icon>
          <span>自选股</span>
        </el-menu-item>
        <el-menu-item index="/paper">
          <el-icon><Wallet /></el-icon>
          <span>模拟盘</span>
        </el-menu-item>
        <el-menu-item index="/backtest">
          <el-icon><Histogram /></el-icon>
          <span>回测中心</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon>
          <span>参数设置</span>
        </el-menu-item>
        <el-menu-item v-if="auth.user?.is_admin" index="/users">
          <el-icon><UserFilled /></el-icon>
          <span>用户管理</span>
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <!-- 顶部 -->
      <el-header class="header">
        <div class="header-left">
          <span class="page-title">{{ $route.meta.title }}</span>
        </div>
        <div class="header-right">
          <!-- 运行中：显示进度摘要 + 查看详情 -->
          <template v-if="refreshing">
            <span class="refresh-running-hint">
              <el-icon class="spin"><Loading /></el-icon>
              {{ runningTask || '更新中...' }}
            </span>
            <el-button size="small" text @click="showProgress = true">查看进度</el-button>
          </template>
          <!-- 空闲：普通按钮 -->
          <el-button v-else size="small" @click="startRefresh">
            <el-icon><Refresh /></el-icon> 更新数据
          </el-button>

          <!-- 用户菜单 -->
          <el-dropdown trigger="click" @command="onUserMenu">
            <div class="user-chip">
              <el-avatar :size="28" class="user-avatar">
                {{ (auth.displayName?.[0] || 'U').toUpperCase() }}
              </el-avatar>
              <span class="user-name">{{ auth.displayName }}</span>
              <el-icon><ArrowDown /></el-icon>
            </div>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  <el-icon><Cellphone /></el-icon>
                  {{ auth.user?.phone }}
                </el-dropdown-item>
                <el-dropdown-item divided command="change-password">
                  <el-icon><Key /></el-icon> 修改密码
                </el-dropdown-item>
                <el-dropdown-item command="logout">
                  <el-icon><SwitchButton /></el-icon> 退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 主内容 -->
      <el-main class="main">
        <!--
          路由切换强制重建策略（避免 BacktestReport 的 el-drawer Teleport +
          异步组件 + 旧实例残留导致"点击左侧菜单页面不刷新"）：
          1) 用 v-if + nextTick 强制销毁再挂载：route 变化时先把 routeKey
             置 null 让 router-view 整体卸载，下一个 tick 再恢复 → 保证
             老组件完全销毁，新组件干净初始化。
          2) 不使用 <transition mode="out-in">（会等 Teleport 节点卸载）。
        -->
        <router-view v-if="routeKey" :key="routeKey" />
      </el-main>
    </el-container>
  </el-container>

  <!-- ══ 修改密码对话框 ══ -->
  <el-dialog
    v-model="showChangePwd"
    title="修改密码"
    width="420px"
    :close-on-click-modal="false"
  >
    <el-form
      ref="pwdFormRef"
      :model="pwdForm"
      :rules="pwdRules"
      label-position="top"
    >
      <el-form-item label="原密码" prop="old_password">
        <el-input v-model="pwdForm.old_password" type="password" show-password />
      </el-form-item>
      <el-form-item label="新密码" prop="new_password">
        <el-input v-model="pwdForm.new_password" type="password" show-password />
      </el-form-item>
      <el-form-item label="确认新密码" prop="confirm">
        <el-input v-model="pwdForm.confirm" type="password" show-password />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="showChangePwd = false">取消</el-button>
      <el-button type="primary" :loading="pwdSubmitting" @click="submitChangePwd">
        提交
      </el-button>
    </template>
  </el-dialog>

  <!-- ══ 进度抽屉 ══ -->
  <el-drawer
    v-model="showProgress"
    title="数据更新进度"
    direction="rtl"
    size="360px"
  >
    <div class="prog-drawer">
      <!-- 整体状态 -->
      <div class="prog-overall">
        <el-tag :type="overallTagType" size="large" effect="dark">
          {{ overallLabel }}
        </el-tag>
        <span class="prog-elapsed" v-if="refreshing">已运行 {{ elapsed }}s</span>
      </div>

      <!-- 三步任务 -->
      <div class="task-list">
        <div
          v-for="(task, i) in tasks"
          :key="i"
          class="task-item"
          :class="'task-' + task.status"
        >
          <div class="task-icon">
            <el-icon v-if="task.status === 'done'"><CircleCheck /></el-icon>
            <el-icon v-else-if="task.status === 'running'" class="spin"><Loading /></el-icon>
            <el-icon v-else-if="task.status === 'error'"><CircleClose /></el-icon>
            <span v-else class="task-num">{{ i + 1 }}</span>
          </div>
          <div class="task-body">
            <div class="task-name">{{ task.name }}</div>
            <div class="task-detail" v-if="task.detail">{{ task.detail }}</div>
            <div class="task-elapsed" v-if="task.status !== 'pending'">
              {{ task.status === 'running' ? '进行中...' : `耗时 ${task.elapsed}s` }}
            </div>
          </div>
        </div>
      </div>

      <!-- 完成提示 -->
      <el-alert
        v-if="progress.overall === 'done'"
        title="全部完成！行业评分和买卖信号已是最新状态。"
        type="success" :closable="false"
        style="margin-top:16px"
      />

      <!-- 再次更新 -->
      <el-button
        v-if="progress.overall === 'done'"
        style="width:100%;margin-top:12px"
        @click="startRefresh"
      >
        再次更新
      </el-button>
    </div>
  </el-drawer>
</template>

<script setup>
import { ref, reactive, computed, onUnmounted, nextTick, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { authApi, dataApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

// ── 路由强制重建：v-if+null+nextTick 保证老组件完全销毁 ──
const route = useRoute()
const routeKey = ref(route.fullPath)
watch(() => route.fullPath, async (p) => {
  routeKey.value = null
  await nextTick()
  routeKey.value = p
})

const refreshing    = ref(false)
const showProgress  = ref(false)
const progress      = ref({ overall: 'idle', tasks: [] })
const tasks         = computed(() => progress.value.tasks || [])
let   pollTimer     = null
let   elapsedTimer  = null
const elapsed       = ref(0)

const overallLabel = computed(() => ({
  idle: '待启动', running: '更新中', done: '已完成',
}[progress.value.overall] ?? ''))

const overallTagType = computed(() => ({
  idle: 'info', running: 'primary', done: 'success',
}[progress.value.overall] ?? 'info'))

const runningTask = computed(() => {
  const t = tasks.value.find(t => t.status === 'running')
  return t ? `${t.name}...` : '更新中...'
})

async function startRefresh() {
  refreshing.value   = true
  showProgress.value = true
  elapsed.value      = 0
  progress.value     = {
    overall: 'running',
    tasks: [
      { name: '宏观数据', status: 'pending', detail: '', elapsed: 0 },
      { name: '行业评分', status: 'pending', detail: '', elapsed: 0 },
      { name: '信号刷新', status: 'pending', detail: '', elapsed: 0 },
    ],
  }

  try {
    await dataApi.refreshAll()
    startPolling()
  } catch {
    refreshing.value = false
  }
}

function startPolling() {
  stopPolling()

  // 计时器
  elapsedTimer = setInterval(() => { elapsed.value++ }, 1000)

  // 轮询进度
  pollTimer = setInterval(async () => {
    try {
      const data = await dataApi.refreshProgress()
      progress.value = data
      if (data.overall === 'done') {
        stopPolling()
        refreshing.value = false
        ElMessage.success('数据更新完成！')
      }
    } catch {}
  }, 2000)
}

function stopPolling() {
  if (pollTimer)     { clearInterval(pollTimer);     pollTimer = null }
  if (elapsedTimer)  { clearInterval(elapsedTimer);  elapsedTimer = null }
}

onUnmounted(stopPolling)

// ── 用户菜单 ──
const showChangePwd  = ref(false)
const pwdSubmitting  = ref(false)
const pwdFormRef     = ref(null)
const pwdForm        = reactive({ old_password: '', new_password: '', confirm: '' })
const pwdRules = {
  old_password: [{ required: true, message: '请输入原密码', trigger: 'blur' }],
  new_password: [
    { required: true, message: '请输入新密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码须 6-64 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入新密码', trigger: 'blur' },
    {
      validator: (_, v, cb) => {
        if (v !== pwdForm.new_password) cb(new Error('两次输入的密码不一致'))
        else cb()
      },
      trigger: 'blur',
    },
  ],
}

function onUserMenu(cmd) {
  if (cmd === 'logout')          doLogout()
  else if (cmd === 'change-password') {
    pwdForm.old_password = pwdForm.new_password = pwdForm.confirm = ''
    showChangePwd.value = true
  }
}

async function doLogout() {
  try {
    await ElMessageBox.confirm('确认退出登录？', '提示', {
      confirmButtonText: '退出',
      cancelButtonText:  '取消',
      type: 'warning',
    })
  } catch { return }
  try { await authApi.logout() } catch {}
  auth.logout()
  // 整页刷新，清空所有组件里可能缓存的用户数据
  window.location.href = '/login'
}

async function submitChangePwd() {
  const ok = await pwdFormRef.value?.validate().catch(() => false)
  if (!ok) return
  pwdSubmitting.value = true
  try {
    await authApi.changePassword(pwdForm.old_password, pwdForm.new_password)
    ElMessage.success('密码已修改，请重新登录')
    showChangePwd.value = false
    auth.logout()
    window.location.href = '/login'
  } catch {
    // 错误由拦截器提示
  } finally {
    pwdSubmitting.value = false
  }
}
</script>

<style>
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: #f0f2f5;
  color: #1f2328;
}

.app-layout { height: 100vh; }

.aside {
  background: #001529;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 16px;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  border-bottom: 1px solid #ffffff18;
}

.header {
  background: #fff;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  box-shadow: 0 1px 4px rgba(0,0,0,.08);
  height: 56px !important;
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: #1f2328;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.refresh-running-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: #409eff;
}

/* 用户菜单 */
.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px 4px 4px;
  border-radius: 20px;
  cursor: pointer;
  transition: background 0.15s;
}
.user-chip:hover { background: #f4f5f7; }
.user-avatar {
  background: linear-gradient(135deg, #409eff, #337ecc);
  color: #fff;
  font-weight: 600;
  font-size: 13px;
}
.user-name {
  font-size: 13px;
  color: #1f2328;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.main {
  padding: 24px;
  overflow-y: auto;
}

/* 路由过渡 */
.fade-enter-active, .fade-leave-active { transition: opacity .18s; }
.fade-enter-from, .fade-leave-to       { opacity: 0; }

/* 全局卡片 */
.el-card { border-radius: 8px; border: 1px solid #e8eaed; }
.el-card__header { font-weight: 600; }

/* 旋转动画 */
.spin { animation: spin 1s linear infinite; }
@keyframes spin { from { transform: rotate(0) } to { transform: rotate(360deg) } }
</style>

<style scoped>
/* 进度抽屉 */
.prog-drawer { padding: 4px 0; }

.prog-overall {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
}
.prog-elapsed { font-size: 13px; color: #888; }

.task-list { display: flex; flex-direction: column; gap: 16px; }

.task-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 14px 16px;
  border-radius: 8px;
  border: 1px solid #e8eaed;
  transition: all .2s;
}
.task-pending  { background: #fafafa; }
.task-running  { background: #ecf5ff; border-color: #b3d8ff; }
.task-done     { background: #f0f9eb; border-color: #c2e7b0; }
.task-error    { background: #fef0f0; border-color: #fbc4c4; }

.task-icon {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.task-pending  .task-icon { background: #e8eaed; }
.task-running  .task-icon { color: #409eff; background: #d9ecff; }
.task-done     .task-icon { color: #67c23a; background: #e1f3d8; }
.task-error    .task-icon { color: #f56c6c; background: #fde2e2; }

.task-num { font-size: 12px; font-weight: 700; color: #aaa; }

.task-body { flex: 1; }
.task-name   { font-size: 14px; font-weight: 600; color: #1f2328; }
.task-detail { font-size: 12px; color: #67c23a; margin-top: 2px; }
.task-elapsed { font-size: 12px; color: #888; margin-top: 2px; }
.task-error .task-detail { color: #f56c6c; }
</style>
