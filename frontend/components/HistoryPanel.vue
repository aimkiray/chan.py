<template>
  <div class="h-full flex flex-col">
    <div class="p-2 border-b border-gray-100 dark:border-gray-800 flex justify-between items-center bg-gray-50 dark:bg-gray-900">
      <h3 class="text-base font-bold text-gray-700 dark:text-gray-200 m-0">{{ t('history.title') }}</h3>
      <div class="flex items-center gap-2">
         <USelect 
            v-model="pageSize" 
            :options="[10, 20, 50, 100]" 
            size="sm"
            @update:model-value="resetAndFetch"
            class="w-20"
         />
         <UButton
           size="sm"
           @click="downloadHistory"
           :loading="downloading"
           :disabled="loading || total === 0"
           color="white"
           variant="solid"
           icon="i-heroicons-arrow-down-tray"
           :title="t('history.download')"
         />
         <UButton size="sm" @click="fetchHistory" :loading="loading" color="white" variant="solid" icon="i-heroicons-arrow-path" />
      </div>
    </div>
    
    <div class="flex-1 overflow-auto p-2">
      <UTable 
        :rows="historyList" 
        :columns="columns" 
        :loading="loading" 
        class="w-full" 
        :ui="{ th: { base: 'whitespace-nowrap', padding: 'px-3 py-2' }, td: { padding: 'px-3 py-2' } }"
      >
        <template #signal-data="{ row }">
           <UBadge v-if="row.signal_type" size="xs" :color="row.is_buy ? 'red' : 'green'" variant="subtle">
              {{ row.signal_type }}
           </UBadge>
           <span v-else class="text-gray-400">-</span>
        </template>
        <template #actions-data="{ row }">
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <UButton variant="link" size="xs" @click="viewResult(row)" color="primary">
                  {{ t('history.viewChart') }}
                </UButton>
                <UButton variant="link" size="xs" @click="deleteItem(row)" color="red">
                   {{ t('app.delete') }}
                </UButton>
            </div>
        </template>
      </UTable>
    </div>

    <!-- Pagination -->
    <div class="p-2 border-t border-gray-100 dark:border-gray-800 flex justify-between items-center bg-gray-50 dark:bg-gray-900">
        <span class="text-xs text-gray-500">
            {{ (page - 1) * pageSize + 1 }}-{{ Math.min(page * pageSize, total) }} / {{ total }}
        </span>
        <UPagination v-model="page" :page-count="pageSize" :total="total" size="sm" :max="5" show-last show-first @update:model-value="fetchHistory" />
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import axios from 'axios'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()
const emit = defineEmits(['view'])
const historyList = ref([])
const loading = ref(false)
const downloading = ref(false)
const toast = useToast()

const page = ref(1)
const pageSize = ref(10)
const total = ref(0)

const columns = computed(() => [
  { key: 'code', label: t('history.code'), sortable: true },
  { key: 'frequency', label: t('history.period') },
  { key: 'model', label: t('history.model') },
  { key: 'created_at', label: t('history.analyzeTime'), sortable: true },
  { key: 'data_latest_time', label: t('history.dataTime') },
  { key: 'signal', label: t('history.signal') },
  { key: 'accuracy', label: t('history.accuracy') },
  { key: 'actions', label: t('history.action') }
])

const fetchHistory = async () => {
  loading.value = true
  try {
    const res = await axios.get('/api/history', {
        params: {
            page: page.value,
            page_size: pageSize.value
        }
    })
    
    // Handle both new paginated response and potential old array response (though we updated backend)
    if (res.data.items) {
        historyList.value = res.data.items
        total.value = res.data.total
        // Update local params if backend returned them
        if (res.data.page) page.value = res.data.page
        if (res.data.page_size) pageSize.value = res.data.page_size
    } else if (Array.isArray(res.data)) {
         // Fallback for array response
        historyList.value = res.data
        total.value = res.data.length
    }
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

const resetAndFetch = () => {
    page.value = 1
    fetchHistory()
}

const escapeCsvValue = (value) => {
  if (value === null || value === undefined) return ''
  let str = String(value)
  if (str.includes('"')) str = str.replace(/"/g, '""')
  if (/[",\n\r]/.test(str)) str = `"${str}"`
  return str
}

const downloadHistory = async () => {
  downloading.value = true
  try {
    const items = []
    const pageSizeForDownload = 200
    let currentPage = 1
    let totalCount = null

    while (true) {
      const res = await axios.get('/api/history', {
        params: { page: currentPage, page_size: pageSizeForDownload }
      })

      if (Array.isArray(res.data)) {
        items.push(...res.data)
        break
      }

      const batch = res.data?.items || []
      totalCount = typeof res.data?.total === 'number' ? res.data.total : totalCount
      items.push(...batch)

      if (!totalCount || items.length >= totalCount || batch.length === 0) break
      currentPage += 1
    }

    const headers = ['id', 'code', 'frequency', 'model', 'created_at', 'data_latest_time', 'signal_type', 'is_buy', 'accuracy']
    const lines = [headers.join(',')]
    for (const row of items) {
      lines.push(headers.map((h) => escapeCsvValue(row?.[h])).join(','))
    }

    const csv = lines.join('\r\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    const ts = new Date().toISOString().slice(0, 19).replace('T', '_').replace(/:/g, '-')
    link.href = url
    link.setAttribute('download', `history_${ts}.csv`)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)

    toast.add({ title: t('app.downloadSuccess'), color: 'green' })
  } catch (e) {
    console.error(e)
    toast.add({ title: t('app.downloadError'), color: 'red' })
  } finally {
    downloading.value = false
  }
}

const viewResult = (row) => {
    emit('view', row)
}

const deleteItem = async (row) => {
    if (!confirm(t('app.confirmDelete'))) return
    
    try {
        await axios.delete(`/api/history/${row.id}`)
        fetchHistory()
    } catch (e) {
        console.error("Delete failed", e)
    }
}

onMounted(() => {
  fetchHistory()
})

defineExpose({ refresh: fetchHistory })
</script>
