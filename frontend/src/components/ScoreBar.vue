<template>
  <div class="score-bar-wrap">
    <div class="score-bar">
      <div
        class="score-fill"
        :style="{ width: pct + '%', background: color }"
      />
    </div>
    <span class="score-text">{{ displayScore }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  score: { type: Number, default: 0 },
  max:   { type: Number, default: 100 },
  color: { type: String, default: '#409eff' },
})

const pct          = computed(() => Math.min(100, Math.max(0, (props.score || 0) / props.max * 100)))
const displayScore = computed(() => props.score != null ? props.score.toFixed(1) : '-')
</script>

<style scoped>
.score-bar-wrap {
  display: flex; align-items: center; gap: 8px;
}
.score-bar {
  flex: 1; height: 6px; background: #ebedf0;
  border-radius: 4px; overflow: hidden;
}
.score-fill {
  height: 100%; border-radius: 4px;
  transition: width .4s ease;
}
.score-text {
  font-size: 12px; color: #555; min-width: 32px; text-align: right;
}
</style>
