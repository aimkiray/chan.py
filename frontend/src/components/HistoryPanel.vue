<template>
  <div class="h-full flex flex-col">
    <div class="p-2 border-b border-gray-100 flex justify-between items-center bg-gray-50">
      <h3 class="text-base font-bold text-gray-700 m-0">{{ t('history.title') }}</h3>
      <el-button size="small" @click="fetchHistory" :loading="loading">{{ t('history.refresh') }}</el-button>
    </div>
    
    <div class="flex-1 overflow-auto p-2">
      <el-table :data="historyList" style="width: 100%" v-loading="loading" size="small" stripe border>
        <el-table-column prop="code" :label="t('history.code')" width="90" sortable />
        <el-table-column prop="frequency" :label="t('history.period')" width="70" />
        <el-table-column prop="model" :label="t('history.model')" width="90" />
        <el-table-column prop="created_at" :label="t('history.analyzeTime')" width="160" sortable />
        <el-table-column prop="data_latest_time" :label="t('history.dataTime')" width="160" />
        <el-table-column :label="t('history.signal')" width="100">
          <template #default="scope">
             <el-tag v-if="scope.row.signal_type" size="small" :type="scope.row.is_buy ? 'danger' : 'success'">
                {{ scope.row.signal_type }}
             </el-tag>
             <span v-else class="text-gray-400">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="accuracy" :label="t('history.accuracy')" width="80" />
        <el-table-column :label="t('history.action')" min-width="80" fixed="right">
          <template #default="scope">
            <el-button link type="primary" size="small" @click="viewResult(scope.row)">
              {{ t('history.viewChart') }}
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import axios from 'axios'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()
const emit = defineEmits(['view'])
const historyList = ref([])
const loading = ref(false)

const fetchHistory = async () => {
  loading.value = true
  try {
    const res = await axios.get('http://localhost:8000/api/history')
    historyList.value = res.data
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

const viewResult = (row) => {
    emit('view', row)
}

onMounted(() => {
  fetchHistory()
})

defineExpose({ refresh: fetchHistory })
</script>
