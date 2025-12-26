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
          <div class="text-2xl font-bold text-gray-900 dark:text-white">{{ latestClose }}</div>
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
    
    <div v-if="accuracy" class="mt-6 pt-4 border-t border-gray-100 dark:border-gray-800 text-xs text-gray-500 text-center">
      {{ t('analysis.accuracy') }}: {{ accuracy.valid_count }}/{{ accuracy.total_count }} 
      <span class="font-medium ml-1">
        ({{ accuracy.accuracy !== undefined ? (accuracy.accuracy * 100).toFixed(1) : '--' }}%)
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const { t } = useI18n()

const props = defineProps({
  latestClose: [String, Number],
  latestDate: String,
  signal: Object,
  accuracy: Object,
  frequency: {
    type: String,
    default: '1d'
  },
  loading: Boolean
})

const timeDiffLabel = computed(() => {
  if (['1s', '1m', '5m', '15m', '30m', '60m'].includes(props.frequency)) {
    return t('analysis.timeDiff')
  }
  return t('analysis.timeDiffDays')
})

const timeDiffValue = computed(() => {
  if (!props.signal || props.signal.days_diff === undefined) return '--'
  
  const val = props.signal.days_diff
  if (['1s', '1m', '5m', '15m', '30m', '60m'].includes(props.frequency)) {
    // If it's intraday, maybe display as "bars ago" or convert to time
    // Assuming days_diff is actually "bars diff" or "time units diff" for intraday
    // But for now let's just show the number
    return val + ' ' + t('analysis.barsAgo')
  }
  return val + ' ' + t('analysis.days')
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
  if (lowerType.includes('1buy')) return isBuy ? '一买 (1B)' : '一卖 (1S)'
  if (lowerType.includes('2buy')) return isBuy ? '二买 (2B)' : '二卖 (2S)'
  if (lowerType.includes('3buy')) return isBuy ? '三买 (3B)' : '三卖 (3S)'
  if (lowerType.includes('l2buy')) return isBuy ? '类二买 (L2B)' : '类二卖 (L2S)'
  if (lowerType.includes('l3buy')) return isBuy ? '类三买 (L3B)' : '类三卖 (L3S)'
  
  if (lowerType === '3a') return isBuy ? '三买A (3B-A)' : '三卖A (3S-A)'
  if (lowerType === '3b') return isBuy ? '三买B (3B-B)' : '三卖B (3S-B)'
  
  if (lowerType.includes('1sell')) return '一卖 (1S)'
  if (lowerType.includes('2sell')) return '二卖 (2S)'
  if (lowerType.includes('3sell')) return '三卖 (3S)'
  if (lowerType.includes('l2sell')) return '类二卖 (L2S)'
  if (lowerType.includes('l3sell')) return '类三卖 (L3S)'
  
  return type.toUpperCase()
}

const getScoreStatus = (score) => {
  if (!score) return 'gray'
  if (score >= 0.7) return 'green'
  if (score >= 0.5) return 'blue'
  if (score >= 0.3) return 'orange'
  return 'red'
}
</script>
