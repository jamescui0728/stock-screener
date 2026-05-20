<template>
  <div class="backtest">
    <!-- ══════════════════ 进度抽屉 ══════════════════ -->
    <el-drawer
      v-model="showProgress"
      title="回测进度"
      direction="rtl"
      size="480px"
      :close-on-click-modal="false"
      :before-close="handleDrawerClose"
    >
      <div class="progress-panel">
        <!-- 状态标题 -->
        <div class="prog-header">
          <el-tag :type="statusTagType" size="large" effect="dark">
            {{ statusLabel }}
          </el-tag>
          <span class="elapsed">⏱ {{ prog.elapsed }}s</span>
        </div>

        <!-- 当前阶段 -->
        <div class="prog-stage">{{ prog.stage || '等待启动...' }}</div>

        <!-- 主进度条 -->
        <el-progress
          :percentage="prog.pct"
          :color="progressColor"
          :stroke-width="14"
          :status="progressBarStatus"
          class="prog-bar"
        />

        <!-- 窗口进度 -->
        <div v-if="prog.total > 0" class="prog-sub">
          窗口进度：{{ prog.current }} / {{ prog.total }}
          &nbsp;·&nbsp;
          预计剩余：{{ estimatedRemaining }}
        </div>

        <!-- 实时指标 -->
        <el-row :gutter="12" class="prog-metrics" v-if="prog.win_rate != null">
          <el-col :span="12">
            <div class="prog-metric-card">
              <div class="prog-metric-val" :class="prog.win_rate >= 85 ? 'green' : 'orange'">
                {{ prog.win_rate?.toFixed(1) }}%
              </div>
              <div class="prog-metric-label">当前买入胜率</div>
              <div class="prog-metric-target">目标 ≥ 85%</div>
            </div>
          </el-col>
          <el-col :span="12">
            <div class="prog-metric-card">
              <div class="prog-metric-val" :class="(prog.ic || 0) >= 0.1 ? 'green' : 'orange'">
                {{ prog.ic?.toFixed(4) ?? '-' }}
              </div>
              <div class="prog-metric-label">IC 均值</div>
              <div class="prog-metric-target">目标 ≥ 0.1</div>
            </div>
          </el-col>
        </el-row>

        <!-- 实时日志 -->
        <div class="prog-log-wrap">
          <div class="prog-log-title">运行日志</div>
          <div class="prog-log" ref="logBox">
            <div
              v-for="(line, i) in prog.log"
              :key="i"
              class="log-line"
              :class="{
                'log-ok':    line.includes('✅') || line.includes('✓'),
                'log-err':   line.includes('❌'),
                'log-warn':  line.includes('⚠'),
                'log-arrow': line.includes('▶'),
              }"
            >{{ line }}</div>
            <div v-if="prog.status === 'running'" class="log-cursor">▌</div>
          </div>
        </div>

        <!-- 错误信息 -->
        <el-alert
          v-if="prog.status === 'error'"
          :title="prog.error_msg || '未知错误'"
          type="error" show-icon :closable="false"
          style="margin-top:12px"
        />

        <!-- 完成后跳转 -->
        <el-button
          v-if="prog.status === 'done'"
          type="success" style="width:100%;margin-top:16px"
          @click="onProgressDone"
        >
          查看回测报告 →
        </el-button>
      </div>
    </el-drawer>

    <!-- ══════════════════ 主内容 ══════════════════ -->

    <!-- 长/短期切换 Tab -->
    <el-tabs v-model="signalType" class="signal-tabs" @tab-change="onSignalTypeChange">
      <el-tab-pane label="长期回测" name="long">
        <template #label>
          <span class="tab-label">📈 长期回测<span class="tab-hint">基本面 / 6-12月</span></span>
        </template>
      </el-tab-pane>
      <el-tab-pane label="短期回测" name="short">
        <template #label>
          <span class="tab-label">⚡ 短期回测<span class="tab-hint">动量 / 1-2周</span></span>
        </template>
      </el-tab-pane>
    </el-tabs>

    <!-- 价格数据缺失警告 -->
    <el-alert
      v-if="priceCount === 0"
      type="warning"
      show-icon
      :closable="false"
      style="margin-bottom:16px"
    >
      <template #title>
        <span>缺少历史行情数据 — 回测需要股票日K线数据才能计算收益率</span>
      </template>
      <template #default>
        <div style="margin-top:8px">
          当前行情数据：<strong>0 条</strong>。
          请先拉取行情数据，然后再启动回测。
          <el-button type="warning" size="small" style="margin-left:12px"
            @click="fetchPrices" :loading="fetchingPrices"
          >
            {{ fetchingPrices ? '拉取中...' : '开始拉取行情数据' }}
          </el-button>
        </div>
      </template>
    </el-alert>

    <el-row :gutter="20">
      <!-- 左侧：控制面板 -->
      <el-col :span="7">
        <!-- 启动回测 -->
        <el-card class="ctrl-card">
          <template #header>
            启动新{{ signalType === 'short' ? '短期' : '长期' }}回测
          </template>
          <el-form :model="runForm" label-width="90px" size="small">
            <el-form-item label="描述">
              <el-input v-model="runForm.description" placeholder="本次回测备注" />
            </el-form-item>
            <el-form-item label="样本股票数">
              <el-input-number v-model="runForm.sampleSize" :min="0" :max="500" />
              <div class="form-hint">越少速度越快，用于调试；0=全量</div>
            </el-form-item>
            <el-form-item label="持有天数">
              <el-input-number v-model="runForm.holdDays" :min="3" :max="730" />
              <div class="form-hint">
                {{ signalType === 'short' ? '短线 15 天起；与回测样本独立性对齐（避免持仓重叠）' : '长线 365 天起；过短会牺牲 alpha' }}
              </div>
            </el-form-item>
            <el-button
              type="primary" style="width:100%"
              :loading="running" :disabled="running"
              @click="startRun"
            >
              {{ running ? '回测运行中...' : '▶ 启动回测' }}
            </el-button>
            <el-button
              v-if="running"
              style="width:100%;margin-top:8px"
              @click="showProgress = true"
            >
              查看进度
            </el-button>
          </el-form>
        </el-card>

        <!-- 参数优化 -->
        <el-card class="ctrl-card" style="margin-top:16px">
          <template #header>
            <div class="card-header">
              贝叶斯参数优化
              <el-tooltip content="自动调整20+个权重/阈值参数，逼近85%胜率目标">
                <el-icon style="cursor:help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </template>
          <el-form :model="optForm" label-width="90px" size="small">
            <el-form-item label="优化轮次">
              <el-input-number v-model="optForm.n_iter" :min="5" :max="50" />
            </el-form-item>
            <el-form-item label="初始随机">
              <el-input-number v-model="optForm.init_points" :min="3" :max="10" />
            </el-form-item>
            <div class="form-hint" style="margin-bottom:8px">
              预计耗时 ≈ {{ (optForm.n_iter + optForm.init_points) * 3 }} 分钟
            </div>
            <el-tooltip
              :disabled="signalType !== 'short'"
              content="短期信号引擎暂不支持参数覆盖，仅长期回测可参数优化"
              placement="top"
            >
              <el-button
                type="warning" style="width:100%"
                :loading="optimizing"
                :disabled="signalType === 'short'"
                @click="startOptimize"
              >
                {{ optimizing ? '优化运行中...' : '⚡ 启动参数优化' }}
              </el-button>
            </el-tooltip>
          </el-form>
        </el-card>

        <!-- 历史版本列表 -->
        <el-card class="ctrl-card" style="margin-top:16px">
          <template #header>
            <div class="card-header">
              历史版本
              <el-button size="small" text @click="loadRuns">
                <el-icon><Refresh /></el-icon>
              </el-button>
            </div>
          </template>
          <div class="run-list" v-loading="loadingRuns">
            <el-empty v-if="!runs.length" description="暂无回测记录" :image-size="50" />
            <div
              v-for="r in runs" :key="r.run_id"
              class="run-item"
              :class="{ active: selectedRunId === r.run_id }"
              @click="selectRun(r.run_id)"
            >
              <div class="run-top">
                <span class="run-ver">v{{ r.version }}</span>
                <span :class="winRateClass(r.win_rate)">{{ fmtPct(r.win_rate) }}</span>
              </div>
              <div class="run-desc">{{ r.description || '无描述' }}</div>
              <div class="run-meta">
                IC {{ fmtNum(r.ic_mean, 4) }} · Sharpe {{ fmtNum(r.sharpe_ratio) }}
              </div>
            </div>
          </div>
        </el-card>
      </el-col>

      <!-- 右侧：报告 -->
      <el-col :span="17">
        <!-- 无数据占位 -->
        <el-card v-if="!report && !loadingReport" class="empty-card">
          <el-empty description="选择左侧历史版本查看报告，或启动新回测">
            <el-button type="primary" @click="startRun" :disabled="running">
              启动第一次回测
            </el-button>
          </el-empty>
        </el-card>

        <div v-else v-loading="loadingReport" style="min-height:200px">
          <!-- report 依赖内容必须先确认 report 已加载，否则 loadingReport=true & report=null
               时访问 report.summary 会触发 Cannot read properties of null 报错 -->
          <template v-if="report">
          <!-- 汇总指标 -->
          <el-row :gutter="12" class="metric-row">
            <el-col :span="6" v-for="m in summaryMetrics" :key="m.label">
              <el-card class="metric-card">
                <div class="metric-val" :class="m.cls">{{ m.value }}</div>
                <div class="metric-label">{{ m.label }}</div>
                <div v-if="m.target" class="metric-target">目标: {{ m.target }}</div>
              </el-card>
            </el-col>
          </el-row>

          <!-- 胜率进度条 -->
          <el-card style="margin-bottom:16px">
            <template #header>买入胜率 vs 目标（85%）</template>
            <div class="win-rate-wrap">
              <el-progress
                :percentage="Math.min(100, report.summary.win_rate || 0)"
                :color="winRateProgressColor"
                :stroke-width="22"
                :format="() => fmtPct(report.summary.win_rate)"
              />
              <div class="target-marker">
                <div class="target-line" />
                <span class="target-label">目标 85%</span>
              </div>
            </div>
            <div class="gap-hint" v-if="(report.summary.win_rate || 0) < 85">
              距目标还差
              <strong>{{ (85 - (report.summary.win_rate || 0)).toFixed(1) }}%</strong>，
              建议：{{ improveSuggestion }}
            </div>
            <div class="reach-hint" v-else>
              🎉 已达到 85% 胜率目标！
            </div>
          </el-card>

          <el-row :gutter="16">
            <!-- 滚动窗口胜率折线 -->
            <el-col :span="14">
              <el-card>
                <template #header>滚动窗口胜率趋势</template>
                <v-chart :option="windowChartOption" style="height:230px" autoresize />
              </el-card>
            </el-col>
            <!-- 假买入归因饼图 -->
            <el-col :span="10">
              <el-card>
                <template #header>
                  假买入信号归因
                  <el-tooltip content="导致误判的主要维度，优化时优先改进">
                    <el-icon style="cursor:help;margin-left:4px"><QuestionFilled /></el-icon>
                  </el-tooltip>
                </template>
                <v-chart :option="falseBuyPieOption" style="height:230px" autoresize />
              </el-card>
            </el-col>
          </el-row>

          <!-- 月度超额收益 -->
          <el-card style="margin:16px 0">
            <template #header>月度平均超额收益（买入信号）</template>
            <v-chart :option="monthlyChartOption" style="height:200px" autoresize />
          </el-card>

          <!-- 信号日期集中度 -->
          <el-card v-if="report.signal_date_concentration?.length" style="margin:16px 0">
            <template #header>信号日期集中度（买入信号）</template>
            <el-table :data="report.signal_date_concentration" size="small" max-height="260">
              <el-table-column label="信号日" width="110">
                <template #default="{ row }">{{ row.signal_date?.slice(0,10) }}</template>
              </el-table-column>
              <el-table-column prop="n" label="数量" width="70" />
              <el-table-column label="占比" width="80">
                <template #default="{ row }">{{ fmtPct(row.share_pct) }}</template>
              </el-table-column>
              <el-table-column label="胜率" width="80">
                <template #default="{ row }">{{ fmtPct(row.win_rate) }}</template>
              </el-table-column>
              <el-table-column label="平均超额收益">
                <template #default="{ row }">
                  <span :class="(row.avg_excess || 0) >= 0 ? 'text-green' : 'text-red'">
                    {{ signedPct(row.avg_excess) }}
                  </span>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- Top 胜出 / 败出案例 -->
          <el-row :gutter="16">
            <el-col :span="12">
              <el-card>
                <template #header>🏆 Top 10 最优信号</template>
                <el-table :data="report.top_wins" size="small" max-height="260">
                  <el-table-column prop="stock_code" label="代码" width="70" />
                  <el-table-column label="信号日" width="92">
                    <template #default="{ row }">{{ row.signal_date?.slice(0,10) }}</template>
                  </el-table-column>
                  <el-table-column label="超额收益" width="85">
                    <template #default="{ row }">
                      <span class="text-green">+{{ row.excess_return?.toFixed(1) }}%</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="当时评分" width="75">
                    <template #default="{ row }">{{ row.composite_score?.toFixed(1) }}</template>
                  </el-table-column>
                </el-table>
              </el-card>
            </el-col>
            <el-col :span="12">
              <el-card>
                <template #header>⚠️ Top 10 误判案例</template>
                <el-table :data="report.top_losses" size="small" max-height="260">
                  <el-table-column prop="stock_code" label="代码" width="70" />
                  <el-table-column label="信号日" width="92">
                    <template #default="{ row }">{{ row.signal_date?.slice(0,10) }}</template>
                  </el-table-column>
                  <el-table-column label="超额收益" width="85">
                    <template #default="{ row }">
                      <span class="text-red">{{ row.excess_return?.toFixed(1) }}%</span>
                    </template>
                  </el-table-column>
                  <el-table-column label="弱项">
                    <template #default="{ row }">
                      <span class="weak-dim">{{ weakestDim(row.sub_scores) }}</span>
                    </template>
                  </el-table-column>
                </el-table>
              </el-card>
            </el-col>
          </el-row>
          </template>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { backtestApi, dataApi } from '@/api'

// ── 状态 ──
const running       = ref(false)
const optimizing    = ref(false)
const loadingRuns   = ref(false)
const loadingReport = ref(false)
const showProgress  = ref(false)
const runs          = ref([])
const report        = ref(null)
const selectedRunId = ref(null)
const logBox        = ref(null)
let   esRef         = null   // EventSource 引用

const priceCount     = ref(-1)    // -1 = 未加载
const fetchingPrices = ref(false)
// 轮询行情抓取状态的定时器（跨路由也必须清理，否则内存泄漏）
let   priceTimer     = null
let   priceStopTimer = null

// 当前 Tab：long / short — 影响表单默认值、API 过滤、维度映射
const signalType = ref('long')

const runForm = ref({ description: '', sampleSize: 50, holdDays: 365 })
const optForm = ref({ n_iter: 20, init_points: 5 })

// 切换 tab：重置表单默认值 + 重新拉对应类型的版本列表
function onSignalTypeChange(name) {
  // 切换时，hold_days 默认值跟 tab 走
  runForm.value.holdDays = name === 'short' ? 15 : 365
  // 清空当前报告（属于另一类型的）
  report.value = null
  selectedRunId.value = null
  loadRuns()
}

// ── 进度数据 ──
const prog = ref({
  run_id: 0, status: 'idle', stage: '', pct: 0,
  current: 0, total: 0, win_rate: null, ic: null,
  elapsed: 0, log: [], error_msg: '',
})

// ── 进度衍生计算 ──
const statusLabel = computed(() => ({
  idle: '待启动', running: '运行中', done: '已完成', error: '出错',
}[prog.value.status] ?? ''))

const statusTagType = computed(() => ({
  idle: 'info', running: 'primary', done: 'success', error: 'danger',
}[prog.value.status] ?? 'info'))

const progressColor = computed(() => {
  if (prog.value.status === 'error') return '#f56c6c'
  if (prog.value.status === 'done')  return '#67c23a'
  return [
    { color: '#f56c6c', percentage: 30 },
    { color: '#e6a23c', percentage: 60 },
    { color: '#409eff', percentage: 85 },
    { color: '#67c23a', percentage: 100 },
  ]
})

const progressBarStatus = computed(() => {
  if (prog.value.status === 'done')  return 'success'
  if (prog.value.status === 'error') return 'exception'
  return ''
})

const estimatedRemaining = computed(() => {
  const { elapsed, pct } = prog.value
  if (!pct || pct <= 0 || pct >= 100) return '-'
  const total = elapsed / pct * 100
  const remain = Math.max(0, total - elapsed)
  if (remain < 60)  return `${Math.round(remain)}秒`
  return `${Math.round(remain / 60)}分钟`
})

// ── 滚动日志到底部 ──
watch(() => prog.value.log, async () => {
  await nextTick()
  if (logBox.value) logBox.value.scrollTop = logBox.value.scrollHeight
}, { deep: true })

// ── 启动回测 ──
async function startRun() {
  running.value     = true
  showProgress.value = true
  prog.value = {
    run_id: 0, status: 'running', stage: '正在提交回测请求...', pct: 0,
    current: 0, total: 0, win_rate: null, ic: null,
    elapsed: 0, log: ['[--:--:--] 回测请求已发送，等待服务端响应...'], error_msg: '',
  }

  try {
    await backtestApi.run({
      description:  runForm.value.description,
      sample_codes: null,
      signal_type:  signalType.value,
      params:       { hold_days: runForm.value.holdDays },
    })

    // 订阅 SSE 进度
    esRef = backtestApi.subscribeProgress(
      (data) => { prog.value = data },
      (data) => {
        prog.value   = data
        running.value = false
        if (data.status === 'done') {
          ElMessage.success('回测完成！')
          setTimeout(loadRuns, 1000)
        } else if (data.status === 'error') {
          ElMessage.error('回测出错：' + data.error_msg)
        }
      },
    )
  } catch (e) {
    running.value = false
    prog.value.status = 'error'
    prog.value.error_msg = String(e)
  }
}

// ── 参数优化 ──
async function startOptimize() {
  optimizing.value = true
  try {
    await backtestApi.optimize({
      n_iter:       optForm.value.n_iter,
      init_points:  optForm.value.init_points,
      sample_codes: null,
      signal_type:  signalType.value,
    })
    ElMessage.success('参数优化已启动（后台运行），完成后刷新版本列表')
  } finally {
    optimizing.value = false
  }
}

// ── 关闭抽屉 ──
function handleDrawerClose(done) {
  // 运行中时询问是否关闭（回测仍在后台继续）
  if (prog.value.status === 'running') {
    ElMessage.info('回测仍在后台运行，关闭面板不会中断')
  }
  esRef?.close()
  done()
}

// ── 完成后跳转 ──
async function onProgressDone() {
  showProgress.value = false
  await loadRuns()
  if (runs.value.length) selectRun(runs.value[0].run_id)
}

// ── 版本列表 ──
async function loadRuns() {
  loadingRuns.value = true
  try {
    // 按当前 tab 的 signalType 过滤；后端再做一次保险过滤
    const all = await backtestApi.list(signalType.value)
    const sorted = [...all].sort((a, b) => {
      const ta = new Date(a.run_at || 0).getTime()
      const tb = new Date(b.run_at || 0).getTime()
      if (tb !== ta) return tb - ta
      return (b.run_id || 0) - (a.run_id || 0)
    })
    // 保留更多历史版本，方便查看本轮调参与性能优化产生的对照 run。
    runs.value = sorted.slice(0, 20)
  }
  finally { loadingRuns.value = false }
}

async function selectRun(runId) {
  selectedRunId.value = runId
  loadingReport.value = true
  try { report.value = await backtestApi.report(runId) }
  finally { loadingReport.value = false }
}

// ── 汇总指标卡 ──
const summaryMetrics = computed(() => {
  if (!report.value) return []
  const s = report.value.summary
  return [
    { label: '买入胜率',  value: fmtPct(s.win_rate),        target: '85%',  cls: winRateClass(s.win_rate) },
    { label: '年化超额',  value: fmtPct(s.annualized_alpha), cls: (s.annualized_alpha||0)>0?'green':'red' },
    { label: 'IC 均值',  value: fmtNum(s.ic_mean, 4),       target: '>0.1', cls: (s.ic_mean||0)>0.1?'green':'orange' },
    { label: 'Sharpe',   value: fmtNum(s.sharpe_ratio),     cls: (s.sharpe_ratio||0)>1?'green':'orange' },
    { label: '最大回撤',  value: '-'+fmtPct(s.max_drawdown), cls: (s.max_drawdown||0)<15?'green':'red' },
    { label: '买入信号数', value: s.n_buy_signals || 0 },
    { label: '卖出信号数', value: s.n_sell_signals || 0 },
    { label: '最大单日占比', value: fmtPct(s.top_buy_date_share || 0), cls: (s.top_buy_date_share||0)>50?'orange':'green' },
    { label: '优化综合分', value: fmtNum(s.composite_score, 4) },
  ]
})

const winRateProgressColor = computed(() => {
  const r = report.value?.summary?.win_rate || 0
  return r >= 85 ? '#67c23a' : r >= 70 ? '#e6a23c' : '#f56c6c'
})

// 维度标签 + 改进建议按当前 tab 切换
const dimLabels = computed(() => (signalType.value === 'short'
  ? { momentum:'动量', volprice:'量价', macro:'宏观', tech:'科技板块', news_heat:'新闻热度', industry_relative:'行业相对', pricing_power:'定价权' }
  : { fundamental:'基本面', valuation:'估值', sentiment:'舆情', macro:'宏观' }
))

const improveSuggestion = computed(() => {
  if (!report.value) return ''
  const top = report.value.false_buy_patterns?.[0]?.pattern
  const longMap = {
    fundamental: '加强基本面门槛（ROE/现金流要求）',
    valuation:   '收紧估值准入（降低 PE 分位上限）',
    sentiment:   '增加舆情权重或提高负面事件敏感度',
    macro:       '加入更多宏观指标，规避政策逆风期',
  }
  const shortMap = {
    momentum:  '收紧动量门槛（要求 5/20 日同时为正、站上 MA20）',
    volprice:  '量价共振过滤：只在放量上涨时入场',
    macro:     '宏观逆风期不发短期买入',
    tech:      '降低非科技板块的入场权重',
    news_heat: '过滤无新闻的冷门标的或负面消息密集股',
    industry_relative: '收紧行业相对反转门槛（要求跑输行业幅度 >3%）',
    pricing_power: '提高定价权门槛（要求毛利率高于行业 +5% 以上）',
  }
  const map = signalType.value === 'short' ? shortMap : longMap
  return map[top] ?? '调整对应维度的权重或阈值'
})

// ── 图表配置 ──
const windowChartOption = computed(() => {
  if (!report.value) return {}
  const windows = report.value.window_results || []
  return {
    tooltip: { trigger: 'axis' },
    grid: { top: 24, bottom: 30, left: 50, right: 16 },
    xAxis: { type: 'category', data: windows.map(w => w.window?.slice(0,7)), axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: 'value', name: '胜率%', min: 40, max: 100 },
    series: [{
      name: '胜率', type: 'line', smooth: true,
      data: windows.map(w => w.win_rate?.toFixed(1)),
      lineStyle: { color: '#409eff', width: 2.5 },
      areaStyle: { color: '#409eff18' },
      symbol: 'circle', symbolSize: 6, itemStyle: { color: '#409eff' },
      markLine: {
        silent: true,
        data: [{ yAxis: 85, lineStyle: { color: '#67c23a', type: 'dashed', width: 2 } }],
        label: { formatter: '目标85%', color: '#67c23a' },
      },
    }],
  }
})

const falseBuyPieOption = computed(() => {
  if (!report.value) return {}
  const patterns = report.value.false_buy_patterns || []
  const dimMap = dimLabels.value
  return {
    tooltip: { trigger: 'item', formatter: '{b}: {c}次 ({d}%)' },
    legend: { bottom: 0, fontSize: 11 },
    series: [{
      type: 'pie', radius: ['38%', '68%'],
      data: patterns.map(p => ({ name: dimMap[p.pattern] || p.pattern, value: p.count })),
      label: { show: false },
      emphasis: { label: { show: true, fontSize: 13, fontWeight: 'bold' } },
    }],
  }
})

const monthlyChartOption = computed(() => {
  if (!report.value) return {}
  const monthly = report.value.monthly_performance || []
  return {
    tooltip: { trigger: 'axis' },
    grid: { top: 20, bottom: 30, left: 55, right: 16 },
    xAxis: { type: 'category', data: monthly.map(m => m.month), axisLabel: { rotate: 30, fontSize: 10 } },
    yAxis: { type: 'value', name: '超额%', axisLabel: { fontSize: 11 } },
    series: [{
      type: 'bar', barMaxWidth: 20,
      data: monthly.map(m => ({
        value: m.avg_excess?.toFixed(2),
        itemStyle: { color: (m.avg_excess >= 0) ? '#67c23a' : '#f56c6c' },
      })),
    }],
  }
})

// ── 工具函数 ──
function fmtPct(v)     { return v != null ? v.toFixed(1) + '%' : '-' }
function fmtNum(v, d=2){ return v != null ? v.toFixed(d) : '-' }
function signedPct(v)  { return v != null ? `${v >= 0 ? '+' : ''}${v.toFixed(1)}%` : '-' }
function winRateClass(v){ return (v||0)>=85?'green':(v||0)>=70?'orange':'red' }
function weakestDim(sub) {
  if (!sub) return '-'
  const dims = dimLabels.value
  return Object.entries(dims)
    .map(([k, label]) => ({ label, val: sub[k] ?? 0 }))
    .sort((a,b) => a.val - b.val)[0]?.label ?? '-'
}

async function checkPriceData() {
  try {
    const res = await dataApi.financialStatus()
    priceCount.value = res.price_count || 0
  } catch { priceCount.value = -1 }
}

async function fetchPrices() {
  fetchingPrices.value = true
  // 清理上一次可能未结束的定时器，避免重复触发
  if (priceTimer)     { clearInterval(priceTimer);    priceTimer = null }
  if (priceStopTimer) { clearTimeout(priceStopTimer); priceStopTimer = null }
  try {
    await dataApi.updatePrices(0)
    ElMessage.success('行情数据拉取已启动（后台运行），完成后可启动回测')
    // Poll status periodically
    priceTimer = setInterval(async () => {
      await checkPriceData()
      if (priceCount.value > 0) {
        clearInterval(priceTimer); priceTimer = null
        ElMessage.success(`行情数据已就绪（${priceCount.value} 条）`)
        fetchingPrices.value = false
      }
    }, 10000)
    // Safety stop after 30 min
    priceStopTimer = setTimeout(() => {
      if (priceTimer) { clearInterval(priceTimer); priceTimer = null }
      fetchingPrices.value = false
    }, 1800000)
  } catch {
    fetchingPrices.value = false
  }
}

onMounted(() => {
  loadRuns()
  checkPriceData()
})
onUnmounted(() => {
  esRef?.close()
  if (priceTimer)     { clearInterval(priceTimer);    priceTimer = null }
  if (priceStopTimer) { clearTimeout(priceStopTimer); priceStopTimer = null }
})
</script>

<style scoped>
/* ── 长/短期切换 ── */
.signal-tabs { margin-bottom: 12px; }
.tab-label    { display: inline-flex; align-items: center; gap: 8px; font-size: 14px; }
.tab-hint     { font-size: 11px; color: #999; }

/* ── 控制面板 ── */
.ctrl-card    { }
.card-header  { display:flex; align-items:center; gap:6px; }
.form-hint    { font-size:11px; color:#aaa; margin-top:4px; }

/* ── 版本列表 ── */
.run-list { max-height:300px; overflow-y:auto; }
.run-item {
  padding:10px; border-radius:6px; cursor:pointer;
  border:1px solid transparent; margin-bottom:8px; transition:all .2s;
}
.run-item:hover  { background:#f5f7fa; border-color:#dcdfe6; }
.run-item.active { background:#ecf5ff; border-color:#409eff; }
.run-top   { display:flex; justify-content:space-between; align-items:center; }
.run-ver   { font-weight:700; color:#409eff; }
.run-desc  { font-size:12px; color:#555; margin:2px 0; }
.run-meta  { font-size:11px; color:#aaa; }

/* ── 指标卡 ── */
.metric-row  { margin-bottom:16px; }
.metric-card { text-align:center; padding:4px 0; }
.metric-val  { font-size:22px; font-weight:700; }
.metric-val.green  { color:#67c23a; }
.metric-val.orange { color:#e6a23c; }
.metric-val.red    { color:#f56c6c; }
.metric-label  { font-size:12px; color:#888; margin-top:2px; }
.metric-target { font-size:11px; color:#bbb; }

/* ── 胜率进度条 ── */
.win-rate-wrap { position:relative; padding:10px 0 20px; }
.target-marker { position:absolute; left:85%; top:10px; height:calc(100% - 20px);
  display:flex; flex-direction:column; align-items:center; }
.target-line   { width:2px; flex:1; background:#67c23a; opacity:.7; border-radius:1px; }
.target-label  { font-size:11px; color:#67c23a; white-space:nowrap; margin-top:2px; }
.gap-hint   { font-size:13px; color:#e6a23c; margin-top:8px; }
.reach-hint { font-size:14px; color:#67c23a; font-weight:600; margin-top:8px; }

/* ── 表格颜色 ── */
.text-green { color:#67c23a; font-weight:600; }
.text-red   { color:#f56c6c; font-weight:600; }
.weak-dim   { font-size:12px; color:#e6a23c; }

/* ── 空白占位 ── */
.empty-card { min-height:300px; display:flex; align-items:center; justify-content:center; }

/* ══════════════════ 进度抽屉 ══════════════════ */
.progress-panel { padding:4px 0; }

.prog-header {
  display:flex; align-items:center; justify-content:space-between;
  margin-bottom:14px;
}
.elapsed { font-size:13px; color:#888; }

.prog-stage {
  font-size:14px; color:#333; font-weight:500;
  margin-bottom:12px; min-height:20px;
}

.prog-bar  { margin-bottom:8px; }

.prog-sub  { font-size:12px; color:#888; margin-bottom:14px; }

.prog-metrics { margin-bottom:14px; }
.prog-metric-card {
  background:#f8f9fa; border-radius:8px; padding:12px;
  text-align:center;
}
.prog-metric-val {
  font-size:24px; font-weight:700;
}
.prog-metric-val.green  { color:#67c23a; }
.prog-metric-val.orange { color:#e6a23c; }
.prog-metric-label  { font-size:12px; color:#666; margin-top:2px; }
.prog-metric-target { font-size:11px; color:#bbb; }

.prog-log-wrap { }
.prog-log-title {
  font-size:12px; color:#888; font-weight:600;
  margin-bottom:6px; text-transform:uppercase; letter-spacing:.5px;
}
.prog-log {
  background:#0d1117; border-radius:8px; padding:12px;
  font-family:'JetBrains Mono','Fira Code','Menlo',monospace;
  font-size:11.5px; line-height:1.7;
  height:260px; overflow-y:auto;
  color:#c9d1d9;
}
.log-line  { word-break:break-all; }
.log-ok    { color:#56d364; }
.log-err   { color:#f85149; }
.log-warn  { color:#e3b341; }
.log-arrow { color:#79c0ff; }
.log-cursor {
  display:inline-block; animation:blink 1s step-end infinite;
  color:#58a6ff;
}
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }
</style>
