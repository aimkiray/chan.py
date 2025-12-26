<template>
  <el-container class="lg:h-screen min-h-screen bg-white flex flex-col">
    <!-- Desktop Header (Combined Title + Tabs) -->
    <div class="hidden lg:flex items-center h-10 border-b border-gray-200 bg-gray-50 flex-shrink-0 z-10 relative">
       <!-- Logo & Title -->
       <div class="flex items-center gap-2 font-bold text-gray-800 text-base select-none pl-4 border-r border-gray-200 box-border flex-shrink-0 h-full" style="width: 260px">
          <svg width="22" height="22" viewBox="0 0 24 24" class="text-blue-600"><path :d="MemoryDiamond" /></svg>
          <span>{{ t('app.title') }}</span>
       </div>
       
       <!-- Custom Tabs -->
       <div class="flex items-end self-stretch gap-1">
          <button 
             v-for="tab in tabs" 
             :key="tab.name" 
             @click="activeTab = tab.name"
             class="px-4 h-full flex items-center gap-2 text-sm transition-colors border-t border-l border-r border-transparent rounded-t relative top-[1px] outline-none select-none"
             :class="activeTab === tab.name ? 'bg-white text-blue-600 border-gray-200 font-medium' : 'text-gray-600 hover:text-gray-900 hover:bg-gray-100/50'"
          >
             <svg width="16" height="16" viewBox="0 0 24 24"><path :d="tab.icon" /></svg>
             {{ tab.label }}
          </button>
       </div>
       
       <div class="flex-1"></div>
       
       <!-- Lang Switch -->
       <div class="pr-4">
         <el-select v-model="lang" size="small" class="w-24">
           <el-option label="中文" value="zh" />
           <el-option label="English" value="en" />
         </el-select>
       </div>
    </div>

    <!-- Mobile Header (Title Only - Keep Status Quo) -->
    <el-header class="lg:hidden border-b border-gray-200 flex items-center px-4 h-12 gap-3 bg-white">
      <h1 class="text-base font-bold text-gray-800 m-0 truncate flex items-center gap-2">
        <svg width="20" height="20" viewBox="0 0 24 24" class="text-blue-600"><path :d="MemoryDiamond" /></svg>
        <span>{{ t('app.title') }}</span>
      </h1>
      <el-select v-model="lang" size="small" class="ml-auto w-24">
        <el-option label="中文" value="zh" />
        <el-option label="English" value="en" />
      </el-select>
    </el-header>
    
    <el-container class="lg:overflow-hidden lg:h-[calc(100vh-40px)] flex-1 flex flex-col lg:flex-row">
      <!-- Sidebar (Left) - Hidden on Mobile -->
      <el-aside v-show="showDesktopSidebar" width="260px" class="hidden lg:flex bg-gray-50 border-r border-gray-200 flex-col p-5 overflow-y-auto transition-all">
        <SidebarContent 
          v-model:code="code"
          v-model:triggerStep="triggerStep"
          v-model:biStrict="biStrict"
          :loading="loading"
          :downloading="downloading"
          mode="desktop"
          @analyze="analyze(true)"
          @download="handleDownload"
          @toggle="showDesktopSidebar = false"
        />
      </el-aside>

      <!-- Main Content (Right) -->
      <el-main class="p-0 flex flex-col lg:overflow-hidden bg-white flex-1 relative">
        <!-- Floating Toggle Button for Desktop -->
        <div 
           v-if="!showDesktopSidebar"
           class="hidden lg:flex absolute left-0 top-1/2 -translate-y-1/2 z-50 bg-white border border-l-0 border-gray-200 rounded-r shadow-md cursor-pointer hover:bg-gray-50 items-center justify-center w-6 h-12 transition-colors"
           @click="showDesktopSidebar = true"
           :title="t('sidebar.expandSidebar')"
        >
            <svg width="16" height="16" viewBox="0 0 24 24" class="text-gray-500"><path :d="MemoryChevronRight" /></svg>
        </div>

        <!-- Mobile Sidebar (Top) -->
        <div class="lg:hidden bg-gray-50 border-b border-gray-200 flex-shrink-0 p-4">
          <SidebarContent 
            v-model:code="code"
            v-model:triggerStep="triggerStep"
            v-model:biStrict="biStrict"
            :loading="loading"
            :downloading="downloading"
            :collapsed="mobileSidebarCollapsed"
            mode="mobile"
            @analyze="analyze(true); mobileSidebarCollapsed = true"
            @download="handleDownload"
            @toggle="mobileSidebarCollapsed = !mobileSidebarCollapsed"
          />
        </div>

        <!-- Mobile Tabs (Visible only on mobile) -->
        <div class="lg:hidden flex border-b border-gray-200 bg-gray-50 overflow-x-auto shrink-0">
           <button 
              v-for="tab in tabs" 
              :key="tab.name" 
              @click="activeTab = tab.name"
              class="flex-1 py-3 flex justify-center items-center gap-2 text-sm transition-colors border-b-2"
              :class="activeTab === tab.name ? 'bg-white text-blue-600 border-blue-600 font-medium' : 'text-gray-600 border-transparent hover:bg-gray-100'"
           >
              <svg width="18" height="18" viewBox="0 0 24 24"><path :d="tab.icon" /></svg>
              <span>{{ tab.label }}</span>
           </button>
        </div>

        <!-- Content Area -->
        <div class="flex-1 overflow-hidden relative" v-loading="loading && mobileSidebarCollapsed" element-loading-text="正在分析..." element-loading-background="rgba(255, 255, 255, 0.7)">
          
          <!-- Analysis Tab -->
          <div v-show="activeTab === 'analysis'" class="h-full w-full flex flex-col lg:overflow-hidden">
             <!-- Tab Content Container with flex-1 to fill space -->
             <div class="lg:h-full lg:overflow-y-auto p-2 box-border flex flex-col flex-1">
                <div v-if="hasRunAnalysis" class="flex gap-2 flex-col lg:flex-row lg:h-full lg:overflow-hidden flex-1">
                  <!-- Chart Column (75%) -->
                  <div class="flex-none lg:flex-[3] flex flex-col min-w-0 h-[500px] lg:h-full lg:overflow-hidden">
                    <el-card class="flex-1 flex flex-col h-full box-border !border-none !shadow-none" shadow="never" :body-style="{ height: '100%', padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }">
                      <div class="p-3 border-b border-gray-100 flex-shrink-0 flex justify-between items-center bg-gray-50 rounded-t">
                        <h3 class="text-base font-bold text-gray-700 m-0 truncate pr-2">{{ t('app.chart') }}: {{ stockName ? `${stockName} (${code})` : code }}</h3>
                        <el-tag size="small" effect="plain" class="flex-shrink-0">{{ latestDate }}</el-tag>
                      </div>
                      <div class="flex-1 relative min-h-0 border border-t-0 border-gray-100 rounded-b overflow-hidden">
                         <ChanChart ref="chartRef" :loading="loading" />
                      </div>
                    </el-card>
                  </div>
                  
                  <!-- Info Column (25%) -->
                  <div class="flex-none lg:flex-1 min-w-0 lg:min-w-[300px] h-auto lg:h-full flex-shrink-0">
                    <el-card class="h-full !border-none !shadow-none overflow-y-auto" shadow="never" :body-style="{ padding: '0' }">
                      <div class="border border-gray-100 rounded h-full">
                        <AnalysisPanel 
                          :latestClose="latestClose"
                          :latestDate="latestDate"
                          :signal="signal"
                          :accuracy="accuracy"
                          frequency="1d"
                        />
                      </div>
                    </el-card>
                  </div>
                </div>
                
                <div v-else class="h-full flex justify-center items-center">
                  <div class="max-w-2xl w-full flex flex-col gap-8">
                    
                    <el-card class="about-section">
                      <template #header>
                        <div class="font-medium text-lg text-gray-800">{{ t('app.about') }}</div>
                      </template>
                      <p class="text-gray-600 leading-relaxed mb-4" v-html="t('app.aboutContent')"></p>
                      <ul class="list-disc pl-6 text-gray-600 mb-4">
                        <li class="mb-2" v-html="t('app.aboutChan')"></li>
                        <li v-html="t('app.aboutML')"></li>
                      </ul>
                      <p class="text-sm text-gray-500 italic mt-6 pt-4 border-t border-gray-100">{{ t('app.disclaimer') }}</p>
                    </el-card>
                  </div>
                </div>
             </div>
          </div>
          
          <!-- Intraday Tab -->
          <div v-show="activeTab === 'intraday'" class="h-full w-full flex flex-col lg:overflow-hidden relative pb-16 lg:pb-0">
             <div class="lg:h-full lg:overflow-y-auto p-2 box-border flex flex-col flex-1">
                <!-- Frequency Selector -->
               <div class="mb-2 flex flex-col gap-2 bg-gray-50 p-2 rounded border border-gray-100 flex-shrink-0">
                  <div class="flex items-center gap-4">
                     <span class="text-sm font-bold text-gray-700">{{ t('app.periodSelect') }}</span>
                     <el-radio-group v-model="intradayFreq" size="small" @change="handleFreqChange">
                         <el-radio-button value="1m" :disabled="['baostock', 'akshare'].includes(dataSrc)">{{ t('periods.1m') }}</el-radio-button>
                         <el-radio-button value="5m">{{ t('periods.5m') }}</el-radio-button>
                         <el-radio-button value="15m">{{ t('periods.15m') }}</el-radio-button>
                         <el-radio-button value="30m">{{ t('periods.30m') }}</el-radio-button>
                         <el-radio-button value="60m">{{ t('periods.60m') }}</el-radio-button>
                         <el-radio-button value="1d">{{ t('periods.1d') }}</el-radio-button>
                         <el-radio-button value="1w">{{ t('periods.1w') }}</el-radio-button>
                         <el-radio-button value="1mo">{{ t('periods.1mo') }}</el-radio-button>
                     </el-radio-group>
                  </div>
                  
                  <div class="flex items-center gap-4">
                     <span class="text-sm font-bold text-gray-700">{{ t('app.dataSrc') }}</span>
                     <el-radio-group v-model="dataSrc" size="small" @change="handleDataSrcChange">
                         <el-radio-button value="clickhouse">{{ t('app.dataSrcOptions.clickhouse') }}</el-radio-button>
                         <el-radio-button value="baostock">{{ t('app.dataSrcOptions.baostock') }}</el-radio-button>
                         <el-radio-button value="akshare">{{ t('app.dataSrcOptions.akshare') }}</el-radio-button>
                     </el-radio-group>
                  </div>

                  <div class="flex items-center gap-4">
                     <span class="text-sm font-bold text-gray-700">{{ t('app.dataLength') }}</span>
                     <div class="w-96 px-2 flex items-center gap-2">
                        <el-slider 
                           class="custom-slider flex-1"
                           v-model="dataLengthYears" 
                           :min="dataLengthMin" 
                           :max="dataLengthMax" 
                           :step="dataLengthStep" 
                           show-input
                           :show-input-controls="false"
                           size="small"
                           @change="analyze(false)"
                        />
                        <span class="text-xs text-gray-400 whitespace-nowrap">(Max: {{ dataLengthMax }}y)</span>
                     </div>
                     <div class="flex-1"></div>
                     <!-- Desktop Buttons -->
                     <div class="hidden lg:flex gap-2">
                       <el-button type="primary" size="small" :loading="loading" @click="analyze(false)">{{ t('app.refresh') }}</el-button>
                       <el-button type="success" size="small" :loading="loading" @click="analyze(true)" :disabled="!hasRunIntraday">{{ t('app.predict') }}</el-button>
                     </div>
                  </div>

                  <div class="flex items-center gap-4">
                     <span class="text-sm font-bold text-gray-700">{{ t('app.modelSelect') }}</span>
                     <el-radio-group v-model="model" size="small" @change="analyze(false)">
                         <el-radio-button value="xgboost">{{ t('app.modelOptions.xgboost') }}</el-radio-button>
                         <el-radio-button value="lightgbm">{{ t('app.modelOptions.lightgbm') }}</el-radio-button>
                         <el-radio-button value="mlp">{{ t('app.modelOptions.mlp') }}</el-radio-button>
                     </el-radio-group>
                  </div>

                  <!-- Mobile Buttons (Placed below model selection) -->
                  <div class="lg:hidden flex gap-2 w-full mt-2">
                      <el-button type="primary" class="flex-1" :loading="loading" @click="analyze(false)">{{ t('app.refresh') }}</el-button>
                      <el-button type="success" class="flex-1" :loading="loading" @click="analyze(true)" :disabled="!hasRunIntraday">{{ t('app.predict') }}</el-button>
                  </div>
               </div>
               
               <!-- Chart & Info -->
                <div v-if="hasRunIntraday" class="flex gap-2 flex-col lg:flex-row lg:flex-1 lg:overflow-hidden flex-1">
                  <!-- Chart Column (75%) -->
                  <div class="flex-none lg:flex-[3] flex flex-col min-w-0 h-[500px] lg:h-full lg:overflow-hidden">
                    <el-card class="flex-1 flex flex-col h-full box-border !border-none !shadow-none" shadow="never" :body-style="{ height: '100%', padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }">
                      <div class="p-3 border-b border-gray-100 flex-shrink-0 flex justify-between items-center bg-gray-50 rounded-t">
                        <h3 class="text-base font-bold text-gray-700 m-0 truncate pr-2">{{ t('app.intradayChart') }}: {{ stockName ? (stockName + ' (' + code + ')') : code }} - {{ intradayFreq }} - {{ model }}</h3>
                        <el-tag size="small" effect="plain" class="flex-shrink-0">{{ intradayLatestDate }}</el-tag>
                      </div>
                      <div class="flex-1 relative min-h-0 border border-t-0 border-gray-100 rounded-b overflow-hidden">
                         <ChanChart ref="intradayChartRef" :loading="loading"></ChanChart>
                      </div>
                    </el-card>
                  </div>
                  
                  <!-- Info Column (25%) -->
                  <div class="flex-none lg:flex-1 min-w-0 lg:min-w-[300px] h-auto lg:h-full flex-shrink-0">
                    <el-card class="h-full !border-none !shadow-none overflow-y-auto" shadow="never" :body-style="{ padding: '0' }">
                      <div class="border border-gray-100 rounded h-full">
                        <AnalysisPanel 
                          :latestClose="intradayLatestClose"
                          :latestDate="intradayLatestDate"
                          :signal="intradaySignal"
                          :accuracy="intradayAccuracy"
                          :frequency="intradayFreq"
                        />
                      </div>
                    </el-card>
                  </div>
                </div>
                
                <div v-else class="flex-1 flex justify-center items-center text-gray-400">
                    {{ t('app.waitingForData') }}
                </div>
             </div>
          </div>
          
          <!-- History Tab -->
          <div v-show="activeTab === 'history'" class="h-full w-full flex flex-col lg:overflow-hidden">
             <div class="lg:h-full lg:overflow-hidden p-2 box-border bg-white flex-1">
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
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import axios from 'axios'
import { ElMessage, ElNotification } from 'element-plus'
import ChanChart from './components/ChanChart.vue'
import AnalysisPanel from './components/AnalysisPanel.vue'
import HelpPanel from './components/HelpPanel.vue'
import HistoryPanel from './components/HistoryPanel.vue'
import SidebarContent from './components/SidebarContent.vue'
import { MemoryJournal, MemoryDiamond, MemoryChartBar, MemoryBook, MemoryClock, MemoryChevronDown, MemoryFlask, MemoryChevronRight } from '@pictogrammers/memory'
import { useI18n } from './composables/useI18n'

const { currentLang: lang, t } = useI18n()

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
const downloading = ref(false)

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

const dataLengthMin = ref(0.5)
const dataLengthMax = ref(10)
const dataLengthStep = ref(0.5)

// Update slider constraints based on frequency
watch(intradayFreq, (newFreq) => {
  if (newFreq === '1m') {
    dataLengthMin.value = 0.1
    dataLengthMax.value = 1
    dataLengthStep.value = 0.1
    if (dataLengthYears.value > 1) dataLengthYears.value = 1
    if (dataLengthYears.value < 0.1) dataLengthYears.value = 0.1
  } else if (newFreq === '5m') {
    dataLengthMin.value = 0.1
    dataLengthMax.value = 2
    dataLengthStep.value = 0.2
    if (dataLengthYears.value > 2) dataLengthYears.value = 2
    if (dataLengthYears.value < 0.1) dataLengthYears.value = 0.1
  } else if (['15m', '30m', '60m'].includes(newFreq)) {
    dataLengthMin.value = 0.5
    dataLengthMax.value = 5
    dataLengthStep.value = 0.5
    if (dataLengthYears.value > 5) dataLengthYears.value = 5
    if (dataLengthYears.value < 0.5) dataLengthYears.value = 0.5
  } else {
    // 1d, 1w, 1mo
    dataLengthMin.value = 0.5
    dataLengthMax.value = 10
    dataLengthStep.value = 0.5
    // Default max is 10, no need to clamp strictly unless over 10
  }
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

  const savedDataLengthYears = localStorage.getItem('lastDataLengthYears')
  if (savedDataLengthYears) {
    dataLengthYears.value = parseFloat(savedDataLengthYears)
  }
})

// Watch changes and save to localStorage
watch(code, (newVal) => {
  if (newVal) localStorage.setItem('lastStockCode', newVal)
})

watch(activeTab, (newVal) => {
  if (newVal) localStorage.setItem('lastActiveTab', newVal)
})

watch(lang, (newVal) => {
  if (newVal) localStorage.setItem('lastLang', newVal)
})

watch(model, (newVal) => {
  if (newVal) localStorage.setItem('lastModel', newVal)
})

watch(intradayFreq, (newVal) => {
  if (newVal) localStorage.setItem('lastIntradayFreq', newVal)
})

watch(dataSrc, (newVal) => {
  if (newVal) localStorage.setItem('lastDataSrc', newVal)
})

watch(dataLengthYears, (newVal) => {
  if (newVal) localStorage.setItem('lastDataLengthYears', newVal.toString())
})

const handleViewHistory = async (row) => {
  loading.value = true
  try {
    const response = await axios.get(`/api/history/${row.id}`)
    const result = response.data
    const data = result.data
    
    // Update UI State
    code.value = row.code
    stockName.value = result.stock_name
    
    const isIntraday = row.frequency !== '1d'
    
    if (isIntraday) {
        activeTab.value = 'intraday'
        intradayFreq.value = row.frequency
        model.value = row.model
        intradaySignal.value = result.signal
        intradayAccuracy.value = result.accuracy
        intradayLatestClose.value = data.latest_close
        intradayLatestDate.value = data.latest_date
        hasRunIntraday.value = true
        
        setTimeout(() => {
          if (intradayChartRef.value) {
            intradayChartRef.value.renderChart(data)
          }
        }, 100)
    } else {
        activeTab.value = 'analysis'
        model.value = row.model
        signal.value = result.signal
        accuracy.value = result.accuracy
        latestClose.value = data.latest_close
        latestDate.value = data.latest_date
        hasRunAnalysis.value = true
        
        setTimeout(() => {
          if (chartRef.value) {
            chartRef.value.renderChart(data)
          }
        }, 100)
    }
  } catch (error) {
    console.error(error)
    ElMessage.error('加载历史记录失败')
  } finally {
    loading.value = false
  }
}

const handleFreqChange = () => {
    if (hasRunIntraday.value) {
        analyze(false)
    }
}

const handleDataSrcChange = () => {
    // If switching to baostock/akshare, ensure freq is not < 5m
    if (['baostock', 'akshare'].includes(dataSrc.value)) {
        if (['1s', '1m'].includes(intradayFreq.value)) {
            intradayFreq.value = '5m'
        }
    }
    handleFreqChange()
}

const handleDownload = async () => {
  downloading.value = true
  try {
    const isIntraday = activeTab.value === 'intraday'
    const frequency = isIntraday ? intradayFreq.value : '1d'
    const response = await axios.get(`/api/download/${code.value}`, {
      params: { frequency },
      responseType: 'blob'
    })
    
    // Create download link
    const url = window.URL.createObjectURL(new Blob([response.data]))
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', `${code.value}_${frequency}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  } catch (error) {
    ElMessage.error('下载失败')
    console.error(error)
  } finally {
    downloading.value = false
  }
}

const analyze = async (doPredict = false) => {
  loading.value = true
  
  try {
    const isIntraday = activeTab.value === 'intraday'
    console.log('Sending analyze request with force_refresh=true')
    const reqData = {
      code: code.value,
      trigger_step: triggerStep.value,
      bi_strict: biStrict.value,
      frequency: isIntraday ? intradayFreq.value : '1d',
      data_src: isIntraday ? dataSrc.value : 'clickhouse',
      model: isIntraday ? model.value : 'xgboost',
      do_predict: doPredict,
      force_refresh: true,
      data_length_years: isIntraday ? dataLengthYears.value : 6
    }
    
    const response = await axios.post('/api/analyze', reqData)
    
    if (response.data.status === 'success') {
      const data = response.data.data
      
      // Check for data warnings
      if (response.data.data_warnings && response.data.data_warnings.length > 0) {
        response.data.data_warnings.forEach(warning => {
          ElNotification({
            title: '数据源警告',
            message: warning,
            type: 'warning',
            duration: 6000
          })
        })
      }

      if (isIntraday) {
          if (doPredict) {
              intradaySignal.value = response.data.signal
              intradayAccuracy.value = response.data.accuracy
          } else {
              intradaySignal.value = null
              intradayAccuracy.value = null
          }
          intradayLatestClose.value = data.latest_close
          intradayLatestDate.value = data.latest_date
          hasRunIntraday.value = true
          
          setTimeout(() => {
            if (intradayChartRef.value) {
              intradayChartRef.value.renderChart(data)
            }
          }, 100)
      } else {
          // Main Analysis tab always predicts? Or reuse logic?
          // User only asked for Real-time page changes.
          // But 'analyze' is shared. Let's assume Main tab always predicts for now as before?
          // The button in sidebar calls analyze().
          // If we change signature to analyze(doPredict=false), then Sidebar needs update.
          // Sidebar emits 'analyze'. 
          // Let's check Sidebar integration.
          
          signal.value = response.data.signal
          accuracy.value = response.data.accuracy
          latestClose.value = data.latest_close
          latestDate.value = data.latest_date
          hasRunAnalysis.value = true
          
          setTimeout(() => {
            if (chartRef.value) {
              chartRef.value.renderChart(data)
            }
          }, 100)
      }
      
      stockName.value = response.data.stock_name || ''
    }
  } catch (error) {
    console.error(error)
    ElMessage.error('分析失败: ' + (error.response?.data?.detail || error.message))
  } finally {
    loading.value = false
  }
}
</script>

<style>
/* Adjust tab content height */
.el-tabs__content {
  flex: 1;
  overflow: hidden;
  padding: 0 !important;
}

.el-tabs--border-card {
  border: none;
  box-shadow: none;
}

.el-tabs--border-card > .el-tabs__header {
  background-color: #f9fafb;
  border-bottom: 1px solid #e5e7eb;
}

.custom-slider .el-slider__input {
  width: 70px !important;
}
.custom-slider .el-slider__runway {
  margin-right: 80px !important;
}
</style>
