<template>
  <div class="flex flex-col h-full">
    <div class="flex-1">
      <h3 class="text-sm font-semibold text-gray-500 uppercase mb-6">⚙️ 参数设置</h3>
      
      <div class="mb-6">
        <label class="block text-sm font-medium text-gray-700 mb-2">股票代码</label>
        <el-input v-model="proxyCode" placeholder="sz.002701" clearable />
        <small class="block mt-1 text-xs text-gray-500">支持 A 股 (如 sz.002701, sh.600000)</small>
      </div>
      
      <div class="mb-4">
        <el-checkbox v-model="proxyTriggerStep" label="逐步计算 (模拟实盘)" />
        <div class="text-xs text-gray-500 ml-6 mt-1 leading-tight">开启后将模拟真实交易环境，逐根 K 线加载并计算。</div>
      </div>
      
      <div class="mb-6">
        <el-checkbox v-model="proxyBiStrict" label="严格笔模式" />
        <div class="text-xs text-gray-500 ml-6 mt-1 leading-tight">要求顶底分型之间至少有3根非包含关系的K线。</div>
      </div>
      
      <el-button type="primary" class="w-full" :loading="loading" @click="$emit('analyze')">
        {{ loading ? '正在分析...' : '开始分析' }}
      </el-button>
    </div>
    
    <div class="mt-8 pt-4 border-t border-gray-200">
      <div class="mb-2">
        <label class="block text-sm font-medium text-gray-700 mb-2">Language / 语言</label>
        <el-select v-model="proxyLang" class="w-full">
          <el-option label="中文" value="zh" />
          <el-option label="English" value="en" />
        </el-select>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  code: String,
  triggerStep: Boolean,
  biStrict: Boolean,
  loading: Boolean,
  lang: String
})

const emit = defineEmits(['update:code', 'update:triggerStep', 'update:biStrict', 'update:lang', 'analyze'])

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

const proxyLang = computed({
  get: () => props.lang,
  set: (val) => emit('update:lang', val)
})
</script>
