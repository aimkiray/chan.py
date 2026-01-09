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
        <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('analysis.score') }}: <span class="text-primary font-bold">{{ ((signal.score || 0) * 100).toFixed(2) }}%</span></div>
        <div class="flex items-center gap-3">
          <UMeter 
            :value="Number(((signal.score || 0) * 100).toFixed(2))" 
            :color="getScoreStatus(signal.score)" 
            class="flex-1"
          />
        </div>
        
        <UAlert
          :title="(signal.score || 0) > 0.5 ? t('analysis.highScore') : t('analysis.lowScore')"
          :color="(signal.score || 0) > 0.5 ? 'green' : 'orange'"
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

      <div v-if="showCalibrationBins && accuracy.calibration_bins && accuracy.calibration_bins.length" class="mt-3">
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
              <tr v-for="(b, i) in accuracy.calibration_bins" :key="i" class="border-t border-gray-100 dark:border-gray-800">
                <td class="p-1 text-left tabular-nums">{{ Number(b.low).toFixed(1) }}-{{ Number(b.high).toFixed(1) }}</td>
                <td class="p-1 text-left tabular-nums">{{ b.count }}</td>
                <td class="p-1 text-left tabular-nums">{{ (Number(b.avg_pred) * 100).toFixed(1) }}%</td>
                <td class="p-1 text-left tabular-nums">{{ (Number(b.win_rate) * 100).toFixed(1) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

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

const latestCloseText = computed(() => {
  const n = Number(props.latestClose)
  return Number.isFinite(n) ? n.toFixed(2) : '--'
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
