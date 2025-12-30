<template>
  <div class="flex flex-col h-full">
    <div class="flex-1">
      <h3
        class="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase flex items-center justify-between select-none cursor-pointer lg:cursor-default"
        :class="[{ 'mb-6': !collapsed || mode === 'desktop', 'mb-0': collapsed && mode !== 'desktop' }, collapsed && mode !== 'desktop' ? 'h-full' : '']"
        @click="$emit('toggle')"
      >
        <div class="flex items-center gap-2">
          {{ t('sidebar.params') }}
        </div>
        
        <!-- Toggle Button -->
        <div class="text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 cursor-pointer rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors">
            <!-- Desktop: Close Icon -->
            <svg v-if="mode === 'desktop'" width="20" height="20" viewBox="0 0 24 24" @click.stop="$emit('toggle')">
               <path :d="MemoryChevronLeft" />
            </svg>
            <!-- Mobile: Chevron -->
            <svg v-else width="20" height="20" viewBox="0 0 24 24" class="transform transition-transform" :class="{'rotate-180': !collapsed}">
               <path :d="MemoryChevronDown" />
            </svg>
        </div>
      </h3>
      
      <div v-show="!collapsed || mode === 'desktop'" class="space-y-3">
          <!-- Common: Stock Code -->
          <div>
            <div class="flex items-center gap-1 mb-2">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.stockCode') }}</label>
              <UTooltip :text="t('sidebar.codeHint')" :popper="{ placement: 'right' }">
                <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
              </UTooltip>
            </div>
            <UInput v-model="proxyCode" :placeholder="t('sidebar.codePlaceholder')" class="w-full" />
          </div>

          <!-- Intraday Specific Configs -->
          <div v-if="activeTab === 'intraday'" class="space-y-4 pt-4 border-t border-gray-100 dark:border-gray-800">
             <!-- Frequency -->
             <div>
                <div class="flex items-center gap-1 mb-2">
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('app.periodSelect') }}</label>
                  <UTooltip :text="t('sidebar.periodHint')" :popper="{ placement: 'right' }">
                    <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
                </div>
                <div class="grid grid-cols-4 gap-1">
                    <UButton
                      v-for="period in ['1m', '5m', '15m', '30m', '60m', '1d', '1w', '1mo']"
                      :key="period"
                      :label="t('periods.' + period)"
                      size="2xs"
                      :color="proxyIntradayFreq === period ? 'primary' : 'gray'"
                      :variant="proxyIntradayFreq === period ? 'solid' : 'ghost'"
                      :disabled="period === '1m' && ['baostock', 'akshare'].includes(proxyDataSrc)"
                      class="justify-center px-0"
                      @click="proxyIntradayFreq = period"
                    />
                </div>
             </div>

             <!-- Data Source -->
             <div>
                <div class="flex items-center gap-1 mb-2">
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('app.dataSrc') }}</label>
                  <UTooltip :text="t('sidebar.dataSrcHint')" :popper="{ placement: 'right' }">
                    <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
                </div>
                <USelectMenu
                   v-model="proxyDataSrc"
                   :options="['clickhouse', 'baostock', 'akshare']"
                   value-attribute="value"
                   option-attribute="label"
                   class="w-full"
                >
                   <template #label>
                      {{ t('app.dataSrcOptions.' + proxyDataSrc) }}
                   </template>
                   <template #option="{ option }">
                      {{ t('app.dataSrcOptions.' + option) }}
                   </template>
                </USelectMenu>
             </div>

             <!-- Model -->
             <div>
                <div class="flex items-center gap-1 mb-2">
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('app.modelSelect') }}</label>
                  <UTooltip :text="t('sidebar.modelHint')" :popper="{ placement: 'right' }">
                    <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
                </div>
                <USelectMenu
                   v-model="proxyModel"
                   :options="['xgboost', 'lightgbm', 'mlp']"
                   value-attribute="value"
                   option-attribute="label"
                   class="w-full"
                >
                   <template #label>
                      {{ t('app.modelOptions.' + proxyModel) }}
                   </template>
                   <template #option="{ option }">
                      {{ t('app.modelOptions.' + option) }}
                   </template>
                </USelectMenu>
             </div>

             <!-- Pretrained Model -->
             <div>
                <div class="flex justify-between items-center mb-2">
                   <div class="flex items-center gap-1">
                      <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('app.pretrainedSelect') }}</label>
                      <UTooltip :text="t('sidebar.pretrainedHint')" :popper="{ placement: 'right' }">
                        <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                      </UTooltip>
                   </div>
                   <UButton size="2xs" color="gray" variant="ghost" icon="i-heroicons-arrow-path" :loading="pretrainedLoading" @click="$emit('refreshPretrained')" />
                </div>
                <USelectMenu
                  v-model="proxyPretrainedChoice"
                  :options="pretrainedOptions"
                  value-attribute="value"
                  option-attribute="label"
                  size="sm"
                  :disabled="pretrainedLoading"
                  class="w-full"
                />
             </div>

             <!-- Data Length -->
             <div>
                <div class="flex justify-between items-center mb-2">
                   <div class="flex items-center gap-1">
                      <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('app.dataLength') }}</label>
                      <UTooltip :text="t('sidebar.dataLengthHint')" :popper="{ placement: 'right' }">
                        <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                      </UTooltip>
                   </div>
                   <span class="text-xs text-gray-500">{{ proxyDataLengthYears.toFixed(1) }} y</span>
                </div>
                <URange
                  v-model="proxyDataLengthYears"
                  :min="dataLengthMin"
                  :max="dataLengthMax"
                  :step="dataLengthStep"
                  size="sm"
                  class="w-full"
                />
             </div>
          </div>

          <!-- Common / Shared Configs (Step & Strict) -->
          <div class="pt-4 border-t border-gray-100 dark:border-gray-800">
             <div class="mb-4 flex items-center gap-2">
                <UCheckbox v-model="proxyTriggerStep" :label="t('sidebar.stepCalc')" />
                <UTooltip :text="t('sidebar.stepHint')" :popper="{ placement: 'right' }">
                   <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                </UTooltip>
             </div>
             
             <div class="mb-4 flex items-center gap-2">
                <UCheckbox v-model="proxyBiStrict" :label="t('sidebar.strictBi')" />
                <UTooltip :text="t('sidebar.strictHint')" :popper="{ placement: 'right' }">
                   <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                </UTooltip>
             </div>

             <!-- Daily Blend (Only shown for Analysis tab) -->
             <div v-if="activeTab === 'analysis'" class="mb-4 flex items-center gap-2">
                <UCheckbox v-model="proxyBlend" :label="t('app.pretrainedBlend')" />
                <UTooltip :text="t('sidebar.blendHint')" :popper="{ placement: 'right' }">
                   <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                </UTooltip>
             </div>

             <!-- Intraday Blend (Only shown for Intraday tab) -->
             <div v-if="activeTab === 'intraday'" class="mb-4 flex items-center gap-2">
                <UCheckbox v-model="proxyIntradayBlend" :label="t('app.pretrainedBlend')" />
                <UTooltip :text="t('sidebar.blendHint')" :popper="{ placement: 'right' }">
                   <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                </UTooltip>
             </div>
          </div>
          
          <UButton 
            v-if="activeTab === 'intraday'" 
            block 
            color="white" 
            :loading="loading" 
            @click="$emit('refresh')" 
            class="mb-2"
          >
            {{ t('app.refresh') }}
          </UButton>
          
          <UButton block color="primary" :loading="loading" @click="$emit('analyze')">
            {{ loading ? t('sidebar.analyzing') : t('sidebar.analyze') }}
          </UButton>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { MemoryChevronDown, MemoryChevronLeft } from '@pictogrammers/memory'
// Auto-imported composables in Nuxt
const { t } = useI18n()

const props = defineProps({
  activeTab: { type: String, default: 'analysis' },
  code: String,
  triggerStep: Boolean,
  biStrict: Boolean,
  loading: Boolean,
  // Daily Props
  blend: { type: Boolean, default: false },
  // Intraday Props
  dataSrc: { type: String, default: 'clickhouse' },
  model: { type: String, default: 'xgboost' },
  intradayFreq: { type: String, default: '30m' },
  dataLengthYears: { type: Number, default: 0.5 },
  dataLengthMin: { type: Number, default: 0.1 },
  dataLengthMax: { type: Number, default: 10 },
  dataLengthStep: { type: Number, default: 0.1 },
  pretrainedChoice: { type: String, default: 'auto' },
  pretrainedOptions: { type: Array, default: () => [] },
  pretrainedLoading: { type: Boolean, default: false },
  intradayBlend: { type: Boolean, default: true },
  
  collapsed: {
    type: Boolean,
    default: false
  },
  mode: {
    type: String,
    default: 'desktop' // 'desktop' | 'mobile'
  }
})

const emit = defineEmits([
  'update:code', 'update:triggerStep', 'update:biStrict', 'update:blend', 
  'update:dataSrc', 'update:model', 'update:intradayFreq', 'update:dataLengthYears',
  'update:pretrainedChoice', 'update:intradayBlend',
  'analyze', 'toggle', 'refreshPretrained', 'refresh'
])

const proxyCode = computed({
  get: () => props.code,
  set: (val) => emit('update:code', val)
})

const proxyTriggerStep = computed({
  get: () => props.triggerStep,
  set: (val) => emit('update:triggerStep', val)
})

const proxyBiStrict = computed({
  get: () => props.biStrict,
  set: (val) => emit('update:biStrict', val)
})

const proxyBlend = computed({
  get: () => props.blend,
  set: (val) => emit('update:blend', val)
})

const proxyDataSrc = computed({
  get: () => props.dataSrc,
  set: (val) => emit('update:dataSrc', val)
})

const proxyModel = computed({
  get: () => props.model,
  set: (val) => emit('update:model', val)
})

const proxyIntradayFreq = computed({
  get: () => props.intradayFreq,
  set: (val) => emit('update:intradayFreq', val)
})

const proxyDataLengthYears = computed({
  get: () => props.dataLengthYears,
  set: (val) => emit('update:dataLengthYears', val)
})

const proxyPretrainedChoice = computed({
  get: () => props.pretrainedChoice,
  set: (val) => emit('update:pretrainedChoice', val)
})

const proxyIntradayBlend = computed({
  get: () => props.intradayBlend,
  set: (val) => emit('update:intradayBlend', val)
})

</script>
