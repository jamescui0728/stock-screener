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
          <el-button size="small" @click="refreshQuotes" :loading="quotesLoading">
            <el-icon><Refresh /></el-icon> 刷新行情
          </el-button>
        </div>
      </div>

      <!-- 汇总：EMA20 跟随纪律 -->
      <div class="signal-section">
        <div class="signal-row-label">
          EMA20 跟随纪律（线上拿住、线下离场；站稳均线且放量才跟进；涨 30% / 70% 分批止盈）
        </div>
        <div class="signal-stat-grid">
          <el-card class="signal-stat strong-buy-stat">
            <div class="stat-num">{{ tagCounts.FOLLOW }}</div>
            <div class="stat-lbl">🟢 可跟进</div>
          </el-card>
          <el-card class="signal-stat buy-stat">
            <div class="stat-num">{{ tagCounts.HOLD }}</div>
            <div class="stat-lbl">🔵 持有</div>
          </el-card>
          <el-card class="signal-stat hold-stat">
            <div class="stat-num">{{ tagCounts.TAKE_PROFIT }}</div>
            <div class="stat-lbl">🟡 止盈</div>
          </el-card>
          <el-card class="signal-stat sell-stat">
            <div class="stat-num">{{ tagCounts.STOP_LOSS }}</div>
            <div class="stat-lbl">🔴 止损</div>
          </el-card>
          <el-card class="signal-stat strong-sell-stat">
            <div class="stat-num">{{ tagCounts.NO_DATA }}</div>
            <div class="stat-lbl">⚪ 数据不足</div>
          </el-card>
        </div>
      </div>

      <!-- 汇总：跳空缺口形态 -->
      <div class="signal-section">
        <div class="signal-row-label">
          跳空缺口（突破：底部放量跳空 · 加油：上涨途中跳空 · 加仓：回踩缺口上沿守住 · 衰竭：天量长上影）
        </div>
        <div class="signal-stat-grid">
          <el-card class="signal-stat strong-buy-stat">
            <div class="stat-num">{{ gapCounts.BREAKOUT }}</div>
            <div class="stat-lbl">🟢 突破</div>
          </el-card>
          <el-card class="signal-stat buy-stat">
            <div class="stat-num">{{ gapCounts.RUNAWAY }}</div>
            <div class="stat-lbl">🔵 加油</div>
          </el-card>
          <el-card class="signal-stat hold-stat">
            <div class="stat-num">{{ gapCounts.ADD }}</div>
            <div class="stat-lbl">🟡 加仓</div>
          </el-card>
          <el-card class="signal-stat sell-stat">
            <div class="stat-num">{{ gapCounts.EXHAUST }}</div>
            <div class="stat-lbl">🔴 衰竭</div>
          </el-card>
          <el-card class="signal-stat strong-sell-stat">
            <div class="stat-num">{{ gapCounts.NONE }}</div>
            <div class="stat-lbl">⚪ 无形态</div>
          </el-card>
        </div>
        <div class="signal-row-hint" v-if="macdHits > 0">
          ⚡ 另有 {{ macdHits }} 只命中 MACD 零轴上方回踩金叉
        </div>

        <div class="signal-row-hint stale-warn" v-if="klineStaleDays > 7">
          ⚠️ K 线数据截至 <b>{{ klineDate }}</b>，已陈旧 {{ klineStaleDays }} 天。
          EMA20 与纪律标签算的是那天的位置，<b>不是今天的</b>；上方成交价则是实时的，两者时点不一致。
          请到数据管理页跑 <code>update-prices?mode=incremental</code> 补齐后再依此操作。
        </div>
        <div class="signal-row-hint" v-if="tagCounts.NO_DATA > 0">
          ⓘ {{ tagCounts.NO_DATA }} 只 K 线不足 20 根，算不出 EMA20。补历史行情：数据管理页跑
          <code>update-prices?mode=init-missing</code>
        </div>
        <div class="signal-row-hint" v-if="quotesLoading">
          ⏳ 正在拉取最新成交价（首次约 20-30 秒）…标签与 EMA20 已可用
        </div>
        <div class="signal-row-hint" v-else-if="quotesMissing > 0">
          ⓘ {{ quotesMissing }} 只暂无最新成交价，点右上角「刷新行情」拉取
        </div>
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
          :class="'card-tag-' + (s.watch?.tag || 'NO_DATA').toLowerCase()"
        >
          <div class="card-top">
            <div class="card-title" @click="$router.push(`/stocks/${s.code}`)">
              <span class="code">{{ s.code }}</span>
              <span class="name">{{ s.name }}</span>
            </div>
            <div class="card-actions">
              <GapBadge v-if="s.gap_signal" :signal="s.gap_signal"
                        :days-since="s.gap_days_since" :confirm-date="s.gap_confirm_date" :reason="s.gap_reason"
                        :lower="s.gap_lower" :upper="s.gap_upper" />
              <WatchTagBadge :watch="s.watch" />
              <el-button
                size="small" type="danger" text circle
                @click="remove(s.code)" title="移除自选"
              ><el-icon><Delete /></el-icon></el-button>
            </div>
          </div>

          <!-- 最新成交价 + EMA20 对照 -->
          <div class="quote-row">
            <div class="quote-main">
              <span v-if="s.watch?.raw_price != null" class="quote-price">
                {{ s.watch.raw_price.toFixed(2) }}
              </span>
              <el-skeleton v-else-if="quotesLoading" animated :rows="0" class="quote-skeleton">
                <template #template><el-skeleton-item variant="text" style="width:56px" /></template>
              </el-skeleton>
              <span v-else class="quote-price no-data">—</span>
              <span
                v-if="s.watch?.gain_pct != null"
                class="quote-gain"
                :class="s.watch.gain_pct >= 0 ? 'gain-pos' : 'gain-neg'"
              >{{ s.watch.gain_pct >= 0 ? '+' : '' }}{{ s.watch.gain_pct.toFixed(1) }}%</span>
            </div>
            <div class="quote-ema" v-if="s.watch?.above_ema_pct != null">
              距 EMA20
              <span :class="s.watch.above_ema ? 'ema-above' : 'ema-below'">
                {{ s.watch.above_ema ? '▲' : '▼' }}
                {{ s.watch.above_ema_pct >= 0 ? '+' : '' }}{{ s.watch.above_ema_pct.toFixed(1) }}%
              </span>
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

          <p class="reason" v-if="s.watch?.tag_reason">{{ s.watch.tag_reason }}</p>
          <div class="card-footer">
            <span class="update-time">
              {{ s.watch?.raw_price_date ? '行情 ' + s.watch.raw_price_date : '行情未获取' }}
              <template v-if="s.watch?.trade_date"> · K线 {{ s.watch.trade_date }}</template>
            </span>
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
          <el-table-column label="纪律" width="110" sortable prop="watch.tag">
            <template #default="{ row }">
              <WatchTagBadge :watch="row.watch" />
            </template>
          </el-table-column>
          <el-table-column label="缺口" width="110" sortable prop="gap_signal">
            <template #default="{ row }">
              <GapBadge :signal="row.gap_signal" :days-since="row.gap_days_since" :confirm-date="row.gap_confirm_date"
                        :reason="row.gap_reason" :lower="row.gap_lower" :upper="row.gap_upper" />
            </template>
          </el-table-column>
          <el-table-column label="最新价" width="110" sortable prop="watch.raw_price">
            <template #default="{ row }">
              <span v-if="row.watch?.raw_price != null" class="quote-price-cell">
                {{ row.watch.raw_price.toFixed(2) }}
              </span>
              <span v-else-if="quotesLoading" class="no-data">…</span>
              <span v-else class="no-data">—</span>
            </template>
          </el-table-column>
          <el-table-column label="距 EMA20" width="120" sortable prop="watch.above_ema_pct">
            <template #default="{ row }">
              <el-tooltip
                v-if="row.watch?.above_ema_pct != null"
                :content="emaTooltip(row.watch)"
                placement="top" :show-after="200"
              >
                <span class="ema-cell"
                      :class="row.watch.above_ema ? 'ema-above' : 'ema-below'">
                  {{ row.watch.above_ema ? '▲' : '▼' }}
                  {{ row.watch.above_ema_pct >= 0 ? '+' : '' }}{{ row.watch.above_ema_pct.toFixed(1) }}%
                </span>
              </el-tooltip>
              <span v-else class="no-data">—</span>
            </template>
          </el-table-column>
          <el-table-column label="较成本" width="100" sortable prop="watch.gain_pct">
            <template #default="{ row }">
              <span v-if="row.watch?.gain_pct != null"
                    :class="row.watch.gain_pct >= 0 ? 'gain-pos' : 'gain-neg'">
                {{ row.watch.gain_pct >= 0 ? '+' : '' }}{{ row.watch.gain_pct.toFixed(1) }}%
              </span>
              <span v-else class="no-data">—</span>
            </template>
          </el-table-column>
          <el-table-column label="综合评分" width="140" sortable prop="composite_score">
            <template #default="{ row }">
              <ScoreBar :score="row.composite_score" :max="100" />
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
          <el-table-column label="纪律依据" min-width="260" prop="watch.tag_reason">
            <template #default="{ row }">
              <span class="table-reason">{{ row.watch?.tag_reason || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="80" fixed="right">
            <template #default="{ row }">
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
import { watchlistApi } from '@/api'
import ScoreBar from '@/components/ScoreBar.vue'
import WatchTagBadge from '@/components/WatchTagBadge.vue'
import GapBadge from '@/components/GapBadge.vue'

const loading       = ref(false)
const quotesLoading = ref(false)
const stocks        = ref([])
const viewMode      = ref('card')   // 'card' | 'table'

// EMA20 跟随纪律的分布（取代原来的长期 / 短期买卖信号统计）
const tagCounts = computed(() => {
  const c = { FOLLOW: 0, HOLD: 0, TAKE_PROFIT: 0, STOP_LOSS: 0, NO_DATA: 0 }
  for (const s of stocks.value) {
    const t = s.watch?.tag
    if (t && c[t] !== undefined) c[t]++
    else c.NO_DATA++
  }
  return c
})

// 跳空缺口形态的分布。gap_signal 来自全市场扫描落库（stocks 表），
// 与纪律标签相互独立 —— 一只票可以同时有纪律标签和缺口形态。
const gapCounts = computed(() => {
  const c = { BREAKOUT: 0, RUNAWAY: 0, ADD: 0, EXHAUST: 0, NONE: 0 }
  for (const s of stocks.value) {
    const g = s.gap_signal
    if (g && c[g] !== undefined) c[g]++
    else c.NONE++
  }
  return c
})

const macdHits = computed(() =>
  stocks.value.filter(s => s.macd_cross_up).length
)

// 还没拿到最新成交价的只数
const quotesMissing = computed(() =>
  stocks.value.filter(s => s.watch?.raw_price == null).length
)

// K 线截止日（取最新的一只）。EMA20 基于它算，成交价却是实时的 ——
// 两者时点差太远时必须提醒，否则会照着几个月前的均线位置做今天的决定。
const klineDate = computed(() => {
  const ds = stocks.value.map(s => s.watch?.trade_date).filter(Boolean)
  return ds.length ? ds.sort().at(-1) : null
})

const klineStaleDays = computed(() => {
  if (!klineDate.value) return 0
  const diff = Date.now() - new Date(klineDate.value + 'T00:00:00').getTime()
  return Math.max(0, Math.floor(diff / 86400000))
})

/**
 * 两步加载：
 *   第一步 refresh_price=false —— 只读缓存，秒回，标签 / EMA20 / 评分立即可用
 *   第二步 refresh_price=true  —— 后台现拉 sina 不复权实时价（冷缓存约 26 秒），
 *                                 回来后把成交价与止盈涨幅补上
 * 之所以不合成一次请求：自选股是高频打开的列表页，不能每次开都转 26 秒圈。
 */
async function load({ withQuotes = true } = {}) {
  loading.value = true
  try {
    stocks.value = await watchlistApi.get(false)
  } finally {
    loading.value = false
  }
  if (withQuotes && quotesMissing.value > 0) fetchQuotes()
}

async function fetchQuotes() {
  if (quotesLoading.value) return
  quotesLoading.value = true
  try {
    stocks.value = await watchlistApi.get(true)
  } catch {
    // 拉行情失败不影响已经显示出来的标签，静默降级即可
  } finally {
    quotesLoading.value = false
  }
}

async function refreshQuotes() {
  await fetchQuotes()
  if (quotesMissing.value > 0) {
    ElMessage.warning(`仍有 ${quotesMissing.value} 只未取到行情，可稍后再试`)
  } else {
    ElMessage.success('行情已更新')
  }
}

/**
 * 距均线格子的悬浮说明。
 * price_basis='qfq' 时 close/ema20 已换算成前复权，与页面上的成交价同口径，可以直接显示；
 * ='hfq' 时换算不成立（没拿到实时价，或 K 线与实时价不同日），此时那两个数是后复权值，
 * 显示出来会和成交价差好几个数量级，所以只说百分比。
 */
function emaTooltip(w) {
  if (!w) return ''
  const when = w.trade_date ? `（K线截至 ${w.trade_date}）` : ''
  if (w.price_basis === 'qfq') {
    return `前复权收盘 ${w.close} / EMA20 ${w.ema20}${when}`
  }
  return `暂无实时价，无法换算前复权，仅显示相对位置${when}`
}

async function remove(code) {
  await ElMessageBox.confirm('确认移出自选股？', '提示', { type: 'warning' })
  await watchlistApi.remove(code)
  stocks.value = stocks.value.filter(s => s.code !== code)
  ElMessage.success('已移除')
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

.signal-section {
  margin-bottom: 16px;
}
.signal-row-label {
  font-size: 12px;
  color: #888;
  margin-bottom: 6px;
  padding-left: 2px;
  font-weight: 500;
}
.signal-row-hint {
  font-size: 11px;
  color: #b0b0b0;
  margin-top: 4px;
  padding-left: 2px;
}
.signal-stat-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
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
/* 卡片左边框按 EMA20 纪律标签着色，与 WatchTagBadge 的配色保持一致 */
.card-tag-follow      { border-left: 4px solid #67c23a; }
.card-tag-hold        { border-left: 4px solid #409eff; }
.card-tag-take_profit { border-left: 4px solid #e6a23c; }
.card-tag-stop_loss   { border-left: 4px solid #f56c6c; }
.card-tag-no_data     { border-left: 4px solid #dcdfe6; }

/* 最新成交价 */
.quote-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin: 8px 0 10px;
}
.quote-main { display: flex; align-items: baseline; gap: 8px; }
.quote-price {
  font-size: 22px;
  font-weight: 700;
  color: #1f2328;
  font-variant-numeric: tabular-nums;
}
.quote-price.no-data { color: #ccc; font-size: 18px; }
.quote-skeleton { display: inline-block; width: 56px; }
.quote-gain { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.quote-ema { font-size: 12px; color: #999; }
.quote-ema span { font-weight: 600; margin-left: 2px; }
.quote-price-cell { font-weight: 600; font-variant-numeric: tabular-nums; }

.card-top    { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
.card-title  { display: flex; align-items: center; gap: 8px; cursor: pointer; }
.card-title:hover .name { color: #409eff; }
.code { font-weight: 700; color: #409eff; }
.name { font-weight: 600; }
.card-actions { display: flex; align-items: center; gap: 6px; }
.signal-pair-card { display: inline-flex; gap: 4px; }
.no-data { color: #bbb; font-size: 13px; }

/* 距 EMA20 百分比（不显示后复权原值，量级与成交价对不上会误导） */
.ema-cell {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  cursor: help;
}
.ema-above { color: #67c23a; }
.ema-below { color: #f56c6c; }

.stale-warn {
  background: #fdf6ec;
  border: 1px solid #f5dab1;
  border-radius: 4px;
  padding: 8px 12px;
  color: #b88230;
  line-height: 1.6;
}
.stale-warn code {
  background: #fff;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 12px;
}

.gain-pos { color: #f56c6c; font-weight: 600; font-variant-numeric: tabular-nums; }
.gain-neg { color: #67c23a; font-weight: 600; font-variant-numeric: tabular-nums; }

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
