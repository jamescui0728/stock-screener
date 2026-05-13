<template>
  <div class="watchlist">
    <el-card v-if="!stocks.length && !loading">
      <el-empty description="自选股为空，前往公司筛选页添加">
        <el-button type="primary" @click="$router.push('/stocks')">去筛选</el-button>
      </el-empty>
    </el-card>

    <template v-else>
      <!-- 顶栏：标题 + 视图切换 + 更新按钮 -->
      <div class="top-bar">
        <span class="page-title">自选股</span>
        <div class="top-actions">
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button value="card">
              <el-icon><Grid /></el-icon> 卡片
            </el-radio-button>
            <el-radio-button value="table">
              <el-icon><List /></el-icon> 列表
            </el-radio-button>
          </el-radio-group>
          <el-button size="small" @click="refreshAll" :loading="refreshingAll">
            <el-icon><Refresh /></el-icon> 更新数据
          </el-button>
        </div>
      </div>

      <!-- 汇总信号统计（5 等级，flex grid 自适应）-->
      <div class="signal-stat-grid">
        <el-card class="signal-stat strong-buy-stat">
          <div class="stat-num">{{ sigCounts.STRONG_BUY }}</div>
          <div class="stat-lbl">⭐ 必买</div>
        </el-card>
        <el-card class="signal-stat buy-stat">
          <div class="stat-num">{{ sigCounts.BUY }}</div>
          <div class="stat-lbl">🟢 买入</div>
        </el-card>
        <el-card class="signal-stat hold-stat">
          <div class="stat-num">{{ sigCounts.HOLD }}</div>
          <div class="stat-lbl">🟡 持有</div>
        </el-card>
        <el-card class="signal-stat sell-stat">
          <div class="stat-num">{{ sigCounts.SELL }}</div>
          <div class="stat-lbl">🔴 卖出</div>
        </el-card>
        <el-card class="signal-stat strong-sell-stat">
          <div class="stat-num">{{ sigCounts.STRONG_SELL }}</div>
          <div class="stat-lbl">⚠️ 必卖</div>
        </el-card>
      </div>

      <!-- ========== 最新消息 ========== -->
      <el-card shadow="never" class="news-card">
        <template #header>
          <div class="news-head">
            <div class="news-head-left">
              <el-icon><Bell /></el-icon>
              <span class="news-title">最新消息</span>
              <span class="news-hint">
                工作日 09:00 自动汇总近 {{ newsDays }} 天的自选股消息
              </span>
            </div>
            <div class="news-head-right">
              <span v-if="newsLastRefreshed" class="news-updated">
                最近更新：{{ newsLastRefreshed.slice(0, 16) }}
              </span>
              <el-button
                size="small" :icon="Refresh"
                :loading="newsRefreshing"
                @click="refreshNews"
              >立即刷新</el-button>
            </div>
          </div>
        </template>

        <div v-loading="newsLoading">
          <el-empty
            v-if="!newsLoading && !news.length"
            description="暂无最新消息，可点击“立即刷新”拉取"
            :image-size="60"
          />
          <ul v-else class="news-list">
            <li v-for="(n, i) in news" :key="i" class="news-item">
              <el-tag size="small" effect="plain" class="news-stock">
                {{ n.stock_code }} {{ n.stock_name }}
              </el-tag>
              <el-tag
                v-if="n.sentiment_label"
                size="small"
                :type="sentimentTagType(n.sentiment_label)"
                effect="light"
                class="news-tag"
              >{{ sentimentLabelCN(n.sentiment_label) }}</el-tag>
              <el-tag
                v-if="n.event_type"
                size="small"
                type="info"
                effect="plain"
                class="news-tag"
              >{{ n.event_type }}</el-tag>
              <a
                v-if="n.url"
                :href="n.url" target="_blank" rel="noopener"
                class="news-link"
                :title="n.title"
              >{{ n.title }}</a>
              <span v-else class="news-link no-link" :title="n.title">{{ n.title }}</span>
              <span v-if="n.source" class="news-source">· {{ n.source }}</span>
              <span class="news-date">{{ formatNewsDate(n.pub_date) }}</span>
            </li>
          </ul>
        </div>
      </el-card>

      <!-- ========== 卡片视图 ========== -->
      <div v-if="viewMode === 'card'" v-loading="loading" class="stock-cards">
        <el-card
          v-for="s in stocks" :key="s.code"
          class="stock-card"
          :class="'card-' + (s.signal || 'NONE').toLowerCase()"
        >
          <div class="card-top">
            <div class="card-title" @click="$router.push(`/stocks/${s.code}`)">
              <span class="code">{{ s.code }}</span>
              <span class="name">{{ s.name }}</span>
            </div>
            <div class="card-actions">
              <div class="signal-pair-card">
                <SignalBadge :signal="s.signal" />
                <SignalBadge v-if="s.short_signal" :signal="s.short_signal" />
              </div>
              <el-button
                size="small" type="danger" text circle
                @click="remove(s.code)" title="移除自选"
              ><el-icon><Delete /></el-icon></el-button>
            </div>
          </div>

          <div class="score-row">
            <div class="score-col">
              <span class="score-label">综合</span>
              <ScoreBar :score="s.composite_score" :max="100" />
            </div>
            <div class="score-col">
              <span class="score-label">基本面</span>
              <ScoreBar :score="s.fundamental_score" :max="80" color="#67c23a" />
            </div>
            <div class="score-col">
              <span class="score-label">估值</span>
              <ScoreBar :score="s.score_valuation" :max="20" color="#f56c6c" />
            </div>
          </div>

          <p class="reason" v-if="s.signal_reason">{{ s.signal_reason }}</p>
          <div class="card-footer">
            <span class="update-time">
              {{ s.signal_updated ? '更新于 ' + s.signal_updated.slice(0, 16) : '未评估' }}
            </span>
            <el-button
              size="small" text type="primary"
              @click="refreshOne(s.code)" :loading="refreshingCode === s.code"
            >重新评估</el-button>
          </div>
        </el-card>
      </div>

      <!-- ========== 列表视图 ========== -->
      <el-card v-else v-loading="loading">
        <el-table
          :data="stocks"
          @row-click="(row) => $router.push(`/stocks/${row.code}`)"
          highlight-current-row
          style="cursor:pointer"
          :default-sort="{ prop: 'composite_score', order: 'descending' }"
        >
          <el-table-column prop="code" label="代码" width="90" fixed />
          <el-table-column prop="name" label="名称" width="100" fixed />
          <el-table-column label="长期" width="90" sortable prop="signal">
            <template #default="{ row }">
              <SignalBadge :signal="row.signal" />
            </template>
          </el-table-column>
          <el-table-column label="短期" width="90" sortable prop="short_signal">
            <template #default="{ row }">
              <SignalBadge v-if="row.short_signal" :signal="row.short_signal" />
              <span v-else class="no-data">—</span>
            </template>
          </el-table-column>
          <el-table-column label="综合评分" width="140" sortable prop="composite_score">
            <template #default="{ row }">
              <ScoreBar :score="row.composite_score" :max="100" />
            </template>
          </el-table-column>
          <el-table-column label="短期评分" width="120" sortable prop="short_composite_score">
            <template #default="{ row }">
              <ScoreBar v-if="row.short_composite_score != null"
                        :score="row.short_composite_score" :max="100" color="#9c27b0" />
              <span v-else class="no-data">—</span>
            </template>
          </el-table-column>
          <el-table-column label="基本面" width="130" sortable prop="fundamental_score">
            <template #default="{ row }">
              <ScoreBar :score="row.fundamental_score" :max="80" color="#67c23a" />
            </template>
          </el-table-column>
          <el-table-column label="ROE质量" width="110" sortable prop="score_roe_quality">
            <template #default="{ row }">
              <ScoreBar :score="row.score_roe_quality" :max="25" color="#409eff" />
            </template>
          </el-table-column>
          <el-table-column label="盈利增长" width="110" sortable prop="score_profit_growth">
            <template #default="{ row }">
              <ScoreBar :score="row.score_profit_growth" :max="20" color="#e6a23c" />
            </template>
          </el-table-column>
          <el-table-column label="现金流" width="110" sortable prop="score_cashflow">
            <template #default="{ row }">
              <ScoreBar :score="row.score_cashflow" :max="20" color="#9c27b0" />
            </template>
          </el-table-column>
          <el-table-column label="估值" width="100" sortable prop="score_valuation">
            <template #default="{ row }">
              <ScoreBar :score="row.score_valuation" :max="20" color="#f56c6c" />
            </template>
          </el-table-column>
          <el-table-column label="信号理由" min-width="240" prop="signal_reason">
            <template #default="{ row }">
              <span class="table-reason">{{ row.signal_reason || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" fixed="right">
            <template #default="{ row }">
              <el-button size="small" text type="primary"
                @click.stop="refreshOne(row.code)" :loading="refreshingCode === row.code"
              >评估</el-button>
              <el-button size="small" text type="danger"
                @click.stop="remove(row.code)"
              >移除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, Bell } from '@element-plus/icons-vue'
import { watchlistApi, stockApi } from '@/api'
import ScoreBar from '@/components/ScoreBar.vue'
import SignalBadge from '@/components/SignalBadge.vue'

const loading        = ref(false)
const refreshingCode = ref('')
const refreshingAll  = ref(false)
const stocks         = ref([])
const viewMode       = ref('card')   // 'card' | 'table'

const sigCounts = computed(() => {
  const c = { STRONG_BUY: 0, BUY: 0, HOLD: 0, SELL: 0, STRONG_SELL: 0 }
  for (const s of stocks.value) {
    if (s.signal && c[s.signal] !== undefined) c[s.signal]++
  }
  return c
})

async function load() {
  loading.value = true
  try { stocks.value = await watchlistApi.get() }
  finally { loading.value = false }
}

async function remove(code) {
  await ElMessageBox.confirm('确认移出自选股？', '提示', { type: 'warning' })
  await watchlistApi.remove(code)
  stocks.value = stocks.value.filter(s => s.code !== code)
  ElMessage.success('已移除')
}

async function refreshOne(code) {
  refreshingCode.value = code
  try {
    await stockApi.refreshSignal(code)
    await load()
    ElMessage.success('信号已更新')
  } finally {
    refreshingCode.value = ''
  }
}

async function refreshAll() {
  refreshingAll.value = true
  try {
    for (const s of stocks.value) {
      refreshingCode.value = s.code
      try { await stockApi.refreshSignal(s.code) } catch {}
    }
    await load()
    ElMessage.success('所有自选股已更新')
  } finally {
    refreshingAll.value = false
    refreshingCode.value = ''
  }
}

// ── 最新消息 ──
const news               = ref([])
const newsLoading        = ref(false)
const newsRefreshing     = ref(false)
const newsLastRefreshed  = ref(null)
const newsDays           = ref(3)

async function loadNews() {
  newsLoading.value = true
  try {
    const data = await watchlistApi.news(newsDays.value, 50)
    news.value              = data.items || []
    newsLastRefreshed.value = data.last_refreshed_at || null
  } catch {} finally {
    newsLoading.value = false
  }
}

async function refreshNews() {
  newsRefreshing.value = true
  try {
    const r = await watchlistApi.refreshNews()
    ElMessage.success(`已新增 ${r.fetched || 0} 条消息`)
    await loadNews()
  } catch {} finally {
    newsRefreshing.value = false
  }
}

function sentimentTagType(label) {
  return ({
    positive: 'success',
    negative: 'danger',
    neutral:  'info',
  })[label] || 'info'
}
function sentimentLabelCN(label) {
  return ({
    positive: '利好',
    negative: '利空',
    neutral:  '中性',
  })[label] || label
}

function formatNewsDate(s) {
  // "2026-04-18 17:08:00" → 今天显示 "17:08"，否则显示 "04-18 17:08"
  if (!s) return ''
  const today = new Date().toISOString().slice(0, 10)  // 2026-04-19
  if (s.startsWith(today)) return s.slice(11, 16)
  return s.slice(5, 16)  // 04-18 17:08
}

onMounted(async () => {
  await load()
  loadNews()
})
</script>

<style scoped>
.top-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-title { font-size: 20px; font-weight: 700; color: #1f2328; }
.top-actions { display: flex; align-items: center; gap: 12px; }

.signal-stat-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 20px;
}
.signal-stat {
  flex: 1 1 0;
  text-align: center;
  min-width: 0;   /* 允许 flex 子元素压缩 */
}
.stat-num { font-size: 28px; font-weight: 700; line-height: 1.1; }
.stat-lbl { font-size: 13px; color: #555; margin-top: 4px; font-weight: 500; }

/* 5 等级配色，从必买 → 必卖：金 → 绿 → 黄 → 红 → 深红 */
.strong-buy-stat .stat-num  { color: #d4a017; }    /* 金色，提示机会 */
.strong-buy-stat            { background: linear-gradient(180deg, #fffbe6 0%, #fff 70%); }
.buy-stat .stat-num         { color: #67c23a; }
.hold-stat .stat-num        { color: #e6a23c; }
.sell-stat .stat-num        { color: #f56c6c; }
.strong-sell-stat .stat-num { color: #c92a2a; }    /* 深红，提示需立即处理 */
.strong-sell-stat           { background: linear-gradient(180deg, #fff5f5 0%, #fff 70%); }

/* 响应式：平板换 3 张/行，手机换 2 张/行 */
@media (max-width: 768px) {
  .signal-stat { flex: 1 1 calc(33.33% - 8px); }
  .stat-num    { font-size: 24px; }
}
@media (max-width: 480px) {
  .signal-stat { flex: 1 1 calc(50% - 6px); }
  .stat-num    { font-size: 22px; }
  .stat-lbl    { font-size: 12px; }
}

/* ── 卡片视图 ── */
.stock-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.stock-card { transition: box-shadow .2s; }
.stock-card:hover { box-shadow: 0 4px 16px rgba(0,0,0,.12); }
.card-buy  { border-left: 4px solid #67c23a; }
.card-hold { border-left: 4px solid #e6a23c; }
.card-sell { border-left: 4px solid #f56c6c; }

.card-top    { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.card-title  { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.card-title:hover .name { color: #409eff; }
.code { font-weight: 700; color: #409eff; }
.name { font-weight: 600; }
.card-actions { display: flex; align-items: center; gap: 6px; }
.signal-pair-card { display: inline-flex; gap: 4px; }
.no-data { color: #bbb; font-size: 13px; }

.score-row { display: flex; flex-direction: column; gap: 6px; margin-bottom: 10px; }
.score-col { display: flex; align-items: center; gap: 6px; }
.score-label { font-size: 12px; color: #888; width: 42px; flex-shrink: 0; }

.reason { font-size: 12px; color: #555; line-height: 1.6; margin-bottom: 10px;
  max-height: 60px; overflow: hidden; }
.card-footer { display: flex; justify-content: space-between; align-items: center; }
.update-time { font-size: 11px; color: #bbb; }

/* ── 最新消息 ── */
.news-card { margin-bottom: 20px; }
.news-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.news-head-left  { display: flex; align-items: center; gap: 8px; }
.news-head-right { display: flex; align-items: center; gap: 12px; }
.news-title  { font-size: 15px; font-weight: 600; color: #1f2328; }
.news-hint   { font-size: 12px; color: #888; }
.news-updated { font-size: 12px; color: #aaa; }

.news-list {
  list-style: none;
  padding: 0;
  margin: 0;
  max-height: 420px;
  overflow-y: auto;
}
.news-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 4px;
  border-bottom: 1px solid #f0f2f5;
  font-size: 13px;
  min-width: 0;   /* 让 flex 子项可以收缩并触发 ellipsis */
}
.news-item:last-child { border-bottom: none; }

.news-stock  { font-family: Menlo, monospace; flex-shrink: 0; }
.news-tag    { flex-shrink: 0; }

.news-link {
  flex: 1 1 auto;
  min-width: 0;
  color: #1f2328;
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  line-height: 1.5;
}
.news-link:hover   { color: #409eff; text-decoration: underline; }
.news-link.no-link { color: #606266; cursor: default; }
.news-link.no-link:hover { color: #606266; text-decoration: none; }

.news-source {
  flex-shrink: 0;
  font-size: 12px;
  color: #aaa;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.news-date {
  flex-shrink: 0;
  font-size: 12px;
  color: #aaa;
  font-family: Menlo, monospace;
  margin-left: 4px;
}

/* ── 列表视图 ── */
.table-reason {
  font-size: 12px;
  color: #666;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  line-height: 1.5;
}
</style>
