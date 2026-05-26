<template>
  <div class="auto-follow" v-loading="loading" element-loading-text="加载中…">

    <!-- ═══════ 顶部概览 ═══════ -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="4">
        <el-card class="stat-card">
          <div class="stat-lbl">账户净值</div>
          <div class="stat-num">¥ {{ fmtMoney(perf.nav) }}</div>
          <div class="stat-sub" :class="pnlClass(perf.nav - perf.initial_cash)">
            初始 ¥ {{ fmtMoney(perf.initial_cash) }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card class="stat-card">
          <div class="stat-lbl">总收益率</div>
          <div class="stat-num" :class="pnlClass(perf.total_return)">
            {{ perf.total_return != null ? (perf.total_return > 0 ? '+' : '') + perf.total_return + '%' : '--' }}
          </div>
          <div class="stat-sub" :class="pnlClass(perf.nav - perf.initial_cash)">
            {{ fmtSigned(perf.nav - perf.initial_cash) }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card class="stat-card">
          <div class="stat-lbl">可用现金</div>
          <div class="stat-num">¥ {{ fmtMoney(perf.cash_balance) }}</div>
          <div class="stat-sub">持仓市值 ¥ {{ fmtMoney(perf.market_value) }}</div>
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card class="stat-card">
          <div class="stat-lbl">胜率</div>
          <div class="stat-num" :class="perf.win_rate >= 50 ? 'text-up' : 'text-down'">
            {{ perf.win_rate != null ? perf.win_rate + '%' : '--' }}
          </div>
          <div class="stat-sub">
            已实现 {{ fmtSigned(perf.total_realized_pnl) }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card class="stat-card">
          <div class="stat-lbl">交易次数</div>
          <div class="stat-num">{{ perf.n_buys + perf.n_sells }}</div>
          <div class="stat-sub">买 {{ perf.n_buys }} / 卖 {{ perf.n_sells }}</div>
        </el-card>
      </el-col>
      <el-col :span="4">
        <el-card class="stat-card">
          <div class="stat-lbl">当前持仓</div>
          <div class="stat-num">{{ perf.n_open }} 只</div>
          <div class="stat-sub">
            <el-button size="small" :loading="refreshing" @click="load">
              <el-icon><Refresh /></el-icon> 刷新
            </el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ═══════ 当前持仓 ═══════ -->
    <el-card class="section-card">
      <template #header>
        <div class="section-header">
          <span>📊 当前持仓</span>
          <el-tag size="small" type="info">{{ perf.positions?.length || 0 }} 只</el-tag>
        </div>
      </template>

      <el-table
        :data="perf.positions"
        stripe
        size="small"
        empty-text="暂无持仓"
      >
        <el-table-column label="代码" prop="code" width="100" />
        <el-table-column label="名称" prop="name" min-width="120" />
        <el-table-column label="持股数" prop="shares" width="90" align="right" />
        <el-table-column label="成本价" width="90" align="right">
          <template #default="{ row }">{{ fmtPrice(row.avg_cost) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="90" align="right">
          <template #default="{ row }">
            <span :class="pnlClass(row.current_price - row.avg_cost)">
              {{ fmtPrice(row.current_price) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="市值" width="110" align="right">
          <template #default="{ row }">¥ {{ fmtMoney(row.market_value) }}</template>
        </el-table-column>
        <el-table-column label="浮盈" width="110" align="right">
          <template #default="{ row }">
            <span :class="pnlClass(row.unrealized)">
              {{ fmtSigned(row.unrealized) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="浮盈%" width="90" align="right">
          <template #default="{ row }">
            <span :class="pnlClass(row.unrealized)">
              {{ row.avg_cost && row.current_price
                  ? ((row.current_price - row.avg_cost) / row.avg_cost * 100).toFixed(2) + '%'
                  : '--' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="持有天数" width="90" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.held_days >= 20 ? 'warning' : 'info'">
              {{ row.held_days ?? '--' }}天
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ═══════ 历史交易流水 ═══════ -->
    <el-card class="section-card">
      <template #header>
        <div class="section-header">
          <span>📋 历史交易</span>
          <el-tag size="small" type="info">{{ txns.length }} 笔</el-tag>
        </div>
      </template>

      <el-table
        :data="txns"
        stripe
        size="small"
        empty-text="暂无交易记录"
        max-height="400"
      >
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ fmtTime(row.trade_time) }}</template>
        </el-table-column>
        <el-table-column label="方向" width="70" align="center">
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.side === 'BUY' ? 'danger' : 'success'"
              effect="light"
            >{{ row.side === 'BUY' ? '买入' : '卖出' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="代码" prop="stock_code" width="100" />
        <el-table-column label="数量" prop="shares" width="80" align="right" />
        <el-table-column label="价格" width="90" align="right">
          <template #default="{ row }">{{ fmtPrice(row.price) }}</template>
        </el-table-column>
        <el-table-column label="金额" width="110" align="right">
          <template #default="{ row }">¥ {{ fmtMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="已实现盈亏" width="120" align="right">
          <template #default="{ row }">
            <span v-if="row.realized_pnl != null" :class="pnlClass(row.realized_pnl)">
              {{ fmtSigned(row.realized_pnl) }}
            </span>
            <span v-else class="text-muted">--</span>
          </template>
        </el-table-column>
        <el-table-column label="备注" min-width="120">
          <template #default="{ row }">
            <span class="text-muted">{{ row.note }}</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { autoFollowApi } from '@/api'

// ── 状态 ──
const loading   = ref(false)
const refreshing = ref(false)
const perf      = ref({
  nav: 0, initial_cash: 0, cash_balance: 0, market_value: 0,
  total_return: null, win_rate: null, total_realized_pnl: 0,
  n_buys: 0, n_sells: 0, n_open: 0, positions: [],
  account_id: null,
})
const txns = ref([])

// ── 加载 ──
async function load() {
  loading.value = true
  try {
    const [data, list] = await Promise.all([
      autoFollowApi.performance(),
      autoFollowApi.transactions(500),
    ])
    perf.value = data
    txns.value = list
  } finally {
    loading.value = false
  }
}

onMounted(load)

// ── 格式化工具 ──
function fmtMoney(v) {
  if (v == null) return '--'
  return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtPrice(v) {
  if (v == null) return '--'
  return Number(v).toFixed(2)
}
function fmtSigned(v) {
  if (v == null) return '--'
  const s = v >= 0 ? '+' : ''
  return s + Number(v).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
function fmtTime(v) {
  if (!v) return '--'
  return new Date(v).toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}
function pnlClass(v) {
  if (v == null) return ''
  return v > 0 ? 'text-up' : v < 0 ? 'text-down' : ''
}
</script>

<style scoped>
.auto-follow { display: flex; flex-direction: column; gap: 20px; }

/* 顶部统计卡 */
.stat-row { margin-bottom: 4px; }
.stat-card { text-align: center; padding: 4px 0; }
.stat-lbl  { font-size: 12px; color: #888; margin-bottom: 6px; }
.stat-num  { font-size: 22px; font-weight: 700; color: #1f2328; line-height: 1.2; }
.stat-sub  { font-size: 12px; color: #888; margin-top: 4px; }

/* 涨跌颜色 */
.text-up   { color: #e5343d; }
.text-down { color: #0fa858; }
.text-muted { color: #aaa; font-size: 12px; }

/* 区块卡 */
.section-card { border-radius: 8px; }
.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
}
</style>
