<template>
  <div class="h-full flex flex-col">
    <div class="flex-1 min-h-0 p-3 flex flex-col">
      <div class="grid grid-cols-1 gap-3 h-full">
        <UCard :ui="{ body: { base: 'flex-1 min-h-0 flex flex-col', padding: 'p-3' } }" class="shadow-none border border-gray-200 dark:border-gray-800 flex flex-col min-h-0 h-full">
          <div class="flex items-center justify-between gap-2">
            <div class="flex items-center gap-2 min-w-0">
              <UButton
                v-if="selectedRunDetail"
                size="xs"
                color="gray"
                variant="ghost"
                icon="i-heroicons-arrow-left"
                :label="t('strategy.back')"
                @click="closeDetail"
              />
              <div class="text-sm font-medium text-gray-700 dark:text-gray-300 truncate">
                {{ selectedRunDetail ? t('strategy.detailTitle') : t('strategy.listTitle') }}
              </div>
            </div>

            <div v-if="!selectedRunDetail" class="flex items-center gap-2">
              <span v-if="selectedCount > 0" class="text-xs text-gray-500">
                {{ t('strategy.selectedCount', { n: selectedCount }) }}
              </span>
              <UButton
                size="xs"
                color="red"
                variant="soft"
                :disabled="selectedCount <= 0"
                :loading="batchDeleting"
                @click="batchDelete"
              >
                {{ t('strategy.batchDelete') }}
              </UButton>
              <UButton size="xs" color="gray" :loading="listLoading" @click="fetchRuns">
                {{ t('strategy.refreshList') }}
              </UButton>
            </div>
          </div>

          <div v-if="!selectedRunDetail" class="mt-2 flex-1 min-h-0 flex flex-col">
            <div class="flex-1 min-h-0 overflow-auto">
              <UTable
                :rows="runs"
                :columns="listColumns"
                :loading="listLoading"
                class="w-full"
                :ui="{ th: { base: 'whitespace-nowrap', padding: 'px-3 py-2' }, td: { padding: 'px-3 py-2' } }"
              >
                <template #select-header>
                  <UCheckbox
                    :model-value="allVisibleSelected"
                    :disabled="selectableRunIds.length === 0"
                    @update:model-value="toggleSelectAllVisible"
                  />
                </template>

                <template #select-data="{ row }">
                  <UCheckbox
                    :model-value="selectedIdSet.has(row.id)"
                    :disabled="!isRunDeletable(row)"
                    @click.stop
                    @update:model-value="(val) => toggleSelectRun(row.id, val)"
                  />
                </template>

                <template #status-data="{ row }">
                  <div class="min-w-[90px]">
                    <div class="flex items-center gap-2">
                      <UBadge
                        v-if="!['running'].includes(String(row.status || ''))"
                        :color="getStatusColor(row.status)"
                        size="xs"
                      >
                        {{ getStatusLabel(row.status) }}
                      </UBadge>
                    </div>
                    <div v-if="String(row.status || '') === 'running'" class="mt-2 space-y-1">
                      <div class="text-xs text-gray-500 whitespace-nowrap">
                        {{ row.processed || 0 }} / {{ row.total || '?' }} · {{ row.progress }}%
                      </div>
                      <UProgress :value="row.progress" size="xs" />
                    </div>
                  </div>
                </template>

                <template #result_count-data="{ row }">
                  <div class="text-xs tabular-nums text-gray-600 dark:text-gray-300 text-left whitespace-nowrap">
                    {{ row.result_count !== undefined && row.result_count !== null ? row.result_count : '-' }}
                  </div>
                </template>

                <template #params-data="{ row }">
                  <div class="min-w-0 cursor-pointer" @click="openRun(row)">
                    <div class="flex items-center gap-2 min-w-0">
                      <span class="text-xs font-medium text-gray-700 dark:text-gray-300 truncate" :title="row.strategy_name">
                        {{ row.strategy_name || '-' }}
                      </span>
                      <span class="text-[10px] text-gray-400 font-mono shrink-0">{{ shortId(row.id) }}</span>
                    </div>
                    <div v-if="runParamsLine(row)" class="text-[11px] text-gray-500 dark:text-gray-400 truncate">
                      {{ runParamsLine(row) }}
                    </div>
                  </div>
                </template>

                <template #updated_at-data="{ row }">
                  <span class="text-xs text-gray-500 whitespace-nowrap" :title="row.created_at || row.updated_at || ''">
                    {{ formatTime(row.created_at || row.updated_at) }}
                  </span>
                </template>

                <template #actions-data="{ row }">
                  <UButton size="xs" variant="link" color="primary" @click="openRun(row)">
                    {{ t('strategy.viewDetail') }}
                  </UButton>
                  <UButton
                    size="xs"
                    variant="link"
                    color="red"
                    :loading="deletingRunId === row.id"
                    :disabled="!isRunDeletable(row)"
                    @click="deleteRun(row)"
                  >
                    {{ t('app.delete') }}
                  </UButton>
                </template>
              </UTable>
            </div>

            <div v-if="pages > 1" class="mt-2 flex items-center justify-end gap-2 shrink-0">
              <span class="text-xs text-gray-500">{{ t('strategy.pageInfo', { page, pages, total }) }}</span>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="page <= 1"
                @click="page = 1; fetchRuns()"
              >
                &lt;&lt;
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="page <= 1"
                @click="prevPage"
              >
                {{ t('strategy.prevPage') }}
              </UButton>
              <div class="flex items-center gap-1">
                <UInput
                  v-model="pageInput"
                  size="xs"
                  class="w-12 text-center"
                  @keydown.enter="jumpToPage"
                />
              </div>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="page >= pages"
                @click="nextPage"
              >
                {{ t('strategy.nextPage') }}
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="page >= pages"
                @click="page = pages; fetchRuns()"
              >
                &gt;&gt;
              </UButton>
            </div>
          </div>

          <div v-else class="mt-3 border-t border-gray-100 dark:border-gray-800 pt-3 flex-1 min-h-0 flex flex-col">
            <div class="flex items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex items-center gap-2 min-w-0">
                  <h3 class="font-bold text-lg text-gray-800 dark:text-gray-100 truncate">{{ selectedRunDetail.strategy_name }}</h3>
                  <UBadge :color="getStatusColor(selectedRunDetail.status)" size="xs">{{ getStatusLabel(selectedRunDetail.status) }}</UBadge>
                </div>
                <div class="text-xs text-gray-500 mt-1 break-all">
                  {{ t('strategy.params') }}: {{ formatParams(selectedRunDetail.params) }}
                </div>
              </div>
              <div v-if="selectedRunDetail.results" class="text-sm font-medium text-gray-700 dark:text-gray-300 whitespace-nowrap">
                {{ t('strategy.found') }}: {{ selectedRunDetail.results.length }}
              </div>
            </div>

            <div class="mt-3 flex-1 min-h-0 overflow-auto">
              <UTable
                v-if="selectedRunDetail.results && selectedRunDetail.results.length > 0"
                :rows="paginatedDetailResults"
                :columns="columns"
                class="w-full"
                :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }"
              >
                <template #code-data="{ row }">
                  <span class="font-mono text-primary-600 dark:text-primary-400 cursor-pointer hover:underline" @click="viewStock(row.code)">{{ row.code }}</span>
                </template>
                <template #accuracy-data="{ row }">
                  <span :class="row.accuracy >= 0.8 ? 'text-green-600 dark:text-green-400 font-bold' : ''">{{ (row.accuracy * 100).toFixed(1) }}%</span>
                </template>
              </UTable>
            </div>

              <div v-if="detailTotalPages > 1" class="mt-2 flex items-center justify-end gap-2 border-t border-gray-100 dark:border-gray-800 pt-2 shrink-0">
                <span class="text-xs text-gray-500">
                  {{ t('strategy.pageInfo', { page: detailPage, pages: detailTotalPages, total: selectedRunDetail.results.length }) }}
                </span>
                <UButton
                  size="xs"
                  color="gray"
                  variant="soft"
                  :disabled="detailPage <= 1"
                  @click="detailPage = 1"
                >
                  &lt;&lt;
                </UButton>
                <UButton
                  size="xs"
                  color="gray"
                  variant="soft"
                  :disabled="detailPage <= 1"
                  @click="detailPage = Math.max(1, detailPage - 1)"
                >
                  {{ t('strategy.prevPage') }}
                </UButton>
                <div class="flex items-center gap-1">
                  <UInput
                    v-model="detailPageInput"
                    size="xs"
                    class="w-12 text-center"
                    @keydown.enter="jumpToDetailPage"
                  />
                </div>
                <UButton
                  size="xs"
                  color="gray"
                  variant="soft"
                  :disabled="detailPage >= detailTotalPages"
                  @click="detailPage = Math.min(detailTotalPages, detailPage + 1)"
                >
                  {{ t('strategy.nextPage') }}
                </UButton>
                <UButton
                  size="xs"
                  color="gray"
                  variant="soft"
                  :disabled="detailPage >= detailTotalPages"
                  @click="detailPage = detailTotalPages"
                >
                  &gt;&gt;
                </UButton>
              </div>

              <div v-if="!selectedRunDetail.results || selectedRunDetail.results.length === 0" class="p-8 text-center text-gray-500">
                <div v-if="selectedRunDetail.status === 'completed'">
                  {{ t('strategy.noResults') }}
                </div>
                <div v-else>
                  {{ selectedRunDetail.status === 'pending' || selectedRunDetail.status === 'queued' ? t('strategy.waiting') : t('strategy.running') }}
                </div>
              </div>
          </div>
        </UCard>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, computed } from 'vue'
import axios from 'axios'
import { useI18n } from '../composables/useI18n'

const { t, formatTime } = useI18n()
const emit = defineEmits(['view-stock'])

const columns = [
  { key: 'code', label: t('sidebar.stockCode') },
  { key: 'name', label: 'Name' },
  { key: 'accuracy', label: t('analysis.accuracy') },
  { key: 'signal_type', label: 'Signal' },
  { key: 'latest_date', label: t('analysis.signalDate') }
]

const getStatusLabel = (status) => {
  const st = String(status || '')
  if (st === 'pending' || st === 'queued') return t('strategy.waiting')
  if (st === 'running') return t('strategy.runningShort')
  if (st === 'completed') return t('strategy.completed')
  if (st === 'failed') return t('strategy.failed')
  return st || '-'
}

const getStatusColor = (status) => {
  switch (status) {
    case 'completed': return 'green'
    case 'failed': return 'red'
    case 'running': return 'blue'
    case 'pending': return 'orange'
    case 'queued': return 'orange'
    default: return 'gray'
  }
}


const props = defineProps({
    form: { type: Object, default: () => ({}) },
    chanConfig: { type: Object, default: () => ({}) }
})

// Removed internal state for form and chanConfig
// const form = ref(...)
// const chanConfig = ref(...)
// const selectedPreset = ref('custom')
// const poolOptions = ref([])

const runs = ref([])
const poolMap = ref({})
const selectedRunId = ref(null)
const selectedRunDetail = ref(null)
const running = ref(false)
const listLoading = ref(false)
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const detailPage = ref(1)
const detailPageSize = ref(20)
const pageInput = ref('')
const detailPageInput = ref('')
const selectedRunIds = ref([])
const batchDeleting = ref(false)
const deletingRunId = ref(null)
let pollTimer = null

// Use props in runStrategy
const runStrategy = async () => {
  running.value = true
  try {
    const userName = String(props.form.strategy_name || '').trim()
    const isDefaultName = /^Strategy-\d+$/.test(userName)

    const pid = String(props.form.pool_id || '').trim() || String(props.form.scope || '').trim() || 'HS300'
    const poolLabel = poolMap.value[pid] || pid
    const freqRaw = String(props.form.frequency || '').trim() || '1d'
    const freqLabel = ({ '1d': '日线', '30m': '30分钟', '60m': '60分钟', '5m': '5分钟' }[freqRaw] || freqRaw)

    const modelRaw = String(props.form.model || '').trim() || 'xgboost'
    const modelLabel = ({ xgboost: 'XGB', lightgbm: 'LGBM', mlp: 'MLP' }[modelRaw] || modelRaw)

    const minAcc = Number(props.form.min_accuracy)
    const minAccLabel = Number.isFinite(minAcc) ? `≥${(minAcc * 100).toFixed(0)}%` : ''

    const years = Number(props.form.data_length_years)
    const yearsLabel = Number.isFinite(years) ? `${years.toFixed(1).replace(/\.0$/, '')}y` : ''

    const rollingLabel = props.form.enable_rolling_lookback === false ? '' : '滚动'

    const reqSig = !!props.chanConfig.require_signal
    const sigDir = String(props.chanConfig.signal_direction || '').trim() || 'buy'
    const sigLookback = Number(props.chanConfig.signal_lookback)
    const sigLabel = reqSig ? `${sigDir}@${Number.isFinite(sigLookback) ? sigLookback : 5}` : ''

    const ts = formatTime(new Date().toISOString())

    const nameParts = [
      poolLabel,
      freqLabel,
      modelLabel,
      minAccLabel,
      yearsLabel,
      rollingLabel,
      sigLabel,
      ts
    ].filter(Boolean)

    const strategyName = userName && !isDefaultName ? userName : nameParts.join(' | ')
    const params = {
      ...props.form,
      strategy_name: strategyName,
      chan_config: {
          bi_strict: props.chanConfig.bi_strict,
          trigger_step: props.chanConfig.trigger_step,
          bsp2_follow_1: props.chanConfig.bsp2_follow_1,
          bsp3_follow_1: props.chanConfig.bsp3_follow_1,
          bs_type: props.chanConfig.bs_type,
          macd_algo: props.chanConfig.macd_algo,
          min_zs_cnt: props.chanConfig.min_zs_cnt,
          gap_as_kl: props.chanConfig.gap_as_kl,
          bi_allow_sub_peak: props.chanConfig.bi_allow_sub_peak
      },
      require_signal: props.chanConfig.require_signal,
      signal_lookback: props.chanConfig.signal_lookback,
      signal_direction: props.chanConfig.signal_direction
    }
    const res = await axios.post('/api/strategy/run', params)
    if (res.data.status === 'success') {
      fetchRuns()
    }
  } catch (e) {
    console.error(e)
  } finally {
    running.value = false
  }
}

// Expose runStrategy
defineExpose({ runStrategy })

const formatParams = (params) => {
  if (!params) return ''
  return `Pool: ${params.pool_id || 'HS300'}, Model: ${params.model}, MinAcc: ${params.min_accuracy}`
}

// Removed fetchPools - sidebar handles pool fetching/selection now (or app.vue)
// Actually we kept fetchPools in SidebarContent.vue

const pages = computed(() => {
  const t = Number(total.value || 0)
  const ps = Number(pageSize.value || 20)
  const p = Math.ceil(t / Math.max(1, ps))
  return Math.max(1, p || 1)
})

const detailTotalPages = computed(() => {
  const t = selectedRunDetail.value?.results?.length || 0
  const ps = Number(detailPageSize.value || 20)
  const p = Math.ceil(t / Math.max(1, ps))
  return Math.max(1, p || 1)
})

const paginatedDetailResults = computed(() => {
  const all = selectedRunDetail.value?.results || []
  const ps = Number(detailPageSize.value || 20)
  const p = Math.max(1, detailPage.value)
  const start = (p - 1) * ps
  return all.slice(start, start + ps)
})

const listColumns = computed(() => [
  { key: 'select', label: '', class: 'w-[36px]' },
  { key: 'status', label: t('strategy.status'), class: 'w-[110px]' },
  { key: 'result_count', label: t('strategy.found'), class: 'w-[90px]' },
  { key: 'params', label: t('strategy.params') },
  { key: 'updated_at', label: t('strategy.updatedAt'), class: 'w-[110px]' },
  { key: 'actions', label: t('strategy.actions'), class: 'w-[90px]' }
])

const selectedIdSet = computed(() => new Set(selectedRunIds.value || []))
const selectedCount = computed(() => (selectedRunIds.value || []).length)
const selectableRunIds = computed(() => (runs.value || []).filter(isRunDeletable).map(r => r.id))
const allVisibleSelected = computed(() => {
  const ids = selectableRunIds.value || []
  if (!ids.length) return false
  const set = selectedIdSet.value
  for (const id of ids) {
    if (!set.has(id)) return false
  }
  return true
})

const isRunDeletable = (run) => {
  const st = String(run?.status || '')
  return !['running', 'pending', 'queued'].includes(st)
}

const toggleSelectRun = (id, val) => {
  const rid = Number(id)
  if (!Number.isFinite(rid)) return
  const next = new Set(selectedIdSet.value)
  if (val) next.add(rid)
  else next.delete(rid)
  selectedRunIds.value = Array.from(next)
}

const toggleSelectAllVisible = (val) => {
  const ids = selectableRunIds.value || []
  if (!ids.length) return
  const next = new Set(selectedIdSet.value)
  if (val) {
    for (const id of ids) next.add(id)
  } else {
    for (const id of ids) next.delete(id)
  }
  selectedRunIds.value = Array.from(next)
}

const shortId = (id) => {
  const raw = String(id ?? '')
  if (!raw) return '-'
  return raw.length > 8 ? raw.slice(0, 8) : raw
}

const runParamsLine = (run) => {
  const p = run?.params
  if (!p || typeof p !== 'object') return ''
  return formatParams(p)
}

const fetchRuns = async ({ quiet } = {}) => {
  listLoading.value = !quiet
  try {
    const res = await axios.get('/api/strategy/runs', { params: { page: page.value, page_size: pageSize.value } })
    const data = res.data
    if (Array.isArray(data)) {
      runs.value = data
      total.value = data.length
      page.value = 1
    } else {
      runs.value = data?.items || []
      total.value = data?.total ?? 0
      const p = Number(data?.page || page.value || 1)
      page.value = Number.isFinite(p) ? p : 1
    }

    if (page.value > pages.value) {
      page.value = pages.value
      const res2 = await axios.get('/api/strategy/runs', { params: { page: page.value, page_size: pageSize.value } })
      const data2 = res2.data
      runs.value = data2?.items || []
      total.value = data2?.total ?? total.value
    }
    
    // Check if any run is active, if so, poll
    const hasActive = runs.value.some(r => ['running', 'pending', 'queued'].includes(r.status))
    if (hasActive && !pollTimer) {
      startPolling()
    } else if (!hasActive && pollTimer) {
      stopPolling()
    }
  } catch (e) {
    console.error(e)
  } finally {
    listLoading.value = false
  }
}

const prevPage = () => {
  if (page.value <= 1) return
  page.value -= 1
  fetchRuns()
}

const nextPage = () => {
  if (page.value >= pages.value) return
  page.value += 1
  fetchRuns()
}

const jumpToPage = () => {
  const p = parseInt(pageInput.value)
  if (Number.isFinite(p) && p >= 1 && p <= pages.value) {
    page.value = p
    fetchRuns()
    pageInput.value = ''
  }
}

const jumpToDetailPage = () => {
  const p = parseInt(detailPageInput.value)
  if (Number.isFinite(p) && p >= 1 && p <= detailTotalPages.value) {
    detailPage.value = p
    detailPageInput.value = ''
  }
}

const deleteRun = async (run) => {
  const id = run?.id
  if (!isRunDeletable(run)) return
  if (!confirm(t('strategy.deleteConfirm'))) return
  deletingRunId.value = id
  try {
    await axios.delete(`/api/strategy/runs/${id}`)
    const set = new Set(selectedIdSet.value)
    set.delete(Number(id))
    selectedRunIds.value = Array.from(set)
    if (selectedRunId.value === id) {
      selectedRunId.value = null
      selectedRunDetail.value = null
    }
    if (page.value > pages.value) page.value = pages.value
    await fetchRuns()
  } catch (e) {
    console.error(e)
  } finally {
    deletingRunId.value = null
  }
}

const batchDelete = async () => {
  if (selectedCount.value <= 0) return
  if (!confirm(t('strategy.batchDeleteConfirm'))) return
  batchDeleting.value = true
  try {
    const ids = Array.from(selectedIdSet.value)
    const res = await axios.post('/api/strategy/runs/batch_delete', { run_ids: ids })
    const failed = res.data?.failed || []
    if (failed.length) {
      alert(failed.map(f => `${f.run_id}: ${f.reason || 'failed'}`).join('\n'))
    }
    selectedRunIds.value = []
    if (selectedRunId.value && (res.data?.deleted || []).includes(selectedRunId.value)) {
      selectedRunId.value = null
      selectedRunDetail.value = null
    }
    await fetchRuns()
  } catch (e) {
    console.error(e)
  } finally {
    batchDeleting.value = false
  }
}

const closeDetail = () => {
  selectedRunId.value = null
  selectedRunDetail.value = null
}

const openRun = async (run) => {
  selectedRunId.value = run.id
  detailPage.value = 1
  try {
    const res = await axios.get(`/api/strategy/runs/${run.id}`)
    selectedRunDetail.value = res.data
  } catch (e) {
    console.error(e)
  }
}

const startPolling = () => {
  if (pollTimer) return
  pollTimer = setInterval(() => {
    fetchRuns({ quiet: true })
    if (selectedRunId.value) {
      // Refresh detail if running
      const currentRun = runs.value.find(r => r.id === selectedRunId.value)
      if (currentRun && ['running', 'pending', 'queued'].includes(currentRun.status)) {
        openRun(currentRun)
      }
    }
  }, 3000)
}

const stopPolling = () => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const viewStock = (code) => {
    emit('view-stock', code)
}

onMounted(async () => {
  fetchRuns()
  try {
    const res = await axios.get('/api/stock_pools')
    if (res.data?.items) {
      const map = {}
      for (const p of res.data.items) {
        map[p.pool_id] = `${p.pool_name} (${p.stock_count})`
      }
      poolMap.value = map
    }
  } catch (e) {
    // ignore
  }
})

onUnmounted(() => {
  stopPolling()
})
</script>
