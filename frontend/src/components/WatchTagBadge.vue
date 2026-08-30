<template>
  <el-tooltip
    :content="watch?.tag_reason || '暂无数据'"
    placement="top"
    :show-after="200"
  >
    <el-tag
      :type="tagType"
      :effect="tagEffect"
      size="small"
      class="watch-tag"
      :class="tagClass"
    >
      {{ label }}
    </el-tag>
  </el-tooltip>
</template>

<script setup>
import { computed } from 'vue'

// watch = 后端 /api/watchlist 每条里的 watch 对象
// { tag, tp_level, tag_reason, close, ema20, above_ema, vol_ratio, cost, gain_pct }
const props = defineProps({
  watch: { type: Object, default: null },
})

// 标签含义见 backend/engines/watch_tag.py：EMA20 跟随纪律，与长/短期信号无关
const MAP = {
  FOLLOW:      { label: '🟢 可跟进', type: 'success', effect: 'dark',  cls: 'wt-follow' },
  HOLD:        { label: '🔵 持有',   type: 'primary', effect: 'light', cls: 'wt-hold' },
  TAKE_PROFIT: { label: '🟡 止盈',   type: 'warning', effect: 'dark',  cls: 'wt-take-profit' },
  STOP_LOSS:   { label: '🔴 止损',   type: 'danger',  effect: 'dark',  cls: 'wt-stop-loss' },
  NO_DATA:     { label: '⚪ 数据不足', type: 'info',   effect: 'plain', cls: 'wt-no-data' },
}

const entry = computed(() => MAP[props.watch?.tag] || MAP.NO_DATA)

// 止盈分两档（涨 30% / 涨 70%），标签上直接标出来是第几档
const label = computed(() => {
  const lv = props.watch?.tp_level
  if (props.watch?.tag === 'TAKE_PROFIT' && lv) {
    return `${entry.value.label}${lv === 2 ? '②' : '①'}`
  }
  return entry.value.label
})

const tagType   = computed(() => entry.value.type)
const tagEffect = computed(() => entry.value.effect)
const tagClass  = computed(() => entry.value.cls)
</script>

<style scoped>
.watch-tag {
  font-weight: 600;
  font-size: 12px;
  cursor: help;
}
/* 止损最需要一眼看见 —— 这是纪律里唯一"无论如何都要走"的状态 */
.wt-stop-loss {
  font-weight: 700;
  letter-spacing: 0.3px;
  box-shadow: 0 0 0 1px #f56c6c;
}
.wt-follow {
  font-weight: 700;
  letter-spacing: 0.3px;
}
.wt-no-data {
  opacity: 0.7;
}
</style>
