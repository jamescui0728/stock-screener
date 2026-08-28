<template>
  <el-tooltip v-if="signal" :content="tooltip" placement="top" :show-after="200">
    <el-tag :type="entry.type" effect="dark" size="small" class="gap-tag">
      {{ entry.label }}<span v-if="dateText" class="gap-days">{{ dateText }}</span>
    </el-tag>
  </el-tooltip>
  <span v-else class="no-data">—</span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  signal:     { type: String, default: null },   // BREAKOUT / RUNAWAY / EXHAUST / ADD
  daysSince:  { type: Number, default: null },   // 距确认日几个交易日（相对末根K线，不是今天）
  confirmDate:{ type: String, default: null },   // 确认日的真实日期，展示以它为准
  reason:     { type: String, default: '' },
  lower:      { type: Number, default: null },   // 缺口下沿
  upper:      { type: Number, default: null },   // 缺口上沿
})

// 前三个是看涨/中继，衰竭是风险提示，用 danger 与它们区分开
const MAP = {
  BREAKOUT: { label: '突破', type: 'success' },
  RUNAWAY:  { label: '加油', type: 'primary' },
  EXHAUST:  { label: '衰竭', type: 'danger'  },
  ADD:      { label: '加仓', type: 'warning' },
}

const entry = computed(() => MAP[props.signal] || { label: props.signal, type: 'info' })

// 徽章上标**确认日期**而不是相对天数。
// daysSince 是相对"末根 K 线"数的，K 线停更时会骗人：末根若是 4 个月前，
// "+4" 看着像 4 天前，实际是 4 个月前 —— 日期是绝对的，不会随数据陈旧漂移。
const dateText = computed(() => {
  if (!props.confirmDate) return ''
  return ` ${props.confirmDate.slice(5)}`     // 2026-04-27 → 04-27
})

// 确认日距今天的真实自然日数（与 daysSince 无关）
const staleDays = computed(() => {
  if (!props.confirmDate) return null
  const d = new Date(props.confirmDate + 'T00:00:00')
  return Math.floor((Date.now() - d.getTime()) / 86400000)
})

const tooltip = computed(() => {
  const parts = []
  if (props.reason) parts.push(props.reason)
  if (props.confirmDate) {
    const n = staleDays.value
    parts.push(n <= 1 ? `确认于 ${props.confirmDate}` : `确认于 ${props.confirmDate}，距今 ${n} 天`)
  }
  return parts.join(' · ')
})
</script>

<style scoped>
.gap-tag { font-weight: 700; cursor: help; }
.gap-days { font-weight: 500; opacity: 0.85; margin-left: 1px; }
.no-data { color: #c0c4cc; font-size: 13px; }
</style>
