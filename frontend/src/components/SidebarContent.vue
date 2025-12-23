<template>
  <div class="flex flex-col h-full">
    <div class="flex-1">
      <h3 class="text-sm font-semibold text-gray-500 uppercase flex items-center justify-between select-none cursor-pointer lg:cursor-default" :class="{'mb-6': !collapsed || mode === 'desktop', 'mb-0': collapsed && mode !== 'desktop'}" @click="$emit('toggle')">
        <div class="flex items-center gap-2">
          <svg width="18" height="18" viewBox="0 0 24 24"><path :d="MemoryFlask" /></svg>
          {{ t('sidebar.params') }}
        </div>
        
        <!-- Toggle Button -->
        <div class="text-gray-400 hover:text-gray-600 cursor-pointer p-1 rounded hover:bg-gray-100 transition-colors">
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
      
      <div v-show="!collapsed || mode === 'desktop'">
          <div class="mb-6">
            <label class="block text-sm font-medium text-gray-700 mb-2">{{ t('sidebar.stockCode') }}</label>
            <el-input v-model="proxyCode" :placeholder="t('sidebar.codePlaceholder')" clearable />
            <small class="block mt-1 text-xs text-gray-500">{{ t('sidebar.codeHint') }}</small>
          </div>
          
          <div class="mb-4">
            <el-checkbox v-model="proxyTriggerStep" :label="t('sidebar.stepCalc')" />
            <div class="text-xs text-gray-500 ml-6 mt-1 leading-tight">{{ t('sidebar.stepHint') }}</div>
          </div>
          
          <div class="mb-6">
            <el-checkbox v-model="proxyBiStrict" :label="t('sidebar.strictBi')" />
            <div class="text-xs text-gray-500 ml-6 mt-1 leading-tight">{{ t('sidebar.strictHint') }}</div>
          </div>
          
          <el-button type="primary" class="w-full" :loading="loading" @click="$emit('analyze')">
            {{ loading ? t('sidebar.analyzing') : t('sidebar.analyze') }}
          </el-button>
          
          <el-button class="w-full mt-2 !ml-0" :loading="downloading" @click="$emit('download')">
            {{ downloading ? t('sidebar.downloading') : t('sidebar.download') }}
          </el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { MemoryFlask, MemoryChevronDown, MemoryChevronLeft } from '@pictogrammers/memory'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()

const props = defineProps({
  code: String,
  triggerStep: Boolean,
  biStrict: Boolean,
  loading: Boolean,
  downloading: Boolean,
  collapsed: {
    type: Boolean,
    default: false
  },
  mode: {
    type: String,
    default: 'desktop' // 'desktop' | 'mobile'
  }
})

const emit = defineEmits(['update:code', 'update:triggerStep', 'update:biStrict', 'analyze', 'download', 'toggle'])

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

</script>
