<template>
  <el-tag
    :type="tagType"
    :effect="tagEffect"
    size="small"
    class="signal-tag"
    :class="signalClass"
  >
    {{ label }}
  </el-tag>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  signal: { type: String, default: '' },
  effect: { type: String, default: '' },   // 留空时 STRONG_* 自动用 dark
})

// 5 等级：必买 / 买入 / 持有 / 卖出 / 必卖
const MAP = {
  STRONG_BUY:  { label: '🟢 必买', type: 'success', effect: 'dark',  cls: 'sig-strong-buy'  },
  BUY:         { label: '🟢 买入', type: 'success', effect: 'light', cls: 'sig-buy'         },
  HOLD:        { label: '🟡 持有', type: 'warning', effect: 'light', cls: 'sig-hold'        },
  SELL:        { label: '🔴 卖出', type: 'danger',  effect: 'light', cls: 'sig-sell'        },
  STRONG_SELL: { label: '🔴 必卖', type: 'danger',  effect: 'dark',  cls: 'sig-strong-sell' },
}

const tagType    = computed(() => MAP[props.signal]?.type   || 'info')
const tagEffect  = computed(() => props.effect || MAP[props.signal]?.effect || 'light')
const label      = computed(() => MAP[props.signal]?.label  || props.signal || '待评估')
const signalClass= computed(() => MAP[props.signal]?.cls    || '')
</script>

<style scoped>
.signal-tag { font-weight: 600; font-size: 12px; }

/* 必买：深绿 + 加粗，让它在列表里一眼能看见 */
.sig-strong-buy {
  font-weight: 700;
  letter-spacing: 0.3px;
  box-shadow: 0 0 0 1px #67c23a;
}
/* 必卖：深红 + 加粗 */
.sig-strong-sell {
  font-weight: 700;
  letter-spacing: 0.3px;
  box-shadow: 0 0 0 1px #f56c6c;
}
</style>
