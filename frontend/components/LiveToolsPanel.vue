<template>
  <div class="h-full flex flex-col">
    <div class="flex-1 min-h-0 p-3 flex flex-col">
      <UCard :ui="{ body: { base: 'flex-1 min-h-0 flex flex-col', padding: 'p-3' } }" class="shadow-none border border-gray-200 dark:border-gray-800 flex flex-col min-h-0 h-full">
        <template #header>
          <div class="flex items-center justify-between gap-2">
            <div class="font-bold">{{ t('strategy.liveToolsTitle') }}</div>
          </div>
        </template>

        <div class="flex items-center gap-2 border-b border-gray-200 dark:border-gray-800 pb-2">
          <UButton
            size="sm"
            variant="solid"
            color="primary"
          >
            {{ t('strategy.featureScan') }}
          </UButton>
        </div>

        <div class="mt-3 grid grid-cols-2 gap-3">
          <div>
            <div class="text-xs text-gray-500 mb-1">{{ t('strategy.poolId') }}</div>
            <USelectMenu v-model="liveConfig.pool_id" :options="poolOptionsForLive" value-attribute="value" option-attribute="label" />
          </div>
          <div>
            <div class="text-xs text-gray-500 mb-1">{{ t('sidebar.dataSrc') }}</div>
            <USelectMenu v-model="liveConfig.data_src" :options="dataSrcOptions" value-attribute="value" option-attribute="label" />
          </div>
          <div>
            <div class="text-xs text-gray-500 mb-1">{{ t('sidebar.autype') }}</div>
            <USelectMenu v-model="liveConfig.autype" :options="autypeOptions" value-attribute="value" option-attribute="label" />
          </div>
          <div>
            <div class="text-xs text-gray-500 mb-1">{{ t('sidebar.frequency') }}</div>
            <USelectMenu v-model="liveConfig.frequency" :options="frequencyOptions" value-attribute="value" option-attribute="label" />
          </div>
        </div>

        <div class="mt-4 flex-1 min-h-0 flex flex-col">
          <div class="grid grid-cols-2 gap-3">
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('strategy.lookbackDays') }}</div>
              <UInput v-model="liveScan.lookback_days" type="number" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('strategy.bspFeatureMode') }}</div>
              <USelectMenu v-model="liveScan.bsp_feature_mode" :options="bspFeatureModeOptions" value-attribute="value" option-attribute="label" />
            </div>
            <div>
              <div class="text-xs text-gray-500 mb-1">{{ t('strategy.limit') }}</div>
              <UInput v-model="liveScan.limit" type="number" />
            </div>
            <div class="flex items-end">
              <UButton block color="primary" :loading="scanLoading" @click="runLiveScan">
                {{ t('strategy.runScan') }}
              </UButton>
            </div>
          </div>

          <div v-if="scanError" class="mt-2 text-xs text-red-500 break-all">{{ scanError }}</div>
          <div v-if="scanMeta" class="mt-2 text-xs text-gray-500">
            {{ t('strategy.scanMeta', { n: scanMeta.n, ts: scanMeta.ts }) }}
          </div>

          <div class="mt-3 flex-1 min-h-0 overflow-auto">
            <UTable :rows="scanItems" :columns="scanColumns" class="w-full" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
          </div>
        </div>
      </UCard>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import axios from 'axios'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()

const props = defineProps({
  defaultPoolId: { type: String, default: '' },
  defaultDataSrc: { type: String, default: 'clickhouse' },
  defaultAutype: { type: String, default: 'qfq' },
  defaultFrequency: { type: String, default: '30m' },
  enableRollingLookback: { type: Boolean, default: true }
})

const poolMap = ref({})

const scanLoading = ref(false)
const scanError = ref('')
const scanMeta = ref(null)
const scanItems = ref([])

const liveConfig = ref({
  pool_id: 'index:IDX399673',
  data_src: 'clickhouse',
  autype: 'qfq',
  frequency: '30m'
})

const liveScan = ref({
  lookback_days: 120,
  bsp_feature_mode: 'skip',
  limit: 0
})

const poolOptionsForLive = computed(() => {
  const items = []
  for (const [k, v] of Object.entries(poolMap.value || {})) {
    items.push({ value: k, label: v })
  }
  items.sort((a, b) => String(a.label).localeCompare(String(b.label)))
  if (!items.find(x => x.value === 'index:IDX399673')) {
    items.unshift({ value: 'index:IDX399673', label: '创业板50 (50)' })
  }
  return items
})

const frequencyOptions = computed(() => ([
  { label: t('periods.30m'), value: '30m' },
  { label: t('periods.60m'), value: '60m' },
  { label: t('periods.15m'), value: '15m' },
  { label: t('periods.5m'), value: '5m' },
  { label: t('periods.1m'), value: '1m' }
]))

const dataSrcOptions = computed(() => ([
  { label: t('app.dataSrcOptions.clickhouse'), value: 'clickhouse' },
  { label: t('app.dataSrcOptions.baostock'), value: 'baostock' },
  { label: t('app.dataSrcOptions.akshare'), value: 'akshare' }
]))

const autypeOptions = computed(() => ([
  { label: t('sidebar.autypeQfq'), value: 'qfq' },
  { label: t('sidebar.autypeHfq'), value: 'hfq' },
  { label: 'None', value: 'none' }
]))

const bspFeatureModeOptions = computed(() => ([
  { label: t('strategy.bspModeSkip'), value: 'skip' },
  { label: t('strategy.bspModeLatest'), value: 'latest_bsp' }
]))

const scanColumns = computed(() => ([
  { key: 'code', label: t('sidebar.stockCode') },
  { key: 'volatility_250', label: 'volatility_250' },
  { key: 'volatility_250_rank', label: 'volatility_250_rank' },
  { key: 'zs_last_height', label: 'zs_last_height' },
  { key: 'zs_last_height_rank', label: 'zs_last_height_rank' },
  { key: 'bsp3_zs_height', label: 'bsp3_zs_height' },
  { key: 'ok', label: t('strategy.status') }
]))

const runLiveScan = async () => {
  scanError.value = ''
  scanMeta.value = null
  scanLoading.value = true
  try {
    const payload = {
      ...liveConfig.value,
      ...liveScan.value,
      enable_rolling_lookback: props.enableRollingLookback !== false
    }
    const res = await axios.post('/api/live/scan_features', payload)
    const data = res.data || {}
    scanItems.value = Array.isArray(data.items) ? data.items : []
    scanMeta.value = { n: scanItems.value.length, ts: data.ts }
  } catch (e) {
    scanError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    scanLoading.value = false
  }
}

const syncDefaults = () => {
  if (props.defaultPoolId) liveConfig.value.pool_id = String(props.defaultPoolId)
  if (props.defaultDataSrc) liveConfig.value.data_src = String(props.defaultDataSrc)
  if (props.defaultAutype) liveConfig.value.autype = String(props.defaultAutype)
  if (props.defaultFrequency) liveConfig.value.frequency = String(props.defaultFrequency)
}

watch(
  () => [props.defaultPoolId, props.defaultDataSrc, props.defaultAutype, props.defaultFrequency],
  () => syncDefaults(),
  { immediate: true }
)

onMounted(async () => {
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
  }
})

onUnmounted(() => {
})
</script>
