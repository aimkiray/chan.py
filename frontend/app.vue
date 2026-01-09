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
          v-model:autype="autype"
          v-model:triggerStep="triggerStep"
          v-model:biStrict="biStrict"
          v-model:enableRollingLookback="enableRollingLookback"
          v-model:blend="dailyBlendModels"
          v-model:dataSrc="dataSrc"
          v-model:model="model"
          v-model:intradayFreq="intradayFreq"
          v-model:dataLengthYears="dataLengthYears"
          v-model:pretrainedChoice="intradayPretrainedChoice"
          v-model:intradayBlend="intradayBlendModels"
          v-model:strategyForm="strategyForm"
          v-model:strategyChanConfig="strategyChanConfig"
          v-model:pretrainConfig="pretrainConfig"
          :activeTab="activeTab"
          :loading="loading"
          :dataLengthMin="dataLengthMin"
          :dataLengthMax="dataLengthMax"
          :dataLengthStep="dataLengthStep"
          :pretrainedOptions="intradayPretrainedOptions"
          :pretrainedLoading="intradayPretrainedLoading"
          mode="desktop"
          @analyze="analyze(true)"
          @runStrategy="strategyPanelRef.runStrategy()"
          @runPretrain="pretrainPanelRef.runPretrain()"
          @refresh="analyze(false, false, false)"
            @toggle="handleManualClose"
            @refreshPretrained="fetchIntradayPretrainedModels"
            @searchHistory="handleSearchHistory"
          />
        </div>

      <!-- Main Content (Right) -->
      <div class="p-0 flex flex-col lg:overflow-hidden bg-white dark:bg-gray-900 flex-1 relative">
        <!-- Floating Toggle Button for Desktop -->
        <div 
           v-if="!showDesktopSidebar"
           class="hidden lg:flex absolute left-0 top-1/2 -translate-y-1/2 z-50 bg-white dark:bg-gray-800 border border-l-0 border-gray-200 dark:border-gray-700 rounded-r shadow-md cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-700 items-center justify-center w-6 h-12 transition-colors"
           @click="handleManualOpen"
           :title="t('sidebar.expandSidebar')"
        >
            <svg width="16" height="16" viewBox="0 0 24 24" class="text-gray-500 dark:text-gray-400"><path :d="MemoryChevronRight" /></svg>
        </div>

        <!-- Mobile Tabs (Visible only on mobile) -->
        <div class="lg:hidden relative border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 shrink-0">
          <div
            ref="mobileTabsEl"
            class="flex items-stretch gap-1 overflow-x-auto overflow-y-hidden whitespace-nowrap px-2 snap-x snap-mandatory scroll-px-2 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
            style="-webkit-overflow-scrolling: touch;"
            @scroll="updateMobileTabsHint"
          >
            <button 
              v-for="tab in tabs" 
              :key="tab.name" 
              @click="activeTab = tab.name"
              class="shrink-0 px-3 h-11 flex items-center gap-2 text-sm transition-colors border-b-2 snap-start"
              :class="activeTab === tab.name ? 'bg-white dark:bg-gray-900 text-blue-600 border-blue-600 font-medium' : 'text-gray-600 dark:text-gray-400 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800'"
            >
              <svg width="18" height="18" viewBox="0 0 24 24"><path :d="tab.icon" /></svg>
              <span class="whitespace-nowrap">{{ tab.label }}</span>
            </button>
          </div>

          <div v-show="mobileTabsCanScrollLeft" class="pointer-events-none absolute left-0 top-0 bottom-0 w-10 bg-gradient-to-r from-gray-50 dark:from-gray-950 to-transparent flex items-center justify-start pl-1">
            <UIcon name="i-heroicons-chevron-left" class="w-4 h-4 text-gray-400" />
          </div>
          <div v-show="mobileTabsCanScrollRight" class="pointer-events-none absolute right-0 top-0 bottom-0 w-10 bg-gradient-to-l from-gray-50 dark:from-gray-950 to-transparent flex items-center justify-end pr-1">
            <UIcon name="i-heroicons-chevron-right" class="w-4 h-4 text-gray-400" />
          </div>
        </div>

        <!-- Mobile Sidebar (Top) -->
        <div
          class="lg:hidden bg-gray-50 dark:bg-gray-950 border-b border-gray-200 dark:border-gray-800 flex-shrink-0 overflow-hidden"
          :class="mobileSidebarCollapsed ? 'h-12 px-4' : 'p-4'"
        >
          <SidebarContent 
            v-model:code="code"
            v-model:autype="autype"
            v-model:triggerStep="triggerStep"
            v-model:biStrict="biStrict"
            v-model:enableRollingLookback="enableRollingLookback"
            v-model:blend="dailyBlendModels"
            v-model:dataSrc="dataSrc"
            v-model:model="model"
            v-model:intradayFreq="intradayFreq"
            v-model:dataLengthYears="dataLengthYears"
            v-model:pretrainedChoice="intradayPretrainedChoice"
            v-model:intradayBlend="intradayBlendModels"
            v-model:strategyForm="strategyForm"
            v-model:strategyChanConfig="strategyChanConfig"
            v-model:pretrainConfig="pretrainConfig"
            :activeTab="activeTab"
            :loading="loading"
            :dataLengthMin="dataLengthMin"
            :dataLengthMax="dataLengthMax"
            :dataLengthStep="dataLengthStep"
            :pretrainedOptions="intradayPretrainedOptions"
            :pretrainedLoading="intradayPretrainedLoading"
            :collapsed="mobileSidebarCollapsed"
            mode="mobile"
            @analyze="analyze(true); mobileSidebarCollapsed = true"
            @runStrategy="strategyPanelRef.runStrategy()"
            @runPretrain="pretrainPanelRef.runPretrain()"
            @refresh="analyze(false, false, false); mobileSidebarCollapsed = true"
            @toggle="mobileSidebarCollapsed = !mobileSidebarCollapsed"
            @refreshPretrained="fetchIntradayPretrainedModels"
          />
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
                  <!-- Chart Column (66%) -->
                  <div class="flex-none lg:flex-[2] flex flex-col min-w-0 h-[500px] lg:h-full lg:overflow-hidden min-h-[400px]">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0', base: 'flex-1 h-full min-h-0' }, header: { padding: 'p-3 sm:p-3' } }" class="flex-1 flex flex-col h-full box-border shadow-none border border-gray-200 dark:border-gray-800">
                      <template #header>
                        <div class="flex justify-between items-center">
                          <h3 class="text-base font-bold text-gray-700 dark:text-gray-200 m-0 truncate pr-2">{{ t('app.chart') }}: {{ stockName ? `${stockName} (${code})` : code }}</h3>
                          <UBadge color="gray" variant="solid" size="xs">{{ latestDate }}</UBadge>
                        </div>
                      </template>
                      <div class="flex-1 relative min-h-0 overflow-hidden h-full">
                         <ChanChart ref="chartRef" :loading="loading" />
                      </div>
                    </UCard>
                  </div>
                  
                  <!-- Info Column (33%) -->
                  <div class="flex-none lg:flex-1 min-w-0 lg:min-w-[300px] h-auto lg:h-full flex-shrink-0">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0' } }" class="h-full shadow-none border border-gray-200 dark:border-gray-800 overflow-y-auto">
                      <div class="h-full">
                        <AnalysisPanel 
                          :latestClose="latestClose"
                          :latestDate="latestDate"
                          :signal="signal"
                          :accuracy="accuracy"
                          frequency="1d"
                          :showCalibrationBins="false"
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
               <!-- Config moved to sidebar -->
               
               <!-- Chart & Info -->
                <div v-if="hasRunIntraday" class="flex gap-2 flex-col lg:flex-row lg:flex-1 lg:overflow-hidden flex-1">
                  <!-- Chart Column (66%) -->
                  <div class="flex-none lg:flex-[2] flex flex-col min-w-0 h-[500px] lg:h-full lg:overflow-hidden min-h-[400px]">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0', base: 'flex-1 h-full min-h-0' }, header: { padding: 'p-3 sm:p-3' } }" class="flex-1 flex flex-col h-full box-border shadow-none border border-gray-200 dark:border-gray-800">
                      <template #header>
                        <div class="flex justify-between items-center">
                          <h3 class="text-base font-bold text-gray-700 dark:text-gray-200 m-0 truncate pr-2">{{ t('app.intradayChart') }}: {{ stockName ? (stockName + ' (' + code + ')') : code }} - {{ intradayFreq }} - {{ model }}</h3>
                          <UBadge color="gray" variant="solid" size="xs">{{ intradayLatestDate }}</UBadge>
                        </div>
                      </template>
                      <div class="flex-1 relative min-h-0 overflow-hidden h-full">
                         <ChanChart ref="intradayChartRef" :loading="loading"></ChanChart>
                      </div>
                    </UCard>
                  </div>
                  
                  <!-- Info Column (33%) -->
                  <div class="flex-none lg:flex-1 min-w-0 lg:min-w-[300px] h-auto lg:h-full flex-shrink-0">
                    <UCard :ui="{ body: { padding: 'p-0 sm:p-0' } }" class="h-full shadow-none border border-gray-200 dark:border-gray-800 overflow-y-auto">
                      <div class="h-full">
                        <AnalysisPanel 
                          :latestClose="intradayLatestClose"
                          :latestDate="intradayLatestDate"
                          :signal="intradaySignal"
                          :accuracy="intradayAccuracy"
                          :frequency="intradayFreq"
                          :showCalibrationBins="true"
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
          
          <!-- Pretrain Tab -->
          <div v-show="activeTab === 'pretrain'" class="h-full w-full flex flex-col lg:overflow-hidden">
             <div class="lg:h-full lg:overflow-hidden p-2 box-border bg-white dark:bg-gray-900 flex-1">
                <PretrainPanel ref="pretrainPanelRef" :config="pretrainConfig" :defaultDataSrc="dataSrc" :defaultModel="model" />
             </div>
          </div>

          <!-- Strategy Tab -->
          <div v-show="activeTab === 'strategy'" class="h-full w-full flex flex-col lg:overflow-hidden">
             <div class="lg:h-full lg:overflow-hidden p-2 box-border bg-white dark:bg-gray-900 flex-1">
                <StrategyPanel 
                  ref="strategyPanelRef" 
                  :form="strategyForm"
                  :chanConfig="strategyChanConfig"
                  @view-stock="handleViewStockFromStrategy" 
                />
             </div>
          </div>

          <!-- History Tab -->
          <div v-show="activeTab === 'history'" class="h-full w-full flex flex-col lg:overflow-hidden">
             <div class="lg:h-full lg:overflow-hidden p-2 box-border bg-white dark:bg-gray-900 flex-1">
                <HistoryPanel ref="historyPanelRef" @view="handleViewHistory" />
             </div>
          </div>
          
          <!-- Help Tab -->
          <div id="help-container" v-show="activeTab === 'help'" class="h-full w-full overflow-y-auto">
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
import { ref, watch, onMounted, onBeforeUnmount, nextTick, computed } from 'vue'
import axios from 'axios'
import { MemoryJournal, MemoryChartBar, MemoryBook, MemoryClock, MemoryChevronRight, MemoryFilter } from '@pictogrammers/memory'
import { useI18n } from './composables/useI18n'

const { currentLang: lang, t, formatTime } = useI18n()
const toast = useToast()

const tabs = computed(() => [
  { name: 'analysis', label: t('app.stockAnalysis'), icon: MemoryChartBar },
  { name: 'intraday', label: t('app.intradayAnalysis'), icon: MemoryClock },
  { name: 'pretrain', label: t('app.pretrain'), icon: MemoryChartBar },
  { name: 'strategy', label: t('app.strategy'), icon: MemoryFilter },
  { name: 'history', label: t('app.history'), icon: MemoryBook },
  { name: 'help', label: t('app.guide'), icon: MemoryJournal }
])

const code = ref('002701')
const mobileTabsEl = ref(null)
const mobileTabsCanScrollLeft = ref(false)
const mobileTabsCanScrollRight = ref(false)
const mobileSidebarCollapsed = ref(false)
const showDesktopSidebar = ref(true)
const activeTab = ref('analysis')
const userManuallyHidden = ref(false)

const updateMobileTabsHint = () => {
  const el = mobileTabsEl.value
  if (!el) {
    mobileTabsCanScrollLeft.value = false
    mobileTabsCanScrollRight.value = false
    return
  }
  const maxScrollLeft = Math.max(0, el.scrollWidth - el.clientWidth)
  const left = el.scrollLeft
  mobileTabsCanScrollLeft.value = left > 2
  mobileTabsCanScrollRight.value = left < (maxScrollLeft - 2)
}

const handleManualClose = () => {
  showDesktopSidebar.value = false
  // If we are on a "Show" page, mark as manually hidden
  if (!['pretrain', 'history', 'help'].includes(activeTab.value)) {
    userManuallyHidden.value = true
  }
}

const handleManualOpen = () => {
  showDesktopSidebar.value = true
  userManuallyHidden.value = false
}

const triggerStep = ref(true)
const biStrict = ref(false)
const enableRollingLookback = ref(true)
const dailyBlendModels = ref(false)
const autype = ref('hfq')
const loading = ref(false)
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

const pretrainPanelRef = ref(null)
const strategyPanelRef = ref(null)
const historyPanelRef = ref(null)
const strategyForm = ref({
  strategy_name: `Strategy-${new Date().getTime()}`,
  pool_id: '',
  model: 'xgboost',
  min_accuracy: 0.8,
  min_recent_accuracy: null,
  recent_accuracy_years: 1.0,
  min_signal_score: null,
  min_bsp_count: 0,
  min_test_count: 0,
  profit_threshold: 0.01,
  auto_profit_quantile: 0.7,
  profit_lookahead: 5,
  frequency: '1d',
  data_length_years: 1.0,
  enable_rolling_lookback: true
})
const strategyChanConfig = ref({
  bi_strict: true,
  bsp2_follow_1: false,
  bsp3_follow_1: false,
  gap_as_kl: false,
  bi_allow_sub_peak: false,
  macd_algo: 'peak',
  min_zs_cnt: 0,
  require_signal: false,
  signal_lookback: 5,
  signal_direction: 'buy',
  bs_type: '1,2,3a,1p,2s,3b'
})

const pretrainConfig = ref({
  selectedPoolId: '',
  poolName: '',
  overwriteCodes: true,
  codesText: '',
  frequency: '1d',
  model: 'xgboost',
  dataSrc: 'clickhouse',
  dataLengthMode: 'years',
  dataLengthYears: 5.0,
  forceRefresh: false
})

const intradaySignal = ref(null)
const intradayAccuracy = ref(null)
const intradayLatestClose = ref('--')
const intradayLatestDate = ref('--')
const intradayChartRef = ref(null)

const intradayPretrainedChoice = ref('auto')
const dailyPretrainedChoice = ref('auto')
const intradayBlendModels = ref(true)
const intradayPretrainedLoading = ref(false)
const intradayPretrainedModels = ref([])
let intradayPretrainedFetchedAt = 0

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
  const savedAutype = localStorage.getItem('lastAutype')
  if (savedAutype) {
    const raw = String(savedAutype).trim().toLowerCase()
    const nextAutype = raw
    autype.value = ['hfq', 'qfq', 'none'].includes(nextAutype) ? nextAutype : 'hfq'
    if (autype.value !== raw) {
      localStorage.setItem('lastAutype', autype.value)
    }
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
  } else if (['1w', '1mo'].includes(newFreq)) {
    dataLengthMin.value = 0.1
    dataLengthMax.value = 20.0
    dataLengthStep.value = 0.1
  } else {
    // 1d
    dataLengthMin.value = 0.1
    dataLengthMax.value = 20.0
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

  const savedEnableRollingLookback = localStorage.getItem('lastEnableRollingLookback')
  if (savedEnableRollingLookback) {
    enableRollingLookback.value = savedEnableRollingLookback === 'true'
  }

  const savedIntradayPretrained = localStorage.getItem('intradayPretrainedChoice')
  if (savedIntradayPretrained) {
    intradayPretrainedChoice.value = savedIntradayPretrained
  }
  const savedDailyPretrained = localStorage.getItem('dailyPretrainedChoice')
  if (savedDailyPretrained) {
    dailyPretrainedChoice.value = savedDailyPretrained
  }
  const savedDailyBlend = localStorage.getItem('dailyBlendModels')
  if (savedDailyBlend) {
    dailyBlendModels.value = savedDailyBlend !== 'false'
  }
  const savedIntradayBlend = localStorage.getItem('intradayBlendModels')
  if (savedIntradayBlend) {
    intradayBlendModels.value = savedIntradayBlend !== 'false'
  }

  const savedStrategyChanConfig = localStorage.getItem('strategyChanConfig')
  if (savedStrategyChanConfig) {
    try {
      Object.assign(strategyChanConfig.value, JSON.parse(savedStrategyChanConfig))
    } catch (e) { /* ignore */ }
  }

  const savedPretrainConfig = localStorage.getItem('pretrainConfig')
  if (savedPretrainConfig) {
    try {
      Object.assign(pretrainConfig.value, JSON.parse(savedPretrainConfig))
    } catch (e) { /* ignore */ }
  }

  fetchIntradayPretrainedModels({ quiet: true })

  // Apply initial sidebar state
  if (['history', 'help'].includes(activeTab.value)) {
    showDesktopSidebar.value = true
  }

  nextTick(() => {
    isInitialized.value = true
    updateMobileTabsHint()
  })

  window.addEventListener('resize', updateMobileTabsHint, { passive: true })
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateMobileTabsHint)
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

  // Sidebar Visibility Logic
  if (['history', 'help'].includes(newVal)) {
    showDesktopSidebar.value = true
  } else if (userManuallyHidden.value) {
    showDesktopSidebar.value = false
  } else {
    showDesktopSidebar.value = true
  }
  
  // Refresh pretrained models list when entering analysis/intraday tabs
  if (newVal === 'intraday' || newVal === 'analysis') {
     fetchIntradayPretrainedModels({ quiet: true })
  }
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

watch(autype, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('lastAutype', String(newVal))
})

watch(enableRollingLookback, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('lastEnableRollingLookback', newVal)
})

watch(intradayPretrainedChoice, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('intradayPretrainedChoice', newVal || 'auto')
})

watch(intradayBlendModels, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('intradayBlendModels', newVal ? 'true' : 'false')
})

watch(dailyPretrainedChoice, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('dailyPretrainedChoice', newVal || 'auto')
})

watch(dailyBlendModels, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('dailyBlendModels', newVal ? 'true' : 'false')
})

watch(strategyForm, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('strategyForm', JSON.stringify(newVal))
}, { deep: true })

watch(strategyChanConfig, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('strategyChanConfig', JSON.stringify(newVal))
}, { deep: true })

watch(pretrainConfig, (newVal) => {
  if (!isInitialized.value) return
  localStorage.setItem('pretrainConfig', JSON.stringify(newVal))
}, { deep: true })

watch([intradayFreq, dataSrc, model], () => {
  fetchIntradayPretrainedModels({ quiet: true })
})

const fetchIntradayPretrainedModels = async ({ quiet } = {}) => {
  const now = Date.now()
  if (quiet && now - intradayPretrainedFetchedAt < 10000) return
  intradayPretrainedLoading.value = !quiet
  try {
    const pageSize = 200
    let page = 1
    let total = null
    const all = []
    while (true) {
      const res = await axios.get('/api/pretrained_models', { params: { page, page_size: pageSize } })
      const data = res.data || {}
      const items = Array.isArray(data.items) ? data.items : []
      if (total === null && data.total !== undefined) {
        const n = Number(data.total)
        total = Number.isFinite(n) ? n : null
      }
      all.push(...items)
      if (!items.length) break
      if (total !== null && all.length >= total) break
      if (items.length < pageSize) break
      page += 1
      if (page > 10) break
    }
    intradayPretrainedModels.value = all
    intradayPretrainedFetchedAt = now
  } catch (e) {
    intradayPretrainedModels.value = []
  } finally {
    intradayPretrainedLoading.value = false
  }
}

const matchedIntradayPretrainedModels = computed(() => {
  const freq = String(intradayFreq.value || '')
  const src = String(dataSrc.value || '')
  const mt = String(model.value || '')
  return (intradayPretrainedModels.value || []).filter((m) => {
    return String(m?.frequency || '') === freq && String(m?.data_src || '') === src && String(m?.model_type || '') === mt
  })
})

const intradayPretrainedOptions = computed(() => {
  const opts = [
    { label: t('app.pretrainedAuto'), value: 'auto' },
    { label: t('app.pretrainedOff'), value: 'none' }
  ]
  for (const m of matchedIntradayPretrainedModels.value || []) {
    const key = String(m?.key || '')
    if (!key) continue
    const name = String(m?.display_name || m?.name || '').trim()
    const trainedAt = formatTime(m?.trained_at)
    const feat = Number(m?.feature_count || 0)
    const metaLabel = `${trainedAt || '-'} F${feat} ${key.slice(0, 8)}`
    opts.push({ label: name ? `${name} · ${metaLabel}` : metaLabel, value: key })
  }
  return opts
})

const matchedDailyPretrainedModels = computed(() => {
  const freq = '1d'
  const src = String(dataSrc.value || '')
  const mt = String(model.value || '')
  return (intradayPretrainedModels.value || []).filter((m) => {
    return String(m?.frequency || '') === freq && String(m?.data_src || '') === src && String(m?.model_type || '') === mt
  })
})

const getPretrainedAvailability = ({ isIntraday }) => {
  const blendOn = isIntraday ? intradayBlendModels.value : dailyBlendModels.value
  const choice = isIntraday ? intradayPretrainedChoice.value : dailyPretrainedChoice.value
  if (!blendOn || choice === 'none') return { requested: false, available: true }

  const matched = isIntraday ? matchedIntradayPretrainedModels.value : matchedDailyPretrainedModels.value
  if (choice === 'auto') return { requested: true, available: (matched || []).length > 0 }

  const key = String(choice || '')
  const ok = (matched || []).some((m) => String(m?.key || '') === key)
  return { requested: true, available: ok }
}

  const analyze = async (isPrediction = false, silent = false, forceRefresh = false) => {
    const isAnalysisTab = activeTab.value === 'analysis'
    const separatePrediction = isAnalysisTab && isPrediction

    loading.value = true
    
    try {
      const isIntraday = activeTab.value === 'intraday'
      const frequency = isIntraday ? intradayFreq.value : '1d'
      
      const firstPassPrediction = separatePrediction ? false : isPrediction

      const pretrainedAvail = getPretrainedAvailability({ isIntraday })
      if (!silent && pretrainedAvail.requested && !pretrainedAvail.available) {
        toast.add({
          title: t('app.pretrainedUnavailableTitle'),
          description: t('app.pretrainedUnavailableDesc'),
          color: 'orange'
        })
      }

      const params = {
        code: code.value,
        frequency: frequency,
        do_predict: firstPassPrediction,
        autype: autype.value,
        trigger_step: triggerStep.value,
        bi_strict: biStrict.value,
        enable_rolling_lookback: enableRollingLookback.value,
        profit_threshold: strategyForm.value.profit_threshold,
        auto_profit_quantile: strategyForm.value.auto_profit_quantile,
        profit_lookahead: strategyForm.value.profit_lookahead,
        data_src: dataSrc.value,
        model: model.value,
        force_refresh: forceRefresh,
        data_length_years: dataLengthYears.value,
        use_pretrained: isIntraday
            ? (intradayBlendModels.value ? (intradayPretrainedChoice.value === 'none' ? false : true) : false)
            : (dailyBlendModels.value ? (dailyPretrainedChoice.value === 'none' ? false : true) : false),
        pretrained_model_key:
          isIntraday 
            ? (intradayBlendModels.value && intradayPretrainedChoice.value && !['auto', 'none'].includes(intradayPretrainedChoice.value) ? intradayPretrainedChoice.value : undefined)
            : (dailyBlendModels.value && dailyPretrainedChoice.value && !['auto', 'none'].includes(dailyPretrainedChoice.value) ? dailyPretrainedChoice.value : undefined),
        blend_models:
          isIntraday 
            ? (intradayBlendModels.value && intradayPretrainedChoice.value !== 'none' ? true : undefined)
            : (dailyBlendModels.value && dailyPretrainedChoice.value !== 'none' ? true : undefined)
      }

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
               autype: autype.value,
               trigger_step: triggerStep.value,
               bi_strict: biStrict.value,
               profit_threshold: strategyForm.value.profit_threshold,
               auto_profit_quantile: strategyForm.value.auto_profit_quantile,
               profit_lookahead: strategyForm.value.profit_lookahead,
               data_src: dataSrc.value,
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

const handleSearchHistory = (searchCode) => {
  activeTab.value = 'history'
  nextTick(() => {
    historyPanelRef.value?.search(searchCode)
  })
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

const handleViewStockFromStrategy = (stockCode) => {
  code.value = stockCode
  activeTab.value = 'analysis'
  analyze(true)
}
</script>

<style lang="postcss">
/* Add any global styles or overrides here */
body {
  @apply bg-white dark:bg-gray-900;
}
</style>
