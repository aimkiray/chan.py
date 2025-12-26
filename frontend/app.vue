<template>
  <div class="lg:h-screen min-h-screen bg-white dark:bg-gray-900 flex flex-col text-gray-700 dark:text-gray-200">
    <UNotifications />
    <!-- Desktop Header (Combined Title + Tabs) -->
    <div class="hidden lg:flex items-center h-10 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 flex-shrink-0 z-10 relative">
       <!-- Logo & Title -->
       <div class="flex items-center gap-2 font-bold text-gray-800 dark:text-white text-base select-none pl-4 border-r border-gray-200 dark:border-gray-800 box-border flex-shrink-0 h-full" style="width: 260px">
          <span class="app-title-pixel">{{ t('app.title') }}</span>
       </div>
       
       <!-- Custom Tabs -->
       <div class="flex items-end self-stretch gap-1">
          <button 
             v-for="tab in tabs" 
             :key="tab.name" 
             @click="activeTab = tab.name"
             class="px-4 h-full flex items-center gap-2 text-sm transition-colors border-b-2 outline-none select-none"
             :class="activeTab === tab.name ? 'border-blue-600 text-blue-600 font-medium bg-white dark:bg-gray-900' : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 hover:bg-gray-100/50 dark:hover:bg-gray-800/50'"
          >
             <svg width="16" height="16" viewBox="0 0 24 24"><path :d="tab.icon" /></svg>
             {{ tab.label }}
          </button>
       </div>
       
       <div class="flex-1"></div>
       
       <!-- Lang Switch -->
       <div class="pr-4">
         <USelectMenu v-model="lang" :options="[{ label: '中文', value: 'zh' }, { label: 'English', value: 'en' }]" value-attribute="value" option-attribute="label" size="sm" class="w-24" />
       </div>
    </div>

    <!-- Mobile Header (Title Only - Keep Status Quo) -->
    <div class="lg:hidden border-b border-gray-200 dark:border-gray-800 flex items-center px-4 h-12 gap-3 bg-white dark:bg-gray-900">
      <h1 class="text-base font-bold text-gray-800 dark:text-white m-0 truncate flex items-center gap-2">
        <span class="app-title-pixel">{{ t('app.title') }}</span>
      </h1>
      <USelectMenu v-model="lang" :options="[{ label: '中文', value: 'zh' }, { label: 'English', value: 'en' }]" value-attribute="value" option-attribute="label" size="sm" class="ml-auto w-24" />
    </div>
    
    <div class="lg:overflow-hidden lg:h-[calc(100vh-40px)] flex-1 flex flex-col lg:flex-row">
      <!-- Sidebar (Left) - Hidden on Mobile -->
      <div v-show="showDesktopSidebar" class="hidden lg:flex w-[260px] bg-gray-50 dark:bg-gray-950 border-r border-gray-200 dark:border-gray-800 flex-col p-4 overflow-y-auto transition-all">
        <SidebarContent 
          v-model:code="code"
          v-model:triggerStep="triggerStep"
          v-model:biStrict="biStrict"
          :loading="loading"
          mode="desktop"
          @analyze="analyze(true)"
          @toggle="showDesktopSidebar = false"
        />
      </div>

      <!-- Main Content (Right) -->
      <div class="p-0 flex flex-col lg:overflow-hidden bg-white dark:bg-gray-900 flex-1 relative">
        <!-- Floating Toggle Button for Desktop -->
        <div 
           v-if="!showDesktopSidebar"
           class="hidden lg:flex absolute left-0 top-1/2 -translate-y-1/2 z-50 bg-white dark:bg-gray-800 border border-l-0 border-gray-200 dark:border-gray-700 rounded-r shadow-md cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 items-center justify-center w-6 h-12 transition-colors"
           @click="showDesktopSidebar = true"
           :title="t('sidebar.expandSidebar')"
        >
            <svg width="16" height="16" viewBox="0 0 24 24" class="text-gray-500 dark:text-gray-400"><path :d="MemoryChevronRight" /></svg>
        </div>

        <!-- Mobile Sidebar (Top) -->
        <div
          class="lg:hidden bg-gray-50 dark:bg-gray-950 border-b border-gray-200 dark:border-gray-800 flex-shrink-0 overflow-hidden"
          :class="mobileSidebarCollapsed ? 'h-12 px-4' : 'p-4'"
        >
          <SidebarContent 
            v-model:code="code"
            v-model:triggerStep="triggerStep"
            v-model:biStrict="biStrict"
            :loading="loading"
            :collapsed="mobileSidebarCollapsed"
            mode="mobile"
            @analyze="analyze(true); mobileSidebarCollapsed = true"
            @toggle="mobileSidebarCollapsed = !mobileSidebarCollapsed"
          />
        </div>

        <!-- Mobile Tabs (Visible only on mobile) -->
        <div class="lg:hidden flex border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 overflow-x-auto shrink-0">
           <button 
              v-for="tab in tabs" 
              :key="tab.name" 
              @click="activeTab = tab.name"
              class="flex-1 py-3 flex justify-center items-center gap-2 text-sm transition-colors border-b-2"
              :class="activeTab === tab.name ? 'bg-white dark:bg-gray-900 text-blue-600 border-blue-600 font-medium' : 'text-gray-600 dark:text-gray-400 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'"
           >
              <svg width="18" height="18" viewBox="0 0 24 24"><path :d="tab.icon" /></svg>
              <span>{{ tab.label }}</span>
           </button>
        </div>

        <!-- Content Area -->
        <div class="flex-1 overflow-hidden relative">
          <!-- Loading Overlay -->
          <div v-if="loading && mobileSidebarCollapsed" class="absolute inset-0 bg-white/70 dark:bg-gray-900/70 z-50 flex items-center justify-center">
            <div class="flex flex-col items-center">
              <UIcon name="i-heroicons-arrow-path" class="animate-spin w-8 h-8 text-blue-500" />
              <span class="mt-2 text-sm text-gray-500">正在分析...</span>
            </div>
          </div>
          
          <!-- Analysis Tab -->
          <div v-show="activeTab === 'analysis'" class="h-full w-full flex flex-col lg:overflow-hidden">
             <!-- Tab Content Container with flex-1 to fill space -->
             <div class="lg:h-full lg:overflow-y-auto p-2 box-border flex flex-col flex-1">
                <div v-if="hasRunAnalysis" class="flex gap-2 flex-col lg:flex-row lg:h-full lg:overflow-hidden flex-1">
                  <!-- Chart Column (75%) -->
                  <div class="flex-none lg:flex-[3] flex flex-col min-w-0 h-[500px] lg:h-full lg:overflow-hidden min-h-[400px]">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0', base: 'flex-1 h-full min-h-0' }, header: { padding: 'p-3 sm:p-3' } }" class="flex-1 flex flex-col h-full box-border shadow-none border border-gray-200 dark:border-gray-800">
                      <template #header>
                        <div class="flex justify-between items-center">
                          <h3 class="text-base font-bold text-gray-700 dark:text-gray-200 m-0 truncate pr-2">{{ t('app.chart') }}: {{ stockName ? `${stockName} (${code})` : code }}</h3>
                          <UBadge color="gray" variant="solid" size="xs">{{ latestDate }}</UBadge>
                        </div>
                      </template>
                      <div class="flex-1 relative min-h-0 overflow-hidden h-full">
                         <ChanChart ref="chartRef" />
                      </div>
                    </UCard>
                  </div>
                  
                  <!-- Info Column (25%) -->
                  <div class="flex-none lg:flex-1 min-w-0 lg:min-w-[300px] h-auto lg:h-full flex-shrink-0">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0' } }" class="h-full shadow-none border border-gray-200 dark:border-gray-800 overflow-y-auto">
                      <div class="h-full">
                        <AnalysisPanel 
                          :latestClose="latestClose"
                          :latestDate="latestDate"
                          :signal="signal"
                          :accuracy="accuracy"
                          frequency="1d"
                          :loading="loading"
                        />
                      </div>
                    </UCard>
                  </div>
                </div>
                
                <div v-else class="h-full flex justify-center items-center">
                  <div class="max-w-2xl w-full flex flex-col gap-8">
                    
                    <UCard class="about-section">
                      <template #header>
                        <div class="font-medium text-lg text-gray-800 dark:text-white">{{ t('app.about') }}</div>
                      </template>
                      <p class="text-gray-600 dark:text-gray-300 leading-relaxed mb-4" v-html="t('app.aboutContent')"></p>
                      <ul class="list-disc pl-6 text-gray-600 dark:text-gray-300 mb-4">
                        <li class="mb-2" v-html="t('app.aboutChan')"></li>
                        <li v-html="t('app.aboutML')"></li>
                      </ul>
                      <p class="text-sm text-gray-500 italic mt-6 pt-4 border-t border-gray-100 dark:border-gray-800">{{ t('app.disclaimer') }}</p>
                    </UCard>
                  </div>
                </div>
             </div>
          </div>
          
          <!-- Intraday Tab -->
          <div v-show="activeTab === 'intraday'" class="h-full w-full flex flex-col lg:overflow-hidden relative pb-16 lg:pb-0">
             <div class="lg:h-full lg:overflow-y-auto p-2 box-border flex flex-col flex-1">
                <!-- Frequency Selector -->
               <div class="mb-2 flex flex-col gap-2 bg-gray-50 dark:bg-gray-900 p-2 rounded border border-gray-100 dark:border-gray-800 flex-shrink-0">
                  <div class="flex flex-col gap-2 lg:flex-row lg:items-center lg:gap-4">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-sm font-bold text-gray-700 dark:text-gray-300">{{ t('app.dataSrc') }}</span>
                      <div class="flex gap-1">
                        <UButton
                          v-for="src in ['clickhouse', 'baostock', 'akshare']"
                          :key="src"
                          :label="t('app.dataSrcOptions.' + src)"
                          size="xs"
                          :color="dataSrc === src ? 'primary' : 'gray'"
                          :variant="dataSrc === src ? 'solid' : 'ghost'"
                          @click="dataSrc = src"
                        />
                      </div>
                    </div>

                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-sm font-bold text-gray-700 dark:text-gray-300">{{ t('app.modelSelect') }}</span>
                      <div class="flex gap-1">
                        <UButton
                          v-for="m in ['xgboost', 'lightgbm', 'mlp']"
                          :key="m"
                          :label="t('app.modelOptions.' + m)"
                          size="xs"
                          :color="model === m ? 'primary' : 'gray'"
                          :variant="model === m ? 'solid' : 'ghost'"
                          @click="model = m"
                        />
                      </div>
                    </div>

                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-sm font-bold text-gray-700 dark:text-gray-300">{{ t('app.dataLength') }}</span>
                      <div class="w-44 xl:w-60 max-w-full px-1 flex items-center gap-1">
                        <URange
                          class="flex-1"
                          v-model="dataLengthYears"
                          :min="dataLengthMin"
                          :max="dataLengthMax"
                          :step="dataLengthStep"
                          size="sm"
                        />
                        <span class="text-xs text-gray-500 w-4 m-1 xl:m-2 text-right tabular-nums">{{ dataLengthYearsDisplay }}</span>
                        <span class="hidden xl:inline text-xs text-gray-400 whitespace-nowrap">(Max: {{ dataLengthMax }}y)</span>
                      </div>
                    </div>
                  </div>

                  <div class="flex flex-col gap-2 lg:flex-row lg:items-center lg:gap-3">
                    <div class="flex items-center gap-2 flex-wrap">
                      <span class="text-sm font-bold text-gray-700 dark:text-gray-300">{{ t('app.periodSelect') }}</span>
                      <div class="flex gap-1 flex-wrap">
                        <UButton
                          v-for="period in ['1m', '5m', '15m', '30m', '60m', '1d', '1w', '1mo']"
                          :key="period"
                          :label="t('periods.' + period)"
                          size="xs"
                          :color="intradayFreq === period ? 'primary' : 'gray'"
                          :variant="intradayFreq === period ? 'solid' : 'ghost'"
                          :disabled="period === '1m' && ['baostock', 'akshare'].includes(dataSrc)"
                          @click="intradayFreq = period"
                        />
                      </div>
                    </div>

                    <div class="hidden lg:flex ml-auto gap-2">
                      <UButton color="gray" size="sm" :loading="loading" @click="analyze(false)">{{ t('app.refresh') }}</UButton>
                      <UButton color="primary" size="sm" :loading="loading" @click="analyze(true)" :disabled="!hasRunIntraday">{{ t('app.predict') }}</UButton>
                    </div>
                  </div>

                  <!-- Mobile Buttons -->
                  <div class="lg:hidden grid grid-cols-1 sm:grid-cols-2 gap-2 w-full mt-2">
                      <UButton block color="gray" :loading="loading" @click="analyze(false)">{{ t('app.refresh') }}</UButton>
                      <UButton block color="primary" :loading="loading" @click="analyze(true)" :disabled="!hasRunIntraday">{{ t('app.predict') }}</UButton>
                  </div>
               </div>
               
               <!-- Chart & Info -->
                <div v-if="hasRunIntraday" class="flex gap-2 flex-col lg:flex-row lg:flex-1 lg:overflow-hidden flex-1">
                  <!-- Chart Column (75%) -->
                  <div class="flex-none lg:flex-[3] flex flex-col min-w-0 h-[500px] lg:h-full lg:overflow-hidden min-h-[400px]">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0', base: 'flex-1 h-full min-h-0' }, header: { padding: 'p-3 sm:p-3' } }" class="flex-1 flex flex-col h-full box-border shadow-none border border-gray-200 dark:border-gray-800">
                      <template #header>
                        <div class="flex justify-between items-center">
                          <h3 class="text-base font-bold text-gray-700 dark:text-gray-200 m-0 truncate pr-2">{{ t('app.intradayChart') }}: {{ stockName ? (stockName + ' (' + code + ')') : code }} - {{ intradayFreq }} - {{ model }}</h3>
                          <UBadge color="gray" variant="solid" size="xs">{{ intradayLatestDate }}</UBadge>
                        </div>
                      </template>
                      <div class="flex-1 relative min-h-0 overflow-hidden h-full">
                         <ChanChart ref="intradayChartRef"></ChanChart>
                      </div>
                    </UCard>
                  </div>
                  
                  <!-- Info Column (25%) -->
                  <div class="flex-none lg:flex-1 min-w-0 lg:min-w-[300px] h-auto lg:h-full flex-shrink-0">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0' } }" class="h-full shadow-none border border-gray-200 dark:border-gray-800 overflow-y-auto">
                      <div class="h-full">
                        <AnalysisPanel 
                          :latestClose="intradayLatestClose"
                          :latestDate="intradayLatestDate"
                          :signal="intradaySignal"
                          :accuracy="intradayAccuracy"
                          :frequency="intradayFreq"
                          :loading="loading"
                        />
                      </div>
                    </UCard>
                  </div>
                </div>
                
                <div v-else class="flex-1 flex justify-center items-center text-gray-400">
                    {{ t('app.waitingForData') }}
                </div>
             </div>
          </div>
          
          <!-- History Tab -->
          <div v-show="activeTab === 'history'" class="h-full w-full flex flex-col lg:overflow-hidden">
             <div class="lg:h-full lg:overflow-hidden p-2 box-border bg-white dark:bg-gray-900 flex-1">
                <HistoryPanel @view="handleViewHistory" />
             </div>
          </div>
          
          <!-- Help Tab -->
          <div v-show="activeTab === 'help'" class="h-full w-full overflow-y-auto">
             <div class="p-5">
               <HelpPanel />
             </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, nextTick, computed } from 'vue'
import axios from 'axios'
import { MemoryJournal, MemoryChartBar, MemoryBook, MemoryClock, MemoryChevronRight } from '@pictogrammers/memory'
import { useI18n } from './composables/useI18n'

const { currentLang: lang, t } = useI18n()
const toast = useToast()

const tabs = [
  { name: 'analysis', label: t('app.stockAnalysis'), icon: MemoryChartBar },
  { name: 'intraday', label: t('app.intradayAnalysis'), icon: MemoryClock },
  { name: 'history', label: t('app.history'), icon: MemoryBook },
  { name: 'help', label: t('app.guide'), icon: MemoryJournal }
]

const code = ref('002701')
const mobileSidebarCollapsed = ref(false)
const showDesktopSidebar = ref(true)
const triggerStep = ref(true)
const biStrict = ref(false)
const loading = ref(false)
const activeTab = ref('analysis')
const hasRunAnalysis = ref(false)
const hasRunIntraday = ref(false)
const intradayFreq = ref('30m')
const dataSrc = ref('clickhouse')
const dataLengthYears = ref(0.5)
const model = ref('xgboost')

const signal = ref(null)
const accuracy = ref(null)
const latestClose = ref('--')
const latestDate = ref('--')
const stockName = ref('')
const chartRef = ref(null)

const intradaySignal = ref(null)
const intradayAccuracy = ref(null)
const intradayLatestClose = ref('--')
const intradayLatestDate = ref('--')
const intradayChartRef = ref(null)

const isInitialized = ref(false)

const dataLengthMin = ref(0.1)
const dataLengthMax = ref(10)
const dataLengthStep = ref(0.1)
const dataLengthYearsDisplay = computed(() => Number(dataLengthYears.value).toFixed(1))

const normalizeToStep = (value, min, max, step) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return min
  const clamped = Math.min(max, Math.max(min, value))
  const steps = Math.round((clamped - min) / step)
  const snapped = min + steps * step
  const rounded = Math.round(snapped * 1000) / 1000
  return Math.min(max, Math.max(min, rounded))
}

if (typeof window !== 'undefined') {
  const savedDataLengthYears = localStorage.getItem('lastDataLengthYears')
  if (savedDataLengthYears) {
    dataLengthYears.value = parseFloat(savedDataLengthYears)
  }
}

// Update slider constraints based on frequency
watch(intradayFreq, (newFreq) => {
  if (newFreq === '1m') {
    dataLengthMin.value = 0.1
    dataLengthMax.value = 1.0
    dataLengthStep.value = 0.1
    if (dataLengthYears.value > 1.0) dataLengthYears.value = 1.0
    if (dataLengthYears.value < 0.1) dataLengthYears.value = 0.1
  } else if (newFreq === '5m') {
    dataLengthMin.value = 0.1
    dataLengthMax.value = 2.0
    dataLengthStep.value = 0.1
    if (dataLengthYears.value > 2.0) dataLengthYears.value = 2.0
    if (dataLengthYears.value < 0.1) dataLengthYears.value = 0.1
  } else if (['15m', '30m', '60m'].includes(newFreq)) {
    dataLengthMin.value = 0.1
    dataLengthMax.value = 5.0
    dataLengthStep.value = 0.1
    if (dataLengthYears.value > 5.0) dataLengthYears.value = 5.0
    if (dataLengthYears.value < 0.1) dataLengthYears.value = 0.1
  } else {
    // 1d, 1w, 1mo
    dataLengthMin.value = 0.1
    dataLengthMax.value = 8.0
    dataLengthStep.value = 0.1
  }

  dataLengthYears.value = normalizeToStep(
    dataLengthYears.value,
    dataLengthMin.value,
    dataLengthMax.value,
    dataLengthStep.value
  )
}, { immediate: true })

// Restore settings from localStorage
onMounted(() => {
  const savedCode = localStorage.getItem('lastStockCode')
  if (savedCode) {
    code.value = savedCode
  }
  
  const savedTab = localStorage.getItem('lastActiveTab')
  if (savedTab) {
    activeTab.value = savedTab
  }

  const savedLang = localStorage.getItem('lastLang')
  if (savedLang) {
    lang.value = savedLang
  }

  const savedModel = localStorage.getItem('lastModel')
  if (savedModel) {
    model.value = savedModel
  }

  const savedFreq = localStorage.getItem('lastIntradayFreq')
  if (savedFreq) {
    intradayFreq.value = savedFreq
  }

  const savedDataSrc = localStorage.getItem('lastDataSrc')
  if (savedDataSrc) {
    dataSrc.value = savedDataSrc
  }

  const savedTriggerStep = localStorage.getItem('lastTriggerStep')
  if (savedTriggerStep) {
    triggerStep.value = savedTriggerStep === 'true'
  }

  const savedBiStrict = localStorage.getItem('lastBiStrict')
  if (savedBiStrict) {
    biStrict.value = savedBiStrict === 'true'
  }

  nextTick(() => {
    isInitialized.value = true
  })
})

// Watch changes and save to localStorage
watch(code, (newVal) => {
  if (!isInitialized.value) return
  if (newVal) {
    localStorage.setItem('lastStockCode', newVal)
  }
})

watch(activeTab, (newVal) => {
  if (!isInitialized.value) return
  if (newVal) localStorage.setItem('lastActiveTab', newVal)
})

watch(lang, (newVal) => {
  if (!isInitialized.value) return
  if (newVal) localStorage.setItem('lastLang', newVal)
})

watch(model, (newVal) => {
  if (!isInitialized.value) return
  if (newVal) {
    localStorage.setItem('lastModel', newVal)
  }
})

watch(intradayFreq, (newVal) => {
  if (!isInitialized.value) return
  if (newVal) {
    localStorage.setItem('lastIntradayFreq', newVal)
  }
})

watch(dataSrc, (newVal) => {
  if (!isInitialized.value) return
  if (newVal) {
    localStorage.setItem('lastDataSrc', newVal)
  }
})

watch(dataLengthYears, (newVal) => {
  if (!isInitialized.value) return
  if (newVal) {
    localStorage.setItem('lastDataLengthYears', newVal)
  }
})

watch(triggerStep, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('lastTriggerStep', newVal)
})

watch(biStrict, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('lastBiStrict', newVal)
})

// API Calls
const analyze = async (isPrediction = false, silent = false) => {
  // Determine if we need a two-step process (Chart first, then Prediction)
  // Only for Analysis Tab when prediction is requested
  const isAnalysisTab = activeTab.value === 'analysis'
  const separatePrediction = isAnalysisTab && isPrediction

  loading.value = true
  
  try {
    const isIntraday = activeTab.value === 'intraday'
    const frequency = isIntraday ? intradayFreq.value : '1d'
    
    // First pass: 
    // If separatePrediction is true, force include_prediction to false for the first request
    const firstPassPrediction = separatePrediction ? false : isPrediction

    // Construct API params
    const params = {
      code: code.value,
      frequency: frequency,
      do_predict: firstPassPrediction,
      step_calc: triggerStep.value,
      bi_strict: biStrict.value,
      data_source: isIntraday ? dataSrc.value : undefined, // Only send data_source for intraday for now, or as needed
      model: model.value,
      data_length_years: isIntraday ? dataLengthYears.value : undefined
    }

    // Call API (Adjust endpoint as necessary)
    // Assuming the original logic used a relative path /api/analyze or similar
    // I need to check where the original axios calls went.
    // Based on vite config, it was proxying /api to localhost:8001
    
    const response = await axios.post('/api/analyze', params)
    
    if (response.data.status === 'success') {
      const res = response.data
      const data = res.data
      stockName.value = res.stock_name || ''
      
      if (isIntraday) {
        intradaySignal.value = res.signal
        intradayAccuracy.value = res.accuracy
        intradayLatestClose.value = data.latest_close
        intradayLatestDate.value = data.latest_date

        hasRunIntraday.value = true
        await nextTick()

        if (intradayChartRef.value) {
          intradayChartRef.value.updateChart(data)
        }
      } else {
        signal.value = res.signal
        accuracy.value = res.accuracy
        latestClose.value = data.latest_close
        latestDate.value = data.latest_date

        hasRunAnalysis.value = true
        await nextTick()

        if (chartRef.value) {
          chartRef.value.updateChart(data)
        }
      }
    } else {
      let errorMsg = response.data.message || 'Analysis failed'
      if (response.data.status === 'error' && response.data.detail) {
          errorMsg = response.data.detail
      }
      if (!silent) toast.add({ title: t('app.error'), description: errorMsg, color: 'red' })
      loading.value = false
      return
    }
  } catch (e) {
    console.error(e)
    let errorMsg = e.message || 'Network error'
    
    // Check for response details
    if (e.response && e.response.data && e.response.data.detail) {
        errorMsg = e.response.data.detail
    }
    
    // Customize for 404
    if (e.response && e.response.status === 404) {
        errorMsg = t('app.stockNotFound')
    }

    if (!silent) toast.add({ title: t('app.error'), description: errorMsg, color: 'red' })
    loading.value = false
    return
  }

  // First pass done. Turn off loading.
  loading.value = false

  // Second pass: Background Prediction if needed
   if (separatePrediction) {
       try {
           const params = {
               code: code.value,
               frequency: '1d',
               do_predict: true,
               step_calc: triggerStep.value,
               bi_strict: biStrict.value,
               model: model.value
           }
          
          const response = await axios.post('/api/analyze', params)
          
          if (response.data.status === 'success') {
              const res = response.data
              // Update only signal and accuracy for Analysis Tab
              signal.value = res.signal
              accuracy.value = res.accuracy
          }
      } catch (e) {
          console.error("Background prediction failed", e)
      }
  }
}

const handleViewHistory = async (item) => {
  loading.value = true
  try {
    const response = await axios.get(`/api/history/${item.id}`)
    const res = response.data
    if (!res || res.status !== 'success') {
      const errorMsg = res?.detail || res?.message || 'Analysis failed'
      toast.add({ title: t('app.error'), description: errorMsg, color: 'red' })
      return
    }

    const isIntraday = item.frequency && item.frequency !== '1d'

    code.value = item.code
    stockName.value = res.stock_name || ''
    model.value = item.model || model.value

    if (isIntraday) {
      activeTab.value = 'intraday'
      intradayFreq.value = item.frequency
    } else {
      activeTab.value = 'analysis'
    }

    await nextTick()

    const data = res.data
    if (isIntraday) {
      intradaySignal.value = res.signal
      intradayAccuracy.value = res.accuracy
      intradayLatestClose.value = data.latest_close
      intradayLatestDate.value = data.latest_date
      hasRunIntraday.value = true

      await nextTick()
      if (intradayChartRef.value) {
        intradayChartRef.value.updateChart(data)
      }
    } else {
      signal.value = res.signal
      accuracy.value = res.accuracy
      latestClose.value = data.latest_close
      latestDate.value = data.latest_date
      hasRunAnalysis.value = true

      await nextTick()
      if (chartRef.value) {
        chartRef.value.updateChart(data)
      }
    }
  } catch (e) {
    console.error(e)
    let errorMsg = e.message || 'Network error'
    if (e.response && e.response.data && e.response.data.detail) {
      errorMsg = e.response.data.detail
    }
    toast.add({ title: t('app.error'), description: errorMsg, color: 'red' })
  } finally {
    loading.value = false
  }
}
</script>

<style lang="postcss">
/* Add any global styles or overrides here */
body {
  @apply bg-white dark:bg-gray-900;
}
</style>
