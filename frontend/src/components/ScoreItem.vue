<template>
  <div class="score-item">
    <span class="label" :class="{ bold }">{{ label }}</span>
    <div class="bar-wrap">
      <div class="bar-bg">
        <div class="bar-fill" :style="{ width: pct + '%', background: color }" />
      </div>
      <span class="value" :style="bold ? 'font-weight:700;font-size:15px' : ''">
        {{ score != null ? score.toFixed(1) : '-' }}
        <small v-if="!bold">/ {{ max }}</small>
        <small v-else> / 100</small>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  label: String,
  score: { type: Number, default: 0 },
  max:   { type: Number, default: 100 },
  color: { type: String, default: '#409eff' },
  bold:  { type: Boolean, default: false },
})
const pct = computed(() => Math.min(100, Math.max(0, (props.score || 0) / props.max * 100)))
</script>

<style scoped>
.score-item { display: flex; align-items: center; gap: 12px; }
.label { width: 80px; font-size: 13px; color: #555; flex-shrink: 0; }
.label.bold { font-weight: 700; color: #1f2328; }
.bar-wrap { flex: 1; display: flex; align-items: center; gap: 8px; }
.bar-bg { flex: 1; height: 8px; background: #ebedf0; border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; transition: width .4s; }
.value { font-size: 13px; color: #333; min-width: 60px; text-align: right; }
</style>
