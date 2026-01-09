<script setup>
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()
</script>

<template>
  <div class="max-w-[1400px] mx-auto">
    <!-- Main Content -->
    <div class="flex-1 min-w-0">
      <div class="p-8 bg-white dark:bg-gray-900 rounded-lg shadow-sm border border-gray-100 dark:border-gray-800">
        <h1 class="text-3xl font-bold text-gray-900 dark:text-white mb-8 pb-4 border-b border-gray-200 dark:border-gray-700">
          {{ t('help.title') }}
        </h1>

        <div class="prose dark:prose-invert max-w-none space-y-12">
          
          <!-- 1. Introduction -->
          <section id="intro" class="scroll-mt-20">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-4">{{ t('help.intro.title') }}</h2>
            <div class="text-gray-600 dark:text-gray-300 leading-relaxed" v-html="t('help.intro.content')"></div>
          </section>

          <!-- 2. Chan Theory -->
          <section id="chan" class="scroll-mt-20">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-6">{{ t('help.chan.title') }}</h2>
            <p class="text-gray-600 dark:text-gray-300 mb-6">{{ t('help.chan.intro') }}</p>
            
            <div class="grid md:grid-cols-2 gap-6">
              <div id="chan-fractal" class="bg-gray-50 dark:bg-gray-800/50 p-6 rounded-xl border border-gray-100 dark:border-gray-800">
                <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3">{{ t('help.chan.fractal.title') }}</h3>
                <div class="space-y-3 text-sm text-gray-600 dark:text-gray-300">
                  <p v-html="t('help.chan.fractal.top')"></p>
                  <p v-html="t('help.chan.fractal.bottom')"></p>
                  <p class="text-xs text-gray-500 mt-2 pt-2 border-t border-gray-200 dark:border-gray-700" v-html="t('help.chan.fractal.note')"></p>
                </div>
              </div>

              <div id="chan-bi" class="bg-gray-50 dark:bg-gray-800/50 p-6 rounded-xl border border-gray-100 dark:border-gray-800">
                <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3">{{ t('help.chan.bi.title') }}</h3>
                <div class="space-y-3 text-sm text-gray-600 dark:text-gray-300">
                  <p v-html="t('help.chan.bi.def')"></p>
                  <div class="bg-blue-50 dark:bg-blue-900/20 p-3 rounded text-blue-800 dark:text-blue-200 text-xs">
                    <strong class="block mb-1">{{ t('help.chan.bi.strictTitle') }}</strong>
                    {{ t('help.chan.bi.strictDesc') }}
                  </div>
                </div>
              </div>

              <div id="chan-seg" class="bg-gray-50 dark:bg-gray-800/50 p-6 rounded-xl border border-gray-100 dark:border-gray-800">
                <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3">{{ t('help.chan.seg.title') }}</h3>
                <p class="text-sm text-gray-600 dark:text-gray-300" v-html="t('help.chan.seg.def')"></p>
              </div>

              <div id="chan-pivot" class="bg-gray-50 dark:bg-gray-800/50 p-6 rounded-xl border border-gray-100 dark:border-gray-800">
                <h3 class="text-lg font-bold text-gray-900 dark:text-white mb-3">{{ t('help.chan.pivot.title') }}</h3>
                <p class="text-sm text-gray-600 dark:text-gray-300" v-html="t('help.chan.pivot.def')"></p>
              </div>
            </div>
          </section>

          <!-- 3. Buy/Sell Points -->
          <section id="bs" class="scroll-mt-20">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-6">{{ t('help.bs.title') }}</h2>
            <div id="bs-type" class="overflow-hidden border border-gray-200 dark:border-gray-700 rounded-lg mb-8">
              <table class="w-full text-sm text-left">
                <thead class="bg-gray-50 dark:bg-gray-800 text-gray-700 dark:text-gray-200">
                  <tr>
                    <th class="p-4 font-semibold">{{ t('help.bs.table.type') }}</th>
                    <th class="p-4 font-semibold">{{ t('help.bs.table.name') }}</th>
                    <th class="p-4 font-semibold">{{ t('help.bs.table.desc') }}</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-gray-200 dark:divide-gray-700 bg-white dark:bg-gray-900">
                  <tr>
                    <td class="p-4 font-medium">1B / 1S</td>
                    <td class="p-4">{{ t('help.bs.table.t1.name') }}</td>
                    <td class="p-4 text-gray-600 dark:text-gray-400">{{ t('help.bs.table.t1.desc') }}</td>
                  </tr>
                  <tr>
                    <td class="p-4 font-medium">2B / 2S</td>
                    <td class="p-4">{{ t('help.bs.table.t2.name') }}</td>
                    <td class="p-4 text-gray-600 dark:text-gray-400">{{ t('help.bs.table.t2.desc') }}</td>
                  </tr>
                  <tr>
                    <td class="p-4 font-medium">3B / 3S</td>
                    <td class="p-4">{{ t('help.bs.table.t3.name') }}</td>
                    <td class="p-4 text-gray-600 dark:text-gray-400">{{ t('help.bs.table.t3.desc') }}</td>
                  </tr>
                  <tr>
                    <td class="p-4 font-medium">L2B / L2S</td>
                    <td class="p-4">{{ t('help.bs.table.l2.name') }}</td>
                    <td class="p-4 text-gray-600 dark:text-gray-400">{{ t('help.bs.table.l2.desc') }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div id="bs-visual" class="bg-amber-50 dark:bg-amber-900/20 p-4 rounded-lg text-amber-800 dark:text-amber-200 text-sm">
              <h4 class="font-bold mb-2">{{ t('help.bs.visual.title') }}</h4>
              <p>{{ t('help.bs.visual.desc') }}</p>
            </div>
          </section>

          <!-- 4. AI Analysis -->
          <section id="ai" class="scroll-mt-20">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-4">{{ t('help.ai.title') }}</h2>
            <div class="bg-white dark:bg-gray-800 p-6 rounded-xl border border-gray-200 dark:border-gray-700 shadow-sm">
              <p class="mb-4 text-gray-600 dark:text-gray-300">{{ t('help.ai.intro') }}</p>
              <ul class="list-disc pl-5 space-y-2 text-gray-600 dark:text-gray-300">
                <li v-html="t('help.ai.feature')"></li>
                <li v-html="t('help.ai.model')"></li>
                <li v-html="t('help.ai.score')"></li>
              </ul>
            </div>
          </section>

          <!-- 5. Interface -->
          <section id="ui" class="scroll-mt-20">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-6">{{ t('help.ui.title') }}</h2>
            
            <div id="ui-sidebar" class="mb-8">
              <h3 class="text-xl font-semibold text-gray-800 dark:text-gray-100 mb-4">{{ t('help.ui.sidebar.title') }}</h3>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div class="p-4 rounded border border-gray-100 dark:border-gray-800">
                  <div class="font-medium text-blue-600 dark:text-blue-400 mb-1" v-html="t('help.ui.sidebar.stock')"></div>
                  <div class="text-xs text-gray-500" v-html="t('help.ui.sidebar.stockDesc')"></div>
                </div>
                <div class="p-4 rounded border border-gray-100 dark:border-gray-800">
                  <div class="font-medium text-blue-600 dark:text-blue-400 mb-1" v-html="t('help.ui.sidebar.period')"></div>
                  <div class="text-xs text-gray-500" v-html="t('help.ui.sidebar.periodDesc')"></div>
                </div>
                <div class="p-4 rounded border border-gray-100 dark:border-gray-800">
                  <div class="font-medium text-blue-600 dark:text-blue-400 mb-1" v-html="t('help.ui.sidebar.model')"></div>
                  <div class="text-xs text-gray-500" v-html="t('help.ui.sidebar.modelDesc')"></div>
                </div>
                <div class="p-4 rounded border border-gray-100 dark:border-gray-800">
                  <div class="font-medium text-blue-600 dark:text-blue-400 mb-1" v-html="t('help.ui.sidebar.pretrained')"></div>
                  <div class="text-xs text-gray-500" v-html="t('help.ui.sidebar.pretrainedDesc')"></div>
                </div>
              </div>
            </div>

            <div id="ui-chart">
              <h3 class="text-xl font-semibold text-gray-800 dark:text-gray-100 mb-4">{{ t('help.ui.chart.title') }}</h3>
              <ul class="list-disc pl-5 space-y-2 text-gray-600 dark:text-gray-300 text-sm">
                <li v-html="t('help.ui.chart.kline')"></li>
                <li v-html="t('help.ui.chart.bi')"></li>
                <li v-html="t('help.ui.chart.seg')"></li>
                <li v-html="t('help.ui.chart.center')"></li>
              </ul>
            </div>
          </section>

          <!-- 6. Strategy -->
          <section id="strategy" class="scroll-mt-20">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-6">{{ t('help.strategy.title') }}</h2>
            <p class="text-gray-600 dark:text-gray-300 mb-6">{{ t('help.strategy.intro') }}</p>

            <div id="strategy-presets" class="mb-8">
              <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-3">{{ t('help.strategy.presets.title') }}</h3>
              <div class="grid gap-3">
                <div v-for="(key, idx) in ['dealer', 'quantControl', 'practical', 'conservative', 'aggressive']" :key="idx"
                     class="p-3 bg-gray-50 dark:bg-gray-800 rounded border-l-4 border-blue-500">
                  <div class="text-sm text-gray-700 dark:text-gray-200" v-html="t(`help.strategy.presets.${key}`)"></div>
                </div>
              </div>
            </div>

            <div id="strategy-basic" class="mb-8">
              <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-3">{{ t('help.strategy.basic.title') }}</h3>
              <ul class="list-disc pl-5 space-y-2 text-sm text-gray-600 dark:text-gray-300">
                <li v-html="t('help.strategy.basic.scope')"></li>
                <li v-html="t('help.strategy.basic.accuracy')"></li>
                <li v-html="t('help.strategy.basic.len')"></li>
              </ul>
            </div>

            <div id="strategy-advanced" class="mb-8">
              <h3 class="text-lg font-bold text-gray-800 dark:text-gray-100 mb-3">{{ t('help.strategy.advanced.title') }}</h3>
              <div class="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4 text-sm text-gray-600 dark:text-gray-300">
                <div v-html="t('help.strategy.advanced.recentAcc')"></div>
                <div v-html="t('help.strategy.advanced.profit')"></div>
                <div v-html="t('help.strategy.advanced.rolling')"></div>
                <div v-html="t('help.strategy.advanced.strict')"></div>
                <div v-html="t('help.strategy.advanced.signal')"></div>
              </div>
            </div>

            <div id="strategy-examples" class="bg-indigo-50 dark:bg-indigo-900/20 p-6 rounded-xl">
              <h3 class="text-lg font-bold text-indigo-900 dark:text-indigo-200 mb-4">{{ t('help.strategy.examples.title') }}</h3>
              <div class="space-y-4">
                <div class="bg-white dark:bg-gray-800 p-4 rounded shadow-sm">
                  <p class="text-sm text-gray-700 dark:text-gray-200" v-html="t('help.strategy.examples.ex1')"></p>
                </div>
                <div class="bg-white dark:bg-gray-800 p-4 rounded shadow-sm">
                  <p class="text-sm text-gray-700 dark:text-gray-200" v-html="t('help.strategy.examples.ex2')"></p>
                </div>
                <div class="bg-white dark:bg-gray-800 p-4 rounded shadow-sm">
                  <p class="text-sm text-gray-700 dark:text-gray-200" v-html="t('help.strategy.examples.ex3')"></p>
                </div>
              </div>
            </div>
          </section>

          <!-- 7. Pretraining -->
          <section id="pretrain" class="scroll-mt-20">
            <h2 class="text-2xl font-bold text-gray-900 dark:text-white mb-4">{{ t('help.pretrain.title') }}</h2>
            <p class="text-gray-600 dark:text-gray-300 mb-4">{{ t('help.pretrain.intro') }}</p>
            <ul class="list-disc pl-5 space-y-2 text-sm text-gray-600 dark:text-gray-300">
              <li v-html="t('help.pretrain.pool')"></li>
              <li v-html="t('help.pretrain.force')"></li>
            </ul>
          </section>

        </div>
      </div>
    </div>
  </div>
</template>
