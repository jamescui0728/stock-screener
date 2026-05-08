<template>
  <div class="settings-page">
    <el-row :gutter="24">

      <!-- ══ 左栏 ══ -->
      <el-col :span="14">

        <!-- 1. 综合评分权重 -->
        <el-card class="s-card">
          <template #header>
            <div class="card-hd">
              <span>📊 综合评分权重</span>
              <el-tag type="info" size="small">总和应为 1.00 · 当前 {{ weightSum }}</el-tag>
            </div>
          </template>
          <el-alert v-if="weightError" :title="weightError" type="warning" :closable="false" style="margin-bottom:16px" />

          <el-row :gutter="16">
            <el-col :span="12" v-for="w in weightFields" :key="w.key">
              <div class="field-wrap">
                <div class="field-label">{{ w.label }}</div>
                <div class="field-desc">{{ w.desc }}</div>
                <el-slider
                  v-model="form[w.key]"
                  :min="0.05" :max="0.70" :step="0.01"
                  :format-tooltip="v => (v*100).toFixed(0)+'%'"
                  show-input input-size="small"
                  @change="calcWeightSum"
                />
              </div>
            </el-col>
          </el-row>
        </el-card>

        <!-- 2. 买卖信号阈值 -->
        <el-card class="s-card">
          <template #header><span>🎯 买卖信号阈值（综合分 0-100）</span></template>
          <div class="threshold-preview">
            <div class="th-bar">
              <div class="th-sell" :style="{ width: form.SELL_THRESHOLD + '%' }">卖出 &lt;{{ form.SELL_THRESHOLD }}</div>
              <div class="th-hold" :style="{ width: (form.BUY_THRESHOLD - form.SELL_THRESHOLD) + '%' }">持有观察</div>
              <div class="th-buy" :style="{ width: (100 - form.BUY_THRESHOLD) + '%' }">≥{{ form.BUY_THRESHOLD }} 买入</div>
            </div>
          </div>
          <el-row :gutter="24" style="margin-top:20px">
            <el-col :span="12">
              <div class="field-wrap">
                <div class="field-label">买入阈值</div>
                <div class="field-desc">综合分超过此值 → BUY 信号</div>
                <el-input-number v-model="form.BUY_THRESHOLD" :min="55" :max="90" :step="1" style="width:100%" />
              </div>
            </el-col>
            <el-col :span="12">
              <div class="field-wrap">
                <div class="field-label">卖出阈值</div>
                <div class="field-desc">综合分低于此值 → SELL 信号</div>
                <el-input-number v-model="form.SELL_THRESHOLD" :min="20" :max="65" :step="1" style="width:100%" />
              </div>
            </el-col>
          </el-row>
        </el-card>

        <!-- 3. 公司准入条件 -->
        <el-card class="s-card">
          <template #header>
            <div class="card-hd">
              <span>🏢 公司基本面准入条件</span>
              <el-tooltip content="不满足准入条件的公司，即使评分高也不会发出买入信号">
                <el-icon style="cursor:help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </template>
          <el-row :gutter="16">
            <el-col :span="12" v-for="f in companyFields" :key="f.key">
              <div class="field-wrap">
                <div class="field-label">{{ f.label }}</div>
                <div class="field-desc">{{ f.desc }}</div>
                <el-input-number
                  v-model="form[f.key]"
                  :min="f.min" :max="f.max" :step="f.step"
                  :precision="f.precision ?? 1"
                  style="width:100%"
                />
              </div>
            </el-col>
          </el-row>
        </el-card>

      </el-col>

      <!-- ══ 右栏 ══ -->
      <el-col :span="10">

        <!-- 4. 行业准入 -->
        <el-card class="s-card">
          <template #header><span>🏭 行业准入线</span></template>
          <div class="field-wrap">
            <div class="field-label">行业最低评分</div>
            <div class="field-desc">低于此分的行业，内部个股不列入买入候选</div>
            <el-slider
              v-model="form.INDUSTRY_MIN_SCORE"
              :min="0" :max="90" :step="5"
              :marks="{ 0:'0', 40:'宽松', 60:'默认', 80:'严格', 90:'90' }"
              show-input input-size="small"
            />
          </div>
        </el-card>

        <!-- 5. 回测参数 -->
        <el-card class="s-card">
          <template #header><span>🔁 回测参数</span></template>
          <div v-for="f in backtestFields" :key="f.key" class="field-wrap">
            <div class="field-label">{{ f.label }}</div>
            <div class="field-desc">{{ f.desc }}</div>
            <el-input-number
              v-model="form[f.key]"
              :min="f.min" :max="f.max" :step="f.step ?? 1"
              :precision="0"
              style="width:100%"
            />
          </div>
        </el-card>

        <!-- 6. 优化目标权重 -->
        <el-card class="s-card">
          <template #header>
            <div class="card-hd">
              <span>⚡ 贝叶斯优化目标权重</span>
              <el-tag type="info" size="small">总和 {{ optWeightSum }}</el-tag>
            </div>
          </template>
          <div v-for="f in optFields" :key="f.key" class="field-wrap">
            <div class="field-label">{{ f.label }}</div>
            <div class="field-desc">{{ f.desc }}</div>
            <el-slider
              v-model="form[f.key]"
              :min="0.05" :max="0.80" :step="0.05"
              :format-tooltip="v => (v*100).toFixed(0)+'%'"
              show-input input-size="small"
            />
          </div>
        </el-card>

        <!-- 7. 模拟盘 -->
        <el-card class="s-card">
          <template #header>
            <div class="card-hd">
              <span>💰 模拟盘</span>
              <el-tooltip content="初始资金只在下次「重置账户」时生效，费率立即生效">
                <el-icon style="cursor:help"><QuestionFilled /></el-icon>
              </el-tooltip>
            </div>
          </template>
          <div class="field-wrap">
            <div class="field-label">初始资金（元）</div>
            <div class="field-desc">重置账户时使用的起始现金，支持 1 万~1 亿</div>
            <el-input-number
              v-model="form.PAPER_INIT_CASH"
              :min="10000" :max="100000000" :step="100000" :precision="0"
              style="width:100%"
            />
          </div>
          <div class="field-wrap">
            <div class="field-label">手续费率</div>
            <div class="field-desc">买卖双边都收；A 股券商实际约万分之三（0.03%）</div>
            <el-slider
              v-model="form.PAPER_FEE_RATE"
              :min="0" :max="0.003" :step="0.0001"
              :format-tooltip="v => (v*10000).toFixed(2)+'‱'"
              show-input input-size="small"
            />
          </div>
          <div class="field-wrap">
            <div class="field-label">单笔最低手续费（元）</div>
            <div class="field-desc">即使按费率算出很少，也至少收这么多</div>
            <el-input-number
              v-model="form.PAPER_MIN_FEE"
              :min="0" :max="100" :step="1" :precision="2"
              style="width:100%"
            />
          </div>
          <div class="field-wrap">
            <div class="field-label">最小交易单位（股）</div>
            <div class="field-desc">A 股标准为 100 股/手；改为 1 则支持 1 股交易（仿美股）</div>
            <el-input-number
              v-model="form.PAPER_LOT_SIZE"
              :min="1" :max="1000" :step="1" :precision="0"
              style="width:100%"
            />
          </div>
        </el-card>

        <!-- 操作按钮 -->
        <el-card class="s-card">
          <el-button type="primary" style="width:100%" :loading="saving" @click="save">
            💾 保存设置（立即生效）
          </el-button>
          <el-popconfirm
            title="确定恢复所有默认值？"
            confirm-button-text="确定"
            cancel-button-text="取消"
            @confirm="reset"
          >
            <template #reference>
              <el-button style="width:100%;margin:10px 0 0" :loading="resetting">
                🔄 恢复默认值
              </el-button>
            </template>
          </el-popconfirm>
          <div v-if="lastSaved" class="save-hint">✓ 已于 {{ lastSaved }} 保存</div>
        </el-card>

      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { settingsApi } from '@/api'

const saving    = ref(false)
const resetting = ref(false)
const lastSaved = ref('')
const form      = ref({})

// ── 字段定义 ──
const weightFields = [
  { key: 'FUNDAMENTAL_WEIGHT', label: '基本面权重', desc: 'ROE / 盈利增长 / 现金流 / 财务健康' },
  { key: 'VALUATION_WEIGHT',   label: '估值权重',   desc: 'PE/PB 历史分位安全边际' },
  { key: 'SENTIMENT_WEIGHT',   label: '舆情权重',   desc: '新闻情感与重大事件影响' },
  { key: 'MACRO_WEIGHT',       label: '宏观权重',   desc: 'PMI / CPI / 北向资金流向' },
]

const companyFields = [
  { key: 'ROE_MIN',                 label: 'ROE 最低值 %',       desc: '近几年平均 ROE 须高于此值',         min: 5,   max: 30,  step: 1,    precision: 1 },
  { key: 'ROE_MIN_YEARS',           label: 'ROE 连续达标年数',   desc: 'ROE ≥ 下限需持续的最少年数',       min: 3,   max: 15,  step: 1,    precision: 0 },
  { key: 'MAX_DEBT_RATIO',          label: '资产负债率上限',     desc: '超过此值视为高杠杆风险（0-1）',     min: 0.3, max: 0.9, step: 0.05, precision: 2 },
  { key: 'MIN_PROFIT_GROWTH_YEARS', label: '连续盈利增长年数',   desc: '净利润需连续正增长的最少年数',     min: 2,   max: 10,  step: 1,    precision: 0 },
  { key: 'MIN_FCF_RATIO',           label: '现金流/净利润下限', desc: '经营现金流 ÷ 净利润的最低比值',    min: 0.3, max: 2.0, step: 0.1,  precision: 1 },
]

const backtestFields = [
  { key: 'BACKTEST_START_YEAR',  label: '回测起始年份', desc: '历史回测从哪年开始',            min: 2010, max: 2020 },
  { key: 'BACKTEST_TRAIN_YEARS', label: '训练窗口（年）', desc: '每个滚动窗口的训练期长度',   min: 2,    max: 10 },
  { key: 'BACKTEST_VAL_YEARS',   label: '验证窗口（年）', desc: '每个滚动窗口的验证期长度',   min: 1,    max: 5 },
  { key: 'HOLD_MONTHS',          label: '持股周期（月）', desc: '买入后持有多少个月计算收益', min: 3,    max: 24 },
]

const optFields = [
  { key: 'OPT_W_WIN_RATE', label: '胜率权重', desc: '优化时买入胜率的目标权重' },
  { key: 'OPT_W_IC',       label: 'IC 权重',  desc: '信息系数（评分与收益相关性）的权重' },
  { key: 'OPT_W_SHARPE',   label: 'Sharpe 权重', desc: '风险调整收益的权重' },
]

// ── 权重校验 ──
const weightSum = computed(() => {
  const s = (weightFields.reduce((a, f) => a + (form.value[f.key] || 0), 0))
  return s.toFixed(2)
})
const weightError = computed(() =>
  Math.abs(parseFloat(weightSum.value) - 1.0) > 0.02
    ? `当前权重总和为 ${weightSum.value}，建议调整到 1.00`
    : null
)
const optWeightSum = computed(() =>
  optFields.reduce((a, f) => a + (form.value[f.key] || 0), 0).toFixed(2)
)

// ── 加载 ──
async function load() {
  const data = await settingsApi.get()
  form.value = { ...data }
}

// ── 保存 ──
async function save() {
  if (weightError.value) {
    ElMessage.warning('权重总和不等于 1.00，请先调整')
    return
  }
  saving.value = true
  try {
    await settingsApi.save(form.value)
    lastSaved.value = new Date().toLocaleTimeString()
    ElMessage.success('设置已保存，立即生效')
  } finally {
    saving.value = false
  }
}

// ── 重置 ──
async function reset() {
  resetting.value = true
  try {
    const data = await settingsApi.reset()
    form.value = { ...data }
    lastSaved.value = ''
    ElMessage.success('已恢复默认值')
  } finally {
    resetting.value = false
  }
}

function calcWeightSum() {} // 触发 computed 重算

onMounted(load)
</script>

<style scoped>
.settings-page { }

.s-card { margin-bottom: 20px; }

.card-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.field-wrap { margin-bottom: 20px; }
.field-label {
  font-size: 13px;
  font-weight: 600;
  color: #1f2328;
  margin-bottom: 2px;
}
.field-desc {
  font-size: 12px;
  color: #888;
  margin-bottom: 8px;
  line-height: 1.4;
}

/* 阈值预览条 */
.threshold-preview { margin-bottom: 4px; }
.th-bar {
  display: flex;
  height: 32px;
  border-radius: 6px;
  overflow: hidden;
  font-size: 12px;
  font-weight: 600;
}
.th-sell { background: #fef0f0; color: #f56c6c; display:flex; align-items:center; justify-content:center; min-width:60px; }
.th-hold { background: #fdf6ec; color: #e6a23c; display:flex; align-items:center; justify-content:center; flex:1; }
.th-buy  { background: #f0f9eb; color: #67c23a; display:flex; align-items:center; justify-content:center; min-width:80px; }

.save-hint { font-size:12px; color:#67c23a; margin-top:10px; text-align:center; }

/* Slider 数字输入框宽度 */
:deep(.el-slider__input) { width: 70px !important; }
</style>
