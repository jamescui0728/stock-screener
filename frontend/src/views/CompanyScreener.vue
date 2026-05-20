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
        <el-form-item label="长期信号">
          <el-select v-model="filter.signal" clearable placeholder="全部" style="width:120px">
            <el-option label="🟢 必买" value="STRONG_BUY" />
            <el-option label="🟢 买入" value="BUY" />
            <el-option label="🟡 持有" value="HOLD" />
            <el-option label="🔴 卖出" value="SELL" />
            <el-option label="🔴 必卖" value="STRONG_SELL" />
          </el-select>
        </el-form-item>
        <el-form-item label="短期信号">
          <el-select v-model="filter.short_signal" clearable placeholder="全部" style="width:120px">
            <el-option label="🟢 必买" value="STRONG_BUY" />
            <el-option label="🟢 买入" value="BUY" />
            <el-option label="🟠 观察" value="WATCH" />
            <el-option label="🟡 持有" value="HOLD" />
            <el-option label="🔴 卖出" value="SELL" />
            <el-option label="🔴 必卖" value="STRONG_SELL" />
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
          <el-button size="small" @click="refreshAll" :loading="refreshing">
            刷新长期信号
          </el-button>
          <el-button size="small" @click="refreshAllShort" :loading="refreshingShort">
            刷新短期信号
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
        <el-table-column label="长期信号" width="100">
          <template #header>
            <el-tooltip content="侧重基本面 + 估值，hold 6-12 月" placement="top">
              <span>长期信号 <el-icon><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <SignalBadge :signal="row.signal" />
          </template>
        </el-table-column>
        <el-table-column label="短期信号" width="100">
          <template #header>
            <el-tooltip content="侧重动量 + 量价 + 宏观，hold 1-2 周" placement="top">
              <span>短期信号 <el-icon><QuestionFilled /></el-icon></span>
            </el-tooltip>
          </template>
          <template #default="{ row }">
            <el-tooltip
              v-if="row.short_observe_candidate"
              :content="row.short_signal_reason || '短期评分已达买入阈值，但市场趋势过滤未通过'"
              placement="top"
            >
              <el-tag type="warning" effect="dark" size="small" class="observe-tag">观察</el-tag>
            </el-tooltip>
            <SignalBadge v-else-if="row.short_signal" :signal="row.short_signal" />
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
        <el-table-column label="操作" width="140" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" text
              @click.stop="$router.push(`/stocks/${row.code}`)">详情</el-button>
            <el-button size="small" text @click.stop="addWatch(row)">
              <el-icon><Star /></el-icon>
            </el-button>
            <el-button size="small" text @click.stop="refreshOne(row.code)" :loading="refreshingCode === row.code">
              <el-icon><Refresh /></el-icon>
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
import SignalBadge from '@/components/SignalBadge.vue'

const route  = useRoute()
const router = useRouter()

const loading      = ref(false)
const refreshing      = ref(false)
const refreshingShort = ref(false)
const refreshingCode  = ref('')
const stocks       = ref([])
const industries   = ref([])
const total        = ref(0)
const page         = ref(1)

const filter = ref({
  keyword:         '',
  signal:          '',
  short_signal:    '',
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
    keyword: '', signal: '', short_signal: '',
    industry_code: '', min_fundamental: 0, min_composite: 0,
  }
  load()
}

async function refreshOne(code) {
  refreshingCode.value = code
  try {
    await stockApi.refreshSignal(code)
    await load()
    ElMessage.success('信号已刷新')
  } finally {
    refreshingCode.value = ''
  }
}

async function refreshAll() {
  refreshing.value = true
  try {
    await stockApi.refreshAllSignals()
    ElMessage.success('长期信号刷新已启动')
  } finally {
    refreshing.value = false
  }
}

async function refreshAllShort() {
  refreshingShort.value = true
  try {
    await stockApi.refreshAllShortSignals()
    ElMessage.success('短期信号刷新已启动（约 1-2 分钟后再点筛选查看）')
  } finally {
    refreshingShort.value = false
  }
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
.observe-tag { font-weight: 700; }
</style>
