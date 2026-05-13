<template>
  <div v-loading="loading" class="company-detail">
    <template v-if="detail">
      <!-- 头部：基本信息 + 信号 -->
      <el-card class="header-card">
        <div class="company-header">
          <div class="company-title">
            <el-button class="back-btn" text @click="router.back()">
              <el-icon :size="18"><ArrowLeft /></el-icon>
            </el-button>
            <span class="code">{{ detail.info.code }}</span>
            <span class="name">{{ detail.info.name }}</span>
            <span class="signal-pair">
              <span class="signal-label">长期</span>
              <SignalBadge :signal="detail.info.signal" effect="dark" />
              <span class="signal-label" style="margin-left:10px">短期</span>
              <SignalBadge v-if="detail.info.short_signal" :signal="detail.info.short_signal" effect="dark" />
              <span v-else style="color:#bbb;font-size:12px">— 数据不足</span>
            </span>
          </div>
          <div class="header-actions">
            <el-button size="small" @click="addWatch">
              <el-icon><Star /></el-icon> 加自选
            </el-button>
            <el-button size="small" type="primary" @click="refreshSignal" :loading="refreshing">
              重新评估
            </el-button>
            <el-button size="small" @click="refreshShortSignal" :loading="refreshingShort">
              重算短期
            </el-button>
            <el-button size="small" @click="updateNews" :loading="updatingNews">
              更新舆情
            </el-button>
          </div>
        </div>

        <!-- 长期信号理由 -->
        <el-alert
          v-if="detail.info.signal_reason"
          :title="'长期：' + detail.info.signal_reason"
          :type="alertType"
          show-icon :closable="false"
          class="signal-alert"
        />
        <!-- 短期信号理由 -->
        <el-alert
          v-if="detail.info.short_signal_reason"
          :title="'短期：' + detail.info.short_signal_reason"
          :type="shortAlertType"
          show-icon :closable="false"
          class="signal-alert"
        />

        <!-- 评分雷达图 + 子分条 -->
        <el-row :gutter="24" style="margin-top:20px">
          <el-col :span="10">
            <v-chart :option="radarOption" style="height:260px" autoresize />
          </el-col>
          <el-col :span="14">
            <div class="score-grid">
              <ScoreItem label="综合评分" :score="detail.info.composite_score" :max="100" color="#409eff" bold />
              <ScoreItem label="基本面"   :score="detail.info.fundamental_score" :max="80" color="#67c23a" />
              <ScoreItem label="ROE质量"  :score="detail.info.score_roe_quality" :max="25" color="#409eff" />
              <ScoreItem label="盈利增长" :score="detail.info.score_profit_growth" :max="20" color="#e6a23c" />
              <ScoreItem label="现金流健康" :score="detail.info.score_cashflow" :max="20" color="#9c27b0" />
              <ScoreItem label="财务稳健" :score="detail.info.score_financial_health" :max="15" color="#00bcd4" />
              <ScoreItem label="估值安全" :score="detail.info.score_valuation" :max="20" color="#f56c6c" />
            </div>
          </el-col>
        </el-row>
      </el-card>

      <el-row :gutter="20">
        <!-- 左列：财务趋势 -->
        <el-col :span="16">
          <el-card class="chart-card">
            <template #header>
              <div class="card-header">
                财务趋势（近10年）
                <el-radio-group v-model="chartMode" size="small">
                  <el-radio-button value="profit">净利润</el-radio-button>
                  <el-radio-button value="roe">ROE</el-radio-button>
                  <el-radio-button value="margin">利润率</el-radio-button>
                  <el-radio-button value="cashflow">现金流</el-radio-button>
                </el-radio-group>
              </div>
            </template>
            <v-chart :option="financialChartOption" style="height:280px" autoresize />
          </el-card>

          <!-- 估值历史 -->
          <el-card class="chart-card">
            <template #header>PE-TTM 历史走势（近1年）</template>
            <v-chart :option="peChartOption" style="height:200px" autoresize />
          </el-card>
        </el-col>

        <!-- 右列：舆情 + 财务表 -->
        <el-col :span="8">
          <!-- 舆情 -->
          <el-card class="news-card">
            <template #header>
              <div class="card-header">
                最新舆情
                <el-tag size="small" :type="sentimentTagType">{{ sentimentLabel }}</el-tag>
              </div>
            </template>
            <div class="news-list">
              <div v-for="n in detail.news" :key="n.pub_date + n.title" class="news-item">
                <div class="news-top">
                  <el-tag size="small" :type="n.sentiment_label === 'positive' ? 'success' : n.sentiment_label === 'negative' ? 'danger' : 'info'">
                    {{ n.event_type || '综合' }}
                  </el-tag>
                  <span class="news-date">{{ n.pub_date.slice(0, 10) }}</span>
                </div>
                <p class="news-title">{{ n.title }}</p>
              </div>
              <el-empty v-if="!detail.news.length" description="暂无新闻" :image-size="60" />
            </div>
          </el-card>

          <!-- 关键财务指标表 -->
          <el-card style="margin-top:20px">
            <template #header>关键财务指标</template>
            <el-table :data="latestFinancials" size="small" :show-header="false">
              <el-table-column prop="label" width="110" />
              <el-table-column prop="value" align="right">
                <template #default="{ row }">
                  <span :class="row.positive ? 'text-green' : row.negative ? 'text-red' : ''">
                    {{ row.value }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { stockApi, watchlistApi, dataApi } from '@/api'
import SignalBadge from '@/components/SignalBadge.vue'
import ScoreItem from '@/components/ScoreItem.vue'

const route       = useRoute()
const router      = useRouter()
const loading         = ref(false)
const refreshing      = ref(false)
const refreshingShort = ref(false)
const updatingNews    = ref(false)
const detail          = ref(null)
const chartMode       = ref('profit')

// 5 等级映射到 element-plus el-alert 的 4 种 type
function _sigToAlertType(s) {
  if (s === 'STRONG_BUY' || s === 'BUY') return 'success'
  if (s === 'STRONG_SELL' || s === 'SELL') return 'error'
  return 'warning'
}
const alertType      = computed(() => _sigToAlertType(detail.value?.info?.signal))
const shortAlertType = computed(() => _sigToAlertType(detail.value?.info?.short_signal))

const sentimentTagType = computed(() => {
  if (!detail.value?.news?.length) return 'info'
  const pos = detail.value.news.filter(n => n.sentiment_label === 'positive').length
  const neg = detail.value.news.filter(n => n.sentiment_label === 'negative').length
  if (neg > pos) return 'danger'
  if (pos > neg) return 'success'
  return 'info'
})

const sentimentLabel = computed(() => {
  if (!detail.value?.news?.length) return '无数据'
  const pos = detail.value.news.filter(n => n.sentiment_label === 'positive').length
  const neg = detail.value.news.filter(n => n.sentiment_label === 'negative').length
  if (neg > pos + 1) return '偏负面'
  if (pos > neg + 1) return '偏正面'
  return '中性'
})

// ── 雷达图 ──
const radarOption = computed(() => {
  if (!detail.value) return {}
  const info = detail.value.info
  return {
    radar: {
      indicator: [
        { name: 'ROE质量',  max: 25 },
        { name: '盈利增长', max: 20 },
        { name: '现金流',   max: 20 },
        { name: '财务稳健', max: 15 },
        { name: '估值',     max: 20 },
      ],
      radius: 90,
    },
    series: [{
      type: 'radar',
      data: [{
        value: [
          info.score_roe_quality || 0,
          info.score_profit_growth || 0,
          info.score_cashflow || 0,
          info.score_financial_health || 0,
          info.score_valuation || 0,
        ],
        name: info.name,
        areaStyle: { opacity: 0.2 },
        lineStyle: { color: '#409eff' },
        itemStyle: { color: '#409eff' },
      }],
    }],
  }
})

// ── 财务趋势图 ──
const financialChartOption = computed(() => {
  if (!detail.value) return {}
  const fins = detail.value.financials
  const years = fins.map(f => f.period.slice(0, 4))

  const r2 = v => v != null ? +Number(v).toFixed(2) : null
  const seriesMap = {
    profit:   { name: '净利润(亿)', data: fins.map(f => f.net_profit ? +(f.net_profit / 1e8).toFixed(2) : null) },
    roe:      { name: 'ROE(%)',     data: fins.map(f => r2(f.roe)) },
    margin:   { name: '净利率(%)',  data: fins.map(f => r2(f.net_margin)) },
    cashflow: { name: '经营现金流(亿)', data: fins.map(f => f.operating_cashflow ? +(f.operating_cashflow / 1e8).toFixed(2) : null) },
  }
  const s = seriesMap[chartMode.value]

  return {
    tooltip: { trigger: 'axis' },
    grid: { top: 30, bottom: 30, left: 60, right: 20 },
    xAxis: { type: 'category', data: years, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar',
      name: s.name,
      data: s.data,
      itemStyle: { color: '#409eff', borderRadius: [3, 3, 0, 0] },
      label: { show: true, position: 'top', fontSize: 10, formatter: ({ value }) => value != null ? Number(value).toFixed(1) : '' },
    }],
  }
})

// ── PE 走势图 ──
const peChartOption = computed(() => {
  if (!detail.value) return {}
  const prices = detail.value.prices.filter(p => p.pe_ttm)
  return {
    tooltip: { trigger: 'axis' },
    grid: { top: 20, bottom: 30, left: 55, right: 20 },
    xAxis: { type: 'category', data: prices.map(p => p.date), axisLabel: { fontSize: 10, rotate: 30 } },
    yAxis: { type: 'value', name: 'PE', axisLabel: { fontSize: 11 } },
    series: [
      {
        type: 'line',
        data: prices.map(p => p.pe_ttm),
        smooth: true,
        lineStyle: { color: '#e6a23c', width: 2 },
        areaStyle: { color: '#e6a23c22' },
        symbol: 'none',
      },
    ],
  }
})

// ── 关键指标表 ──
const latestFinancials = computed(() => {
  if (!detail.value?.financials?.length) return []
  const f = detail.value.financials[detail.value.financials.length - 1]
  const pct = v => v != null ? v.toFixed(1) + '%' : '-'
  const yi  = v => v != null ? (v / 1e8).toFixed(2) + ' 亿' : '-'
  return [
    { label: '报告期',     value: f.period?.slice(0, 4) + '年' },
    { label: '净利润',     value: yi(f.net_profit),     positive: (f.net_profit || 0) > 0 },
    { label: '毛利率',     value: pct(f.gross_margin),  positive: (f.gross_margin || 0) > 30 },
    { label: '净利率',     value: pct(f.net_margin),    positive: (f.net_margin || 0) > 10 },
    { label: 'ROE',        value: pct(f.roe),           positive: (f.roe || 0) > 15 },
    { label: '资产负债率', value: pct(f.debt_ratio),    negative: (f.debt_ratio || 0) > 65 },
    { label: 'FCF/净利润', value: f.fcf_ratio != null ? f.fcf_ratio.toFixed(2) : '-',
      positive: (f.fcf_ratio || 0) > 1 },
  ]
})

async function load() {
  loading.value = true
  try {
    detail.value = await stockApi.detail(route.params.code)
  } finally {
    loading.value = false
  }
}

async function refreshSignal() {
  refreshing.value = true
  try {
    await stockApi.refreshSignal(route.params.code)
    await load()
    ElMessage.success('长期信号已更新')
  } finally {
    refreshing.value = false
  }
}

async function refreshShortSignal() {
  refreshingShort.value = true
  try {
    await stockApi.refreshShortSignal(route.params.code)
    await load()
    ElMessage.success('短期信号已更新')
  } catch (e) {
    ElMessage.error('刷新短期信号失败：' + (e.response?.data?.detail || e.message))
  } finally {
    refreshingShort.value = false
  }
}

async function updateNews() {
  updatingNews.value = true
  try {
    await dataApi.updateNews(route.params.code)
    await load()
    ElMessage.success('舆情数据已更新')
  } finally {
    updatingNews.value = false
  }
}

async function addWatch() {
  await watchlistApi.add(route.params.code)
  ElMessage.success('已加入自选股')
}

onMounted(load)
</script>

<style scoped>
.company-header { display: flex; justify-content: space-between; align-items: center; }
.company-title  { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.signal-pair    { display: inline-flex; align-items: center; gap: 6px; }
.signal-label   { font-size: 12px; color: #888; font-weight: 500; }
.back-btn {
  padding: 4px 6px;
  color: #606266;
  font-size: 14px;
  margin-right: 2px;
}
.back-btn:hover { color: #409eff; }
.code  { font-size: 20px; font-weight: 700; color: #409eff; }
.name  { font-size: 22px; font-weight: 700; }
.header-actions { display: flex; gap: 8px; }
.signal-alert { margin-top: 16px; }
.header-card  { margin-bottom: 20px; }
.chart-card   { margin-bottom: 20px; }
.card-header  { display: flex; justify-content: space-between; align-items: center; }
.score-grid   { display: flex; flex-direction: column; gap: 10px; padding: 10px 0; }
.news-card    { height: 100%; }
.news-list    { max-height: 380px; overflow-y: auto; }
.news-item    { padding: 10px 0; border-bottom: 1px solid #f0f0f0; }
.news-item:last-child { border-bottom: none; }
.news-top     { display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px; }
.news-title   { font-size: 13px; color: #333; line-height: 1.5; }
.news-date    { font-size: 11px; color: #999; }
.text-green   { color: #67c23a; font-weight: 600; }
.text-red     { color: #f56c6c; font-weight: 600; }
</style>
