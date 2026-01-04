<template>
  <div class="h-full flex flex-col">
    <div class="flex-1 min-h-0 p-3 flex flex-col">
      <div class="grid grid-cols-1 gap-3 h-full">
        <UCard :ui="{ body: { base: 'flex-1 min-h-0 flex flex-col', padding: 'p-3' } }" class="shadow-none border border-gray-200 dark:border-gray-800 flex flex-col min-h-0 h-full">
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
            <div v-if="!selectedJob" class="flex items-center gap-2">
              <span v-if="selectedCount > 0" class="text-xs text-gray-500">
                {{ t('pretrain.selectedCount').replace('{n}', String(selectedCount)) }}
              </span>
              <UButton
                size="xs"
                color="red"
                variant="soft"
                :disabled="selectedCount <= 0"
                :loading="batchDeleting"
                @click="batchDelete"
              >
                {{ t('pretrain.batchDelete') }}
              </UButton>
              <UButton size="xs" color="gray" :loading="listLoading" @click="refreshLists">
                {{ t('pretrain.refreshList') }}
              </UButton>
            </div>
          </div>

          <div v-if="!selectedJob" class="mt-2 flex-1 min-h-0 flex flex-col">
            <div class="flex-1 min-h-0 overflow-auto">
              <UTable
              :rows="listRows"
              :columns="listColumns"
              :loading="listLoading"
              class="w-full"
              :ui="{ th: { base: 'whitespace-nowrap', padding: 'px-3 py-2' }, td: { padding: 'px-3 py-2' } }"
            >
              <template #select-header>
                <UCheckbox
                  :model-value="allVisibleSelected"
                  :disabled="selectableRowKeys.length === 0"
                  @update:model-value="toggleSelectAllVisible"
                />
              </template>

              <template #select-data="{ row }">
                <UCheckbox
                  :model-value="isSelected(row)"
                  :disabled="!isRowDeletable(row)"
                  @click.stop
                  @update:model-value="(v) => setSelected(row, v)"
                />
              </template>

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
                <UButton
                  size="xs"
                  variant="link"
                  color="red"
                  :loading="deleteLoadingKey === rowKey(row)"
                  :disabled="row.kind === 'job' && row.status === 'running'"
                  @click="deleteItem(row, $event)"
                >
                  {{ t('pretrain.delete') }}
                </UButton>
              </template>
            </UTable>
            </div>
            <div v-if="modelsTotalPages > 1" class="mt-2 flex items-center justify-end gap-2 shrink-0">
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
                @click="changeModelsPage(1)"
              >
                &lt;&lt;
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="modelsPage <= 1"
                @click="changeModelsPage(modelsPage - 1)"
              >
                {{ t('pretrain.prevPage') }}
              </UButton>
              <div class="flex items-center gap-1">
                <UInput
                  v-model="modelsPageInput"
                  size="xs"
                  class="w-12 text-center"
                  @keydown.enter="jumpToModelsPage"
                />
              </div>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="modelsPage >= modelsTotalPages"
                @click="changeModelsPage(modelsPage + 1)"
              >
                {{ t('pretrain.nextPage') }}
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="modelsPage >= modelsTotalPages"
                @click="changeModelsPage(modelsTotalPages)"
              >
                &gt;&gt;
              </UButton>
            </div>
          </div>

          <div v-else class="mt-3 border-t border-gray-100 dark:border-gray-800 pt-3 flex-1 min-h-0 flex flex-col">
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

              <div v-if="detailResult.status === 'success' && perCodeRows.length" class="border-t border-gray-100 dark:border-gray-800 pt-3 flex-1 min-h-0 flex flex-col">
                <div class="font-medium text-gray-700 dark:text-gray-300 mb-2 text-sm shrink-0">{{ t('pretrain.perCodeSamples') }}</div>
                <div class="flex-1 min-h-0 overflow-auto">
                  <UTable
                  :rows="paginatedPerCodeRows"
                  :columns="perCodeColumns"
                  class="w-full"
                  :ui="{ th: { base: 'whitespace-nowrap', padding: 'px-3 py-2' }, td: { padding: 'px-3 py-2' } }"
                />
                </div>
                <div v-if="detailTotalPages > 1" class="mt-2 flex items-center justify-end gap-2 border-t border-gray-100 dark:border-gray-800 pt-2 shrink-0">
                  <span class="text-xs text-gray-500">
                    {{ t('pretrain.pageInfo', { page: detailPage, pages: detailTotalPages, total: perCodeRows.length }) }}
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
                    {{ t('pretrain.prevPage') }}
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
                    {{ t('pretrain.nextPage') }}
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

const { t, formatTime } = useI18n()
const toast = useToast()

const formatAxiosError = (e) => {
  const detail = e?.response?.data?.detail
  if (detail) return String(detail)
  const msg = String(e?.message || '')
  if (!e?.response && (e?.code === 'ERR_NETWORK' || msg.toLowerCase().includes('network'))) {
    return t('app.networkError')
  }
  return msg
}

const props = defineProps({
  defaultDataSrc: {
    type: String,
    default: 'clickhouse'
  },
  defaultModel: {
    type: String,
    default: 'xgboost'
  },
  config: {
    type: Object,
    default: () => ({})
  }
})

const loading = ref(false)
const result = ref(null)

const jobId = ref('')
let jobPollTimer = null
let listPollTimer = null
let lastModelsFetchAt = 0

const listLoading = ref(false)
const jobItems = ref([])
const modelItems = ref([])
const modelsPage = ref(1)
const modelsPageSize = ref(20)
const modelsTotal = ref(0)
const detailPage = ref(1)
const detailPageSize = ref(20)
const modelsPageInput = ref('')
const detailPageInput = ref('')

const renameOpen = ref(false)
const renameSaving = ref(false)
const renameKey = ref('')
const renameValue = ref('')
const deleteLoadingKey = ref('')
const batchDeleting = ref(false)
const selectedRowKeys = ref([])

const modelsTotalPages = computed(() => {
  const ps = Number(modelsPageSize.value || 20)
  const total = Number(modelsTotal.value || 0)
  if (!Number.isFinite(ps) || ps <= 0) return 1
  if (!Number.isFinite(total) || total <= 0) return 1
  return Math.max(1, Math.ceil(total / ps))
})

const selectedJob = ref(null)
const detailResult = ref(null)

const selectedCount = computed(() => (selectedRowKeys.value || []).length)

const codes = computed(() => {
  const raw = (props.config.codesText || '').trim()
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

const detailTotalPages = computed(() => {
  const t = perCodeRows.value?.length || 0
  const ps = Number(detailPageSize.value || 20)
  const p = Math.ceil(t / Math.max(1, ps))
  return Math.max(1, p || 1)
})

const paginatedPerCodeRows = computed(() => {
  const all = perCodeRows.value || []
  const ps = Number(detailPageSize.value || 20)
  const p = Math.max(1, detailPage.value)
  const start = (p - 1) * ps
  return all.slice(start, start + ps)
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



onMounted(() => {
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

const jumpToModelsPage = () => {
  const p = parseInt(modelsPageInput.value)
  if (Number.isFinite(p) && p >= 1 && p <= modelsTotalPages.value) {
    changeModelsPage(p)
    modelsPageInput.value = ''
  }
}

const jumpToDetailPage = () => {
  const p = parseInt(detailPageInput.value)
  if (Number.isFinite(p) && p >= 1 && p <= detailTotalPages.value) {
    detailPage.value = p
    detailPageInput.value = ''
  }
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
  { key: 'select', label: '', class: 'w-[36px]' },
  { key: 'combined', label: t('pretrain.statusProgressStage'), class: 'w-[60px]' },
  { key: 'params', label: t('pretrain.params') },
  { key: 'updated_at', label: t('pretrain.updatedAt'), class: 'w-[100px]' },
  { key: 'actions', label: t('pretrain.actions'), class: 'w-[80px]' }
])

const openItem = async (row) => {
  if (!row) return
  selectedJob.value = row
  detailResult.value = null
  detailPage.value = 1
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

const rowKey = (row) => {
  if (!row) return ''
  if (row.kind === 'model') return `model:${String(row.key || '')}`
  if (row.kind === 'job') return `job:${String(row.job_id || '')}`
  return ''
}

const isRowDeletable = (row) => {
  if (!row) return false
  if (row.kind === 'job') return String(row.status || '') !== 'running'
  if (row.kind === 'model') return true
  return false
}

const selectedKeySet = computed(() => new Set((selectedRowKeys.value || []).filter(Boolean)))

const isSelected = (row) => {
  const k = rowKey(row)
  if (!k) return false
  return selectedKeySet.value.has(k)
}

const setSelected = (row, val) => {
  const k = rowKey(row)
  if (!k) return
  if (!isRowDeletable(row)) return
  const next = new Set(selectedKeySet.value)
  if (val) next.add(k)
  else next.delete(k)
  selectedRowKeys.value = Array.from(next)
}

const selectableRowKeys = computed(() => {
  const rows = listRows.value || []
  const out = []
  for (const r of rows) {
    if (!isRowDeletable(r)) continue
    const k = rowKey(r)
    if (k) out.push(k)
  }
  return out
})

const allVisibleSelected = computed(() => {
  const keys = selectableRowKeys.value || []
  if (!keys.length) return false
  const set = selectedKeySet.value
  for (const k of keys) {
    if (!set.has(k)) return false
  }
  return true
})

const toggleSelectAllVisible = (val) => {
  const keys = selectableRowKeys.value || []
  if (!keys.length) return
  const next = new Set(selectedKeySet.value)
  if (val) {
    for (const k of keys) next.add(k)
  } else {
    for (const k of keys) next.delete(k)
  }
  selectedRowKeys.value = Array.from(next)
}

const batchDelete = async () => {
  if (selectedCount.value <= 0) return
  if (!confirm(t('pretrain.batchDeleteConfirm'))) return

  const set = selectedKeySet.value
  const jobIds = []
  const modelKeys = []
  for (const k of set) {
    if (k.startsWith('job:')) {
      const jid = k.slice(4)
      if (jid) jobIds.push(jid)
    } else if (k.startsWith('model:')) {
      const mk = k.slice(6)
      if (mk) modelKeys.push(mk)
    }
  }
  if (!jobIds.length && !modelKeys.length) return

  batchDeleting.value = true
  try {
    const failed = []
    if (modelKeys.length) {
      const res = await axios.post('/api/pretrained_models/batch_delete', { keys: modelKeys })
      for (const f of res.data?.failed || []) failed.push(f)
    }
    if (jobIds.length) {
      const res = await axios.post('/api/pretrain_jobs/batch_delete', { job_ids: jobIds })
      for (const f of res.data?.failed || []) failed.push(f)
    }
    selectedRowKeys.value = []
    await refreshLists({ quiet: true, forceModels: true })
    if (failed.length) {
      toast.add({ title: t('pretrain.deleteFailed'), description: String(failed.length), color: 'red' })
    } else {
      toast.add({ title: t('pretrain.deleted'), color: 'green' })
    }
  } catch (e) {
    console.error(e)
    toast.add({ title: t('pretrain.deleteFailed'), description: formatAxiosError(e), color: 'red' })
  } finally {
    batchDeleting.value = false
  }
}

const deleteItem = async (row, ev) => {
  if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
  if (!row) return
  if (!isRowDeletable(row)) return
  if (!confirm(t('pretrain.deleteConfirm'))) return

  const k = rowKey(row)
  deleteLoadingKey.value = k
  try {
    if (row.kind === 'model' && row.key) {
      await axios.delete(`/api/pretrained_models/${encodeURIComponent(row.key)}`)
      selectedRowKeys.value = (selectedRowKeys.value || []).filter((x) => x !== k)
      if (selectedJob.value?.kind === 'model' && selectedJob.value?.key === row.key) {
        closeDetail()
      }
      await refreshLists({ quiet: true, forceModels: true })
      toast.add({ title: t('pretrain.deleted'), color: 'green' })
      return
    }
    if (row.kind === 'job' && row.job_id) {
      await axios.delete(`/api/pretrain_jobs/${encodeURIComponent(row.job_id)}`)
      selectedRowKeys.value = (selectedRowKeys.value || []).filter((x) => x !== k)
      if (selectedJob.value?.kind === 'job' && selectedJob.value?.job_id === row.job_id) {
        closeDetail()
      }
      await refreshLists({ quiet: true })
      toast.add({ title: t('pretrain.deleted'), color: 'green' })
    }
  } catch (e) {
    console.error(e)
    toast.add({ title: t('pretrain.deleteFailed'), description: formatAxiosError(e), color: 'red' })
  } finally {
    if (deleteLoadingKey.value === k) deleteLoadingKey.value = ''
  }
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
    const body = {
      codes: codes.value,
      frequency: props.config.frequency,
      data_src: props.config.dataSrc,
      model: props.config.model,
      data_length_mode: props.config.dataLengthMode === 'years' ? 'default' : 'max',
      data_length_years: props.config.dataLengthMode === 'years' ? Number(props.config.dataLengthYears) : null,
      force_refresh: !!props.config.forceRefresh,
      pool_name: props.config.poolName || null
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

defineExpose({ runPretrain })
</script>
