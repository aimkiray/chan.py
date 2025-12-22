<template>
  <el-container class="h-screen bg-white">
    <el-header class="border-b border-gray-200 flex items-center px-4 h-16 gap-3">
      <el-button class="lg:hidden" circle text @click="drawerVisible = true">
        <span class="text-xl">☰</span>
      </el-button>
      <h1 class="text-xl font-bold text-gray-800 m-0 truncate">📈 缠论量化分析 <span class="hidden sm:inline">(Chan Theory Quant)</span></h1>
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
          @analyze="analyze"
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
            @analyze="handleMobileAnalyze"
          />
        </div>
      </el-drawer>
      
      <!-- Main Content (Right) -->
      <el-main class="p-0 flex flex-col overflow-hidden bg-white">
        <el-tabs v-model="activeTab" class="h-full flex flex-col border-none" type="border-card">
          <el-tab-pane label="📈 股票分析" name="analysis" class="h-full overflow-hidden flex flex-col">
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
                    <div class="text-xl text-gray-600 bg-gray-50 p-6 rounded-lg text-center border border-gray-200">
                      👈 请在左侧输入股票代码并点击 '开始分析'。
                    </div>
                    
                    <el-card class="about-section">
                      <template #header>
                        <div class="font-medium text-lg text-gray-800">关于本应用</div>
                      </template>
                      <p class="text-gray-600 leading-relaxed mb-4">本应用结合 <strong>缠论 (Chan Theory)</strong> 与 <strong>XGBoost</strong> 机器学习模型来分析股票走势。</p>
                      <ul class="list-disc pl-6 text-gray-600 mb-4">
                        <li class="mb-2"><strong>缠论</strong>: 识别走势结构（笔、线段、中枢）及买卖点。</li>
                        <li><strong>XGBoost</strong>: 基于历史表现验证信号的有效性。</li>
                      </ul>
                      <p class="text-sm text-gray-500 italic mt-6 pt-4 border-t border-gray-100">*免责声明: 本工具仅供学习研究，不构成投资建议。*</p>
                    </el-card>
                  </div>
                </div>
             </div>
          </el-tab-pane>
          
          <el-tab-pane label="📖 使用指南" name="help" class="h-full overflow-y-auto">
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

const code = ref('sz.002701')
const triggerStep = ref(true)
const biStrict = ref(false)
const loading = ref(false)
const lang = ref('zh')
const activeTab = ref('analysis')
const hasRunAnalysis = ref(false)
const drawerVisible = ref(false)

const signal = ref(null)
const accuracy = ref(null)
const latestClose = ref('--')
const latestDate = ref('--')
const stockName = ref('')
const chartRef = ref(null)

const handleMobileAnalyze = () => {
  drawerVisible.value = false
  analyze()
}

const analyze = async () => {
  loading.value = true
  // Ensure we switch to analysis tab
  activeTab.value = 'analysis'
  
  try {
    const response = await axios.post('/api/analyze', {
      code: code.value,
      trigger_step: triggerStep.value,
      bi_strict: biStrict.value
    })
    
    if (response.data.status === 'success') {
      const data = response.data.data
      signal.value = response.data.signal
      accuracy.value = response.data.accuracy
      latestClose.value = data.latest_close
      latestDate.value = data.latest_date
      stockName.value = response.data.stock_name || ''
      
      hasRunAnalysis.value = true
      
      // Wait for DOM update then render chart
      setTimeout(() => {
        if (chartRef.value) {
          chartRef.value.renderChart(data)
        }
      }, 100)
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
