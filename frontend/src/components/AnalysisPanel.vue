<template>
  <div class="analysis-panel p-4">
    <div class="mb-4">
      <h3 class="text-lg font-medium text-gray-800 mb-4">分析结果</h3>
      
      <div class="bg-gray-50 rounded p-4 mb-4 flex justify-between items-center">
        <div>
          <div class="text-sm text-gray-500 mb-1">最新收盘价</div>
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
        <div class="text-sm font-medium text-gray-700 mb-2">最新信号</div>
        <el-tag :type="signal.is_buy ? 'success' : 'danger'" effect="dark" size="large" class="text-base">
          {{ signal.is_buy ? '[买入 (BUY)]' : '[卖出 (SELL)]' }}
        </el-tag>
      </div>
      
      <el-descriptions :column="1" border size="small">
        <el-descriptions-item label="类型">
          <span class="font-sans font-medium">{{ formatSignalType(signal.type, signal.is_buy) }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="信号日期">{{ signal.date }}</el-descriptions-item>
        <el-descriptions-item label="距今 (天)">{{ signal.days_diff !== undefined ? signal.days_diff.toFixed(1) : '--' }}</el-descriptions-item>
      </el-descriptions>
      
      <div v-if="signalDesc" class="mt-4 bg-blue-50 text-blue-700 p-3 rounded text-sm leading-relaxed border border-blue-100">
        <i class="el-icon-info mr-1">P.S.</i> {{ signalDesc }}
      </div>
      
      <div class="mt-6">
        <div class="text-sm font-medium text-gray-700 mb-2">信号有效性评分: <span class="text-primary font-bold">{{ ((signal.score || 0) * 100).toFixed(2) }}%</span></div>
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
          :title="(signal.score || 0) > 0.5 ? '信号有效性较高' : '信号可能较弱'"
          :type="(signal.score || 0) > 0.5 ? 'success' : 'warning'"
          :closable="false"
          show-icon
          class="mt-3"
        />
      </div>
    </div>
    
    <div v-else class="bg-blue-50 text-blue-700 p-4 rounded border border-blue-100 text-center">
      P.S. 近期未发现买卖点信号。
    </div>
    
    <div v-if="accuracy" class="mt-6 pt-4 border-t border-gray-100 text-xs text-gray-500 text-center">
      历史准确率 (自2020起): {{ accuracy.valid_count }}/{{ accuracy.total_count }} 
      <span class="font-medium ml-1">
        ({{ accuracy.accuracy !== undefined ? (accuracy.accuracy * 100).toFixed(1) : '--' }}%)
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  latestClose: [String, Number],
  latestDate: String,
  signal: Object,
  accuracy: Object
})

const signalDesc = computed(() => {
  if (!props.signal) return ''
  const typeKey = props.signal.type.split(',')[0].trim() // Take first type if multiple
  const descriptions = {
    '1': "一买/卖：趋势背驰引发的转折点，通常是最佳的抄底/逃顶机会。",
    '2': "二买/卖：第一类买卖点出现后的次级别回抽确认，风险较小。",
    '3': "三买/卖：突破中枢后的回抽不进入中枢内部，往往伴随主升浪或主跌浪。",
    '2s': "类二买/卖：强力底分型或类二买卖点，属于进阶信号。",
    '2p': "类二买/卖：强力底分型或类二买卖点，属于进阶信号。",
    '1p': "类一买/卖：盘整背驰引发的转折点。",
    '3a': "三买/卖 A：中枢破坏后的回抽确认。",
    '3b': "三买/卖 B：中枢破坏后的回抽确认。",
  }
  return descriptions[typeKey] || ''
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
