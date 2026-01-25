<template>
  <div class="analysis-panel p-4 relative min-h-[200px]">
    <div v-if="loading" class="absolute inset-0 bg-white/80 dark:bg-gray-900/80 z-10 flex flex-col items-center justify-center backdrop-blur-sm rounded-lg">
      <UIcon name="i-heroicons-arrow-path" class="animate-spin w-10 h-10 text-primary-500" />
      <span class="mt-3 text-sm text-gray-500 font-medium">{{ t('sidebar.analyzing') }}</span>
    </div>

    <div class="mb-4">
      <h3 class="text-lg font-medium text-gray-800 dark:text-gray-200 mb-4">{{ t('analysis.result') }}</h3>
      
      <div class="bg-gray-50 dark:bg-gray-800 rounded p-4 mb-4 flex justify-between items-center">
        <div>
          <div class="text-sm text-gray-500 dark:text-gray-400 mb-1">{{ t('analysis.latestPrice') }}</div>
          <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ latestCloseText }}</div>
        </div>
        <div class="text-right">
          <div class="text-sm text-emerald-500">{{ latestDate }}</div>
        </div>
      </div>
    </div>
    
    <UDivider class="my-4" />
    
    <div v-if="signal">
      <div class="mb-4">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('analysis.latestSignal') }}</div>
        <UBadge :color="signal.is_buy ? 'green' : 'red'" variant="solid" size="lg" class="text-base">
          {{ signal.is_buy ? `[${t('analysis.buy')} (BUY)]` : `[${t('analysis.sell')} (SELL)]` }}
        </UBadge>
      </div>

      <!-- Model Source Info -->
      <div v-if="accuracy && accuracy.mode" class="mb-4 p-3 bg-gray-50 dark:bg-gray-800 rounded border border-gray-100 dark:border-gray-700 text-xs">
         <div class="flex justify-between items-center mb-2">
            <span class="font-bold text-sm text-gray-700 dark:text-gray-300">{{ t('analysis.modelSource') }}</span>
            <UBadge :color="getModelModeColor(accuracy.mode)" size="sm" variant="subtle">{{ getModelModeLabel(accuracy.mode) }}</UBadge>
         </div>
         
         <!-- Pretrained Details -->
         <div v-if="accuracy.mode === 'pretrained' || accuracy.mode === 'ensemble'" class="mb-1">
            <div class="flex justify-between text-gray-500">
               <span>{{ t('analysis.pretrainedModel') }}</span>
               <span class="text-gray-700 dark:text-gray-300 font-mono">{{ getPretrainedName(accuracy) }}</span>
            </div>
            <div v-if="accuracy.pretrained_trained_at" class="flex justify-between text-gray-400 mt-0.5">
               <span>{{ t('analysis.trainedAt') }}</span>
               <span>{{ formatTime(accuracy.pretrained_trained_at) }}</span>
            </div>
         </div>

         <!-- Ensemble Details -->
         <div v-if="accuracy.mode === 'ensemble'" class="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
            <div class="flex justify-between items-center mb-1">
               <span class="text-gray-500">{{ t('analysis.ensembleScores') }}</span>
            </div>
            <div class="grid grid-cols-2 gap-2">
               <div class="flex flex-col">
                  <span class="text-[10px] text-gray-400">Pretrained ({{ ((accuracy.ensemble_weight_pretrained || 0) * 100).toFixed(0) }}%)</span>
                  <span class="font-medium" :class="getScoreColor(accuracy.pretrained_score)">{{ ((accuracy.pretrained_score || 0) * 100).toFixed(1) }}%</span>
               </div>
               <div class="flex flex-col text-right">
                  <span class="text-[10px] text-gray-400">Online ({{ ((accuracy.ensemble_weight_online || 0) * 100).toFixed(0) }}%)</span>
                  <span class="font-medium" :class="getScoreColor(accuracy.online_score)">{{ ((accuracy.online_score || 0) * 100).toFixed(1) }}%</span>
               </div>
            </div>
         </div>
      </div>
      
      <div class="border border-gray-200 dark:border-gray-700 rounded-md overflow-hidden text-sm">
        <div class="flex border-b border-gray-200 dark:border-gray-700 last:border-b-0">
            <div class="w-1/3 bg-gray-50 dark:bg-gray-800 p-2 font-medium text-gray-600 dark:text-gray-400 border-r border-gray-200 dark:border-gray-700 flex items-center">{{ t('analysis.type') }}</div>
            <div class="w-2/3 p-2 font-sans font-medium text-gray-800 dark:text-gray-200">{{ formatSignalType(signal.type, signal.is_buy) }}</div>
        </div>
        <div class="flex border-b border-gray-200 dark:border-gray-700 last:border-b-0">
            <div class="w-1/3 bg-gray-50 dark:bg-gray-800 p-2 font-medium text-gray-600 dark:text-gray-400 border-r border-gray-200 dark:border-gray-700 flex items-center">{{ t('analysis.signalDate') }}</div>
            <div class="w-2/3 p-2 text-gray-800 dark:text-gray-200">{{ signal.date }}</div>
        </div>
        <div class="flex border-b border-gray-200 dark:border-gray-700 last:border-b-0">
            <div class="w-1/3 bg-gray-50 dark:bg-gray-800 p-2 font-medium text-gray-600 dark:text-gray-400 border-r border-gray-200 dark:border-gray-700 flex items-center">{{ timeDiffLabel }}</div>
            <div class="w-2/3 p-2 text-gray-800 dark:text-gray-200">{{ timeDiffValue }}</div>
        </div>
      </div>
      
      <div v-if="signalDesc" class="mt-4 bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 p-3 rounded text-sm leading-relaxed border border-blue-100 dark:border-blue-800">
        <i class="mr-1 font-bold">P.S.</i> {{ signalDesc }}
      </div>
      
      <div class="mt-6">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('analysis.score') }}: <span class="text-primary font-bold">{{ scorePctText }}</span></div>
        <div class="flex items-center gap-3">
          <UMeter 
            :value="scorePctValue" 
            :color="getScoreStatus(displayScore)" 
            class="flex-1"
          />
        </div>
        
        <UAlert
          :title="displayScore > 0.5 ? t('analysis.highScore') : t('analysis.lowScore')"
          :color="displayScore > 0.5 ? 'green' : 'orange'"
          variant="subtle"
          icon="i-heroicons-information-circle"
          class="mt-3"
        />
      </div>
    </div>
    
    <div v-else class="bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 p-4 rounded border border-blue-100 dark:border-blue-800 text-center">
      {{ t('app.pleasePredict') }}
    </div>
    
    <div v-if="accuracy" class="mt-6 pt-4 border-t border-gray-100 dark:border-gray-800 text-xs text-gray-500">
      <div class="text-center">
        {{ t('analysis.accuracy') }}: {{ accuracy.valid_count }}/{{ accuracy.total_count }}
        <span class="font-medium ml-1">
          ({{ accuracy.accuracy !== undefined ? (accuracy.accuracy * 100).toFixed(1) : '--' }}%)
        </span>
      </div>

      <div v-if="thresholdCurve.length" class="mt-4 p-3 bg-gray-50 dark:bg-gray-800 rounded border border-gray-100 dark:border-gray-700">
        <div class="flex items-center justify-between gap-2 mb-2">
          <div class="font-bold text-sm text-gray-700 dark:text-gray-300">{{ t('analysis.thresholdRecommendation') }}</div>
          <div class="text-[10px] text-gray-400 tabular-nums">
            {{ t('analysis.curvePoints', { n: thresholdCurve.length }) }}
          </div>
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div class="flex items-center justify-between gap-2">
            <div class="text-[11px] text-gray-500">{{ t('analysis.targetWinRate') }}</div>
            <UInput v-model="targetWinPct" size="xs" class="w-16 text-right" />
          </div>
          <div class="flex items-center justify-between gap-2">
            <div class="text-[11px] text-gray-500">{{ t('analysis.minTrades') }}</div>
            <UInput v-model="minTrades" size="xs" class="w-16 text-right" />
          </div>
        </div>

        <div class="mt-3 grid grid-cols-3 gap-2 text-[11px]">
          <div class="flex flex-col">
            <span class="text-gray-400">{{ t('analysis.recommendedThreshold') }}</span>
            <span v-if="recommendedRow" class="font-mono text-gray-700 dark:text-gray-200">{{ Number(recommendedRow.threshold).toFixed(3) }}</span>
            <span v-else class="text-gray-400">--</span>
          </div>
          <div class="flex flex-col">
            <span class="text-gray-400">{{ t('analysis.estimatedTradeCount') }}</span>
            <span v-if="recommendedRow" class="tabular-nums text-gray-700 dark:text-gray-200">{{ Number(recommendedRow.trade_count || 0) }}</span>
            <span v-else class="text-gray-400">--</span>
          </div>
          <div class="flex flex-col text-right">
            <span class="text-gray-400">{{ t('analysis.winRateAtThreshold') }}</span>
            <span v-if="recommendedRow" class="tabular-nums text-gray-700 dark:text-gray-200">{{ (Number(recommendedRow.win_rate || 0) * 100).toFixed(1) }}%</span>
            <span v-else class="text-gray-400">--</span>
          </div>
        </div>

        <div v-if="recommendedRow" class="mt-2 flex items-center justify-between gap-2">
          <div class="text-[10px] text-gray-400">
            {{ t('analysis.avgNetProfit') }}: <span class="font-mono">{{ Number(recommendedRow.avg_net_profit || 0).toFixed(4) }}</span>
          </div>
          <UBadge v-if="recommendedMeetsTarget" color="green" size="xs" variant="subtle">{{ t('analysis.targetMet') }}</UBadge>
          <UBadge v-else color="orange" size="xs" variant="subtle">{{ t('analysis.targetNotMet') }}</UBadge>
        </div>
      </div>

      <div v-if="accuracy.brier_score !== undefined" class="mt-2 text-center">
        {{ t('analysis.brier') }}:
        <span class="font-medium ml-1">{{ Number(accuracy.brier_score).toFixed(4) }}</span>
      </div>

      <div v-if="accuracy.ece !== undefined" class="mt-1 text-center">
        {{ t('analysis.ece') }}:
        <span class="font-medium ml-1">{{ Number(accuracy.ece).toFixed(4) }}</span>
      </div>

      <div v-if="accuracy.method || accuracy.train_count || accuracy.val_count || accuracy.test_count" class="mt-2 text-center">
        <span v-if="accuracy.method" class="mr-2">{{ accuracy.method }}</span>
        <span v-if="accuracy.train_count !== undefined" class="mr-2">train={{ accuracy.train_count }}</span>
        <span v-if="accuracy.val_count !== undefined" class="mr-2">val={{ accuracy.val_count }}</span>
        <span v-if="accuracy.test_count !== undefined">test={{ accuracy.test_count }}</span>
      </div>

      <div v-if="rankDebugEnabled" class="mt-2 text-center">
        <span class="mr-2">{{ t('analysis.rankTransform') }}: {{ rankEffectiveLabel }}</span>
        <span v-if="rankedFeatureCount !== null" class="mr-2">ranked={{ rankedFeatureCount }}</span>
        <span v-if="rankMinN !== null" class="mr-2">min_n={{ rankMinN }}</span>
      </div>

      <div v-if="rankStatLine" class="mt-1 text-center text-[11px] text-gray-400">
        {{ rankStatLine }}
      </div>

      <div v-if="volSingleLine" class="mt-1 text-center text-[11px] text-gray-400">
        {{ volSingleLine }}
      </div>

      <div v-if="foldAucLine" class="mt-1 text-center text-[11px] text-gray-400">
        {{ foldAucLine }}
      </div>

      <div v-if="aucDiagLine" class="mt-1 text-center text-[11px] text-gray-400">
        {{ aucDiagLine }}
      </div>

      <div v-if="accuracy.scale_pos_weight !== undefined" class="mt-2 text-center">
        {{ t('analysis.scalePosWeight') }}:
        <span class="font-medium ml-1">{{ Number(accuracy.scale_pos_weight).toFixed(4) }}</span>
      </div>

      <div v-if="accuracy.class_balance && accuracy.class_balance.train" class="mt-2 text-center">
        {{ t('analysis.classBalanceTrain') }}:
        <span class="font-medium ml-1">
          pos={{ accuracy.class_balance.train.pos }}, neg={{ accuracy.class_balance.train.neg }}
          ({{ (Number(accuracy.class_balance.train.pos_rate || 0) * 100).toFixed(1) }}%)
        </span>
      </div>
      <div v-if="accuracy.class_balance && accuracy.class_balance.val" class="mt-1 text-center">
        {{ t('analysis.classBalanceVal') }}:
        <span class="font-medium ml-1">
          pos={{ accuracy.class_balance.val.pos }}, neg={{ accuracy.class_balance.val.neg }}
          ({{ (Number(accuracy.class_balance.val.pos_rate || 0) * 100).toFixed(1) }}%)
        </span>
      </div>
      <div v-if="accuracy.class_balance && accuracy.class_balance.test" class="mt-1 text-center">
        {{ t('analysis.classBalanceTest') }}:
        <span class="font-medium ml-1">
          pos={{ accuracy.class_balance.test.pos }}, neg={{ accuracy.class_balance.test.neg }}
          ({{ (Number(accuracy.class_balance.test.pos_rate || 0) * 100).toFixed(1) }}%)
        </span>
      </div>

      <div v-if="showCalibrationBins && displayCalibrationBins.length" class="mt-3">
        <div class="text-center mb-2">{{ t('analysis.calibrationBins') }}</div>
        <div class="overflow-x-auto">
          <table class="w-full text-[11px] border border-gray-100 dark:border-gray-800">
            <thead class="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
              <tr>
                <th class="p-1 text-left">bin</th>
                <th class="p-1 text-left">n</th>
                <th class="p-1 text-left">avg</th>
                <th class="p-1 text-left">win</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(b, i) in displayCalibrationBins" :key="i" class="border-t border-gray-100 dark:border-gray-800">
                <td class="p-1 text-left tabular-nums">{{ calibrationBinLabel(b) }}</td>
                <td class="p-1 text-left tabular-nums">{{ b.count }}</td>
                <td class="p-1 text-left tabular-nums">{{ (Number(b.avg_pred) * 100).toFixed(1) }}%</td>
                <td class="p-1 text-left tabular-nums">{{ (Number(b.win_rate) * 100).toFixed(1) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="icTopAbs.length" class="mt-4">
        <div class="text-center mb-2">{{ t('analysis.featureIC') }}</div>
        <div class="text-center text-[11px] text-gray-500 mb-2">
          {{ t('analysis.icMaxAbs') }}:
          <span class="font-medium ml-1">{{ Number(featureIc?.max_abs_ic || 0).toFixed(4) }}</span>
          <span class="mx-2">·</span>
          {{ t('analysis.icKept') }}:
          <span class="font-medium ml-1">{{ Number(featureIc?.kept_count || 0) }}</span>/<span>{{ Number(featureIc?.feature_count || 0) }}</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-[11px] border border-gray-100 dark:border-gray-800">
            <thead class="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
              <tr>
                <th class="p-1 text-left">{{ t('analysis.feature') }}</th>
                <th class="p-1 text-left">IC</th>
                <th class="p-1 text-left">n</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(r, i) in icTopAbs" :key="i" class="border-t border-gray-100 dark:border-gray-800">
                <td class="p-1 text-left font-mono break-all">{{ r.feature }}</td>
                <td class="p-1 text-left tabular-nums">{{ Number(r.ic || 0).toFixed(4) }}</td>
                <td class="p-1 text-left tabular-nums">{{ Number(r.n || 0) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div v-if="baselineRows.length" class="mt-4">
        <div class="text-center mb-2">{{ t('analysis.baselines') }}</div>
        <div class="overflow-x-auto">
          <table class="w-full text-[11px] border border-gray-100 dark:border-gray-800">
            <thead class="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-300">
              <tr>
                <th class="p-1 text-left">{{ t('analysis.model') }}</th>
                <th class="p-1 text-left">{{ t('analysis.accuracy') }}</th>
                <th class="p-1 text-left">{{ t('analysis.brier') }}</th>
                <th class="p-1 text-left">{{ t('analysis.logloss') }}</th>
                <th class="p-1 text-left">{{ t('analysis.rocAuc') }}</th>
                <th class="p-1 text-left">{{ t('analysis.prAuc') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in baselineRows" :key="r.key" class="border-t border-gray-100 dark:border-gray-800">
                <td class="p-1 text-left">
                  <div class="font-medium text-gray-700 dark:text-gray-300">{{ r.label }}</div>
                  <div v-if="r.train_pos_rate !== undefined" class="text-[10px] text-gray-400">
                    {{ t('analysis.trainPosRate') }}: {{ (Number(r.train_pos_rate || 0) * 100).toFixed(1) }}%
                  </div>
                </td>
                <td class="p-1 text-left tabular-nums">{{ (Number(r.accuracy || 0) * 100).toFixed(1) }}%</td>
                <td class="p-1 text-left tabular-nums">{{ Number(r.brier_score || 0).toFixed(4) }}</td>
                <td class="p-1 text-left tabular-nums">{{ Number(r.logloss || 0).toFixed(4) }}</td>
                <td class="p-1 text-left tabular-nums">{{ Number(r.roc_auc || 0).toFixed(4) }}</td>
                <td class="p-1 text-left tabular-nums">{{ Number(r.pr_auc || 0).toFixed(4) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'

const { t, formatTime: formatTimeI18n } = useI18n()

const props = defineProps({
  latestClose: [String, Number],
  latestDate: String,
  signal: Object,
  accuracy: Object,
  frequency: {
    type: String,
    default: '1d'
  },
  showCalibrationBins: {
    type: Boolean,
    default: true
  },
  loading: Boolean
})

const displayScore = computed(() => {
  const raw = Number(props?.signal?.score)
  if (!Number.isFinite(raw)) return 0
  const clipped01 = Math.max(0, Math.min(1, raw))
  return Math.min(0.9999, clipped01)
})

const scorePctValue = computed(() => Number((displayScore.value * 100).toFixed(2)))
const scorePctText = computed(() => `${scorePctValue.value.toFixed(2)}%`)

const targetWinPct = ref(50)
const minTrades = ref(30)

const thresholdCurve = computed(() => {
  const c = props?.accuracy?.threshold_curve
  return Array.isArray(c) ? c : []
})

const recommendedFromCurve = computed(() => {
  const curve = thresholdCurve.value
  if (!curve.length) return { row: null, meetsTarget: false }
  const target = Math.max(0, Math.min(100, Number(targetWinPct.value)))
  const minN = Math.max(1, Number(minTrades.value))
  const targetWin = target / 100

  const rows = curve
    .map(r => ({
      threshold: Number(r?.threshold),
      trade_count: Number(r?.trade_count),
      win_rate: Number(r?.win_rate),
      avg_net_profit: Number(r?.avg_net_profit)
    }))
    .filter(r => Number.isFinite(r.threshold) && Number.isFinite(r.trade_count) && Number.isFinite(r.win_rate))
    .filter(r => r.trade_count >= minN)
    .sort((a, b) => a.threshold - b.threshold)

  if (!rows.length) return { row: null, meetsTarget: false }

  const firstMeet = rows.find(r => r.win_rate >= targetWin) || null
  if (firstMeet) return { row: firstMeet, meetsTarget: true }

  const bestWin = rows.reduce((best, r) => {
    if (!best) return r
    if (r.win_rate > best.win_rate) return r
    if (r.win_rate === best.win_rate && r.threshold > best.threshold) return r
    return best
  }, null)
  return { row: bestWin, meetsTarget: false }
})

const recommendedRow = computed(() => recommendedFromCurve.value.row)
const recommendedMeetsTarget = computed(() => recommendedFromCurve.value.meetsTarget)

const displayCalibrationBins = computed(() => {
  const q = props?.accuracy?.quantile_bins
  if (Array.isArray(q) && q.length) return q
  const fixed = props?.accuracy?.calibration_bins
  if (Array.isArray(fixed) && fixed.length) return fixed
  return []
})

const isQuantileBins = computed(() => {
  const q = props?.accuracy?.quantile_bins
  return Array.isArray(q) && q.length > 0
})

const calibrationBinLabel = (b) => {
  const low = Number(b?.low || 0)
  const high = Number(b?.high || 0)
  if (isQuantileBins.value) return `${(low * 100).toFixed(2)}%-${(high * 100).toFixed(2)}%`
  return `${low.toFixed(1)}-${high.toFixed(1)}`
}

const featureIc = computed(() => props?.accuracy?.feature_ic || null)
const icTopAbs = computed(() => {
  const rows = featureIc.value?.top_abs || []
  return Array.isArray(rows) ? rows.slice(0, 5) : []
})

const baselineRows = computed(() => {
  const baseline = props?.accuracy?.baseline || null
  if (!baseline || typeof baseline !== 'object') return []

  const rows = []
  if (baseline.logistic_regression) {
    rows.push({ key: 'logistic_regression', label: t('analysis.logisticRegression'), ...baseline.logistic_regression })
  }
  if (baseline.prior_from_train) {
    rows.push({ key: 'prior_from_train', label: t('analysis.priorFromTrain'), ...baseline.prior_from_train })
  }
  if (baseline.always_negative) {
    rows.push({ key: 'always_negative', label: t('analysis.alwaysNegative'), ...baseline.always_negative })
  }
  return rows
})

const latestCloseText = computed(() => {
  const n = Number(props.latestClose)
  return Number.isFinite(n) ? n.toFixed(2) : '--'
})

const rankTransform = computed(() => props?.accuracy?.rank_transform || null)
const rankDebugEnabled = computed(() => !!rankTransform.value && typeof rankTransform.value === 'object')
const rankEffectiveLabel = computed(() => {
  const en = rankTransform.value?.enabled
  if (en === true) return t('analysis.enabled')
  if (en === false) return t('analysis.disabled')
  return '--'
})
const rankedFeatureCount = computed(() => {
  const n = rankTransform.value?.ranked_feature_count
  const v = Number(n)
  return Number.isFinite(v) ? v : null
})
const rankMinN = computed(() => {
  const n = rankTransform.value?.min_n
  const v = Number(n)
  return Number.isFinite(v) ? v : null
})

const rankStats = computed(() => props?.accuracy?.feature_value_stats || null)
const rankStatLine = computed(() => {
  const s = rankStats.value
  const pre = s?.train_pre_rank?.volatility_250
  const post = s?.train_post_rank?.volatility_250
  if (!pre || !post) return ''
  const preMax = Number(pre?.max)
  const postMax = Number(post?.max)
  if (!Number.isFinite(preMax) || !Number.isFinite(postMax)) return ''
  return `${t('analysis.volatility250Max')} pre=${preMax.toFixed(4)} post=${postMax.toFixed(4)}`
})

const debugFeatureChecks = computed(() => {
  const a = props?.accuracy
  const c1 = a?.debug_feature_checks_oos
  if (Array.isArray(c1)) return c1
  const c2 = a?.debug_feature_checks_test
  if (Array.isArray(c2)) return c2
  return []
})

const volSingleLine = computed(() => {
  const row = debugFeatureChecks.value.find(r => String(r?.feature) === 'volatility_250')
  if (!row) return ''
  const auc = row?.roc_auc
  const ic = row?.spearman_ic
  const aucNum = Number(auc)
  const icNum = Number(ic)
  const aucText = Number.isFinite(aucNum) ? aucNum.toFixed(4) : '--'
  const icText = Number.isFinite(icNum) ? icNum.toFixed(4) : '--'
  const n = Number(row?.valid_n || 0)
  const nText = Number.isFinite(n) ? n : '--'
  return `${t('analysis.volatility250Single')} auc=${aucText} ic=${icText} n=${nText}`
})

const foldAucLine = computed(() => {
  const folds = Array.isArray(props?.accuracy?.folds) ? props.accuracy.folds : []
  if (!folds.length) return ''
  const lrAucs = folds
    .map(f => Number(f?.baseline_logistic_regression?.roc_auc))
    .filter(v => Number.isFinite(v))
  const volAucs = folds
    .map(f => {
      const rows = Array.isArray(f?.debug_feature_checks) ? f.debug_feature_checks : []
      const r = rows.find(x => String(x?.feature) === 'volatility_250')
      return Number(r?.roc_auc)
    })
    .filter(v => Number.isFinite(v))
  if (!lrAucs.length && !volAucs.length) return ''
  const lrText = lrAucs.length ? lrAucs.map(v => v.toFixed(4)).join(',') : '--'
  const volText = volAucs.length ? volAucs.map(v => v.toFixed(4)).join(',') : '--'
  return `${t('analysis.foldAuc')} lr=[${lrText}] vol=[${volText}]`
})

const aucDiagLine = computed(() => {
  const a = props?.accuracy
  if (!a) return ''
  const modelPooled = Number(a?.roc_auc)
  const modelFoldMean = Number(a?.roc_auc_fold_mean)
  const modelFoldRank = Number(a?.roc_auc_foldrank)
  const lr = a?.baseline?.logistic_regression
  const lrPooled = Number(lr?.roc_auc)
  const lrFoldMean = Number(lr?.roc_auc_fold_mean)
  const lrFoldRank = Number(lr?.roc_auc_foldrank)
  const parts = []
  if (Number.isFinite(modelPooled) || Number.isFinite(modelFoldMean) || Number.isFinite(modelFoldRank)) {
    const p = Number.isFinite(modelPooled) ? modelPooled.toFixed(4) : '--'
    const m = Number.isFinite(modelFoldMean) ? modelFoldMean.toFixed(4) : '--'
    const r = Number.isFinite(modelFoldRank) ? modelFoldRank.toFixed(4) : '--'
    parts.push(`model auc pooled=${p} fold_mean=${m} fold_rank=${r}`)
  }
  if (Number.isFinite(lrPooled) || Number.isFinite(lrFoldMean) || Number.isFinite(lrFoldRank)) {
    const p = Number.isFinite(lrPooled) ? lrPooled.toFixed(4) : '--'
    const m = Number.isFinite(lrFoldMean) ? lrFoldMean.toFixed(4) : '--'
    const r = Number.isFinite(lrFoldRank) ? lrFoldRank.toFixed(4) : '--'
    parts.push(`lr auc pooled=${p} fold_mean=${m} fold_rank=${r}`)
  }
  return parts.join(' | ')
})

const timeDiffLabel = computed(() => {
  if (['1s', '1m', '5m', '15m', '30m', '60m'].includes(props.frequency)) {
    return t('analysis.timeDiff')
  }
  return t('analysis.timeDiffDays')
})

const timeDiffValue = computed(() => {
  if (!props.signal || props.signal.days_diff === undefined) return '--'
  
  const toNumber = (v) => {
    const n = Number(v)
    return Number.isFinite(n) ? n : null
  }

  const formatNumber = (n, decimals = 2) => {
    if (n === null) return '--'
    const s = n.toFixed(decimals)
    return s.replace(/\.?0+$/, '')
  }

  const val = toNumber(props.signal.days_diff)
  if (val === null) return '--'

  if (['1s', '1m', '5m', '15m', '30m', '60m'].includes(props.frequency)) {
    const sec = val * 24 * 3600
    const barSecMap = {
      '1s': 1,
      '1m': 60,
      '5m': 5 * 60,
      '15m': 15 * 60,
      '30m': 30 * 60,
      '60m': 60 * 60
    }
    const barSec = barSecMap[String(props.frequency)] || 60
    const bars = Math.max(0, Math.round(sec / barSec))
    return `${bars} ${t('analysis.barsAgo')}`
  }
  return `${formatNumber(val, 2)} ${t('analysis.days')}`
})

const signalDesc = computed(() => {
  if (!props.signal) return ''
  const type = props.signal.type
  const isBuy = props.signal.is_buy
  const lowerType = type.toLowerCase()
  
  // Try to match specific types to translation keys
  // Keys in translation: 1, 2, 3, 2s, 2p, 1p, 3a, 3b
  let key = ''
  
  if (lowerType.includes('1buy') || lowerType === '1') key = '1'
  else if (lowerType.includes('2buy') || lowerType === '2') key = '2'
  else if (lowerType.includes('3buy') || lowerType === '3') key = '3'
  else if (lowerType.includes('l2buy') || lowerType === '2s') key = '2s'
  else if (lowerType === '3a') key = '3a'
  else if (lowerType === '3b') key = '3b'
  else if (lowerType === '1p') key = '1p'
  else if (lowerType === '2p') key = '2p'
  else if (lowerType.includes('1sell')) key = '1'
  else if (lowerType.includes('2sell')) key = '2'
  else if (lowerType.includes('3sell')) key = '3'
  else if (lowerType.includes('l2sell')) key = '2s'

  if (key) {
      return isBuy ? t(`signal_desc.buy.${key}`) : t(`signal_desc.sell.${key}`)
  }
  
  // Fallback: try using the type as key directly
  const directKey = lowerType
  const directDescKey = isBuy ? `signal_desc.buy.${directKey}` : `signal_desc.sell.${directKey}`
  const directDesc = t(directDescKey)
  
  // If translation exists (doesn't return the key itself)
  if (directDesc !== directDescKey) return directDesc
  
  return isBuy ? t('signal_desc.buy.default') : t('signal_desc.sell.default')
})

const formatSignalType = (type, isBuy) => {
  if (!type) return '--'
  
  const lowerType = type.toLowerCase()
  if (lowerType.includes('1buy')) return isBuy ? t('analysis.signalType.buy1') : t('analysis.signalType.sell1')
  if (lowerType.includes('2buy')) return isBuy ? t('analysis.signalType.buy2') : t('analysis.signalType.sell2')
  if (lowerType.includes('3buy')) return isBuy ? t('analysis.signalType.buy3') : t('analysis.signalType.sell3')
  if (lowerType.includes('l2buy')) return isBuy ? t('analysis.signalType.buyL2') : t('analysis.signalType.sellL2')
  if (lowerType.includes('l3buy')) return isBuy ? t('analysis.signalType.buyL3') : t('analysis.signalType.sellL3')
  
  if (lowerType === '3a') return isBuy ? t('analysis.signalType.buy3a') : t('analysis.signalType.sell3a')
  if (lowerType === '3b') return isBuy ? t('analysis.signalType.buy3b') : t('analysis.signalType.sell3b')
  
  if (lowerType.includes('1sell')) return t('analysis.signalType.sell1')
  if (lowerType.includes('2sell')) return t('analysis.signalType.sell2')
  if (lowerType.includes('3sell')) return t('analysis.signalType.sell3')
  if (lowerType.includes('l2sell')) return t('analysis.signalType.sellL2')
  if (lowerType.includes('l3sell')) return t('analysis.signalType.sellL3')
  
  return type.toUpperCase()
}

const getScoreStatus = (score) => {
  if (!score) return 'gray'
  if (score >= 0.7) return 'green'
  if (score >= 0.5) return 'blue'
  if (score >= 0.3) return 'orange'
  return 'red'
}

const getModelModeColor = (mode) => {
  if (mode === 'ensemble') return 'purple'
  if (mode === 'pretrained') return 'indigo'
  return 'gray'
}

const getModelModeLabel = (mode) => {
  if (mode === 'ensemble') return t('analysis.ensemble')
  if (mode === 'pretrained') return t('analysis.pretrained')
  return t('analysis.online')
}

const getPretrainedName = (acc) => {
  if (!acc) return '--'
  if (acc.pretrained_key) {
      return acc.pretrained_key.slice(0, 8) + '...'
  }
  return t('analysis.pretrained')
}

const formatTime = (iso) => {
  if (!iso) return '--'
  try {
    const s = formatTimeI18n(iso, { withYear: true, withSeconds: false })
    return s || '--'
  } catch (e) {
    return iso
  }
}

const getScoreColor = (score) => {
    if (score > 0.6) return 'text-green-600 dark:text-green-400'
    if (score > 0.5) return 'text-blue-600 dark:text-blue-400'
    return 'text-gray-600 dark:text-gray-400'
}
</script>
