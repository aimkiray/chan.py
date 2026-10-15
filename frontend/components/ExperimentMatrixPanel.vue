<template>
  <div class="h-full flex flex-col bg-white dark:bg-gray-900">
    <div class="p-3 h-full min-h-0 flex flex-col gap-3">
      <div class="shrink-0 rounded border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 p-3">
        <div class="flex flex-col xl:flex-row xl:items-end gap-3">
          <div class="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2 flex-1">
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.startDate') }}</div>
              <UInput v-model="runForm.start_date" size="xs" type="date" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.endDate') }}</div>
              <UInput v-model="runForm.end_date" size="xs" type="date" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.maxDates') }}</div>
              <UInput v-model="runForm.max_dates" size="xs" type="number" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.rollingModes') }}</div>
              <UInput v-model="runForm.rolling_modes" size="xs" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.sideFilters') }}</div>
              <UInput v-model="runForm.side_filters" size="xs" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.lookaheads') }}</div>
              <UInput v-model="runForm.lookaheads" size="xs" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.marketMas') }}</div>
              <UInput v-model="runForm.market_mas" size="xs" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('matrix.limit') }}</div>
              <UInput v-model="runForm.limit" size="xs" type="number" />
            </div>
          </div>

          <div class="flex flex-wrap xl:flex-col gap-2 xl:w-44">
            <UCheckbox v-model="runForm.include_no_market_filter" :label="t('matrix.includeNoMarket')" />
            <UButton size="xs" color="primary" icon="i-heroicons-play" :loading="creating" @click="createMatrix">
              {{ t('matrix.runMatrix') }}
            </UButton>
          </div>
        </div>
      </div>

      <div class="flex-1 min-h-0 grid grid-cols-1 xl:grid-cols-[320px_minmax(0,1fr)] gap-3">
        <div class="rounded border border-gray-200 dark:border-gray-800 min-h-0 flex flex-col overflow-hidden">
          <div class="shrink-0 h-10 px-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between">
            <div class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('matrix.matrixList') }}</div>
            <UButton size="xs" color="gray" variant="ghost" icon="i-heroicons-arrow-path" :loading="listLoading" @click="fetchMatrices" />
          </div>
          <div class="flex-1 min-h-0 overflow-auto">
            <button
              v-for="item in matrices"
              :key="item.matrix_id"
              type="button"
              class="w-full text-left px-3 py-2 border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800"
              :class="selectedMatrixId === item.matrix_id ? 'bg-blue-50 dark:bg-blue-950/40' : ''"
              @click="openMatrix(item.matrix_id)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="font-mono text-xs text-gray-800 dark:text-gray-100 truncate">{{ item.matrix_id }}</span>
                <UBadge size="xs" :color="statusColor(item.status)">{{ statusLabel(item.status) }}</UBadge>
              </div>
              <div class="mt-1 text-[11px] text-gray-500 truncate">
                {{ item.start_date || '-' }} -> {{ item.end_date || '-' }}
              </div>
              <div class="mt-2 flex items-center gap-2">
                <UProgress :value="matrixProgress(item)" size="xs" class="flex-1" />
                <span class="text-[11px] tabular-nums text-gray-500">{{ matrixProgress(item) }}%</span>
              </div>
            </button>
            <div v-if="!matrices.length && !listLoading" class="p-4 text-sm text-gray-400">
              {{ t('matrix.noMatrices') }}
            </div>
          </div>
        </div>

        <div class="rounded border border-gray-200 dark:border-gray-800 min-h-0 flex flex-col overflow-hidden">
          <div class="shrink-0 h-10 px-3 border-b border-gray-200 dark:border-gray-800 flex items-center justify-between gap-2">
            <div class="min-w-0">
              <div class="text-sm font-medium text-gray-700 dark:text-gray-300 truncate">
                {{ detail?.matrix_id || t('matrix.detailTitle') }}
              </div>
            </div>
            <div v-if="detail" class="flex items-center gap-2">
              <USelectMenu
                v-model="selectedLookahead"
                :options="lookaheadOptions"
                value-attribute="value"
                option-attribute="label"
                size="xs"
                class="w-28"
              />
              <UButton size="xs" color="gray" variant="soft" icon="i-heroicons-arrow-path" :loading="detailLoading" @click="openMatrix(detail.matrix_id)">
                {{ t('app.refresh') }}
              </UButton>
              <UButton size="xs" color="gray" variant="soft" :loading="reportLoading" @click="rebuildReport">
                {{ t('matrix.rebuildReport') }}
              </UButton>
              <UButton size="xs" color="primary" variant="soft" :loading="evaluating" @click="evaluateSelected">
                {{ t('matrix.evaluatePending') }}
              </UButton>
            </div>
          </div>

          <div v-if="detailLoading && !detail" class="flex-1 flex items-center justify-center text-gray-400">
            <UIcon name="i-heroicons-arrow-path" class="w-5 h-5 animate-spin mr-2" />
            {{ t('matrix.loading') }}
          </div>

          <div v-else-if="!detail" class="flex-1 flex items-center justify-center text-gray-400">
            {{ t('matrix.selectMatrix') }}
          </div>

          <div v-else class="flex-1 min-h-0 overflow-auto p-3">
            <div class="grid grid-cols-2 md:grid-cols-5 gap-2 mb-3">
              <MetricBox :label="t('matrix.runs')" :value="String(detail.progress?.completed_predictions || 0) + '/' + String(detail.progress?.total_runs || 0)" />
              <MetricBox :label="t('matrix.evaluated')" :value="String(detail.progress?.completed_evaluations || 0)" />
              <MetricBox :label="t('matrix.pending')" :value="String(detail.progress?.pending_evaluations || 0)" />
              <MetricBox :label="t('matrix.bestAlpha')" :value="formatPct(bestAlpha)" :strong="bestAlpha > 0" />
              <MetricBox :label="t('matrix.groups')" :value="String(detail.group_summaries?.length || 0)" />
            </div>

            <div class="flex gap-1 mb-3">
              <UButton size="xs" :color="viewMode === 'heatmap' ? 'primary' : 'gray'" :variant="viewMode === 'heatmap' ? 'solid' : 'soft'" @click="viewMode = 'heatmap'">
                {{ t('matrix.heatmap') }}
              </UButton>
              <UButton size="xs" :color="viewMode === 'runs' ? 'primary' : 'gray'" :variant="viewMode === 'runs' ? 'solid' : 'soft'" @click="viewMode = 'runs'">
                {{ t('matrix.runs') }}
              </UButton>
              <UButton size="xs" :color="viewMode === 'report' ? 'primary' : 'gray'" :variant="viewMode === 'report' ? 'solid' : 'soft'" @click="viewMode = 'report'">
                {{ t('matrix.report') }}
              </UButton>
            </div>

            <div v-if="viewMode === 'heatmap'" class="overflow-auto">
              <table class="min-w-full text-xs border border-gray-200 dark:border-gray-800">
                <thead class="bg-gray-50 dark:bg-gray-950">
                  <tr>
                    <th class="p-2 text-left border-b border-gray-200 dark:border-gray-800 w-28">{{ t('matrix.side') }}</th>
                    <th
                      v-for="col in heatmapColumns"
                      :key="col.key"
                      class="p-2 text-left border-b border-l border-gray-200 dark:border-gray-800 whitespace-nowrap"
                    >
                      {{ col.label }}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="side in sideRows" :key="side">
                    <td class="p-2 border-t border-gray-100 dark:border-gray-800 font-medium">{{ sideLabel(side) }}</td>
                    <td
                      v-for="col in heatmapColumns"
                      :key="side + col.key"
                      class="p-2 border-t border-l border-gray-100 dark:border-gray-800 cursor-pointer min-w-[110px]"
                      :class="cellClass(findGroup(side, col))"
                      @click="selectGroup(findGroup(side, col))"
                    >
                      <template v-if="findGroup(side, col)">
                        <template v-if="isPendingGroup(findGroup(side, col))">
                          <div class="font-semibold">{{ t('matrix.pendingCell') }}</div>
                          <div class="text-[11px] opacity-75">
                            {{ t('matrix.coverage') }} {{ formatUnsignedPct(findGroup(side, col).coverage) }}
                          </div>
                        </template>
                        <template v-else>
                          <div class="font-semibold tabular-nums">{{ formatPct(findGroup(side, col).alpha) }}</div>
                          <div class="text-[11px] opacity-75">{{ findGroup(side, col).trade_count }} {{ t('matrix.trades') }}</div>
                          <div class="text-[11px] opacity-75">{{ t('matrix.avgNet') }} {{ formatPct(findGroup(side, col).avg_net_return) }} · {{ t('matrix.profitFactor') }} {{ formatProfitFactor(findGroup(side, col)) }}</div>
                        </template>
                      </template>
                      <span v-else class="text-gray-400">-</span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-else-if="viewMode === 'runs'" class="overflow-auto">
              <UTable
                :rows="filteredRuns"
                :columns="runColumns"
                class="w-full"
                :ui="{ th: { base: 'whitespace-nowrap', padding: 'px-3 py-2' }, td: { padding: 'px-3 py-2' } }"
              >
                <template #alpha-data="{ row }">
                  <span :class="pctClass(row.alpha)">
                    {{ formatPct(row.alpha) }}
                  </span>
                </template>
                <template #accuracy-data="{ row }">
                  <span class="tabular-nums">{{ formatPct(row.accuracy) }}</span>
                </template>
                <template #baseline_accuracy-data="{ row }">
                  <span class="tabular-nums">{{ formatPct(row.baseline_accuracy) }}</span>
                </template>
                <template #avg_net_return-data="{ row }">
                  <span :class="pctClass(row.avg_net_return)">{{ formatPct(row.avg_net_return) }}</span>
                </template>
                <template #profit_factor-data="{ row }">
                  <span class="tabular-nums">{{ formatProfitFactor(row) }}</span>
                </template>
                <template #max_drawdown-data="{ row }">
                  <span class="tabular-nums text-red-500">{{ formatUnsignedPct(row.max_drawdown) }}</span>
                </template>
                <template #status-data="{ row }">
                  <UBadge size="xs" :color="row.status === 'evaluated' ? 'green' : 'gray'">{{ runStatusLabel(row.status) }}</UBadge>
                </template>
              </UTable>
            </div>

            <pre v-else class="text-xs whitespace-pre-wrap rounded border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 p-3 text-gray-700 dark:text-gray-200 overflow-auto max-h-[65vh]">{{ detail.report_markdown || t('matrix.noReport') }}</pre>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, h, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import axios from 'axios'
import { useI18n } from '../composables/useI18n'

const { t, has } = useI18n()
const toast = useToast()

const MetricBox = (props) => h(
  'div',
  { class: 'rounded border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 p-2 min-w-0' },
  [
    h('div', { class: 'text-[11px] text-gray-500 truncate' }, props.label),
    h('div', { class: ['text-sm tabular-nums truncate', props.strong ? 'font-semibold text-green-600 dark:text-green-400' : 'font-medium text-gray-800 dark:text-gray-100'] }, props.value || '-')
  ]
)

const listLoading = ref(false)
const detailLoading = ref(false)
const creating = ref(false)
const evaluating = ref(false)
const reportLoading = ref(false)
const matrices = ref([])
const detail = ref(null)
const selectedMatrixId = ref('')
const selectedGroupKey = ref('')
const selectedLookahead = ref(null)
const viewMode = ref('heatmap')
let jobPollTimer = null
let jobPollId = null
let jobPollFailures = 0
const maxJobPollFailures = 5
let liveDetailRefreshInFlight = false

const runForm = ref({
  start_date: '',
  end_date: '',
  max_dates: 5,
  rolling_modes: 'none,bsp',
  side_filters: 'buy,sell,both',
  lookaheads: '1,3',
  market_mas: '20,60',
  include_no_market_filter: true,
  limit: 0
})

const parseCsv = (raw) => String(raw || '')
  .split(',')
  .map((x) => x.trim())
  .filter(Boolean)

const parseIntCsv = (raw) => parseCsv(raw)
  .map((x) => Number.parseInt(x, 10))
  .filter((x) => Number.isFinite(x) && x > 0)

const axiosError = (e) => String(e?.response?.data?.detail || e?.message || t('app.networkError'))

const matrixProgress = (item) => {
  const p = item?.progress || {}
  const total = Number(p.total_runs || 0)
  const done = Number(p.completed_predictions || 0)
  if (total <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)))
}

const statusColor = (status) => {
  const s = String(status || '')
  if (['completed', 'success'].includes(s)) return 'green'
  if (['running', 'evaluating', 'queued'].includes(s)) return 'blue'
  if (['pending_evaluation', 'partial'].includes(s)) return 'amber'
  if (s === 'error') return 'red'
  return 'gray'
}

const statusLabel = (status) => {
  const s = String(status || '')
  const key = `matrix.status.${s}`
  // t() returns the key itself for missing keys, so `|| s` would be dead code;
  // has() is the reliable guard.
  return has(key) ? t(key) : (s || '-')
}

const isActiveJobStatus = (status) => ['queued', 'running', 'evaluating'].includes(String(status || ''))

const startPollingIfActive = (item) => {
  const job = item?.job || item
  if (job?.job_id && isActiveJobStatus(job.status || item?.status)) {
    pollJob(job.job_id)
  }
}

const fetchMatrices = async (options = {}) => {
  const silent = !!options.silent
  if (!silent) listLoading.value = true
  try {
    const res = await axios.get('/api/experiments/matrix')
    matrices.value = res.data?.items || []
    if (!selectedMatrixId.value && matrices.value.length) {
      await openMatrix(matrices.value[0].matrix_id)
    }
    const selected = matrices.value.find((item) => item.matrix_id === selectedMatrixId.value)
    startPollingIfActive(selected)
  } catch (e) {
    if (!silent) toast.add({ title: t('matrix.loadFailed'), description: axiosError(e), color: 'red' })
  } finally {
    if (!silent) listLoading.value = false
  }
}

// Out-of-order guard: each openMatrix call gets a sequence token; only the
// latest request may touch `detail` so a slow response for matrix A can't
// overwrite the view after the user already selected matrix B. The spinner is
// owned by the latest non-silent request (openMatrixLoadingSeq) so a silent
// poll refresh neither freezes nor prematurely clears it.
let openMatrixSeq = 0
let openMatrixLoadingSeq = 0
const openMatrix = async (matrixId, options = {}) => {
  if (!matrixId) return
  const silent = !!options.silent
  const preserveSelection = !!options.preserveSelection
  const seq = ++openMatrixSeq
  selectedMatrixId.value = matrixId
  if (!silent) {
    openMatrixLoadingSeq = seq
    detailLoading.value = true
  }
  try {
    const res = await axios.get(`/api/experiments/matrix/${matrixId}`)
    if (seq !== openMatrixSeq || matrixId !== selectedMatrixId.value) return
    detail.value = res.data || null
    if (!preserveSelection) selectedGroupKey.value = ''
    const firstLookahead = lookaheadOptions.value[0]?.value
    if (!selectedLookahead.value && firstLookahead !== undefined) selectedLookahead.value = firstLookahead
    startPollingIfActive(detail.value)
  } catch (e) {
    if (!silent && seq === openMatrixSeq) toast.add({ title: t('matrix.loadFailed'), description: axiosError(e), color: 'red' })
  } finally {
    if (!silent && seq === openMatrixLoadingSeq) detailLoading.value = false
  }
}

const refreshLiveMatrix = async (matrixId) => {
  if (!matrixId || liveDetailRefreshInFlight) return
  liveDetailRefreshInFlight = true
  try {
    await openMatrix(matrixId, { silent: true, preserveSelection: true })
  } finally {
    liveDetailRefreshInFlight = false
  }
}

const createPayload = () => {
  const payload = {
    rolling_modes: parseCsv(runForm.value.rolling_modes),
    side_filters: parseCsv(runForm.value.side_filters),
    lookaheads: parseIntCsv(runForm.value.lookaheads),
    market_mas: parseIntCsv(runForm.value.market_mas),
    include_no_market_filter: !!runForm.value.include_no_market_filter,
    max_dates: Number.parseInt(runForm.value.max_dates || 0, 10) || 0,
    limit: Number.parseInt(runForm.value.limit || 0, 10) || 0
  }
  if (runForm.value.start_date && runForm.value.end_date) {
    payload.start_date = runForm.value.start_date
    payload.end_date = runForm.value.end_date
  }
  return payload
}

const createMatrix = async () => {
  const dateRe = /^\d{4}-\d{2}-\d{2}$/
  const sd = String(runForm.value.start_date || '').trim()
  const ed = String(runForm.value.end_date || '').trim()
  if ((sd && !dateRe.test(sd)) || (ed && !dateRe.test(ed))) {
    toast.add({ title: t('matrix.invalidDateFormat'), color: 'red' })
    return
  }
  creating.value = true
  try {
    const res = await axios.post('/api/experiments/matrix', createPayload())
    selectedMatrixId.value = res.data?.matrix_id || ''
    toast.add({ title: t('matrix.jobQueued'), description: selectedMatrixId.value, color: 'blue' })
    await fetchMatrices()
    if (selectedMatrixId.value) await openMatrix(selectedMatrixId.value)
    pollJob(res.data?.job_id)
  } catch (e) {
    toast.add({ title: t('matrix.runFailed'), description: axiosError(e), color: 'red' })
  } finally {
    creating.value = false
  }
}

const evaluateSelected = async () => {
  if (!detail.value?.matrix_id) return
  evaluating.value = true
  try {
    const res = await axios.post(`/api/experiments/matrix/${detail.value.matrix_id}/evaluate`, {})
    toast.add({ title: t('matrix.jobQueued'), description: t('matrix.evaluatePending'), color: 'blue' })
    pollJob(res.data?.job_id)
  } catch (e) {
    toast.add({ title: t('matrix.evaluateFailed'), description: axiosError(e), color: 'red' })
  } finally {
    evaluating.value = false
  }
}

const rebuildReport = async () => {
  if (!detail.value?.matrix_id) return
  reportLoading.value = true
  try {
    const res = await axios.post(`/api/experiments/matrix/${detail.value.matrix_id}/report`, {})
    detail.value = res.data || detail.value
    toast.add({ title: t('matrix.reportRebuilt'), color: 'green' })
  } catch (e) {
    toast.add({ title: t('matrix.reportFailed'), description: axiosError(e), color: 'red' })
  } finally {
    reportLoading.value = false
  }
}

const applyJobSnapshot = (job) => {
  if (!job?.matrix_id) return
  matrices.value = (matrices.value || []).map((item) => {
    if (item.matrix_id !== job.matrix_id) return item
    return {
      ...item,
      status: job.status || item.status,
      progress: job.matrix_progress || item.progress,
      job: {
        job_id: job.job_id,
        status: job.status,
        stage: job.stage,
        progress: job.progress,
        message: job.message
      }
    }
  })
  if (detail.value?.matrix_id === job.matrix_id) {
    detail.value = {
      ...detail.value,
      status: job.status || detail.value.status,
      progress: job.matrix_progress || detail.value.progress,
      job: {
        job_id: job.job_id,
        status: job.status,
        stage: job.stage,
        progress: job.progress,
        message: job.message
      }
    }
  }
}

const pollJob = (jobId) => {
  if (!jobId) return
  if (jobPollId === jobId && jobPollTimer) return
  if (jobPollTimer) clearInterval(jobPollTimer)
  jobPollId = jobId
  jobPollFailures = 0
  jobPollTimer = setInterval(async () => {
    try {
      const res = await axios.get(`/api/experiments/matrix/jobs/${jobId}`)
      jobPollFailures = 0
      const job = res.data || {}
      applyJobSnapshot(job)
      if (job.matrix_id && selectedMatrixId.value === job.matrix_id) {
        await refreshLiveMatrix(job.matrix_id)
      }
      if (['success', 'partial', 'error'].includes(String(job.status || ''))) {
        clearInterval(jobPollTimer)
        jobPollTimer = null
        jobPollId = null
        jobPollFailures = 0
        await fetchMatrices()
        if (selectedMatrixId.value) await openMatrix(selectedMatrixId.value)
        const isSuccess = job.status === 'success'
        const isPartial = job.status === 'partial'
        toast.add({
          title: isSuccess ? t('matrix.jobDone') : (isPartial ? t('matrix.jobPartial') : t('matrix.jobFailed')),
          description: job.detail || job.matrix_id,
          color: isSuccess ? 'green' : (isPartial ? 'amber' : 'red')
        })
      }
    } catch (e) {
      jobPollFailures += 1
      if (jobPollFailures >= maxJobPollFailures) {
        clearInterval(jobPollTimer)
        jobPollTimer = null
        jobPollId = null
        jobPollFailures = 0
        toast.add({ title: t('matrix.loadFailed'), description: axiosError(e), color: 'amber' })
      }
    }
  }, 2500)
}

const lookaheadOptions = computed(() => {
  const values = new Set()
  for (const g of detail.value?.group_summaries || []) {
    if (g.lookahead !== null && g.lookahead !== undefined) values.add(Number(g.lookahead))
  }
  return Array.from(values).sort((a, b) => a - b).map((v) => ({ label: `${v}D`, value: v }))
})

watch(lookaheadOptions, (opts) => {
  if (!opts?.length) {
    selectedLookahead.value = null
    return
  }
  if (!opts.some((o) => o.value === selectedLookahead.value)) selectedLookahead.value = opts[0].value
})

const groupsForLookahead = computed(() => {
  const lh = Number(selectedLookahead.value)
  return (detail.value?.group_summaries || []).filter((g) => Number(g.lookahead) === lh)
})

const bestAlpha = computed(() => {
  const vals = groupsForLookahead.value.map((g) => Number(g.alpha)).filter((v) => Number.isFinite(v))
  return vals.length ? Math.max(...vals) : null
})

const sideRows = computed(() => {
  const order = ['buy', 'sell', 'both']
  const present = new Set(groupsForLookahead.value.map((g) => String(g.side_filter || '')))
  return order.filter((x) => present.has(x))
})

const heatmapColumns = computed(() => {
  const map = new Map()
  for (const g of groupsForLookahead.value) {
    const market = g.market_filter_enabled ? `MA${g.market_ma}` : t('matrix.noMarket')
    const key = `${g.rolling_mode}|${g.market_filter_enabled ? g.market_ma : 0}`
    if (!map.has(key)) {
      map.set(key, {
        key,
        rolling_mode: g.rolling_mode,
        market_ma: g.market_filter_enabled ? Number(g.market_ma || 0) : 0,
        market_filter_enabled: !!g.market_filter_enabled,
        label: `${g.rolling_mode} / ${market}`
      })
    }
  }
  return Array.from(map.values()).sort((a, b) => String(a.label).localeCompare(String(b.label)))
})

const sideLabel = (side) => {
  if (side === 'buy') return t('matrix.buyOnly')
  if (side === 'sell') return t('matrix.sellOnly')
  if (side === 'both') return t('matrix.both')
  return side || '-'
}

const groupKey = (g) => {
  if (!g) return ''
  return `${g.side_filter}|${g.rolling_mode}|${g.market_filter_enabled ? g.market_ma : 0}|${g.lookahead}`
}

const findGroup = (side, col) => groupsForLookahead.value.find((g) => (
  String(g.side_filter) === String(side) &&
  String(g.rolling_mode) === String(col.rolling_mode) &&
  Boolean(g.market_filter_enabled) === Boolean(col.market_filter_enabled) &&
  Number(g.market_filter_enabled ? g.market_ma : 0) === Number(col.market_filter_enabled ? col.market_ma : 0)
))

const selectGroup = (g) => {
  if (!g) return
  selectedGroupKey.value = groupKey(g)
  viewMode.value = 'runs'
}

const filteredRuns = computed(() => {
  const rows = detail.value?.runs || []
  if (!selectedGroupKey.value) return rows
  return rows.filter((r) => {
    const key = `${r.side_filter}|${r.rolling_mode}|${r.market_filter_enabled ? r.market_ma : 0}|${r.lookahead}`
    return key === selectedGroupKey.value
  })
})

const cellClass = (g) => {
  if (isPendingGroup(g)) return 'bg-gray-50 dark:bg-gray-900 text-gray-400'
  const v = Number(g.alpha)
  if (v >= 0.05) return 'bg-green-600 text-white'
  if (v >= 0.0) return 'bg-green-50 dark:bg-green-950/40 text-green-700 dark:text-green-300'
  if (v <= -0.05) return 'bg-red-600 text-white'
  return 'bg-red-50 dark:bg-red-950/40 text-red-700 dark:text-red-300'
}

const isEmptyMetric = (value) => value === null || value === undefined || value === ''

const isPendingGroup = (g) => !g || Number(g.evaluated_count || 0) <= 0 || isEmptyMetric(g.alpha)

const formatPct = (value) => {
  if (isEmptyMetric(value)) return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  const sign = n > 0 ? '+' : ''
  return `${sign}${(n * 100).toFixed(2)}%`
}

const formatUnsignedPct = (value) => {
  if (isEmptyMetric(value)) return '-'
  const n = Number(value)
  if (!Number.isFinite(n)) return '-'
  return `${(n * 100).toFixed(2)}%`
}

const formatProfitFactor = (row) => {
  if (!row) return '-'
  if (row.profit_factor_is_infinite) return 'inf'
  if (isEmptyMetric(row.profit_factor)) return '-'
  const n = Number(row.profit_factor)
  if (!Number.isFinite(n)) return '-'
  return n.toFixed(3)
}

const pctClass = (value) => {
  if (isEmptyMetric(value)) return 'text-gray-400'
  const n = Number(value)
  if (!Number.isFinite(n)) return 'text-gray-400'
  if (n > 0) return 'text-green-600 dark:text-green-400 font-medium'
  if (n < 0) return 'text-red-600 dark:text-red-400'
  return 'text-gray-500 dark:text-gray-400'
}

const runStatusLabel = (status) => {
  const s = String(status || '')
  if (s === 'evaluated') return t('matrix.status.evaluated')
  if (s === 'pending') return t('matrix.status.pending')
  return s || '-'
}

const runColumns = computed(() => [
  { key: 'date', label: t('matrix.date') },
  { key: 'target_date', label: t('matrix.targetDate') },
  { key: 'side_filter', label: t('matrix.side') },
  { key: 'rolling_mode', label: t('matrix.rolling') },
  { key: 'market_ma', label: t('matrix.marketMa') },
  { key: 'trade_count', label: t('matrix.trades') },
  { key: 'accuracy', label: t('matrix.accuracy') },
  { key: 'baseline_accuracy', label: t('matrix.baseline') },
  { key: 'alpha', label: t('matrix.alpha') },
  { key: 'avg_net_return', label: t('matrix.avgNet') },
  { key: 'profit_factor', label: t('matrix.profitFactor') },
  { key: 'max_drawdown', label: t('matrix.maxDrawdown') },
  { key: 'status', label: t('matrix.statusLabel') }
])

onMounted(fetchMatrices)

onBeforeUnmount(() => {
  if (jobPollTimer) clearInterval(jobPollTimer)
  jobPollId = null
  jobPollFailures = 0
})
</script>
