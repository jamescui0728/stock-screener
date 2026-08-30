<template>
  <div class="screener">
    <!-- 筛选栏 -->
    <el-card class="filter-card">
      <el-form :model="filter" inline label-width="80px" size="small">
        <el-form-item label="搜索">
          <el-input
            v-model="filter.keyword"
            placeholder="输入代码或名称"
            clearable
            style="width:180px"
            @keyup.enter="search"
            :prefix-icon="Search"
          />
        </el-form-item>
        <el-form-item label="纪律">
          <el-select v-model="filter.watch_tag" clearable placeholder="全部" style="width:130px">
            <el-option label="🟢 可跟进" value="FOLLOW" />
            <el-option label="🔵 持有" value="HOLD" />
            <el-option label="🔴 止损" value="STOP_LOSS" />
            <el-option label="⚪ 数据不足" value="NO_DATA" />
          </el-select>
        </el-form-item>
        <el-form-item label="MACD">
          <el-select v-model="filter.macd_cross_up" clearable placeholder="全部" style="width:170px">
            <el-option label="⚡ 零轴上方回踩金叉" :value="true" />
            <el-option label="未命中" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item label="缺口">
          <el-select v-model="filter.gap_signal" clearable placeholder="全部" style="width:130px">
            <el-option label="🟢 突破" value="BREAKOUT" />
            <el-option label="🔵 加油" value="RUNAWAY" />
            <el-option label="🟡 加仓" value="ADD" />
            <el-option label="🔴 衰竭" value="EXHAUST" />
          </el-select>
        </el-form-item>
        <el-form-item label="行业">
          <el-select v-model="filter.industry_code" clearable placeholder="全部行业" style="width:160px" filterable>
            <el-option
              v-for="ind in industries" :key="ind.code"
              :label="ind.name" :value="ind.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="基本面≥">
          <el-input-number v-model="filter.min_fundamental" :min="0" :max="80"
            style="width:100px" />
        </el-form-item>
        <el-form-item label="综合分≥">
          <el-input-number v-model="filter.min_composite" :min="0" :max="100"
            style="width:100px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="search">筛选</el-button>
          <el-button @click="reset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 结果表格 -->
    <el-card>
      <template #header>
        <div class="card-header">
          <span>共 {{ total }} 只股票</span>
          <el-button size="small" @click="refreshWatch" :loading="refreshingWatch">
            重算纪律 / MACD
          </el-button>
        </div>
      </template>

      <el-table
        :data="stocks"
        v-loading="loading"
        @row-click="(row) => $router.push(`/stocks/${row.code}`)"
        highlight-current-row
        style="cursor:pointer"
      >
        <el-table-column prop="code" label="代码" width="90" fixed />
        <el-table-column prop="name" label="名称" width="110" fixed />
        <el-table-column label="所属行业" width="120" prop="industry_name">
          <template #default="{ row }">
            <span class="industry-link" @click.stop="goIndustry(row.industry_code)">
              {{ row.industry_name || '—' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="行业评分" width="120" sortable prop="industry_score">
          <template #default="{ row }">
            <ScoreBar v-if="row.industry_score != null" :score="row.industry_score" :max="100" color="#e6a23c" />
            <span v-else class="no-data">—</span>
          </template>
        </el-table-column>
        <el-table-column label="纪律" width="110" sortable prop="watch_tag">
          <template #header>
            <el-tooltip
              content="EMA20 跟随纪律：线上拿住、线下离场；站稳均线且放量才跟进"
              placement="top"
            >
              <span>纪律 <el-icon><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <WatchTagBadge :watch="rowWatch(row)" />
          </template>
        </el-table-column>
        <el-table-column label="MACD" width="100" sortable prop="macd_cross_up">
          <template #header>
            <el-tooltip
              content="只看日线 MACD 零轴上方的回踩金叉：DIF>0 且 DEA>0 且今日 DIF 上穿 DEA"
              placement="top"
            >
              <span>MACD <el-icon><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <el-tooltip v-if="row.macd_reason" :content="row.macd_reason" placement="top">
              <el-tag v-if="row.macd_cross_up" type="danger" effect="dark" size="small"
                      class="macd-tag">⚡ 金叉</el-tag>
              <span v-else class="no-data">—</span>
            </el-tooltip>
            <span v-else class="no-data">—</span>
          </template>
        </el-table-column>
        <el-table-column label="缺口" width="110" sortable prop="gap_signal">
          <template #header>
            <el-tooltip
              content="跳空缺口形态：突破(底部放量跳空) / 加油(上涨途中跳空) / 加仓(回踩缺口上沿守住) / 衰竭(天量长上影)"
              placement="top"
            >
              <span>缺口 <el-icon><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <GapBadge :signal="row.gap_signal" :days-since="row.gap_days_since" :confirm-date="row.gap_confirm_date"
                      :reason="row.gap_reason" :lower="row.gap_lower" :upper="row.gap_upper" />
          </template>
        </el-table-column>
        <el-table-column label="距 EMA20" width="110" sortable prop="watch_above_ema_pct">
          <template #default="{ row }">
            <span v-if="row.watch_above_ema_pct != null" class="ema-cell"
                  :class="row.watch_above_ema_pct >= 0 ? 'ema-above' : 'ema-below'">
              {{ row.watch_above_ema_pct >= 0 ? '▲ +' : '▼ ' }}{{ row.watch_above_ema_pct.toFixed(1) }}%
            </span>
            <span v-else class="no-data">—</span>
          </template>
        </el-table-column>
        <el-table-column label="K线截止" width="110" sortable prop="watch_kline_date">
          <template #default="{ row }">
            <span v-if="row.watch_kline_date"
                  :class="{ 'kline-stale': isStale(row.watch_kline_date) }">
              {{ row.watch_kline_date }}
            </span>
            <span v-else class="no-data">—</span>
          </template>
        </el-table-column>
        <el-table-column label="综合评分" width="140" sortable prop="composite_score">
          <template #default="{ row }">
            <ScoreBar :score="row.composite_score" :max="100" />
          </template>
        </el-table-column>
        <el-table-column label="总收益分位" width="120" sortable prop="price_pctile_life">
          <template #default="{ row }">
            <ScoreBar v-if="row.price_pctile_life != null"
                      :score="row.price_pctile_life * 100" :max="100" suffix="%" color="#0ea5e9" />
            <span v-else class="no-data">—</span>
          </template>
        </el-table-column>
        <el-table-column label="基本面" width="120" sortable prop="fundamental_score">
          <template #default="{ row }">
            <ScoreBar :score="row.fundamental_score" :max="80" color="#67c23a" />
          </template>
        </el-table-column>
        <el-table-column label="ROE质量" width="110" prop="score_roe_quality">
          <template #default="{ row }">
            <ScoreBar :score="row.score_roe_quality" :max="25" color="#409eff" />
          </template>
        </el-table-column>
        <el-table-column label="盈利增长" width="110" prop="score_profit_growth">
          <template #default="{ row }">
            <ScoreBar :score="row.score_profit_growth" :max="20" color="#e6a23c" />
          </template>
        </el-table-column>
        <el-table-column label="现金流" width="110" prop="score_cashflow">
          <template #default="{ row }">
            <ScoreBar :score="row.score_cashflow" :max="20" color="#9c27b0" />
          </template>
        </el-table-column>
        <el-table-column label="估值" width="110" prop="score_valuation">
          <template #default="{ row }">
            <ScoreBar :score="row.score_valuation" :max="20" color="#f56c6c" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="110" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" text
              @click.stop="$router.push(`/stocks/${row.code}`)">详情</el-button>
            <el-button size="small" text @click.stop="addWatch(row)">
              <el-icon><Star /></el-icon>
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pagination"
        v-model:current-page="page"
        :page-size="50"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="load"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { industryApi, stockApi, watchlistApi } from '@/api'
import ScoreBar from '@/components/ScoreBar.vue'
import WatchTagBadge from '@/components/WatchTagBadge.vue'
import GapBadge from '@/components/GapBadge.vue'

const route  = useRoute()
const router = useRouter()

const loading      = ref(false)
const refreshingWatch = ref(false)
const stocks       = ref([])
const industries   = ref([])
const total        = ref(0)
const page         = ref(1)

const filter = ref({
  keyword:         '',
  watch_tag:       '',
  macd_cross_up:   '',
  gap_signal:      '',
  industry_code:   route.query.industry || '',
  min_fundamental: 0,
  min_composite:   0,
})

async function load() {
  loading.value = true
  try {
    const res = await stockApi.list({
      ...filter.value,
      page: page.value,
      limit: 50,
    })
    stocks.value = res.items
    total.value  = res.total
  } finally {
    loading.value = false
  }
}

function search() { page.value = 1; load() }
function reset()  {
  filter.value = {
    keyword: '', watch_tag: '', macd_cross_up: '', gap_signal: '',
    industry_code: '', min_fundamental: 0, min_composite: 0,
  }
  load()
}

async function refreshWatch() {
  refreshingWatch.value = true
  try {
    const r = await stockApi.refreshWatchTags()
    ElMessage.success(
      `已重算 ${r.scanned} 只：可跟进 ${r.tag_counts.FOLLOW || 0}，` +
      `MACD 金叉 ${r.macd_cross_up}，缺口 ${Object.values(r.gap_counts || {}).reduce((a, b) => a + b, 0)}`
    )
    await load()
  } finally {
    refreshingWatch.value = false
  }
}

// WatchTagBadge 吃的是自选股接口那种嵌套 watch 对象；
// 筛选接口把字段平铺在行上，这里适配一下，避免为此改动组件。
function rowWatch(row) {
  return {
    tag:        row.watch_tag,
    tp_level:   null,
    tag_reason: row.watch_tag_reason,
  }
}

// K 线截止日早于阈值天数 → 标黄。全市场多数股票行情未更新时很常见。
function isStale(d) {
  if (!d) return false
  const days = (Date.now() - new Date(d + 'T00:00:00').getTime()) / 86400000
  return days > 7
}

async function addWatch(row) {
  await watchlistApi.add(row.code, '')
  ElMessage.success(`${row.name} 已加入自选股`)
}

function goIndustry(code) {
  if (code) {
    filter.value.industry_code = code
    search()
  }
}

onMounted(async () => {
  industries.value = await industryApi.list()
  await load()
})

watch(() => route.query.industry, (v) => {
  filter.value.industry_code = v || ''
  load()
})
</script>

<style scoped>
.filter-card { margin-bottom: 20px; }
.card-header { display: flex; justify-content: space-between; align-items: center; }
.pagination  { margin-top: 20px; justify-content: flex-end; display: flex; }
.industry-link {
  color: #409eff;
  cursor: pointer;
  font-size: 13px;
}
.industry-link:hover { text-decoration: underline; }
.no-data { color: #c0c4cc; font-size: 13px; }
.macd-tag { font-weight: 700; }
.ema-cell { font-size: 13px; font-weight: 600; font-variant-numeric: tabular-nums; }
.ema-above { color: #67c23a; }
.ema-below { color: #f56c6c; }
.kline-stale { color: #e6a23c; font-weight: 600; }
</style>
