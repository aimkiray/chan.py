<template>
  <div class="analysis-panel p-4">
    <div class="mb-4">
      <h3 class="text-lg font-medium text-gray-800 mb-4">{{ t('analysis.result') }}</h3>
      
      <div class="bg-gray-50 rounded p-4 mb-4 flex justify-between items-center">
        <div>
          <div class="text-sm text-gray-500 mb-1">{{ t('analysis.latestPrice') }}</div>
          <div class="text-2xl font-bold text-gray-900">{{ latestClose }}</div>
        </div>
        <div class="text-right">
          <div class="text-sm text-emerald-500">{{ latestDate }}</div>
        </div>
      </div>
    </div>
    
    <el-divider />
    
    <div v-if="signal">
      <div class="mb-4">
        <div class="text-sm font-medium text-gray-700 mb-2">{{ t('analysis.latestSignal') }}</div>
        <el-tag :type="signal.is_buy ? 'success' : 'danger'" effect="dark" size="large" class="text-base">
          {{ signal.is_buy ? `[${t('analysis.buy')} (BUY)]` : `[${t('analysis.sell')} (SELL)]` }}
        </el-tag>
      </div>
      
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item :label="t('analysis.type')">
          <span class="font-sans font-medium">{{ formatSignalType(signal.type, signal.is_buy) }}</span>
        </el-descriptions-item>
        <el-descriptions-item :label="t('analysis.signalDate')">{{ signal.date }}</el-descriptions-item>
        <el-descriptions-item :label="timeDiffLabel">{{ timeDiffValue }}</el-descriptions-item>
      </el-descriptions>
      
      <div v-if="signalDesc" class="mt-4 bg-blue-50 text-blue-700 p-3 rounded text-sm leading-relaxed border border-blue-100">
        <i class="el-icon-info mr-1">P.S.</i> {{ signalDesc }}
      </div>
      
      <div class="mt-6">
        <div class="text-sm font-medium text-gray-700 mb-2">{{ t('analysis.score') }}: <span class="text-primary font-bold">{{ ((signal.score || 0) * 100).toFixed(2) }}%</span></div>
        <div class="flex items-center gap-3">
          <el-progress 
            :percentage="Number(((signal.score || 0) * 100).toFixed(2))" 
            :status="getScoreStatus(signal.score)" 
            :stroke-width="10"
            class="flex-1"
            :show-text="false"
          />
        </div>
        
        <el-alert
          :title="(signal.score || 0) > 0.5 ? t('analysis.highScore') : t('analysis.lowScore')"
          :type="(signal.score || 0) > 0.5 ? 'success' : 'warning'"
          :closable="false"
          show-icon
          class="mt-3"
        />
      </div>
    </div>
    
    <div v-else class="bg-blue-50 text-blue-700 p-4 rounded border border-blue-100 text-center">
      {{ t('app.pleasePredict') }}
    </div>
    
    <div v-if="accuracy" class="mt-6 pt-4 border-t border-gray-100 text-xs text-gray-500 text-center">
      {{ t('analysis.accuracy') }}: {{ accuracy.valid_count }}/{{ accuracy.total_count }} 
      <span class="font-medium ml-1">
        ({{ accuracy.accuracy !== undefined ? (accuracy.accuracy * 100).toFixed(1) : '--' }}%)
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()

const props = defineProps({
  latestClose: [String, Number],
  latestDate: String,
  signal: Object,
  accuracy: Object,
  frequency: {
    type: String,
    default: '1d'
  }
})

const timeDiffLabel = computed(() => {
  if (['1s', '1m', '5m', '15m', '30m', '60m'].includes(props.frequency)) {
    return t('analysis.timeDiff')
  }
  return t('analysis.timeDiffDays')
})

const timeDiffValue = computed(() => {
  if (!props.signal || props.signal.days_diff === undefined) return '--'
  
  const days = props.signal.days_diff
  
  if (['1s', '1m', '5m', '15m', '30m', '60m'].includes(props.frequency)) {
    const minutes = Math.round(days * 24 * 60)
    if (minutes < 60) {
      return `${minutes} ${t('analysis.minutes')}`
    } else {
       const hours = (minutes / 60).toFixed(1)
       return `${hours} ${t('analysis.hours')}`
    }
  }
  
  return days.toFixed(1)
})

const signalDesc = computed(() => {
  if (!props.signal) return ''
  const typeKey = props.signal.type.split(',')[0].trim()
  const isBuy = props.signal.is_buy

  const descKey = isBuy ? `signal_desc.buy.${typeKey}` : `signal_desc.sell.${typeKey}`
  const desc = t(descKey)
  
  if (desc === descKey) {
     return isBuy ? t('signal_desc.buy.default') : t('signal_desc.sell.default')
  }
  return desc
})

const getScoreStatus = (score) => {
  if (score > 0.7) return 'success'
  if (score > 0.5) return 'warning'
  return 'exception'
}

const formatSignalType = (type, isBuy) => {
  if (!type) return ''
  const types = type.split(',')
  return types.map(t => {
    const tStr = t.trim()
    // Convert to S1/B1 format as requested
    return isBuy ? `B${tStr}` : `S${tStr}`
  }).join(', ')
}
</script>
