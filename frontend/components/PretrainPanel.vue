<template>
  <div class="h-full flex flex-col">
    <div class="p-2 border-b border-gray-100 dark:border-gray-800 flex justify-between items-center bg-gray-50 dark:bg-gray-900">
      <h3 class="text-base font-bold text-gray-700 dark:text-gray-200 m-0">{{ t('pretrain.title') }}</h3>
      <div class="flex items-center gap-2">
        <UButton size="sm" color="primary" :loading="loading" @click="runPretrain">
          {{ t('pretrain.run') }}
        </UButton>
      </div>
    </div>

    <div class="flex-1 overflow-auto p-3">
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <UCard :ui="{ body: { padding: 'p-3' } }" class="shadow-none border border-gray-200 dark:border-gray-800">
          <div class="space-y-4">
            <div>
              <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.stockPool') }}</div>
              <div class="flex flex-col sm:flex-row gap-2">
                <USelectMenu
                  v-model="selectedPoolId"
                  :options="poolOptions"
                  value-attribute="value"
                  option-attribute="label"
                  size="sm"
                  class="flex-1"
                  :disabled="dataSrc !== 'clickhouse' || poolsLoading"
                  :placeholder="dataSrc !== 'clickhouse' ? t('pretrain.poolClickhouseOnly') : t('pretrain.poolPlaceholder')"
                />
                <UButton
                  size="sm"
                  color="gray"
                  :loading="poolMembersLoading"
                  :disabled="dataSrc !== 'clickhouse' || !selectedPoolId"
                  @click="fillCodesFromPool"
                >
                  {{ t('pretrain.loadPool') }}
                </UButton>
              </div>
              <div class="mt-2 flex items-center justify-between gap-2">
                <UCheckbox v-model="overwriteCodes" :label="t('pretrain.overwriteCodes')" />
                <div class="text-xs text-gray-500">
                  <span v-if="poolsLoading">{{ t('pretrain.loadingPools') }}</span>
                  <span v-else-if="poolMetaLabel">{{ poolMetaLabel }}</span>
                </div>
              </div>
            </div>

            <div>
              <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.codes') }}</div>
              <textarea
                v-model="codesText"
                class="w-full min-h-[120px] rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-2 text-sm text-gray-800 dark:text-gray-200 outline-none focus:ring-2 focus:ring-blue-500/40"
                :placeholder="t('pretrain.codesPlaceholder')"
              />
              <div class="mt-1 text-xs text-gray-500">{{ t('pretrain.codesHint') }}</div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.frequency') }}</div>
                <USelectMenu v-model="frequency" :options="frequencyOptions" value-attribute="value" option-attribute="label" size="sm" />
              </div>
              <div>
                <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.model') }}</div>
                <USelectMenu v-model="model" :options="modelOptions" value-attribute="value" option-attribute="label" size="sm" />
              </div>
            </div>

            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.dataSrc') }}</div>
                <USelectMenu v-model="dataSrc" :options="dataSrcOptions" value-attribute="value" option-attribute="label" size="sm" />
              </div>
              <div>
                <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.dataLength') }}</div>
                <div class="flex items-center gap-2">
                  <USelectMenu v-model="dataLengthMode" :options="dataLengthModeOptions" value-attribute="value" option-attribute="label" size="sm" class="w-40" />
                  <UInput
                    v-model="dataLengthYears"
                    type="number"
                    step="0.1"
                    min="0.1"
                    placeholder="5.0"
                    size="sm"
                    class="flex-1"
                    :disabled="dataLengthMode !== 'years'"
                  />
                </div>
              </div>
            </div>

            <div class="flex items-center justify-between gap-2">
              <UCheckbox v-model="forceRefresh" :label="t('pretrain.forceRefresh')" />
            </div>

            <div class="text-xs text-gray-500">
              {{ t('pretrain.note') }}
            </div>
          </div>
        </UCard>

        <UCard :ui="{ body: { padding: 'p-3' } }" class="shadow-none border border-gray-200 dark:border-gray-800 flex flex-col min-h-0">
          <div class="flex items-center justify-between gap-2">
            <div class="flex items-center gap-2 min-w-0">
              <UButton
                v-if="selectedJob"
                size="xs"
                color="gray"
                variant="ghost"
                icon="i-heroicons-arrow-left"
                :label="t('pretrain.back')"
                @click="closeDetail"
              />
              <div class="text-sm font-medium text-gray-700 dark:text-gray-300 truncate">
                {{ selectedJob ? t('pretrain.detailTitle') : t('pretrain.listTitle') }}
              </div>
            </div>
            <UButton v-if="!selectedJob" size="xs" color="gray" :loading="listLoading" @click="refreshLists">
              {{ t('pretrain.refreshList') }}
            </UButton>
          </div>

          <div v-if="!selectedJob" class="mt-2">
            <UTable
              :rows="listRows"
              :columns="listColumns"
              :loading="listLoading"
              class="w-full"
              :ui="{ th: { base: 'whitespace-nowrap', padding: 'px-3 py-2' }, td: { padding: 'px-3 py-2' } }"
            >
              <template #combined-data="{ row }">
                <div class="flex items-center justify-left min-w-[40px]">
                  <div
                    v-if="row.kind === 'job' && (row.status === 'running' || row.status === 'queued') && progressPct(row) < 100"
                    class="relative w-5 h-5 flex items-center justify-left"
                    :title="stageLabel(row.stage) + (showProgressPct(row) ? ` ${progressPct(row)}%` : '')"
                  >
                    <svg class="w-5 h-5 absolute inset-0 text-primary-500 transform -rotate-90" viewBox="0 0 36 36">
                      <circle cx="18" cy="18" r="16" fill="none" stroke="currentColor" stroke-opacity="0.2" stroke-width="4" />
                      <circle
                        cx="18"
                        cy="18"
                        r="16"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="4"
                        stroke-linecap="round"
                        :stroke-dasharray="`${circleCirc} ${circleCirc}`"
                        :stroke-dashoffset="circleOffset(progressPct(row))"
                      />
                    </svg>
                  </div>
                  <UBadge
                    v-else-if="row.status === 'success'"
                    size="xs"
                    color="green"
                    variant="subtle"
                    class="px-1 py-0 text-[10px]"
                  >
                    {{ t('pretrain.statusSuccess') }}
                  </UBadge>
                  <UBadge
                    v-else-if="row.status === 'error'"
                    size="xs"
                    color="red"
                    variant="subtle"
                    class="px-1 py-0 text-[10px]"
                  >
                    {{ t('pretrain.statusError') }}
                  </UBadge>
                  <span v-else class="text-xs text-gray-400">-</span>
                </div>
              </template>

              <template #params-data="{ row }">
                <div class="min-w-0">
                  <div class="flex items-center gap-2">
                    <span class="text-xs font-medium text-gray-700 dark:text-gray-300 truncate" :title="row.display_name">
                      {{ row.display_name || '-' }}
                    </span>
                    <span class="text-[10px] text-gray-400 font-mono">{{ row.id }}</span>
                  </div>
                  <div class="text-[11px] text-gray-500 dark:text-gray-400 whitespace-nowrap flex items-center gap-1">
                    <span>{{ row.model_type }}</span>
                    <span>·</span>
                    <span>{{ row.frequency }}</span>
                    <template v-if="row.feature_count">
                      <span>·</span>
                      <span>F{{ row.feature_count }}</span>
                    </template>
                    <template v-if="dataLengthShort(row)">
                      <span>·</span>
                      <span>{{ dataLengthShort(row) }}</span>
                    </template>
                  </div>
                </div>
              </template>

              <template #updated_at-data="{ row }">
                <span class="text-xs text-gray-500 whitespace-nowrap" :title="row.updated_at || ''">{{ formatTime(row.updated_at) }}</span>
              </template>

              <template #actions-data="{ row }">
                <UButton size="xs" variant="link" color="primary" @click="openItem(row)">
                  {{ t('pretrain.viewDetail') }}
                </UButton>
                <UButton
                  v-if="row.kind === 'model'"
                  size="xs"
                  variant="link"
                  color="gray"
                  @click="openRename(row)"
                >
                  {{ t('pretrain.rename') }}
                </UButton>
              </template>
            </UTable>
            <div v-if="modelsTotalPages > 1" class="mt-2 flex items-center justify-end gap-2">
              <span class="text-xs text-gray-500">
                {{
                  t('pretrain.pageInfo')
                    .replace('{page}', String(modelsPage))
                    .replace('{pages}', String(modelsTotalPages))
                    .replace('{total}', String(modelsTotal))
                }}
              </span>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="modelsPage <= 1"
                @click="changeModelsPage(modelsPage - 1)"
              >
                {{ t('pretrain.prevPage') }}
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="modelsPage >= modelsTotalPages"
                @click="changeModelsPage(modelsPage + 1)"
              >
                {{ t('pretrain.nextPage') }}
              </UButton>
            </div>
          </div>

          <div v-else class="mt-3 border-t border-gray-100 dark:border-gray-800 pt-3 flex-1 min-h-0 overflow-auto">
            <div
              v-if="selectedJob && (selectedJob.status === 'running' || selectedJob.status === 'queued') && progressPct(selectedJob) < 100"
              class="flex items-start justify-between gap-3"
            >
              <div class="flex items-center gap-3 min-w-0">
                <svg class="w-8 h-8 shrink-0 text-primary-500" viewBox="0 0 36 36">
                  <circle cx="18" cy="18" r="16" fill="none" stroke="currentColor" stroke-opacity="0.15" stroke-width="4" />
                  <circle
                    cx="18"
                    cy="18"
                    r="16"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="4"
                    stroke-linecap="round"
                    :stroke-dasharray="`${circleCirc} ${circleCirc}`"
                    :stroke-dashoffset="circleOffset(progressPct(selectedJob))"
                    transform="rotate(-90 18 18)"
                  />
                </svg>
                <div class="min-w-0">
                  <div class="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
                    <span class="truncate">{{ stageLabel(selectedJob.stage) }}</span>
                    <span v-if="showProgressPct(selectedJob)">{{ progressPct(selectedJob) }}%</span>
                  </div>
                  <div v-if="selectedJob.message" class="text-xs text-gray-500 truncate">{{ selectedJob.message }}</div>
                </div>
              </div>
            </div>

            <div v-else-if="detailResult" class="space-y-3">
              <UAlert
                v-if="detailResult.status === 'success'"
                color="green"
                variant="subtle"
                icon="i-heroicons-check-circle"
                :title="t('pretrain.success')"
              />
              <UAlert
                v-else
                color="red"
                variant="subtle"
                icon="i-heroicons-x-circle"
                :title="t('pretrain.failed')"
                :description="detailResult?.detail || detailResult?.message || ''"
              />

              <div v-if="detailResult.status === 'success'" class="text-sm">
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <div class="text-gray-500">{{ t('pretrain.modelName') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ detailResult.display_name || '-' }}</div>

                  <div class="text-gray-500">{{ t('pretrain.bundlePath') }}</div>
                  <div class="font-mono text-xs break-all text-gray-800 dark:text-gray-200">{{ detailResult.bundle_path }}</div>

                  <div class="text-gray-500">{{ t('pretrain.sampleCount') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ detailResult.sample_count }}</div>

                  <div class="text-gray-500">{{ t('pretrain.featureCount') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ detailResult.feature_count }}</div>

                  <div class="text-gray-500">{{ t('pretrain.model') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ detailResult.model_type }}</div>

                  <div class="text-gray-500">{{ t('pretrain.frequency') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ detailResult.frequency }}</div>

                  <div class="text-gray-500">{{ t('pretrain.dataSrc') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ detailResult.data_src }}</div>
                </div>
              </div>

              <div v-if="detailResult.status === 'success' && detailResult.accuracy" class="text-sm border-t border-gray-100 dark:border-gray-800 pt-3">
                <div class="font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.metrics') }}</div>
                <div class="grid grid-cols-2 gap-2">
                  <div class="text-gray-500">{{ t('analysis.accuracy') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">
                    {{ detailResult.accuracy.valid_count }}/{{ detailResult.accuracy.total_count }}
                    ({{ (Number(detailResult.accuracy.accuracy || 0) * 100).toFixed(1) }}%)
                  </div>

                  <div class="text-gray-500">{{ t('analysis.brier') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ Number(detailResult.accuracy.brier_score || 0).toFixed(4) }}</div>

                  <div class="text-gray-500">{{ t('analysis.ece') }}</div>
                  <div class="text-gray-800 dark:text-gray-200">{{ Number(detailResult.accuracy.ece || 0).toFixed(4) }}</div>
                </div>
              </div>

              <div v-if="detailResult.status === 'success' && perCodeRows.length" class="border-t border-gray-100 dark:border-gray-800 pt-3">
                <div class="font-medium text-gray-700 dark:text-gray-300 mb-2 text-sm">{{ t('pretrain.perCodeSamples') }}</div>
                <UTable
                  :rows="perCodeRows"
                  :columns="perCodeColumns"
                  class="w-full"
                  :ui="{ th: { base: 'whitespace-nowrap', padding: 'px-3 py-2' }, td: { padding: 'px-3 py-2' } }"
                />
              </div>
            </div>

            <div v-else class="h-full flex items-center justify-center text-sm text-gray-400">
              {{ t('pretrain.selectToView') }}
            </div>
          </div>
        </UCard>
      </div>
    </div>
  </div>

  <UModal v-model="renameOpen">
    <UCard :ui="{ body: { padding: 'p-4' } }">
      <div class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">{{ t('pretrain.renameTitle') }}</div>
      <div class="space-y-3">
        <UInput v-model="renameValue" :placeholder="t('pretrain.renamePlaceholder')" />
        <div class="flex justify-end gap-2">
          <UButton size="sm" color="gray" variant="ghost" :disabled="renameSaving" @click="renameOpen = false">
            {{ t('pretrain.cancel') }}
          </UButton>
          <UButton size="sm" color="primary" :loading="renameSaving" @click="saveRename">
            {{ t('pretrain.save') }}
          </UButton>
        </div>
      </div>
    </UCard>
  </UModal>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()
const toast = useToast()

const props = defineProps({
  defaultDataSrc: {
    type: String,
    default: 'clickhouse'
  },
  defaultModel: {
    type: String,
    default: 'xgboost'
  }
})

const loading = ref(false)
const result = ref(null)

const jobId = ref('')
let jobPollTimer = null
let listPollTimer = null
let lastModelsFetchAt = 0

const codesText = ref('')
const frequency = ref('1d')
const dataSrc = ref(props.defaultDataSrc || 'clickhouse')
const model = ref(props.defaultModel || 'xgboost')

const dataLengthMode = ref('years')
const dataLengthYears = ref(5.0)

const poolsLoading = ref(false)
const poolMembersLoading = ref(false)
const pools = ref([])
const selectedPoolId = ref('')
const overwriteCodes = ref(true)
const forceRefresh = ref(false)

const listLoading = ref(false)
const jobItems = ref([])
const modelItems = ref([])
const modelsPage = ref(1)
const modelsPageSize = ref(20)
const modelsTotal = ref(0)

const renameOpen = ref(false)
const renameSaving = ref(false)
const renameKey = ref('')
const renameValue = ref('')

const modelsTotalPages = computed(() => {
  const ps = Number(modelsPageSize.value || 20)
  const total = Number(modelsTotal.value || 0)
  if (!Number.isFinite(ps) || ps <= 0) return 1
  if (!Number.isFinite(total) || total <= 0) return 1
  return Math.max(1, Math.ceil(total / ps))
})

const selectedJob = ref(null)
const detailResult = ref(null)

if (typeof window !== 'undefined') {
  const saved = localStorage.getItem('pretrain.codesText')
  if (saved) codesText.value = saved
}

watch(codesText, (v) => {
  if (typeof window === 'undefined') return
  localStorage.setItem('pretrain.codesText', v || '')
})

const frequencyOptions = computed(() => [
  { label: t('periods.1d'), value: '1d' },
  { label: t('periods.1w'), value: '1w' },
  { label: t('periods.1mo'), value: '1mo' },
  { label: t('periods.60m'), value: '60m' },
  { label: t('periods.30m'), value: '30m' },
  { label: t('periods.15m'), value: '15m' },
  { label: t('periods.5m'), value: '5m' },
  { label: t('periods.1m'), value: '1m' }
])

const dataSrcOptions = computed(() => [
  { label: t('app.dataSrcOptions.clickhouse'), value: 'clickhouse' },
  { label: t('app.dataSrcOptions.baostock'), value: 'baostock' },
  { label: t('app.dataSrcOptions.akshare'), value: 'akshare' }
])

const modelOptions = computed(() => [
  { label: t('app.modelOptions.xgboost'), value: 'xgboost' },
  { label: t('app.modelOptions.lightgbm'), value: 'lightgbm' },
  { label: t('app.modelOptions.mlp'), value: 'mlp' }
])

const dataLengthModeOptions = computed(() => [
  { label: t('pretrain.dataLengthMax'), value: 'max' },
  { label: t('pretrain.dataLengthYears'), value: 'years' }
])

const poolOptions = computed(() =>
  (pools.value || [])
    .slice()
    .sort((a, b) => {
      const an = Number(a?.stock_count || 0)
      const bn = Number(b?.stock_count || 0)
      if (an !== bn) return an - bn
      const al = String(a?.pool_name || a?.pool_id || '')
      const bl = String(b?.pool_name || b?.pool_id || '')
      return al.localeCompare(bl)
    })
    .map((p) => ({
      value: p.pool_id,
      label: p.stock_count ? `${p.pool_name} (${p.stock_count})` : String(p.pool_name || p.pool_id)
    }))
)

const poolMetaLabel = computed(() => {
  if (dataSrc.value !== 'clickhouse') return ''
  const n = (pools.value || []).length
  if (!n) return t('pretrain.noPools')
  return t('pretrain.poolCount').replace('{n}', String(n))
})

const codes = computed(() => {
  const raw = (codesText.value || '').trim()
  if (!raw) return []
  return raw
    .split(/[\s,，;；]+/g)
    .map((s) => s.trim())
    .filter(Boolean)
})

const perCodeRows = computed(() => {
  const map = detailResult.value?.per_code_sample_count || {}
  return Object.keys(map)
    .sort()
    .map((code) => ({ code, samples: map[code] }))
})

const perCodeColumns = computed(() => [
  { key: 'code', label: t('history.code') },
  { key: 'samples', label: t('pretrain.samples') }
])

const stageLabel = (stage) => {
  const s = String(stage || '')
  if (s === 'queued') return t('pretrain.progressQueued')
  if (s === 'collecting') return t('pretrain.progressCollecting')
  if (s === 'training') return t('pretrain.progressTraining')
  if (s === 'saving') return t('pretrain.progressSaving')
  if (s === 'done') return t('pretrain.progressDone')
  if (s === 'error') return t('pretrain.progressError')
  return s || '-'
}

const circleCirc = 2 * Math.PI * 16

const progressPct = (obj) => {
  const p = Number(obj?.progress || 0)
  if (!Number.isFinite(p)) return 0
  return Math.max(0, Math.min(100, Math.round(p * 100)))
}

const showProgressPct = (obj) => {
  if (!obj) return false
  if (progressPct(obj) >= 100) return false
  const stage = String(obj?.stage || '')
  if (stage === 'training') return false
  return true
}

const closeDetail = () => {
  selectedJob.value = null
  detailResult.value = null
}

const circleOffset = (pct) => {
  const p = Number(pct || 0)
  const clamped = Math.max(0, Math.min(100, p))
  return circleCirc * (1 - clamped / 100)
}

const formatTime = (iso) => {
  const raw = String(iso || '')
  if (!raw) return ''
  const d = new Date(raw)
  if (Number.isNaN(d.getTime())) return raw
  const pad2 = (n) => String(n).padStart(2, '0')
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

const dataLengthShort = (row) => {
  const y = row?.data_length_years
  if (y !== null && y !== undefined && y !== '') {
    const n = Number(y)
    if (Number.isFinite(n)) return `${n % 1 === 0 ? n.toFixed(0) : n.toFixed(1)}y`
    return `${String(y)}y`
  }
  const mode = String(row?.data_length_mode || '')
  if (mode === 'max') return 'max'
  if (mode && mode !== 'default') return mode
  return ''
}

const fetchPools = async () => {
  if (dataSrc.value !== 'clickhouse') return
  poolsLoading.value = true
  try {
    const res = await axios.get('/api/stock_pools')
    const items = res.data?.items || []
    pools.value = Array.isArray(items) ? items : []
  } catch (e) {
    console.error(e)
    pools.value = []
  } finally {
    poolsLoading.value = false
  }
}

watch(
  [poolOptions, dataSrc],
  () => {
    if (dataSrc.value !== 'clickhouse') return
    const opts = poolOptions.value || []
    if (!opts.length) return
    const cur = selectedPoolId.value
    if (cur && opts.some((o) => o?.value === cur)) return
    selectedPoolId.value = opts[0].value
  },
  { immediate: true }
)

const fillCodesFromPool = async () => {
  if (dataSrc.value !== 'clickhouse' || !selectedPoolId.value) return
  poolMembersLoading.value = true
  try {
    const res = await axios.get(`/api/stock_pools/${encodeURIComponent(selectedPoolId.value)}/members`, {
      params: { limit: 10000 }
    })
    const codesFromPool = res.data?.codes || []
    if (!Array.isArray(codesFromPool) || codesFromPool.length === 0) {
      toast.add({ title: t('pretrain.poolEmpty'), color: 'orange' })
      return
    }

    if (overwriteCodes.value) {
      codesText.value = codesFromPool.join('\n')
    } else {
      const cur = new Set(codes.value)
      for (const c of codesFromPool) cur.add(c)
      codesText.value = Array.from(cur).join('\n')
    }
    toast.add({ title: t('pretrain.poolLoaded').replace('{n}', String(codesFromPool.length)), color: 'green' })
  } catch (e) {
    console.error(e)
    const detail = e?.response?.data?.detail || e?.message || ''
    toast.add({ title: t('pretrain.failed'), description: detail, color: 'red' })
  } finally {
    poolMembersLoading.value = false
  }
}

watch(
  () => dataSrc.value,
  (v) => {
    if (v !== 'clickhouse') {
      pools.value = []
      selectedPoolId.value = ''
      return
    }
    fetchPools()
  },
  { immediate: true }
)

onMounted(() => {
  fetchPools()
  refreshLists()
  listPollTimer = setInterval(() => {
    refreshLists({ quiet: true })
  }, 2000)
})

onBeforeUnmount(() => {
  if (jobPollTimer) {
    clearInterval(jobPollTimer)
    jobPollTimer = null
  }
  if (listPollTimer) {
    clearInterval(listPollTimer)
    listPollTimer = null
  }
})

const stopJobPolling = () => {
  if (jobPollTimer) {
    clearInterval(jobPollTimer)
    jobPollTimer = null
  }
}

const fetchPretrainJobs = async () => {
  const res = await axios.get('/api/pretrain_jobs', { params: { limit: 200 } })
  const items = res.data?.items || []
  jobItems.value = Array.isArray(items) ? items : []
}

const fetchPretrainedModels = async () => {
  const res = await axios.get('/api/pretrained_models', {
    params: {
      page: modelsPage.value,
      page_size: modelsPageSize.value
    }
  })
  const data = res.data || {}
  const items = data.items || []
  modelItems.value = Array.isArray(items) ? items : []
  modelsTotal.value = Number(data.total || modelItems.value.length || 0)
}

const refreshLists = async ({ quiet, forceModels } = {}) => {
  const now = Date.now()
  const shouldFetchModels = !!forceModels || now - lastModelsFetchAt > 10000 || (modelItems.value || []).length === 0
  listLoading.value = !quiet
  try {
    await fetchPretrainJobs()
    if (shouldFetchModels) {
      await fetchPretrainedModels()
      lastModelsFetchAt = now
    }
    if (selectedJob.value?.kind === 'job') {
      const cur = (jobItems.value || []).find((j) => j?.job_id === selectedJob.value?.job_id)
      if (cur) selectedJob.value = { ...selectedJob.value, ...cur }
    }
  } catch (e) {
    console.error(e)
  } finally {
    listLoading.value = false
  }
}

const changeModelsPage = async (nextPage) => {
  const p = Number(nextPage || 1)
  if (!Number.isFinite(p)) return
  const clamped = Math.max(1, Math.min(modelsTotalPages.value, Math.floor(p)))
  if (clamped === modelsPage.value) return
  modelsPage.value = clamped
  await refreshLists({ quiet: true, forceModels: true })
}

const listRows = computed(() => {
  const rows = []
  for (const j of jobItems.value || []) {
    if (j?.status === 'success') continue
    const jobId = String(j?.job_id || '')
    const req = j?.request || {}
    const modelType = req?.model
    const freq = req?.frequency
    const src = req?.data_src
    const label = [modelType, freq, src].filter(Boolean).join(' ')
    rows.push({
      kind: 'job',
      id: jobId ? jobId.slice(0, 8) : '',
      job_id: jobId,
      status: j?.status,
      progress: j?.progress,
      stage: j?.stage,
      message: j?.message,
      detail: j?.detail,
      result: j?.result,
      created_at: j?.created_at,
      updated_at: j?.updated_at,
      model_type: modelType,
      frequency: freq,
      data_src: src,
      codes_count: req?.codes_count,
      data_length_mode: req?.data_length_mode,
      data_length_years: req?.data_length_years,
      display_name: label || '-'
    })
  }
  for (const m of modelItems.value || []) {
    const key = String(m?.key || '')
    const label = m?.display_name || m?.name || [m?.model_type, m?.frequency, m?.data_src].filter(Boolean).join(' ')
    rows.push({
      kind: 'model',
      id: key ? key.slice(0, 8) : '',
      key,
      status: 'success',
      progress: 1,
      stage: 'done',
      created_at: m?.trained_at,
      updated_at: m?.trained_at,
      model_type: m?.model_type,
      frequency: m?.frequency,
      data_src: m?.data_src,
      feature_count: m?.feature_count,
      name: m?.name,
      display_name: label
    })
  }
  rows.sort((a, b) => String(b?.updated_at || '').localeCompare(String(a?.updated_at || '')))
  return rows
})

const listColumns = computed(() => [
  { key: 'combined', label: t('pretrain.statusProgressStage'), class: 'w-[60px]' },
  { key: 'params', label: t('pretrain.params') },
  { key: 'updated_at', label: t('pretrain.updatedAt'), class: 'w-[100px]' },
  { key: 'actions', label: t('pretrain.actions'), class: 'w-[80px]' }
])

const openItem = async (row) => {
  if (!row) return
  selectedJob.value = row
  detailResult.value = null
  try {
    if (row.kind === 'job' && row.job_id) {
      const res = await axios.get(`/api/pretrain_jobs/${encodeURIComponent(row.job_id)}`)
      const job = res.data || {}
      selectedJob.value = { ...row, ...job, kind: 'job', job_id: row.job_id, id: row.id }
      if (job.status === 'success') {
        const mk = job?.result?.model_key
        if (mk) {
          const d = await axios.get(`/api/pretrained_models/${encodeURIComponent(mk)}`)
          detailResult.value = d.data || job.result || { status: 'success' }
        } else {
          detailResult.value = job.result || { status: 'success' }
        }
      } else if (job.status === 'error') {
        detailResult.value = { status: 'error', detail: job.detail || t('pretrain.failed') }
      }
      return
    }
    if (row.kind === 'model' && row.key) {
      const res = await axios.get(`/api/pretrained_models/${encodeURIComponent(row.key)}`)
      detailResult.value = res.data || { status: 'success' }
      selectedJob.value = { ...row, status: 'success' }
    }
  } catch (e) {
    console.error(e)
    const detail = e?.response?.data?.detail || e?.message || ''
    toast.add({ title: t('pretrain.failed'), description: detail, color: 'red' })
  }
}

const openRename = (row) => {
  if (!row || row.kind !== 'model') return
  renameKey.value = String(row.key || '')
  renameValue.value = String(row.name || '')
  renameOpen.value = true
}

const saveRename = async () => {
  const key = String(renameKey.value || '').trim()
  if (!key) return
  renameSaving.value = true
  try {
    const payload = { name: renameValue.value }
    const res = await axios.post(`/api/pretrained_models/${encodeURIComponent(key)}/name`, payload)
    const data = res.data || {}
    const newName = data.display_name || data.name || ''
    if (selectedJob.value?.kind === 'model' && selectedJob.value?.key === key) {
      selectedJob.value = { ...selectedJob.value, name: data.name, display_name: newName }
    }
    if (detailResult.value?.status === 'success' && selectedJob.value?.kind === 'model' && selectedJob.value?.key === key) {
      detailResult.value = { ...detailResult.value, name: data.name, display_name: newName }
    }
    renameOpen.value = false
    await refreshLists({ quiet: true, forceModels: true })
    toast.add({ title: t('pretrain.renamed'), color: 'green' })
  } catch (e) {
    console.error(e)
    const detail = e?.response?.data?.detail || e?.message || ''
    toast.add({ title: t('pretrain.failed'), description: detail, color: 'red' })
  } finally {
    renameSaving.value = false
  }
}

const pollPretrainJob = async () => {
  if (!jobId.value) return
  try {
    const res = await axios.get(`/api/pretrain_jobs/${encodeURIComponent(jobId.value)}`)
    const job = res.data || {}
    if (selectedJob.value?.kind === 'job' && selectedJob.value?.job_id === jobId.value) {
      selectedJob.value = { ...selectedJob.value, ...job }
    }

    if (job.status === 'success') {
      stopJobPolling()
      loading.value = false
      result.value = job.result || { status: 'success' }
      selectedJob.value = { kind: 'job', job_id: jobId.value, id: String(jobId.value).slice(0, 8), ...job }
      const mk = job?.result?.model_key
      if (mk) {
        try {
          const d = await axios.get(`/api/pretrained_models/${encodeURIComponent(mk)}`)
          detailResult.value = d.data || job.result || { status: 'success' }
        } catch (e) {
          detailResult.value = job.result || { status: 'success' }
        }
      } else {
        detailResult.value = job.result || { status: 'success' }
      }
      modelsPage.value = 1
      await refreshLists({ quiet: true, forceModels: true })
      toast.add({ title: t('pretrain.success'), color: 'green' })
    } else if (job.status === 'error') {
      stopJobPolling()
      loading.value = false
      const detail = job.detail || t('pretrain.failed')
      result.value = { status: 'error', detail }
      selectedJob.value = { kind: 'job', job_id: jobId.value, id: String(jobId.value).slice(0, 8), ...job }
      detailResult.value = { status: 'error', detail }
      await refreshLists({ quiet: true })
      toast.add({ title: t('pretrain.failed'), description: detail, color: 'red' })
    }
  } catch (e) {
    console.error(e)
  }
}

const runPretrain = async () => {
  if (!codes.value.length) {
    toast.add({ title: t('pretrain.needCodes'), color: 'red' })
    return
  }

  loading.value = true
  result.value = null
  stopJobPolling()
  jobId.value = ''
  try {
    const selectedPool = pools.value.find(p => p.pool_id === selectedPoolId.value)
    const poolName = selectedPool ? (selectedPool.pool_name || selectedPool.pool_id) : null

    const body = {
      codes: codes.value,
      frequency: frequency.value,
      data_src: dataSrc.value,
      model: model.value,
      data_length_mode: dataLengthMode.value === 'years' ? 'default' : 'max',
      data_length_years: dataLengthMode.value === 'years' ? Number(dataLengthYears.value) : null,
      force_refresh: !!forceRefresh.value,
      pool_name: poolName
    }

    try {
      const start = async (payload) => {
        const startRes = await axios.post('/api/pretrain_jobs', payload)
        const id = startRes.data?.job_id
        if (!id) throw new Error('missing job_id')
        jobId.value = id
        await refreshLists({ quiet: true })
        // Don't auto-open item
        // await openItem({ kind: 'job', job_id: id, id: String(id).slice(0, 8), status: 'queued', progress: 0, stage: 'queued' })
        await pollPretrainJob()
        jobPollTimer = setInterval(pollPretrainJob, 1000)
      }

      try {
        await start(body)
        return
      } catch (e) {
        const status = e?.response?.status
        const detail = String(e?.response?.data?.detail || '')
        if (status === 409 && detail.includes('duplicate pretrain') && !body.force_refresh) {
          await start({ ...body, force_refresh: true })
          return
        }
        throw e
      }
    } catch (e) {
      const status = e?.response?.status
      if (status !== 404) {
        throw e
      }
    }

    const res = await axios.post('/api/pretrain', body)
    result.value = res.data
    if (res.data?.status === 'success') toast.add({ title: t('pretrain.success'), color: 'green' })
    else toast.add({ title: t('pretrain.failed'), color: 'red' })
  } catch (e) {
    console.error(e)
    const detail = e?.response?.data?.detail || e?.message || ''
    result.value = { status: 'error', detail }
    toast.add({ title: t('pretrain.failed'), description: detail, color: 'red' })
  } finally {
    if (!jobId.value) loading.value = false
  }
}
</script>
