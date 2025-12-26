<script setup>
import { MemoryBook } from '@pictogrammers/memory'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()
</script>

<template>
  <div class="p-8 max-w-[900px] mx-auto bg-white dark:bg-gray-900 rounded-lg shadow-sm border border-gray-100 dark:border-gray-800">
    <h2 class="text-gray-800 dark:text-gray-100 mb-8 border-b-2 border-gray-200 dark:border-gray-700 pb-4 text-2xl font-bold flex items-center gap-2">
      <svg width="24" height="24" viewBox="0 0 24 24" class="text-blue-600"><path :d="MemoryBook" /></svg>
      {{ t('help.title') }}
    </h2>
    
    <div class="text-gray-600 dark:text-gray-400">
      <h3 class="text-gray-700 dark:text-gray-200 mt-6 mb-4 text-xl font-semibold">{{ t('help.section1.title') }}</h3>
      <p class="leading-relaxed mb-4">{{ t('help.section1.intro') }}</p>
      
      <h4 class="text-gray-600 dark:text-gray-300 mt-5 mb-2 text-base font-semibold">{{ t('help.section1.fractal.title') }}</h4>
      <ul class="list-disc pl-6 mb-4 space-y-2">
        <li v-html="t('help.section1.fractal.top')"></li>
        <li v-html="t('help.section1.fractal.bottom')"></li>
        <li v-html="t('help.section1.fractal.meaning')"></li>
      </ul>
      
      <h4 class="text-gray-600 dark:text-gray-300 mt-5 mb-2 text-base font-semibold">{{ t('help.section1.bi.title') }}</h4>
      <ul class="list-disc pl-6 mb-4 space-y-2">
        <li v-html="t('help.section1.bi.def')"></li>
        <li v-html="t('help.section1.bi.comp')"></li>
        <li v-html="t('help.section1.bi.strict')"></li>
        <li v-html="t('help.section1.bi.visual')"></li>
      </ul>
      
      <h4 class="text-gray-600 dark:text-gray-300 mt-5 mb-2 text-base font-semibold">{{ t('help.section1.seg.title') }}</h4>
      <ul class="list-disc pl-6 mb-4 space-y-2">
        <li v-html="t('help.section1.seg.def')"></li>
        <li v-html="t('help.section1.seg.visual')"></li>
      </ul>
      
      <h4 class="text-gray-600 dark:text-gray-300 mt-5 mb-2 text-base font-semibold">{{ t('help.section1.center.title') }}</h4>
      <ul class="list-disc pl-6 mb-4 space-y-2">
        <li v-html="t('help.section1.center.def')"></li>
        <li v-html="t('help.section1.center.visual')"></li>
        <li v-html="t('help.section1.center.meaning')"></li>
      </ul>
      
      <h3 class="text-gray-700 dark:text-gray-200 mt-6 mb-4 text-xl font-semibold">{{ t('help.section2.title') }}</h3>
      <p class="leading-relaxed mb-4">{{ t('help.section2.intro') }}</p>
      
      <div class="overflow-x-auto my-6">
        <table class="w-full border-collapse text-sm">
          <thead>
            <tr class="bg-gray-50 dark:bg-gray-800">
              <th class="border border-gray-300 dark:border-gray-700 p-3 text-left font-semibold text-gray-700 dark:text-gray-200">{{ t('help.section2.table.type') }}</th>
              <th class="border border-gray-300 dark:border-gray-700 p-3 text-left font-semibold text-gray-700 dark:text-gray-200">{{ t('help.section2.table.name') }}</th>
              <th class="border border-gray-300 dark:border-gray-700 p-3 text-left font-semibold text-gray-700 dark:text-gray-200">{{ t('help.section2.table.desc') }}</th>
              <th class="border border-gray-300 dark:border-gray-700 p-3 text-left font-semibold text-gray-700 dark:text-gray-200">{{ t('help.section2.table.risk') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t1.type') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t1.name') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t1.desc') }}</td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t1.risk') }}</td>
            </tr>
            <tr>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t2.type') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t2.name') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t2.desc') }}</td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t2.risk') }}</td>
            </tr>
            <tr>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t3.type') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t3.name') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t3.desc') }}</td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t3.risk') }}</td>
            </tr>
            <tr>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t2s.type') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400"><strong>{{ t('help.section2.table.t2s.name') }}</strong></td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t2s.desc') }}</td>
              <td class="border border-gray-300 dark:border-gray-700 p-3 text-gray-600 dark:text-gray-400">{{ t('help.section2.table.t2s.risk') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      
      <h3 class="text-gray-700 dark:text-gray-200 mt-6 mb-4 text-xl font-semibold">{{ t('help.section3.title') }}</h3>
      <p class="leading-relaxed mb-4" v-html="t('help.section3.intro')"></p>
      <ul class="list-disc pl-6 mb-4 space-y-2">
        <li>{{ t('help.section3.desc1') }}</li>
        <li>{{ t('help.section3.desc2') }}</li>
        <li v-html="t('help.section3.score')"></li>
      </ul>
      
      <h3 class="text-gray-700 dark:text-gray-200 mt-6 mb-4 text-xl font-semibold">{{ t('help.section4.title') }}</h3>
      <ul class="list-disc pl-6 mb-4 space-y-2">
        <li><span v-html="t('help.section4.step')"></span>
          <ul class="list-circle pl-6 mt-2">
            <li v-html="t('help.section4.stepDesc')"></li>
          </ul>
        </li>
        <li><span v-html="t('help.section4.strict')"></span>
          <ul class="list-circle pl-6 mt-2">
            <li v-html="t('help.section4.strictDesc')"></li>
          </ul>
        </li>
      </ul>
    </div>
  </div>
</template>
