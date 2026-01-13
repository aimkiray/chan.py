<template>
  <div class="h-full flex flex-col gap-4 p-2">
    <!-- Top Bar: Tabs for Portfolio vs Backtest -->
    <div class="flex items-center gap-2 border-b border-gray-200 dark:border-gray-800 pb-2">
      <UButton
        size="sm"
        :variant="mode === 'portfolio' ? 'solid' : 'ghost'"
        :color="mode === 'portfolio' ? 'primary' : 'gray'"
        @click="mode = 'portfolio'"
      >
        {{ t('portfolio.myPortfolios') }}
      </UButton>
      <UButton
        size="sm"
        :variant="mode === 'backtest' ? 'solid' : 'ghost'"
        :color="mode === 'backtest' ? 'primary' : 'gray'"
        @click="mode = 'backtest'"
      >
        {{ t('portfolio.backtest') }}
      </UButton>
    </div>

    <!-- Portfolio Mode -->
    <div v-if="mode === 'portfolio'" class="flex-1 flex gap-4 min-h-0">
      <!-- Left: List -->
      <div class="w-1/3 flex flex-col gap-2">
        <div class="flex justify-between items-center">
          <h3 class="font-bold text-gray-700 dark:text-gray-200">{{ t('portfolio.myPortfolios') }}</h3>
          <UButton size="xs" icon="i-heroicons-plus" @click="showCreateModal = true">{{ t('portfolio.create') }}</UButton>
        </div>
        <div class="flex-1 overflow-y-auto border border-gray-200 dark:border-gray-800 rounded">
          <div
            v-for="p in portfolios"
            :key="p.id"
            class="p-3 border-b border-gray-100 dark:border-gray-800 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
            :class="{ 'bg-blue-50 dark:bg-blue-900/20': selectedPortfolioId === p.id }"
            @click="selectPortfolio(p.id)"
          >
            <div class="font-medium">{{ p.name }}</div>
            <div class="text-xs text-gray-500">{{ p.description }}</div>
          </div>
        </div>
      </div>

      <!-- Right: Detail -->
      <div class="flex-1 flex flex-col gap-2">
        <div v-if="selectedPortfolio" class="h-full flex flex-col">
          <div class="flex justify-between items-center mb-2">
            <div>
              <h3 class="font-bold text-lg">{{ selectedPortfolio.name }}</h3>
              <div class="text-xs text-gray-500">{{ selectedPortfolio.description }}</div>
            </div>
            <UButton size="xs" color="red" variant="ghost" @click="deletePortfolio(selectedPortfolio.id)">{{ t('app.delete') }}</UButton>
          </div>
          
          <div class="flex-1 border border-gray-200 dark:border-gray-800 rounded overflow-hidden flex flex-col">
            <div class="p-2 bg-gray-50 dark:bg-gray-950 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
              <span class="font-semibold">{{ t('portfolio.positions') }}</span>
              <UButton size="xs" icon="i-heroicons-plus" @click="showAddPosModal = true">{{ t('portfolio.addPos') }}</UButton>
            </div>
            <div class="flex-1 overflow-auto">
              <table class="w-full text-sm text-left">
                <thead class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-700 dark:text-gray-400 sticky top-0">
                  <tr>
                    <th class="px-3 py-2">{{ t('portfolio.code') }}</th>
                    <th class="px-3 py-2">{{ t('portfolio.stockName') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.volume') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.avgPrice') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.currentPrice') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.marketValue') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.pnl') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="pos in (selectedPortfolio.positions || [])" :key="pos.code" class="border-b dark:border-gray-700">
                    <td class="px-3 py-2 font-mono">{{ pos.code }}</td>
                    <td class="px-3 py-2">{{ pos.name }}</td>
                    <td class="px-3 py-2 text-right">{{ pos.volume }}</td>
                    <td class="px-3 py-2 text-right">{{ pos.avg_price.toFixed(2) }}</td>
                    <td class="px-3 py-2 text-right">{{ pos.current_price?.toFixed(2) || '-' }}</td>
                    <td class="px-3 py-2 text-right">{{ ((pos.current_price || pos.avg_price) * pos.volume).toFixed(2) }}</td>
                    <td class="px-3 py-2 text-right" :class="getPnLColor(pos)">
                      {{ getPnL(pos) }}
                    </td>
                  </tr>
                  <tr v-if="!selectedPortfolio.positions?.length">
                    <td colspan="7" class="px-3 py-4 text-center text-gray-500">{{ t('portfolio.noPositions') }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div v-else class="flex-1 flex items-center justify-center text-gray-400">
          {{ t('pretrain.selectToView') }}
        </div>
      </div>
    </div>

    <!-- Backtest Mode -->
    <div v-else class="flex-1 flex flex-col gap-4 overflow-y-auto">
      <UCard>
        <template #header>
          <div class="font-bold">{{ t('portfolio.simpleBacktest') }}</div>
        </template>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.startDate') }}</label>
            <UInput type="date" v-model="backtestConfig.start_date" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.endDate') }}</label>
            <UInput type="date" v-model="backtestConfig.end_date" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.modelKey') }}</label>
            <UInput v-model="backtestConfig.model_key" :placeholder="t('portfolio.autoPlaceholder')" />
          </div>
          <div class="flex items-end">
            <UButton block color="primary" :loading="backtestLoading" @click="runBacktest">{{ t('portfolio.runBacktest') }}</UButton>
          </div>
        </div>
      </UCard>

      <div v-if="backtestResult" class="flex flex-col gap-4">
        <div class="grid grid-cols-4 gap-4">
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.totalReturn') }}</div>
            <div class="text-xl font-bold" :class="getColor(backtestResult.metrics.total_return)">
              {{ (backtestResult.metrics.total_return * 100).toFixed(2) }}%
            </div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.sharpe') }}</div>
            <div class="text-xl font-bold">{{ backtestResult.metrics.sharpe_ratio.toFixed(2) }}</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.maxDD') }}</div>
            <div class="text-xl font-bold text-red-500">{{ (backtestResult.metrics.max_drawdown * 100).toFixed(2) }}%</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.finalEquity') }}</div>
            <div class="text-xl font-bold">{{ backtestResult.metrics.final_equity.toFixed(2) }}</div>
          </UCard>
        </div>

        <!-- Simple Chart Placeholder -->
        <UCard>
           <template #header>{{ t('portfolio.equityCurve') }}</template>
           <div class="h-[300px] w-full" ref="chartContainer"></div>
        </UCard>
      </div>
    </div>

    <!-- Modals -->
    <UModal v-model="showCreateModal">
      <UCard>
        <template #header>{{ t('portfolio.create') }}</template>
        <div class="space-y-4">
          <UInput v-model="newPortfolio.name" :placeholder="t('portfolio.name')" />
          <UInput v-model="newPortfolio.description" :placeholder="t('portfolio.desc')" />
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="showCreateModal = false">{{ t('portfolio.cancel') }}</UButton>
            <UButton color="primary" @click="createPortfolio">{{ t('portfolio.create') }}</UButton>
          </div>
        </template>
      </UCard>
    </UModal>

    <UModal v-model="showAddPosModal">
      <UCard>
        <template #header>{{ t('portfolio.addPos') }}</template>
        <div class="space-y-4">
          <UInput v-model="newPosition.code" :placeholder="t('portfolio.code')" />
          <UInput v-model="newPosition.name" :placeholder="t('portfolio.stockName')" />
          <UInput type="number" v-model="newPosition.volume" :placeholder="t('portfolio.volume')" />
          <UInput type="number" v-model="newPosition.price" :placeholder="t('portfolio.price')" />
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="showAddPosModal = false">{{ t('portfolio.cancel') }}</UButton>
            <UButton color="primary" @click="addPosition">{{ t('portfolio.add') }}</UButton>
          </div>
        </template>
      </UCard>
    </UModal>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()

const mode = ref('portfolio')
const portfolios = ref([])
const selectedPortfolioId = ref(null)
const selectedPortfolio = ref(null)

const showCreateModal = ref(false)
const newPortfolio = ref({ name: '', description: '' })

const showAddPosModal = ref(false)
const newPosition = ref({ code: '', name: '', volume: 0, price: 0 })

// Backtest
const backtestConfig = ref({
  start_date: '',
  end_date: '',
  model_key: ''
})
const backtestLoading = ref(false)
const backtestResult = ref(null)
const chartContainer = ref(null)
let chartInstance = null

const fetchPortfolios = async () => {
  try {
    const res = await axios.get('/api/portfolios')
    portfolios.value = res.data
  } catch (e) {
    console.error(e)
  }
}

const selectPortfolio = async (id) => {
  selectedPortfolioId.value = id
  try {
    const res = await axios.get(`/api/portfolios/${id}`)
    selectedPortfolio.value = res.data
  } catch (e) {
    console.error(e)
  }
}

const createPortfolio = async () => {
  try {
    await axios.post('/api/portfolios', newPortfolio.value)
    showCreateModal.value = false
    newPortfolio.value = { name: '', description: '' }
    fetchPortfolios()
  } catch (e) {
    alert(t('portfolio.createFailed'))
  }
}

const deletePortfolio = async (id) => {
  if (!confirm(t('portfolio.deleteConfirm'))) return
  try {
    await axios.delete(`/api/portfolios/${id}`)
    if (selectedPortfolioId.value === id) {
      selectedPortfolioId.value = null
      selectedPortfolio.value = null
    }
    fetchPortfolios()
  } catch (e) {
    alert(t('portfolio.deleteFailed'))
  }
}

const addPosition = async () => {
  if (!selectedPortfolioId.value) return
  try {
    await axios.post(`/api/portfolios/${selectedPortfolioId.value}/positions`, {
      code: newPosition.value.code,
      name: newPosition.value.name,
      volume: Number(newPosition.value.volume),
      price: Number(newPosition.value.price)
    })
    showAddPosModal.value = false
    newPosition.value = { code: '', name: '', volume: 0, price: 0 }
    selectPortfolio(selectedPortfolioId.value)
  } catch (e) {
    alert(t('portfolio.addFailed'))
  }
}

const getPnL = (pos) => {
  if (!pos.current_price) return '-'
  const val = (pos.current_price - pos.avg_price) * pos.volume
  return val.toFixed(2)
}

const getPnLColor = (pos) => {
  if (!pos.current_price) return ''
  const val = pos.current_price - pos.avg_price
  return val > 0 ? 'text-red-500' : (val < 0 ? 'text-green-500' : '')
}

const getColor = (val) => {
  return val > 0 ? 'text-red-500' : (val < 0 ? 'text-green-500' : '')
}

const runBacktest = async () => {
  backtestLoading.value = true
  try {
    const res = await axios.post('/api/backtest/vector', backtestConfig.value)
    if (res.data.error) {
        alert(res.data.error)
        return
    }
    backtestResult.value = res.data
    nextTick(() => {
        renderChart()
    })
  } catch (e) {
    alert(t('portfolio.backtestFailed'))
    console.error(e)
  } finally {
    backtestLoading.value = false
  }
}

const renderChart = () => {
    if (!chartContainer.value || !backtestResult.value) return
    if (chartInstance) chartInstance.dispose()
    
    chartInstance = echarts.init(chartContainer.value)
    const curve = backtestResult.value.equity_curve
    
    const option = {
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: curve.map(x => x.time) },
        yAxis: { type: 'value', scale: true },
        series: [{
            data: curve.map(x => x.equity),
            type: 'line',
            smooth: true,
            showSymbol: false,
            lineStyle: { color: '#ef4444' },
            areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{offset: 0, color: 'rgba(239,68,68,0.5)'}, {offset: 1, color: 'rgba(239,68,68,0.0)'}]) }
        }]
    }
    chartInstance.setOption(option)
}

// Expose methods for parent
defineExpose({
    refresh: fetchPortfolios,
    openAddPosition: (code, name) => {
        mode.value = 'portfolio'
        newPosition.value.code = code
        newPosition.value.name = name || ''
        showAddPosModal.value = true
    }
})

onMounted(() => {
  fetchPortfolios()
})
</script>
