<template>
  <div class="auth-page">
    <div class="auth-card">
      <div class="auth-logo">
        <el-icon size="28" color="#409eff"><TrendCharts /></el-icon>
        <span>长期价值筛选</span>
      </div>
      <h2 class="auth-title">注册账户</h2>
      <p class="auth-sub">使用手机号创建账户，登录即可使用模拟盘与自选股</p>

      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        size="large"
        label-position="top"
        @submit.prevent="onSubmit"
      >
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="form.phone" placeholder="11 位手机号" maxlength="11">
            <template #prefix><el-icon><Cellphone /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-form-item label="昵称（可选）" prop="name">
          <el-input v-model="form.name" placeholder="你希望显示的昵称" maxlength="20">
            <template #prefix><el-icon><User /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-form-item label="密码" prop="password">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="至少 6 位"
            show-password
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-form-item label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            placeholder="再次输入密码"
            show-password
            @keyup.enter="onSubmit"
          >
            <template #prefix><el-icon><Lock /></el-icon></template>
          </el-input>
        </el-form-item>

        <el-button
          type="primary"
          native-type="submit"
          :loading="loading"
          style="width: 100%; height: 44px; font-size: 15px; font-weight: 600"
          @click="onSubmit"
        >
          注 册
        </el-button>
      </el-form>

      <div class="auth-footer">
        已经有账号？
        <router-link to="/login" class="auth-link">直接登录</router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive } from 'vue'
import { ElMessage } from 'element-plus'
import { authApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const formRef = ref(null)
const loading = ref(false)
const form = reactive({ phone: '', name: '', password: '', confirm: '' })

const validateConfirm = (_, value, cb) => {
  if (value !== form.password) cb(new Error('两次输入的密码不一致'))
  else cb()
}

const rules = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的 11 位手机号', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请设置密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码须 6-64 位', trigger: 'blur' },
  ],
  confirm: [
    { required: true, message: '请再次输入密码', trigger: 'blur' },
    { validator: validateConfirm, trigger: 'blur' },
  ],
}

async function onSubmit() {
  const ok = await formRef.value?.validate().catch(() => false)
  if (!ok) return
  loading.value = true
  try {
    const res = await authApi.register(form.phone, form.password, form.name)
    auth.setAuth(res.token, res.user)
    ElMessage.success('注册成功，已自动登录')
    // 整页跳转：确保所有页面组件从干净的已登录态重新挂载
    window.location.href = '/industries'
  } catch {
    // 错误已由 axios 拦截器提示
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.auth-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #f0f7ff 0%, #e8f4fd 50%, #d9e8fb 100%);
  padding: 20px;
}
.auth-card {
  width: 100%;
  max-width: 420px;
  background: #fff;
  border-radius: 12px;
  padding: 40px 36px 32px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.08);
}
.auth-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  color: #1f2328;
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 20px;
}
.auth-title { font-size: 24px; color: #1f2328; margin-bottom: 4px; }
.auth-sub   { font-size: 13px; color: #888; margin-bottom: 24px; }
.auth-footer {
  text-align: center;
  margin-top: 20px;
  font-size: 13px;
  color: #888;
}
.auth-link { color: #409eff; text-decoration: none; margin-left: 4px; }
.auth-link:hover { text-decoration: underline; }
</style>
