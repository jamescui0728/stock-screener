<template>
  <div class="users-page">
    <!-- 头部统计卡 -->
    <div class="stats">
      <el-card shadow="never" class="stat-card">
        <div class="stat-label">总用户数</div>
        <div class="stat-value">{{ stats.total }}</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-label">管理员</div>
        <div class="stat-value admin">{{ stats.admins }}</div>
      </el-card>
      <el-card shadow="never" class="stat-card">
        <div class="stat-label">已禁用</div>
        <div class="stat-value disabled">{{ stats.disabled }}</div>
      </el-card>
    </div>

    <!-- 用户表 -->
    <el-card shadow="never">
      <template #header>
        <div class="card-head">
          <span>用户列表</span>
          <div class="card-head-actions">
            <el-button size="small" type="primary" :icon="Plus" @click="openCreate">
              新增用户
            </el-button>
            <el-button size="small" :icon="Refresh" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <el-table
        :data="users"
        v-loading="loading"
        stripe
        border
        style="width: 100%"
      >
        <el-table-column prop="id" label="ID" width="70" align="center" />

        <el-table-column label="用户" min-width="200">
          <template #default="{ row }">
            <div class="user-cell">
              <el-avatar :size="32" class="mini-avatar">
                {{ (row.name?.[0] || row.phone?.[0] || 'U').toUpperCase() }}
              </el-avatar>
              <div>
                <div class="u-name">{{ row.name || '—' }}</div>
                <div class="u-phone">{{ row.phone }}</div>
              </div>
            </div>
          </template>
        </el-table-column>

        <el-table-column label="角色" width="110" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_admin" type="danger" size="small">管理员</el-tag>
            <el-tag v-else type="info" size="small" effect="plain">普通用户</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">启用</el-tag>
            <el-tag v-else type="warning" size="small">已禁用</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="自选股" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.watchlist_count }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="模拟盘" width="100" align="center">
          <template #default="{ row }">
            <el-icon v-if="row.has_paper_account" color="#67c23a"><CircleCheck /></el-icon>
            <span v-else style="color:#aaa">—</span>
          </template>
        </el-table-column>

        <el-table-column label="最近登录" width="180">
          <template #default="{ row }">
            <span v-if="row.last_login_at" class="ts">
              {{ formatTs(row.last_login_at) }}
            </span>
            <span v-else style="color:#aaa">未登录</span>
          </template>
        </el-table-column>

        <el-table-column label="注册时间" width="180">
          <template #default="{ row }">
            <span class="ts">{{ formatTs(row.created_at) }}</span>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="290" fixed="right" align="center">
          <template #default="{ row }">
            <el-button size="small" @click="openEdit(row)">编辑</el-button>
            <el-button size="small" type="warning" @click="openResetPwd(row)">
              重置密码
            </el-button>
            <el-button
              size="small" type="danger"
              :disabled="row.id === me?.id"
              @click="doDelete(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 新增用户对话框 -->
    <el-dialog v-model="createDialog" title="新增用户" width="460px">
      <el-form
        ref="createFormRef"
        :model="createForm"
        :rules="createRules"
        label-width="90px"
      >
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="createForm.phone" maxlength="11" placeholder="11 位中国大陆手机号" />
        </el-form-item>
        <el-form-item label="初始密码" prop="password">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            placeholder="至少 6 位"
          />
        </el-form-item>
        <el-form-item label="昵称" prop="name">
          <el-input v-model="createForm.name" maxlength="30" placeholder="（可留空）" />
        </el-form-item>
        <el-form-item label="管理员">
          <el-switch v-model="createForm.is_admin" active-text="是" inactive-text="否" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="createForm.is_active"
            active-text="启用" inactive-text="禁用"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitCreate">
          创建
        </el-button>
      </template>
    </el-dialog>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editDialog" title="编辑用户" width="440px">
      <el-form
        v-if="editing"
        :model="editForm"
        label-width="90px"
      >
        <el-form-item label="手机号">
          <el-input :value="editing.phone" disabled />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="editForm.name" maxlength="30" placeholder="（可留空）" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch
            v-model="editForm.is_active"
            :disabled="editing.id === me?.id"
            active-text="启用" inactive-text="禁用"
          />
          <div v-if="editing.id === me?.id" class="hint">不能禁用自己</div>
        </el-form-item>
        <el-form-item label="管理员">
          <el-switch
            v-model="editForm.is_admin"
            :disabled="editing.id === me?.id"
            active-text="是" inactive-text="否"
          />
          <div v-if="editing.id === me?.id" class="hint">不能撤销自己的管理员身份</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitEdit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 重置密码对话框 -->
    <el-dialog v-model="pwdDialog" title="重置密码" width="420px">
      <el-form :model="pwdForm" label-width="90px">
        <el-form-item label="目标用户">
          <span>{{ pwdTarget?.phone }}（{{ pwdTarget?.name || '—' }}）</span>
        </el-form-item>
        <el-form-item label="新密码">
          <el-input
            v-model="pwdForm.new_password"
            type="password"
            show-password
            placeholder="至少 6 位"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pwdDialog = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submitResetPwd">
          确认重置
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Plus } from '@element-plus/icons-vue'
import { usersApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const me = computed(() => auth.user)

const users     = ref([])
const loading   = ref(false)
const submitting = ref(false)

const stats = computed(() => ({
  total:    users.value.length,
  admins:   users.value.filter(u => u.is_admin).length,
  disabled: users.value.filter(u => !u.is_active).length,
}))

async function load() {
  loading.value = true
  try {
    users.value = await usersApi.list()
  } catch {} finally {
    loading.value = false
  }
}

onMounted(load)

// ── 新增 ──
const createDialog  = ref(false)
const createFormRef = ref(null)
const createForm    = reactive({
  phone:     '',
  password:  '',
  name:      '',
  is_admin:  false,
  is_active: true,
})
const createRules = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    {
      pattern: /^1[3-9]\d{9}$/,
      message: '请输入正确的 11 位手机号',
      trigger: 'blur',
    },
  ],
  password: [
    { required: true, message: '请输入初始密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码须 6-64 位', trigger: 'blur' },
  ],
}

function openCreate() {
  createForm.phone     = ''
  createForm.password  = ''
  createForm.name      = ''
  createForm.is_admin  = false
  createForm.is_active = true
  createFormRef.value?.clearValidate?.()
  createDialog.value   = true
}

async function submitCreate() {
  const ok = await createFormRef.value?.validate().catch(() => false)
  if (!ok) return
  submitting.value = true
  try {
    const created = await usersApi.create({
      phone:     createForm.phone,
      password:  createForm.password,
      name:      createForm.name || null,
      is_admin:  createForm.is_admin,
      is_active: createForm.is_active,
    })
    users.value.push(created)
    ElMessage.success('已创建')
    createDialog.value = false
  } catch {} finally {
    submitting.value = false
  }
}

// ── 编辑 ──
const editDialog = ref(false)
const editing    = ref(null)
const editForm   = reactive({ name: '', is_active: true, is_admin: false })

function openEdit(row) {
  editing.value       = row
  editForm.name       = row.name || ''
  editForm.is_active  = row.is_active
  editForm.is_admin   = row.is_admin
  editDialog.value    = true
}

async function submitEdit() {
  submitting.value = true
  try {
    const updated = await usersApi.update(editing.value.id, {
      name:      editForm.name,
      is_active: editForm.is_active,
      is_admin:  editForm.is_admin,
    })
    // 本地更新
    const idx = users.value.findIndex(u => u.id === updated.id)
    if (idx >= 0) users.value[idx] = updated
    ElMessage.success('已保存')
    editDialog.value = false
  } catch {} finally {
    submitting.value = false
  }
}

// ── 重置密码 ──
const pwdDialog = ref(false)
const pwdTarget = ref(null)
const pwdForm   = reactive({ new_password: '' })

function openResetPwd(row) {
  pwdTarget.value      = row
  pwdForm.new_password = ''
  pwdDialog.value      = true
}

async function submitResetPwd() {
  if (!pwdForm.new_password || pwdForm.new_password.length < 6) {
    ElMessage.warning('新密码至少 6 位')
    return
  }
  submitting.value = true
  try {
    await usersApi.resetPassword(pwdTarget.value.id, pwdForm.new_password)
    ElMessage.success('密码已重置')
    pwdDialog.value = false
  } catch {} finally {
    submitting.value = false
  }
}

// ── 删除 ──
async function doDelete(row) {
  try {
    await ElMessageBox.confirm(
      `确认删除用户 ${row.phone}（${row.name || '—'}）？\n` +
      `该用户的自选股和模拟盘账户也会一并清除，且无法恢复。`,
      '危险操作',
      {
        confirmButtonText: '确认删除',
        cancelButtonText:  '取消',
        type: 'warning',
      },
    )
  } catch { return }
  try {
    await usersApi.remove(row.id)
    users.value = users.value.filter(u => u.id !== row.id)
    ElMessage.success('已删除')
  } catch {}
}

// ── 工具 ──
function formatTs(s) {
  if (!s) return '—'
  // "2026-04-19 08:33:40.772245" → "2026-04-19 08:33"
  return s.slice(0, 16)
}
</script>

<style scoped>
.users-page { padding-bottom: 40px; }

.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}
.stat-card {
  background: #fff;
  border: 1px solid #e8eaed;
}
.stat-label { font-size: 13px; color: #888; }
.stat-value { font-size: 28px; font-weight: 700; color: #1f2328; margin-top: 4px; }
.stat-value.admin    { color: #f56c6c; }
.stat-value.disabled { color: #e6a23c; }

.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-head-actions {
  display: flex;
  gap: 8px;
}

.user-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}
.mini-avatar {
  background: linear-gradient(135deg, #409eff, #337ecc);
  color: #fff;
  font-weight: 600;
  font-size: 14px;
  flex-shrink: 0;
}
.u-name  { font-size: 14px; color: #1f2328; font-weight: 500; }
.u-phone { font-size: 12px; color: #888; }

.ts { font-size: 12px; color: #606266; font-family: Menlo, monospace; }

.hint { font-size: 12px; color: #e6a23c; margin-top: 2px; }
</style>
