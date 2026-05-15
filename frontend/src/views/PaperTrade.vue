<template>
  <div
    class="paper-trade"
    v-loading="loading"
    element-loading-text="正在加载账户实时行情，首次约 5-10 秒…"
    element-loading-background="rgba(255, 255, 255, 0.85)"
  >
    <!-- ═══════ 账户切换栏 ═══════ -->
    <div class="account-bar">
      <span class="account-bar-label">账户：</span>
      <el-select
        v-model="currentAccountId"
        size="default"
        class="account-select"
        @change="onAccountChange"
      >
        <el-option
          v-for="a in accounts"
          :key="a.id"
          :label="a.name + ' (¥ ' + fmtMoney(a.cash_balance) + ')'"
          :value="a.id"
        />
      </el-select>
      <el-button size="small" @click="openCreateAccountDialog">
        <el-icon><Plus /></el-icon> 新建账户
      </el-button>
      <el-button size="small" @click="openRenameAccountDialog" :disabled="!currentAccount">
        重命名
      </el-button>
      <el-button
        size="small" type="danger" text
        :disabled="accounts.length <= 1"
        @click="doDeleteAccount"
      >删除账户</el-button>
    </div>

    <!-- ═══════ 顶部：账户概览 ═══════ -->
    <el-row :gutter="16" class="stat-row">
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-lbl">总资产</div>
          <div class="stat-num">¥ {{ fmtMoney(account.total_assets) }}</div>
          <div class="stat-sub" :class="pnlClass(account.total_pnl)">
            {{ fmtSigned(account.total_pnl) }} ({{ fmtPct(account.total_return_pct) }})
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-lbl">可用现金</div>
          <div class="stat-num">¥ {{ fmtMoney(account.cash_balance) }}</div>
          <div class="stat-sub">初始 ¥ {{ fmtMoney(account.initial_cash) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-lbl">持仓市值</div>
          <div class="stat-num">¥ {{ fmtMoney(account.market_value) }}</div>
          <div class="stat-sub" :class="pnlClass(account.unrealized_pnl)">
            浮盈 {{ fmtSigned(account.unrealized_pnl) }}
          </div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card class="stat-card">
          <div class="stat-lbl">持仓股票</div>
          <div class="stat-num">{{ account.position_count || 0 }} 只</div>
          <div class="stat-sub">
            <el-button size="small" type="primary" @click="openBuyDialog()">
              <el-icon><Plus /></el-icon> 买入
            </el-button>
            <el-tooltip content="轻刷新（重读缓存）" placement="top">
              <el-button size="small" @click="refreshAll">
                <el-icon><Refresh /></el-icon>
              </el-button>
            </el-tooltip>
            <el-tooltip content="强制重新拉 Sina 实时价（约 10 秒）" placement="top">
              <el-button size="small" type="warning" text :loading="warmingUp" @click="forceRefreshPrices">
                {{ warmingUp ? '刷新中…' : '刷新行情' }}
              </el-button>
            </el-tooltip>
            <el-button size="small" type="danger" text @click="doReset">重置账户</el-button>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- ═══════ 持仓列表 ═══════ -->
    <el-card class="section">
      <template #header>
        <div class="section-head">
          <span>当前持仓</span>
          <span class="section-hint" v-if="account.positions?.length">
            共 {{ account.positions.length }} 只 · 点击行可跳转公司详情
          </span>
        </div>
      </template>

      <el-empty v-if="!account.positions?.length" description="暂无持仓，点右上角买入" />

      <el-table
        v-else
        :data="account.positions"
        @row-click="(row) => $router.push(`/stocks/${row.stock_code}`)"
        style="cursor:pointer"
        :default-sort="{ prop: 'market_value', order: 'descending' }"
      >
        <el-table-column prop="stock_code" label="代码" width="90" fixed />
        <el-table-column prop="stock_name" label="名称" width="120" fixed />
        <el-table-column label="长期" width="80">
          <template #default="{ row }">
            <SignalBadge :signal="row.signal" />
          </template>
        </el-table-column>
        <el-table-column label="短期" width="80">
          <template #default="{ row }">
            <SignalBadge :signal="row.short_signal" />
          </template>
        </el-table-column>
        <el-table-column label="持股" width="90" align="right" prop="shares" sortable>
          <template #default="{ row }">{{ fmtInt(row.shares) }}</template>
        </el-table-column>
        <el-table-column label="成本价" width="90" align="right" prop="avg_cost" sortable>
          <template #default="{ row }">{{ row.avg_cost?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="现价" width="90" align="right" prop="current_price" sortable>
          <template #default="{ row }">
            {{ row.current_price?.toFixed(2) ?? '-' }}
          </template>
        </el-table-column>
        <el-table-column label="市值" width="120" align="right" prop="market_value" sortable>
          <template #default="{ row }">¥ {{ fmtMoney(row.market_value) }}</template>
        </el-table-column>
        <el-table-column label="浮盈" width="110" align="right" prop="unrealized_pnl" sortable>
          <template #default="{ row }">
            <span :class="pnlClass(row.unrealized_pnl)">
              {{ fmtSigned(row.unrealized_pnl) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="收益率" width="100" align="right" prop="pnl_pct" sortable>
          <template #default="{ row }">
            <span :class="pnlClass(row.pnl_pct)">
              {{ fmtPct(row.pnl_pct) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="190" fixed="right" align="center">
          <template #default="{ row }">
            <el-button size="small" type="success" @click.stop="openBuyDialog(row)">加仓</el-button>
            <el-button size="small" type="danger" @click.stop="openSellDialog(row)">卖出</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- ═══════ 交易流水 ═══════ -->
    <el-card class="section">
      <template #header>
        <div class="section-head">
          <span>交易流水</span>
          <span class="section-hint">最近 200 条</span>
        </div>
      </template>
      <el-empty v-if="!transactions.length" description="暂无交易记录" />
      <el-table v-else :data="transactions" max-height="420">
        <el-table-column label="时间" width="170" prop="trade_time">
          <template #default="{ row }">{{ fmtTime(row.trade_time) }}</template>
        </el-table-column>
        <el-table-column label="方向" width="80">
          <template #default="{ row }">
            <el-tag size="small" :type="row.side === 'BUY' ? 'success' : 'danger'">
              {{ row.side === 'BUY' ? '买入' : '卖出' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="stock_code" label="代码" width="90" />
        <el-table-column prop="stock_name" label="名称" width="130" />
        <el-table-column label="股数" width="90" align="right" prop="shares">
          <template #default="{ row }">{{ fmtInt(row.shares) }}</template>
        </el-table-column>
        <el-table-column label="成交价" width="90" align="right" prop="price">
          <template #default="{ row }">{{ row.price?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="金额" width="120" align="right" prop="amount">
          <template #default="{ row }">¥ {{ fmtMoney(row.amount) }}</template>
        </el-table-column>
        <el-table-column label="手续费" width="80" align="right" prop="fee">
          <template #default="{ row }">{{ row.fee?.toFixed(2) }}</template>
        </el-table-column>
        <el-table-column label="已实现盈亏" width="110" align="right">
          <template #default="{ row }">
            <span v-if="row.realized_pnl == null" style="color:#bbb">-</span>
            <span v-else :class="pnlClass(row.realized_pnl)">
              {{ fmtSigned(row.realized_pnl) }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="备注" prop="note" min-width="120" />
      </el-table>
    </el-card>

    <!-- ═══════ 买入对话框 ═══════ -->
    <el-dialog v-model="buyVisible" title="买入股票" width="460px" align-center>
      <el-form :model="buyForm" label-width="80px">
        <el-form-item label="股票代码">
          <el-input
            v-model="buyForm.stock_code"
            placeholder="6 位代码，如 600036"
            maxlength="6"
            :disabled="buyLockedCode"
            @blur="loadQuote(buyForm.stock_code)"
            @keyup.enter="loadQuote(buyForm.stock_code)"
          >
            <template #append>
              <el-button @click="loadQuote(buyForm.stock_code)">查询</el-button>
            </template>
          </el-input>
        </el-form-item>
        <el-form-item label="名称" v-if="quote.name">
          <div class="quote-line">
            <span class="quote-name">{{ quote.name }}</span>
            <SignalBadge :signal="quote.signal" />
            <span v-if="quote.composite_score != null" class="quote-score">
              {{ quote.composite_score?.toFixed(1) }}
            </span>
            <SignalBadge :signal="quote.short_signal" />
            <span v-if="quote.short_composite_score != null" class="quote-score short">
              {{ quote.short_composite_score?.toFixed(1) }}
            </span>
          </div>
        </el-form-item>
        <el-form-item label="最新价" v-if="quote.close">
          <span class="quote-price">¥ {{ quote.close?.toFixed(2) }}</span>
          <span class="quote-date">（{{ quote.trade_date }}）</span>
        </el-form-item>
        <el-form-item label="买入股数">
          <el-input-number
            v-model="buyForm.shares"
            :min="rules.lot_size" :step="rules.lot_size" :step-strictly="rules.lot_size > 1"
            style="width:180px"
          />
          <span class="hint">每手 {{ rules.lot_size }} 股</span>
        </el-form-item>
        <el-form-item label="预估金额" v-if="buyEstAmount > 0">
          <span class="est-amount">¥ {{ fmtMoney(buyEstAmount) }}</span>
          <span class="hint">（手续费约 ¥ {{ buyEstFee.toFixed(2) }}）</span>
          <div v-if="buyEstAmount + buyEstFee > account.cash_balance"
               class="err-hint">现金不足！当前余额 ¥ {{ fmtMoney(account.cash_balance) }}</div>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="buyForm.note" placeholder="可选，如：看好行业景气" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="buyVisible = false">取消</el-button>
        <el-button
          type="primary" :loading="submitting"
          :disabled="!canBuy"
          @click="doBuy"
        >确认买入</el-button>
      </template>
    </el-dialog>

    <!-- ═══════ 卖出对话框 ═══════ -->
    <el-dialog v-model="sellVisible" title="卖出股票" width="460px" align-center>
      <el-form :model="sellForm" label-width="80px" v-if="sellForm.stock_code">
        <el-form-item label="股票">
          <span class="quote-name">{{ sellForm.stock_name }}</span>
          <span style="margin-left:8px;color:#888">({{ sellForm.stock_code }})</span>
        </el-form-item>
        <el-form-item label="持仓">
          {{ fmtInt(sellForm.max_shares) }} 股 · 成本 {{ sellForm.avg_cost?.toFixed(2) }}
        </el-form-item>
        <el-form-item label="现价" v-if="quote.close">
          <span class="quote-price">¥ {{ quote.close?.toFixed(2) }}</span>
          <span class="quote-date">（{{ quote.trade_date }}）</span>
        </el-form-item>
        <el-form-item label="卖出股数">
          <el-input-number
            v-model="sellForm.shares"
            :min="Math.min(rules.lot_size, sellForm.max_shares)" :max="sellForm.max_shares"
            :step="rules.lot_size"
            :step-strictly="rules.lot_size > 1 && sellForm.shares !== sellForm.max_shares"
            style="width:180px"
          />
          <el-button size="small" text type="primary" @click="sellForm.shares = sellForm.max_shares">
            全部清仓
          </el-button>
        </el-form-item>
        <el-form-item label="预估盈亏" v-if="sellEstPnl != null">
          <span :class="pnlClass(sellEstPnl)">{{ fmtSigned(sellEstPnl) }}</span>
          <span class="hint">（成交额 ¥ {{ fmtMoney(sellEstAmount) }}，手续费 ¥ {{ sellEstFee.toFixed(2) }}）</span>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="sellForm.note" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sellVisible = false">取消</el-button>
        <el-button
          type="danger" :loading="submitting"
          @click="doSell"
        >确认卖出</el-button>
      </template>
    </el-dialog>

    <!-- ═══════ 新建账户对话框 ═══════ -->
    <el-dialog v-model="createAccountVisible" title="新建账户" width="420px" align-center>
      <el-form :model="createAccountForm" label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="createAccountForm.name" placeholder="如：低估值蓝筹仓" maxlength="30" show-word-limit />
        </el-form-item>
        <el-form-item label="初始资金">
          <el-input-number
            v-model="createAccountForm.initial_cash"
            :min="10000" :step="100000"
            :precision="0"
            style="width:200px"
          />
          <span class="hint">默认 ¥ {{ fmtMoney(rules.init_cash) }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createAccountVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="doCreateAccount">创建</el-button>
      </template>
    </el-dialog>

    <!-- ═══════ 重命名账户对话框 ═══════ -->
    <el-dialog v-model="renameAccountVisible" title="重命名账户" width="380px" align-center>
      <el-form :model="renameAccountForm" label-width="60px">
        <el-form-item label="名称">
          <el-input v-model="renameAccountForm.name" maxlength="30" show-word-limit />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="renameAccountVisible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="doRenameAccount">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { paperApi } from '@/api'
import SignalBadge from '@/components/SignalBadge.vue'

// ── 费率/最低股数：从后端读（响应"参数设置"热更新）──
const rules = ref({ fee_rate: 0.0003, min_fee: 5.0, lot_size: 100, init_cash: 1_000_000 })

const loading      = ref(false)
const submitting   = ref(false)
const warmingUp    = ref(false)   // "刷新行情"按钮的 loading 状态（区别于轻刷新）
const account      = ref({})
const transactions = ref([])

// 多账户
const accounts         = ref([])      // [{id, name, cash_balance, ...}]
const currentAccountId = ref(null)
const currentAccount   = computed(() => accounts.value.find(a => a.id === currentAccountId.value))

const ACCT_LS_KEY = 'ss_paper_account_id'

const buyVisible   = ref(false)
const sellVisible  = ref(false)
const buyLockedCode = ref(false)   // 加仓时锁定 code 输入框

const createAccountVisible = ref(false)
const renameAccountVisible = ref(false)
const createAccountForm = reactive({ name: '', initial_cash: 1_000_000 })
const renameAccountForm = reactive({ name: '' })

const quote = reactive({ name: '', signal: '', composite_score: null, short_signal: '', short_composite_score: null, close: null, trade_date: '' })

const buyForm = reactive({
  stock_code: '',
  shares: 100,
  note: '',
})

const sellForm = reactive({
  stock_code: '',
  stock_name: '',
  shares: 100,
  max_shares: 0,
  avg_cost: 0,
  note: '',
})

// ── 估算：金额 / 手续费 / 盈亏 ──
const buyEstAmount = computed(() =>
  (quote.close || 0) * (buyForm.shares || 0)
)
const buyEstFee = computed(() =>
  Math.max(+(buyEstAmount.value * rules.value.fee_rate).toFixed(2),
           buyEstAmount.value > 0 ? rules.value.min_fee : 0)
)
const canBuy = computed(() => {
  if (!buyForm.stock_code || !buyForm.shares) return false
  if (!quote.close) return false
  if (buyEstAmount.value + buyEstFee.value > account.value.cash_balance) return false
  return true
})

const sellEstAmount = computed(() =>
  (quote.close || 0) * (sellForm.shares || 0)
)
const sellEstFee = computed(() =>
  Math.max(+(sellEstAmount.value * rules.value.fee_rate).toFixed(2),
           sellEstAmount.value > 0 ? rules.value.min_fee : 0)
)
const sellEstPnl = computed(() => {
  if (!quote.close || !sellForm.shares) return null
  return (quote.close - sellForm.avg_cost) * sellForm.shares - sellEstFee.value
})

// ── 格式化 ──
// fmtMoney: v 为 null/undefined 时返回 '--'（区分"加载中"与"真为 0"）
const fmtMoney  = v => (v == null) ? '--' : v.toLocaleString('zh-CN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const fmtInt    = v => (v || 0).toLocaleString('zh-CN')
const fmtSigned = v => (v == null ? '-' : (v >= 0 ? '+' : '') + fmtMoney(v))
const fmtPct    = v => (v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(2) + '%')
const fmtTime   = v => v?.slice(0, 19).replace('T', ' ')
const pnlClass  = v => v > 0 ? 'pnl-pos' : (v < 0 ? 'pnl-neg' : '')

// ── 数据加载 ──
async function loadAccounts() {
  const list = await paperApi.listAccounts()
  accounts.value = list
  // 选定当前账户：localStorage 优先，其次第一个
  const saved = parseInt(localStorage.getItem(ACCT_LS_KEY))
  if (saved && list.some(a => a.id === saved)) {
    currentAccountId.value = saved
  } else if (list.length) {
    currentAccountId.value = list[0].id
    localStorage.setItem(ACCT_LS_KEY, String(currentAccountId.value))
  } else {
    currentAccountId.value = null
  }
}

async function load() {
  loading.value = true
  try {
    if (!currentAccountId.value) {
      await loadAccounts()
    }
    const aid = currentAccountId.value
    const [a, t, r] = await Promise.all([
      paperApi.account(aid),
      paperApi.transactions(aid, 200),
      paperApi.rules(),
    ])
    account.value = a
    transactions.value = t
    rules.value = r
    // 同步 accounts 列表里这个账户的 cash_balance（下拉显示用）
    const cur = accounts.value.find(x => x.id === aid)
    if (cur) cur.cash_balance = a.cash_balance
  } finally {
    loading.value = false
  }
}

async function refreshAll() {
  await loadAccounts()
  await load()
  ElMessage.success('已刷新')
}

// 强制刷新行情：先清后端缓存重新打 sina，再 load。约 10 秒（持仓多则更久）。
async function forceRefreshPrices() {
  warmingUp.value = true
  try {
    const r = await paperApi.warmupCache()
    await load()
    if (!r.refreshed) {
      // 无持仓 / 无账户场景：用 info 而非 success 更准确
      ElMessage({ type: 'info', message: r.message || '无持仓需刷新', duration: 4000 })
    } else {
      ElMessage({ type: 'success', message: r.message, duration: 5000 })
    }
  } catch (e) {
    // 后端 429 节流也走这里
    ElMessage({
      type: 'error',
      message: '刷新行情失败：' + (e.response?.data?.detail || e.message),
      duration: 5000,
    })
  } finally {
    warmingUp.value = false
  }
}

async function onAccountChange(id) {
  localStorage.setItem(ACCT_LS_KEY, String(id))
  await load()
}

// ── 查询报价 ──
async function loadQuote(code) {
  if (!code || code.length < 6) return
  try {
    const q = await paperApi.quote(code)
    Object.assign(quote, q)
  } catch {
    Object.assign(quote, { name: '', signal: '', composite_score: null, short_signal: '', short_composite_score: null, close: null, trade_date: '' })
  }
}

// ── 买入 ──
function openBuyDialog(row = null) {
  Object.assign(quote, { name: '', signal: '', composite_score: null, short_signal: '', short_composite_score: null, close: null, trade_date: '' })
  const lot = rules.value.lot_size || 100
  if (row) {
    // 持仓加仓：锁定 code
    buyForm.stock_code = row.stock_code
    buyForm.shares = lot
    buyForm.note = ''
    buyLockedCode.value = true
    loadQuote(row.stock_code)
  } else {
    buyForm.stock_code = ''
    buyForm.shares = lot
    buyForm.note = ''
    buyLockedCode.value = false
  }
  buyVisible.value = true
}

async function doBuy() {
  if (!canBuy.value) return
  submitting.value = true
  try {
    const r = await paperApi.buy(
      currentAccountId.value, buyForm.stock_code, buyForm.shares, null, buyForm.note
    )
    ElMessage.success(r.message)
    buyVisible.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

// ── 卖出 ──
function openSellDialog(row) {
  const lot = rules.value.lot_size || 100
  sellForm.stock_code = row.stock_code
  sellForm.stock_name = row.stock_name
  sellForm.max_shares = row.shares
  sellForm.avg_cost   = row.avg_cost
  sellForm.shares     = Math.min(lot, row.shares)
  sellForm.note       = ''
  loadQuote(row.stock_code)
  sellVisible.value = true
}

async function doSell() {
  submitting.value = true
  try {
    const r = await paperApi.sell(
      currentAccountId.value, sellForm.stock_code, sellForm.shares, null, sellForm.note
    )
    ElMessage.success(r.message)
    sellVisible.value = false
    await load()
  } finally {
    submitting.value = false
  }
}

// ── 重置 ──
async function doReset() {
  const ic = rules.value.init_cash
  const acctName = currentAccount.value?.name || '当前账户'
  await ElMessageBox.confirm(
    `确认清空账户「${acctName}」的所有持仓并重置现金到 ¥ ${fmtMoney(ic)}？`
    + `\n（初始资金可在"参数设置 → 模拟盘"修改）`
    + '\n此操作不可恢复！',
    '重置账户',
    { type: 'warning', confirmButtonText: '重置', cancelButtonText: '取消' },
  )
  await paperApi.reset(currentAccountId.value)   // initial_cash 不传 → 后端用 settings.PAPER_INIT_CASH
  ElMessage.success('账户已重置')
  await loadAccounts()
  await load()
}

// ── 账户管理 ──
function openCreateAccountDialog() {
  createAccountForm.name = ''
  createAccountForm.initial_cash = rules.value.init_cash || 1_000_000
  createAccountVisible.value = true
}

async function doCreateAccount() {
  const name = (createAccountForm.name || '').trim() || '新账户'
  submitting.value = true
  try {
    const acct = await paperApi.createAccount(name, createAccountForm.initial_cash)
    ElMessage.success(`账户「${acct.name}」已创建`)
    createAccountVisible.value = false
    // 自动切到新账户
    await loadAccounts()
    currentAccountId.value = acct.id
    localStorage.setItem(ACCT_LS_KEY, String(acct.id))
    await load()
  } finally {
    submitting.value = false
  }
}

function openRenameAccountDialog() {
  if (!currentAccount.value) return
  renameAccountForm.name = currentAccount.value.name
  renameAccountVisible.value = true
}

async function doRenameAccount() {
  const name = (renameAccountForm.name || '').trim()
  if (!name) {
    ElMessage.warning('名称不能为空')
    return
  }
  submitting.value = true
  try {
    await paperApi.updateAccount(currentAccountId.value, { name })
    ElMessage.success('已重命名')
    renameAccountVisible.value = false
    await loadAccounts()
  } finally {
    submitting.value = false
  }
}

async function doDeleteAccount() {
  if (accounts.value.length <= 1) {
    ElMessage.warning('至少保留一个账户')
    return
  }
  const acctName = currentAccount.value?.name || '当前账户'
  await ElMessageBox.confirm(
    `确认删除账户「${acctName}」？\n该账户的全部持仓和交易流水都会一并删除！`,
    '删除账户',
    { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
  )
  await paperApi.deleteAccount(currentAccountId.value)
  ElMessage.success('账户已删除')
  // 切到剩下的第一个
  localStorage.removeItem(ACCT_LS_KEY)
  currentAccountId.value = null
  await loadAccounts()
  await load()
}

onMounted(async () => {
  await loadAccounts()
  await load()
})
</script>

<style scoped>
.paper-trade { padding: 4px 0; }

.account-bar {
  display: flex; align-items: center; gap: 8px;
  margin-bottom: 12px;
  padding: 10px 14px;
  background: #f7f8fa;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}
.account-bar-label { font-size: 13px; color: #555; font-weight: 500; }
.account-select { width: 280px; }

.stat-row { margin-bottom: 16px; }

.stat-card { height: 100%; }
.stat-lbl { font-size: 13px; color: #888; }
.stat-num { font-size: 26px; font-weight: 700; margin: 6px 0; color: #1f2328; }
.stat-sub {
  font-size: 12px; color: #888;
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
}

.section { margin-top: 16px; }
.section-head {
  display: flex; align-items: center; justify-content: space-between;
}
.section-hint { font-size: 12px; color: #888; font-weight: 400; }

.quote-line { display: flex; align-items: center; gap: 8px; }
.quote-name { font-weight: 600; font-size: 15px; }
.quote-score { font-size: 12px; color: #409eff; }
.quote-score.short { color: #e6a23c; }
.quote-price { font-size: 18px; font-weight: 700; color: #f56c6c; }
.quote-date  { font-size: 12px; color: #888; margin-left: 6px; }

.est-amount  { font-size: 16px; font-weight: 600; color: #409eff; }
.hint        { font-size: 12px; color: #888; margin-left: 8px; }
.err-hint    { font-size: 12px; color: #f56c6c; margin-top: 4px; }

.pnl-pos { color: #f56c6c; font-weight: 600; }   /* A 股惯例：红涨 */
.pnl-neg { color: #67c23a; font-weight: 600; }   /* A 股惯例：绿跌 */
</style>
