<template>
  <div class="industry-map">

    <!-- ══ 数据初始化向导（无财报数据时显示） ══ -->
    <el-alert
      v-if="showSetupBanner"
      type="warning"
      :closable="false"
      style="margin-bottom:20px"
    >
      <template #title>
        <span style="font-size:15px;font-weight:600">⚠ 尚未抓取财报数据，行业评分无法计算</span>
      </template>
      <div style="margin-top:8px;line-height:2">
        请按顺序执行以下步骤，首次完成后后续自动更新：<br>
        <el-steps :active="setupStep" finish-status="success" simple style="margin:12px 0 16px">
          <el-step title="抓取财报" />
          <el-step title="批量评分" />
          <el-step title="生成买卖信号" />
        </el-steps>

        <!-- 步骤 1：启动入口 -->
        <div v-if="setupStep === 0 && !fetchingFinancials" class="setup-action">
          <span>第 1 步：从 AKShare 抓取股票财报（约需 5-30 分钟，取决于数量）</span>
          <div style="display:flex;gap:8px;margin-top:8px;align-items:center;flex-wrap:wrap">
            <span style="font-size:13px;color:#666">抓取数量：</span>
            <el-input-number v-model="fetchLimit" :min="50" :max="5500" :step="50" size="small" style="width:130px" />
            <span style="font-size:12px;color:#999">（建议先抓 200 只测试，全量约需数小时）</span>
            <el-button type="warning" @click="fetchFinancials">▶ 开始抓取财报</el-button>
          </div>
        </div>

        <!-- 步骤 1：抓取进度 -->
        <div v-if="fetchingFinancials" style="margin-top:8px">
          <div style="display:flex;justify-content:space-between;margin-bottom:6px;font-size:13px">
            <span>正在抓取财报... {{ fetchProg.current }}/{{ fetchProg.total }} 只</span>
            <span style="color:#888">已入库 <b>{{ fetchProg.saved }}</b> 条 · 跳过 {{ fetchProg.skipped }} 只 · 预计剩余 {{ fetchProg.eta }}</span>
          </div>
          <el-progress
            :percentage="fetchProg.pct"
            :stroke-width="10"
            :color="[{color:'#e6a23c',percentage:50},{color:'#409eff',percentage:80},{color:'#67c23a',percentage:100}]"
          />
          <div v-if="fetchProg.status==='done'" style="color:#67c23a;margin-top:6px;font-weight:600">
            ✓ 抓取完成！共入库 {{ fetchProg.saved }} 条，现在可以批量评分了。
          </div>
        </div>

        <!-- 步骤 2 -->
        <div v-if="setupStep >= 1 && !fetchingFinancials" class="setup-action">
          <span>✓ 财报数据已就绪（{{ financialCount }} 条）。第 2 步：批量计算行业评分</span>
          <el-button type="primary" :loading="rescoring" @click="rescoreAllIndustries" style="margin-left:12px">
            {{ rescoring ? '评分中...' : '▶ 批量评分行业' }}
          </el-button>
        </div>

        <!-- 步骤 3 -->
        <div v-if="setupStep === 2" class="setup-action">
          <span>✓ 行业评分完成。第 3 步：生成个股买卖信号</span>
          <el-button type="success" :loading="refreshingSignals" @click="refreshSignals" style="margin-left:12px">
            {{ refreshingSignals ? '生成中...' : '▶ 生成买卖信号' }}
          </el-button>
        </div>

        <!-- 步骤完成 -->
        <div v-if="setupStep >= 3" class="setup-action" style="color:#67c23a;font-weight:600">
          🎉 初始化完成！所有数据已就绪。
        </div>
      </div>
    </el-alert>

    <!-- ══ 统计卡片 ══ -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-value">{{ stats.total }}</div>
          <div class="stat-label">覆盖行业数</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-value green">{{ stats.qualified }}</div>
          <div class="stat-label">优质行业（评分≥60）</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-value">{{ stats.avgScore }}</div>
          <div class="stat-label">平均行业评分</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-value blue">{{ stats.topIndustry }}</div>
          <div class="stat-label">最高分行业</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ══ 行业热力图 ══ -->
    <el-card class="chart-card">
      <template #header>
        <div class="card-header">
          <span>行业评分热力图（Top 30）</span>
          <div style="display:flex;gap:8px">
            <el-button size="small" :loading="rescoring" @click="rescoreAllIndustries">
              {{ rescoring ? '评分中...' : '重新评分' }}
            </el-button>
            <el-button size="small" @click="load">刷新</el-button>
          </div>
        </div>
      </template>

      <!-- 无评分数据占位 -->
      <div v-if="scoredIndustries.length === 0" class="empty-heatmap">
        <el-empty description="暂无行业评分数据">
          <template #description>
            <p style="color:#888">尚未计算行业评分</p>
            <p style="font-size:12px;color:#bbb;margin-top:4px">
              请先抓取财报数据，再点击「重新评分」
            </p>
          </template>
        </el-empty>
      </div>
      <v-chart v-else :option="heatmapOption" style="height:380px" autoresize />
    </el-card>

    <!-- ══ 行业列表 ══ -->
    <el-card>
      <template #header>
        <div class="card-header">
          <span>行业详情列表</span>
          <el-input
            v-model="search" placeholder="搜索行业名称"
            style="width:200px" clearable size="small"
          >
            <template #prefix><el-icon><Search /></el-icon></template>
          </el-input>
        </div>
      </template>

      <el-table
        :data="filteredIndustries"
        v-loading="loading"
        row-key="code"
        @row-click="(row) => $router.push({ path: '/stocks', query: { industry: row.code } })"
        highlight-current-row
        style="cursor:pointer"
      >
        <el-table-column prop="name" label="行业名称" min-width="140" />
        <el-table-column label="综合评分" width="130" sortable :sort-method="(a,b) => (a.total_score||0)-(b.total_score||0)">
          <template #default="{ row }">
            <ScoreBar :score="row.total_score" :max="100" />
          </template>
        </el-table-column>
        <el-table-column label="营收稳定" width="110" prop="score_revenue_stability">
          <template #default="{ row }">
            <ScoreBar :score="row.score_revenue_stability" :max="30" color="#67c23a" />
          </template>
        </el-table-column>
        <el-table-column label="盈利稳定" width="110" prop="score_profit_stability">
          <template #default="{ row }">
            <ScoreBar :score="row.score_profit_stability" :max="30" color="#409eff" />
          </template>
        </el-table-column>
        <el-table-column label="抗周期" width="100" prop="score_anti_cycle">
          <template #default="{ row }">
            <ScoreBar :score="row.score_anti_cycle" :max="20" color="#e6a23c" />
          </template>
        </el-table-column>
        <el-table-column label="竞争格局" width="100" prop="score_competition">
          <template #default="{ row }">
            <ScoreBar :score="row.score_competition" :max="20" color="#9c27b0" />
          </template>
        </el-table-column>
        <el-table-column prop="stock_count" label="成分股数" width="90" />
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small" type="primary" text
              @click.stop="$router.push({ path: '/stocks', query: { industry: row.code } })"
            >查看个股</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { industryApi, dataApi, stockApi } from '@/api'
import ScoreBar from '@/components/ScoreBar.vue'

const loading            = ref(false)
const rescoring          = ref(false)
const fetchingFinancials = ref(false)
const refreshingSignals  = ref(false)
const search             = ref('')
const industries         = ref([])
const financialCount     = ref(0)
const fetchLimit         = ref(200)
const fetchProg          = ref({ status:'idle', total:0, current:0, saved:0, skipped:0, pct:0, eta:'-' })

let pollTimer = null

// ── 初始化向导判断 ──
const scoredIndustries = computed(() =>
  industries.value.filter(i => i.total_score && i.total_score > 0)
)

// 抓取中 或 没有数据 → step 0；有数据但未评分 → step 1；完成 → step 3
const setupStep = computed(() => {
  if (fetchingFinancials.value) return 0
  if (financialCount.value === 0) return 0
  if (scoredIndustries.value.length === 0) return 1
  return 3
})

const showSetupBanner = computed(() => setupStep.value < 3)

// ── 统计 ──
const stats = computed(() => {
  const list      = industries.value
  const scored    = list.filter(i => (i.total_score || 0) > 0)
  const qualified = scored.filter(i => i.total_score >= 60)
  const scores    = scored.map(i => i.total_score)
  const top       = scored.reduce((a, b) => (a.total_score || 0) > (b.total_score || 0) ? a : b, {})
  return {
    total:       list.length,
    qualified:   qualified.length,
    avgScore:    scores.length ? (scores.reduce((a, b) => a + b, 0) / scores.length).toFixed(1) : '-',
    topIndustry: top.name || '-',
  }
})

const filteredIndustries = computed(() =>
  industries.value.filter(i => !search.value || i.name.includes(search.value))
)

// ── 热力图 ──
const heatmapOption = computed(() => {
  const sorted = [...scoredIndustries.value]
    .sort((a, b) => b.total_score - a.total_score)
    .slice(0, 30)

  const names = sorted.map(i => i.name)
  const dims  = ['营收稳定', '盈利稳定', '抗周期', '竞争格局']
  const keys  = ['score_revenue_stability', 'score_profit_stability', 'score_anti_cycle', 'score_competition']
  const maxes = [30, 30, 20, 20]

  const data = []
  sorted.forEach((ind, yi) => {
    keys.forEach((k, xi) => {
      const raw = ind[k] || 0
      const pct = Math.round(raw / maxes[xi] * 100)
      data.push([xi, yi, pct])
    })
  })

  return {
    tooltip: {
      formatter: p => `${names[p.value[1]]} · ${dims[p.value[0]]}: ${p.value[2]}%`
    },
    grid: { top: 10, bottom: 60, left: 130, right: 20 },
    xAxis: { type: 'category', data: dims, splitArea: { show: true } },
    yAxis: {
      type: 'category', data: names, splitArea: { show: true },
      axisLabel: { fontSize: 11, width: 120, overflow: 'truncate' },
    },
    visualMap: {
      min: 0, max: 100,
      calculable: true,
      orient: 'horizontal',
      left: 'center', bottom: 0,
      inRange: { color: ['#f5f5f5', '#b7d8f7', '#409eff', '#0055cc'] },
    },
    series: [{
      type: 'heatmap',
      data,
      label: { show: true, formatter: p => p.value[2] + '%', fontSize: 10 },
      emphasis: { itemStyle: { shadowBlur: 10 } },
    }],
  }
})

// ── 数据操作 ──
async function loadStatus() {
  try {
    const res = await fetch('/api/status/financial-count')
    if (res.ok) {
      const d = await res.json()
      financialCount.value = d.count || 0
      if (d.fetch) fetchProg.value = d.fetch
    }
  } catch {}
}

async function load() {
  loading.value = true
  try {
    await Promise.all([
      industryApi.list().then(v => { industries.value = v }),
      loadStatus(),
    ])
  } finally {
    loading.value = false
  }
}

async function fetchFinancials() {
  fetchingFinancials.value = true
  fetchProg.value = { status:'running', total: fetchLimit.value, current:0, saved:0, skipped:0, pct:0, eta:'-' }
  try {
    await dataApi.updateFinancials(fetchLimit.value)
    startPollFinancials()
  } catch {
    fetchingFinancials.value = false
  }
}

function startPollFinancials() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = setInterval(async () => {
    await loadStatus()
    const prog = fetchProg.value
    // 后端标记完成
    if (prog.status === 'done') {
      clearInterval(pollTimer)
      pollTimer = null
      fetchingFinancials.value = false
      ElMessage.success(`财报抓取完成！共入库 ${prog.saved} 条`)
      await load()
    }
  }, 3000)
}

async function rescoreAllIndustries() {
  rescoring.value = true
  try {
    await industryApi.rescoreAll()
    ElMessage.info('行业评分已在后台启动，约需 1-3 分钟，稍后自动刷新')
    // 轮询等评分完成
    const timer = setInterval(async () => {
      await load()
      if (scoredIndustries.value.length > 0) {
        clearInterval(timer)
        rescoring.value = false
        ElMessage.success(`行业评分完成！共 ${scoredIndustries.value.length} 个行业已评分`)
      }
    }, 4000)
  } catch {
    rescoring.value = false
  }
}

async function refreshSignals() {
  refreshingSignals.value = true
  try {
    await stockApi.refreshAllSignals()
    ElMessage.success('信号生成已在后台启动，完成后在「公司筛选」页查看')
  } finally {
    refreshingSignals.value = false
  }
}

onMounted(load)
onUnmounted(() => { if (pollTimer) clearInterval(pollTimer) })
</script>

<style scoped>
.stat-row  { margin-bottom: 20px; }
.stat-card {
  text-align: center;
}
/* el-card__body 默认是 block，改成 flex 让内容等高对齐 */
.stat-card :deep(.el-card__body) {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 80px;
  padding: 16px 12px;
}
.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #1f2328;
  line-height: 1.2;
  /* 长文本截断，防止撑高卡片 */
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.stat-value.green { color: #67c23a; }
.stat-value.blue  { color: #409eff; font-size: 20px; }
.stat-label { font-size: 13px; color: #888; margin-top: 4px; }

.chart-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }

.empty-heatmap {
  height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.setup-action {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 4px;
  font-size: 13px;
}
</style>
