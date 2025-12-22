<template>
  <el-container class="h-screen bg-white">
    <el-header class="border-b border-gray-200 flex items-center px-4 h-16 gap-3">
      <el-button class="lg:hidden" circle text @click="drawerVisible = true">
        <svg width="24" height="24" viewBox="0 0 24 24" class="text-gray-600"><path :d="MemoryMenuLeft" /></svg>
      </el-button>
      <h1 class="text-xl font-bold text-gray-800 m-0 truncate flex items-center gap-2">
        <svg width="24" height="24" viewBox="0 0 24 24" class="text-blue-600"><path :d="MemoryDiamond" /></svg>
        <span>缠论量化分析 <span class="hidden sm:inline">(Chan Theory Quant)</span></span>
      </h1>
    </el-header>
    
    <el-container class="overflow-hidden h-[calc(100vh-64px)]">
      <!-- Sidebar (Left) - Hidden on Mobile -->
      <el-aside width="320px" class="hidden lg:flex bg-gray-50 border-r border-gray-200 flex-col p-5 overflow-y-auto">
        <SidebarContent 
          v-model:code="code"
          v-model:triggerStep="triggerStep"
          v-model:biStrict="biStrict"
          v-model:lang="lang"
          :loading="loading"
          :downloading="downloading"
          @analyze="analyze"
          @download="handleDownload"
        />
      </el-aside>

      <!-- Mobile Sidebar Drawer -->
      <el-drawer
        v-model="drawerVisible"
        title="设置"
        direction="ltr"
        size="80%"
        class="lg:hidden"
      >
        <div class="h-full p-1">
          <SidebarContent 
            v-model:code="code"
            v-model:triggerStep="triggerStep"
            v-model:biStrict="biStrict"
            v-model:lang="lang"
            :loading="loading"
            :downloading="downloading"
            @analyze="handleMobileAnalyze"
            @download="handleDownload"
          />
        </div>
      </el-drawer>
      
      <!-- Main Content (Right) -->
      <el-main class="p-0 flex flex-col overflow-hidden bg-white">
        <el-tabs v-model="activeTab" class="h-full flex flex-col border-none" type="border-card">
          <el-tab-pane name="analysis" class="h-full overflow-hidden flex flex-col">
             <template #label>
               <span class="flex items-center gap-2">
                 <svg width="16" height="16" viewBox="0 0 24 24"><path :d="MemoryChartBar" /></svg>
                 股票分析
               </span>
             </template>
             <!-- Tab Content Container with flex-1 to fill space -->
             <div class="h-full overflow-y-auto p-2 box-border">
                <div v-if="hasRunAnalysis" class="flex gap-2 flex-col lg:flex-row lg:h-full lg:overflow-hidden">
                  <!-- Chart Column (75%) -->
                  <div class="flex-none lg:flex-[3] flex flex-col min-w-0 h-[500px] lg:h-full overflow-hidden">
                    <el-card class="flex-1 flex flex-col h-full box-border !border-none !shadow-none" shadow="never" :body-style="{ height: '100%', padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }">
                      <div class="p-3 border-b border-gray-100 flex-shrink-0 flex justify-between items-center bg-gray-50 rounded-t">
                        <h3 class="text-base font-bold text-gray-700 m-0 truncate pr-2">走势图: {{ stockName ? `${stockName} (${code})` : code }}</h3>
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
                        />
                      </div>
                    </el-card>
                  </div>
                </div>
                
                <div v-else class="h-full flex justify-center items-center">
                  <div class="max-w-2xl w-full flex flex-col gap-8">
                    
                    <el-card class="about-section">
                      <template #header>
                        <div class="font-medium text-lg text-gray-800">关于本应用</div>
                      </template>
                      <p class="text-gray-600 leading-relaxed mb-4">本应用结合 <strong>缠论 (Chan Theory)</strong> 与 <strong>XGBoost</strong> 机器学习模型来分析股票走势。</p>
                      <ul class="list-disc pl-6 text-gray-600 mb-4">
                        <li class="mb-2"><strong>缠论</strong>: 识别走势结构（笔、线段、中枢）及买卖点。</li>
                        <li><strong>XGBoost</strong>: 基于历史表现验证信号的有效性。</li>
                      </ul>
                      <p class="text-sm text-gray-500 italic mt-6 pt-4 border-t border-gray-100">免责声明: 本工具仅供学习研究，不构成投资建议。</p>
                    </el-card>
                  </div>
                </div>
             </div>
          </el-tab-pane>
          
          <el-tab-pane name="intraday" class="h-full overflow-hidden flex flex-col">
             <template #label>
               <span class="flex items-center gap-2">
                 <svg width="16" height="16" viewBox="0 0 24 24"><path :d="MemoryClock" /></svg>
                 实时分析
               </span>
             </template>
             <div class="h-full overflow-y-auto p-2 box-border flex flex-col">
                <!-- Frequency Selector -->
               <div class="mb-2 flex flex-col gap-2 bg-gray-50 p-2 rounded border border-gray-100 flex-shrink-0">
                  <div class="flex items-center gap-4">
                     <span class="text-sm font-bold text-gray-700">周期选择:</span>
                     <el-radio-group v-model="intradayFreq" size="small" @change="handleFreqChange">
                         <el-radio-button label="1s" :disabled="['baostock', 'akshare'].includes(dataSrc)">1秒</el-radio-button>
                         <el-radio-button label="1m" :disabled="['baostock', 'akshare'].includes(dataSrc)">1分钟</el-radio-button>
                         <el-radio-button label="5m">5分钟</el-radio-button>
                         <el-radio-button label="15m">15分钟</el-radio-button>
                         <el-radio-button label="30m">30分钟</el-radio-button>
                         <el-radio-button label="60m">60分钟</el-radio-button>
                     </el-radio-group>
                  </div>
                  
                  <div class="flex items-center gap-4">
                     <span class="text-sm font-bold text-gray-700">数据源:</span>
                     <el-radio-group v-model="dataSrc" size="small" @change="handleDataSrcChange">
                         <el-radio-button label="baostock">BaoStock</el-radio-button>
                         <el-radio-button label="akshare">AkShare</el-radio-button>
                         <el-radio-button label="jqdata">JQData</el-radio-button>
                         <el-radio-button label="mock">Mock (1s测试)</el-radio-button>
                     </el-radio-group>
                     <div class="flex-1"></div>
                     <el-button type="primary" size="small" :loading="loading" @click="analyze">刷新分析</el-button>
                  </div>
               </div>
               
               <!-- Chart & Info -->
                <div v-if="hasRunIntraday" class="flex gap-2 flex-col lg:flex-row flex-1 overflow-hidden">
                  <!-- Chart Column (75%) -->
                  <div class="flex-none lg:flex-[3] flex flex-col min-w-0 h-[500px] lg:h-full overflow-hidden">
                    <el-card class="flex-1 flex flex-col h-full box-border !border-none !shadow-none" shadow="never" :body-style="{ height: '100%', padding: '0', display: 'flex', flexDirection: 'column', overflow: 'hidden' }">
                      <div class="p-3 border-b border-gray-100 flex-shrink-0 flex justify-between items-center bg-gray-50 rounded-t">
                        <h3 class="text-base font-bold text-gray-700 m-0 truncate pr-2">分时走势: {{ stockName ? `${stockName} (${code})` : code }} - {{ intradayFreq }}</h3>
                        <el-tag size="small" effect="plain" class="flex-shrink-0">{{ intradayLatestDate }}</el-tag>
                      </div>
                      <div class="flex-1 relative min-h-0 border border-t-0 border-gray-100 rounded-b overflow-hidden">
                         <ChanChart ref="intradayChartRef" :loading="loading" />
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
                        />
                      </div>
                    </el-card>
                  </div>
                </div>
                
                <div v-else class="flex-1 flex justify-center items-center text-gray-400">
                    请点击上方“刷新分析”或侧边栏“开始分析”以查看数据
                </div>
             </div>
          </el-tab-pane>
          
          <el-tab-pane name="help" class="h-full overflow-y-auto">
             <template #label>
               <span class="flex items-center gap-2">
                 <svg width="16" height="16" viewBox="0 0 24 24"><path :d="MemoryBook" /></svg>
                 使用指南
               </span>
             </template>
             <div class="p-5">
               <HelpPanel />
             </div>
          </el-tab-pane>
        </el-tabs>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { ref } from 'vue'
import axios from 'axios'
import { ElMessage } from 'element-plus'
import ChanChart from './components/ChanChart.vue'
import AnalysisPanel from './components/AnalysisPanel.vue'
import HelpPanel from './components/HelpPanel.vue'
import SidebarContent from './components/SidebarContent.vue'
import { MemoryDiamond, MemoryMenuLeft, MemoryChartBar, MemoryBook, MemoryClock } from '@pictogrammers/memory'

const code = ref('002701')
const triggerStep = ref(true)
const biStrict = ref(false)
const loading = ref(false)
const lang = ref('zh')
const activeTab = ref('analysis')
const hasRunAnalysis = ref(false)
const hasRunIntraday = ref(false)
const drawerVisible = ref(false)
const intradayFreq = ref('5m')
const dataSrc = ref('baostock')
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

const handleMobileAnalyze = () => {
  drawerVisible.value = false
  analyze()
}

const handleFreqChange = () => {
    if (hasRunIntraday.value) {
        analyze()
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

const analyze = async () => {
  loading.value = true
  
  try {
    const isIntraday = activeTab.value === 'intraday'
    const reqData = {
      code: code.value,
      trigger_step: triggerStep.value,
      bi_strict: biStrict.value,
      frequency: isIntraday ? intradayFreq.value : '1d',
      data_src: isIntraday ? dataSrc.value : 'baostock'
    }
    
    const response = await axios.post('/api/analyze', reqData)
    
    if (response.data.status === 'success') {
      const data = response.data.data
      
      if (isIntraday) {
          intradaySignal.value = response.data.signal
          intradayAccuracy.value = response.data.accuracy
          intradayLatestClose.value = data.latest_close
          intradayLatestDate.value = data.latest_date
          hasRunIntraday.value = true
          
          setTimeout(() => {
            if (intradayChartRef.value) {
              intradayChartRef.value.renderChart(data)
            }
          }, 100)
      } else {
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
</style>
