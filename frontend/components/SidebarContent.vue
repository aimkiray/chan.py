<template>
  <div class="flex flex-col h-full">
    <div class="flex-1">
      <h3
        class="text-sm font-semibold text-gray-500 dark:text-gray-400 uppercase flex items-center justify-between select-none cursor-pointer lg:cursor-default"
        :class="[{ 'mb-6': !collapsed || mode === 'desktop', 'mb-0': collapsed && mode !== 'desktop' }, collapsed && mode !== 'desktop' ? 'h-full' : '']"
        @click="$emit('toggle')"
      >
        <div class="flex items-center gap-2">
          <svg v-if="activeTab === 'help'" width="16" height="16" viewBox="0 0 24 24" class="text-gray-500"><path :d="MemoryBook" /></svg>
          {{ activeTab === 'help' ? t('help.tocTitle') : t('sidebar.params') }}
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
      
      <div v-if="['analysis', 'intraday'].includes(activeTab) && (!collapsed || mode === 'desktop')" class="space-y-3">
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

          <div>
            <div class="flex items-center gap-1 mb-2">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.autype') }}</label>
              <UTooltip :text="t('sidebar.autypeHint')" :popper="{ placement: 'right' }">
                <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
              </UTooltip>
            </div>
            <USelectMenu
              v-model="proxyAutype"
              :options="autypeOptions"
              value-attribute="value"
              option-attribute="label"
              class="w-full"
            />
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
                   :options="dataSrcOptions"
                   value-attribute="value"
                   option-attribute="label"
                   class="w-full"
                >
                   <template #label>
                      {{ t('app.dataSrcOptions.' + proxyDataSrc) }}
                   </template>
                   <template #option="{ option }">
                      {{ option.label }}
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
                   :options="modelOptions"
                   value-attribute="value"
                   option-attribute="label"
                   class="w-full"
                >
                   <template #label>
                      {{ t('app.modelOptions.' + proxyModel) }}
                   </template>
                   <template #option="{ option }">
                      {{ option.label }}
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
                   <span class="text-xs text-gray-500">{{ proxyDataLengthYears.toFixed(1) }} {{ yearUnit }}</span>
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

          <!-- Advanced Configs -->
          <div class="pt-4 border-t border-gray-100 dark:border-gray-800">
            <div class="pt-2">
              <UButton size="xs" variant="ghost" color="gray" block @click="showAdvancedAnalysis = !showAdvancedAnalysis" :icon="showAdvancedAnalysis ? 'i-heroicons-chevron-up' : 'i-heroicons-chevron-down'">
                {{ showAdvancedAnalysis ? t('sidebar.collapseAdvanced') : t('sidebar.expandAdvanced') }}
              </UButton>
            </div>

            <div v-if="showAdvancedAnalysis" class="space-y-3 pt-2 border-t border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/50 p-2 rounded mt-2">
              <div class="flex items-center gap-2">
                  <UCheckbox v-model="proxyTriggerStep" :label="t('sidebar.stepCalc')" />
                  <UTooltip :text="t('sidebar.stepHint')" :popper="{ placement: 'right' }">
                     <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
               </div>
               
               <div class="flex items-center gap-2">
                  <UCheckbox v-model="proxyBiStrict" :label="t('sidebar.strictBi')" />
                  <UTooltip :text="t('sidebar.strictHint')" :popper="{ placement: 'right' }">
                     <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
               </div>

               <div class="flex items-center gap-2">
                  <UCheckbox v-model="proxyEnableRollingLookback" :label="t('sidebar.enableRollingLookback')" />
                  <UTooltip :text="t('sidebar.rollingLookbackHint')" :popper="{ placement: 'right' }">
                     <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
               </div>

               <div class="flex items-center gap-2">
                  <UCheckbox v-model="proxyRankTransformEnabled" :label="t('sidebar.rankTransform')" />
                  <UTooltip :text="t('sidebar.rankTransformHint')" :popper="{ placement: 'right' }">
                     <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
               </div>

               <div>
                 <div class="flex items-center gap-1 mb-2">
                   <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.onlyFeatures') }}</label>
                   <UTooltip :text="t('sidebar.onlyFeaturesHint')" :popper="{ placement: 'right' }">
                     <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                   </UTooltip>
                 </div>
                 <UInput v-model="proxyOnlyFeatures" :placeholder="t('sidebar.onlyFeaturesPlaceholder')" class="w-full" />
               </div>

               <div class="flex items-center gap-2">
                  <UCheckbox v-model="autoProfitThreshold" :label="t('strategy.useAutoProfitThreshold')" />
               </div>

               <div v-if="!autoProfitThreshold">
                 <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.profitThreshold') }}</label>
                 <UInput type="number" v-model="proxyStrategyForm.profit_threshold" step="0.005" min="0" max="1" class="w-full" />
               </div>

               <div>
                 <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.autoProfitQuantile') }}</label>
                 <UInput type="number" v-model="proxyStrategyForm.auto_profit_quantile" step="0.05" min="0" max="1" :disabled="!autoProfitThreshold" class="w-full" />
               </div>

               <div>
                 <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.profitLookahead') }}</label>
                 <UInput type="number" v-model="proxyStrategyForm.profit_lookahead" step="1" min="0" max="250" class="w-full" />
               </div>

               <div>
                 <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.startDate') }}</label>
                 <UInput type="date" v-model="proxyStrategyForm.start_date" class="w-full" />
               </div>

               <div>
                 <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.endDate') }}</label>
                 <UInput type="date" v-model="proxyStrategyForm.end_date" class="w-full" />
               </div>

               <div class="flex items-center gap-2">
                 <UCheckbox v-model="proxyStrategyForm.force_refresh" :label="t('strategy.forceRefresh')" />
               </div>

               <div v-if="activeTab === 'analysis'" class="flex items-center gap-2">
                  <UCheckbox v-model="proxyBlend" :label="t('app.pretrainedBlend')" />
                  <UTooltip :text="t('sidebar.blendHint')" :popper="{ placement: 'right' }">
                     <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
               </div>

               <div v-if="activeTab === 'intraday'" class="flex items-center gap-2">
                  <UCheckbox v-model="proxyIntradayBlend" :label="t('app.pretrainedBlend')" />
                  <UTooltip :text="t('sidebar.blendHint')" :popper="{ placement: 'right' }">
                     <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help" />
                  </UTooltip>
               </div>
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

      <!-- Help TOC -->
      <div v-else-if="activeTab === 'help' && (!collapsed || mode === 'desktop')" class="space-y-1 overflow-y-auto pr-1">
        <div v-for="section in tocStructure" :key="section.id" class="space-y-1">
          <!-- Level 1 -->
          <button
            @click="jumpTo(section.id)"
            class="w-full text-left text-sm font-semibold px-2 py-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-800 dark:text-gray-200 transition-colors"
          >
            {{ t(section.labelKey) }}
          </button>

          <!-- Level 2 -->
          <div v-if="section.children" class="ml-2 pl-2 border-l border-gray-200 dark:border-gray-800 space-y-1">
            <div v-for="child in section.children" :key="child.id">
              <button
                @click="jumpTo(child.id)"
                class="w-full text-left text-xs px-2 py-1.5 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-600 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
              >
                {{ t(child.labelKey) }}
              </button>
              
              <!-- Level 3 (if any) -->
              <div v-if="child.children" class="ml-2 pl-2 border-l border-gray-200 dark:border-gray-800 mt-1 space-y-1">
                <button
                  v-for="subChild in child.children"
                  :key="subChild.id"
                  @click="jumpTo(subChild.id)"
                  class="w-full text-left text-[10px] px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                >
                  {{ t(subChild.labelKey) }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- History Tab Configs -->
      <div v-if="activeTab === 'history' && (!collapsed || mode === 'desktop')" class="space-y-3">
          <!-- Stock Code -->
          <div>
            <div class="flex items-center gap-1 mb-2">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.stockCode') }}</label>
            </div>
            <UInput v-model="proxyCode" :placeholder="t('history.searchPlaceholder')" class="w-full" />
          </div>

          <UButton block color="primary" icon="i-heroicons-magnifying-glass" :loading="loading" @click="$emit('searchHistory', proxyCode)">
            {{ t('history.search') }}
          </UButton>
      </div>

      <!-- Pretrain Tab Configs -->
      <div v-if="activeTab === 'pretrain' && (!collapsed || mode === 'desktop')" class="space-y-3">
        <!-- Stock Pool -->
          <div>
              <div class="flex items-center gap-1 mb-2">
                  <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('pretrain.stockPool') }}</label>
              </div>
              <div class="flex items-center gap-2">
                  <USelectMenu
                      v-model="proxyPretrainConfig.selectedPoolId"
                      :options="poolOptions"
                      value-attribute="value"
                      option-attribute="label"
                      size="sm"
                      class="flex-1 min-w-0"
                      :disabled="proxyPretrainConfig.dataSrc !== 'clickhouse' || loading"
                      :placeholder="proxyPretrainConfig.dataSrc !== 'clickhouse' ? t('pretrain.poolClickhouseOnly') : t('pretrain.poolPlaceholder')"
                  />
                  <UButton
                      size="sm"
                      color="gray"
                      class="flex-shrink-0"
                      :loading="poolMembersLoading"
                      :disabled="proxyPretrainConfig.dataSrc !== 'clickhouse' || !proxyPretrainConfig.selectedPoolId"
                      @click="fillCodesFromPool"
                  >
                      {{ t('pretrain.load') }}
                  </UButton>
              </div>
              <div class="mt-2 flex items-center justify-between gap-2">
                  <UCheckbox v-model="proxyPretrainConfig.overwriteCodes" :label="t('pretrain.overwriteCodes')" />
                  <div class="text-xs text-gray-500">
                      <span v-if="poolMetaLabel">{{ poolMetaLabel }}</span>
                  </div>
              </div>
          </div>

        <!-- Codes -->
        <div>
          <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.codes') }}</div>
          <textarea
            v-model="proxyPretrainConfig.codesText"
            class="w-full min-h-[120px] rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-2 text-sm text-gray-800 dark:text-gray-200 outline-none focus:ring-2 focus:ring-blue-500/40 font-mono"
            :placeholder="t('pretrain.codesPlaceholder')"
          />
          <div class="mt-1 text-xs text-gray-500">{{ t('pretrain.codesHint') }}</div>
        </div>

        <!-- Frequency -->
        <div>
          <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.frequency') }}</div>
          <USelectMenu v-model="proxyPretrainConfig.frequency" :options="frequencyOptions" value-attribute="value" option-attribute="label" size="sm" class="w-full" />
        </div>

        <!-- Model -->
        <div>
          <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.model') }}</div>
          <USelectMenu v-model="proxyPretrainConfig.model" :options="modelOptions" value-attribute="value" option-attribute="label" size="sm" class="w-full" />
        </div>

        <!-- Data Source -->
        <div>
          <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.dataSrc') }}</div>
          <USelectMenu v-model="proxyPretrainConfig.dataSrc" :options="dataSrcOptions" value-attribute="value" option-attribute="label" size="sm" class="w-full" />
        </div>

        <!-- Data Length -->
        <div>
          <div class="flex items-center justify-between mb-2">
             <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('pretrain.dataLength') }}</label>
             <UCheckbox 
               :model-value="proxyPretrainConfig.dataLengthMode === 'max'"
               @update:model-value="val => proxyPretrainConfig.dataLengthMode = val ? 'max' : 'years'"
               :label="t('pretrain.dataLengthMax')" 
             />
          </div>
          
          <div v-if="proxyPretrainConfig.dataLengthMode === 'years'">
             <div class="flex justify-between text-xs text-gray-500 mb-1">
                <span>{{ Number(proxyPretrainConfig.dataLengthYears).toFixed(1) }} {{ yearUnit }}</span>
             </div>
             <URange 
               v-model="proxyPretrainConfig.dataLengthYears" 
               :min="0.1" 
               :max="10" 
               :step="0.1" 
               size="sm" 
               class="w-full"
             />
          </div>
        </div>

        <!-- Rank Mode -->
        <div>
          <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.rankMode') }}</div>
          <USelectMenu v-model="proxyPretrainConfig.rankMode" :options="rankModeOptions" value-attribute="value" option-attribute="label" size="sm" class="w-full" />
        </div>

        <!-- Rolling Sample Mode -->
        <div>
          <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.rollingSampleMode') }}</div>
          <USelectMenu v-model="proxyPretrainConfig.rollingSampleMode" :options="rollingSampleModeOptions" value-attribute="value" option-attribute="label" size="sm" class="w-full" />
        </div>

        <div class="grid grid-cols-2 gap-2">
          <div>
            <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.rollingStride') }}</div>
            <UInput v-model="proxyPretrainConfig.rollingStride" type="number" />
          </div>
          <div>
            <div class="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('pretrain.onlyFeatures') }}</div>
            <UInput v-model="proxyPretrainConfig.onlyFeatures" :placeholder="t('pretrain.onlyFeaturesPlaceholder')" />
          </div>
        </div>

        <!-- Force Refresh -->
        <div class="flex items-center justify-between gap-2">
          <UCheckbox v-model="proxyPretrainConfig.forceRefresh" :label="t('pretrain.forceRefresh')" />
        </div>
        
        <UButton block color="primary" :loading="loading" @click="$emit('runPretrain')">
          {{ t('pretrain.run') }}
        </UButton>
      </div>

      <div v-if="activeTab === 'backtest' && (!collapsed || mode === 'desktop')" class="space-y-3">
        <div>
          <div class="flex items-center justify-between gap-2 mb-2">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.simId') }}</label>
            <UButton size="2xs" color="gray" variant="ghost" icon="i-heroicons-plus" @click="generateSimId">{{ t('strategy.newSim') }}</UButton>
          </div>
          <UInput v-model="proxyBacktestSimId" :placeholder="t('strategy.simIdPlaceholder')" class="w-full" />
        </div>

        <div>
          <div class="flex items-center gap-1 mb-2">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.poolId') }}</label>
          </div>
          <USelectMenu v-model="proxyPaperConfig.pool_id" :options="poolOptions" value-attribute="value" option-attribute="label" class="w-full" />
        </div>

        <div>
          <div class="flex items-center gap-1 mb-2">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.dataSrc') }}</label>
          </div>
          <USelectMenu v-model="proxyPaperConfig.data_src" :options="dataSrcOptions" value-attribute="value" option-attribute="label" class="w-full" />
        </div>

        <div>
          <div class="flex items-center gap-1 mb-2">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.autype') }}</label>
          </div>
          <USelectMenu v-model="proxyPaperConfig.autype" :options="autypeOptions" value-attribute="value" option-attribute="label" class="w-full" />
        </div>

        <div>
          <div class="flex items-center gap-1 mb-2">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.frequency') }}</label>
          </div>
          <USelectMenu v-model="proxyPaperConfig.frequency" :options="paperFrequencyOptions" value-attribute="value" option-attribute="label" class="w-full" />
        </div>

        <div>
          <div class="flex items-center gap-1 mb-2">
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.initialCapital') }}</label>
          </div>
          <UInput type="number" v-model="proxyPaperConfig.initial_capital" class="w-full" />
        </div>

        <div class="pt-2">
          <UButton
            size="xs"
            variant="ghost"
            color="gray"
            block
            @click="showAdvancedPaper = !showAdvancedPaper"
            :icon="showAdvancedPaper ? 'i-heroicons-chevron-up' : 'i-heroicons-chevron-down'"
          >
            {{ showAdvancedPaper ? t('sidebar.collapseAdvanced') : t('sidebar.expandAdvanced') }}
          </UButton>
        </div>

        <div v-if="showAdvancedPaper" class="space-y-3 pt-2 border-t border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/50 p-2 rounded">
          <div class="space-y-2">
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.topN') }}</label>
              <UInput v-model="proxyPaperParams.top_n" type="number" class="w-full" />
            </div>
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.minProb') }}</label>
              <UInput v-model="proxyPaperParams.min_prob" type="number" class="w-full" />
            </div>
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.holdingBars') }}</label>
              <UInput v-model="proxyPaperParams.holding_period" type="number" class="w-full" />
            </div>
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.tradeCost') }}</label>
              <UInput v-model="proxyPaperParams.trade_cost" type="number" class="w-full" />
            </div>
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.lookbackDays') }}</label>
              <UInput v-model="proxyPaperParams.lookback_days" type="number" class="w-full" />
            </div>
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.limit') }}</label>
              <UInput v-model="proxyPaperParams.limit" type="number" class="w-full" />
            </div>
          </div>

          <div class="pt-2 border-t border-gray-100 dark:border-gray-700 space-y-2">
            <div class="flex items-center justify-between gap-2">
              <UCheckbox v-model="proxyPaperParams.use_latest_available_day" :label="t('strategy.useLatestAvailableDay')" />
            </div>
            <div class="space-y-2">
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.walkForwardMaxFolds') }}</label>
                <UInput v-model="proxyPaperParams.walk_forward_max_folds" type="number" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.rollingSampleMode') }}</label>
                <USelectMenu v-model="proxyPaperParams.rolling_sample_mode" :options="paperRollingSampleModeOptions" value-attribute="value" option-attribute="label" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.rollingStride') }}</label>
                <UInput v-model="proxyPaperParams.rolling_stride" type="number" class="w-full" />
              </div>
            </div>
          </div>

          <div class="pt-2 border-t border-gray-100 dark:border-gray-700 space-y-2">
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.sellMode') }}</label>
              <USelectMenu v-model="proxyPaperParams.sell_mode" :options="sellModeOptions" value-attribute="value" option-attribute="label" class="w-full" />
              <div class="mt-1 text-xs text-gray-500">{{ paperSellModeHint }}</div>
            </div>
            <div v-if="paperDecisionTimeVisible">
              <label class="text-xs text-gray-500">{{ t('strategy.t1DecisionTime') }}</label>
              <UInput v-model="proxyPaperParams.t1_decision_time" :placeholder="t('strategy.t1DecisionTimePlaceholder')" class="w-full" />
              <div v-if="t1DecisionTimeError" class="mt-1 text-xs text-red-500">{{ t1DecisionTimeError }}</div>
            </div>
            <div v-if="paperGapRollVisible" class="space-y-2 pl-2 border-l-2 border-gray-200 dark:border-gray-700">
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.t1GapWeakThreshold') }}</label>
                <UInput v-model="proxyPaperParams.t1_gap_weak_threshold" type="number" step="0.001" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.t1GapStrongThreshold') }}</label>
                <UInput v-model="proxyPaperParams.t1_gap_strong_threshold" type="number" step="0.001" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.t1GapVeryStrongThreshold') }}</label>
                <UInput v-model="proxyPaperParams.t1_gap_very_strong_threshold" type="number" step="0.001" class="w-full" />
              </div>
              <div v-if="t1GapThresholdError" class="text-xs text-red-500">{{ t1GapThresholdError }}</div>
            </div>
          </div>

          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.pretrainedModelKey') }}</label>
            <UInput v-model="proxyPaperParams.pretrained_model_key" :placeholder="t('strategy.pretrainedModelKeyPlaceholder')" class="w-full" />
          </div>

          <div class="pt-2 border-t border-gray-100 dark:border-gray-700 space-y-2">
            <div class="flex items-center justify-between gap-2">
              <UCheckbox v-model="proxyPaperParams.risk_filter_enabled" :label="t('strategy.riskEnabled')" />
            </div>
            <div class="space-y-2">
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskMode') }}</label>
                <USelectMenu v-model="proxyPaperParams.risk_mode" :options="riskModeOptions" value-attribute="value" option-attribute="label" class="w-full" />
              </div>
              <div>
                 <label class="text-xs text-gray-500">{{ t('strategy.riskIndexCode') }}</label>
                 <USelectMenu v-model="proxyPaperParams.risk_index_code" :searchable="searchIndices" :searchable-placeholder="t('common.typeToSearch')" :loading="searchingIndices" :options="indexOptions" value-attribute="value" option-attribute="label" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskGapDownThreshold') }}</label>
                <UInput v-model="proxyPaperParams.risk_gap_down_threshold" type="number" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskDropThreshold') }}</label>
                <UInput v-model="proxyPaperParams.risk_drop_threshold" type="number" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskFlatDropThreshold') }}</label>
                <UInput v-model="proxyPaperParams.risk_flat_drop_threshold" type="number" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskDataFailMode') }}</label>
                <USelectMenu v-model="proxyPaperParams.risk_data_fail_mode" :options="riskFailModeOptions" value-attribute="value" option-attribute="label" class="w-full" />
              </div>
            </div>
            <div class="pt-2 border-t border-gray-100 dark:border-gray-700">
              <UCheckbox v-model="proxyPaperParams.risk_intraday_enabled" :label="t('strategy.riskIntradayEnabled')" />
            </div>
            <div class="space-y-2">
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskTrendMaWindow') }}</label>
                <UInput v-model="proxyPaperParams.risk_trend_ma_window" type="number" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskIntradayBearThreshold') }}</label>
                <UInput v-model="proxyPaperParams.risk_intraday_bear_threshold" type="number" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskIntradayBullThreshold') }}</label>
                <UInput v-model="proxyPaperParams.risk_intraday_bull_threshold" type="number" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskIntradayDataFailMode') }}</label>
                <USelectMenu v-model="proxyPaperParams.risk_intraday_data_fail_mode" :options="riskFailModeOptions" value-attribute="value" option-attribute="label" class="w-full" />
              </div>
            </div>
          </div>
        </div>

        <div class="pt-2 border-t border-gray-100 dark:border-gray-800 space-y-2">
          <div class="text-xs text-gray-500">{{ t('portfolio.backtestConfigManage') }}</div>
          <div class="grid grid-cols-2 gap-2">
            <UButton size="xs" color="gray" variant="soft" @click="openBacktestSave">{{ t('portfolio.saveConfig') }}</UButton>
            <UButton size="xs" color="gray" variant="soft" @click="openBacktestLoad">{{ t('portfolio.loadConfig') }}</UButton>
            <UButton size="xs" color="gray" variant="soft" @click="openBacktestExport">{{ t('portfolio.exportConfig') }}</UButton>
            <UButton size="xs" color="gray" variant="soft" @click="openBacktestImport">{{ t('portfolio.importConfig') }}</UButton>
          </div>
        </div>

        <div class="pt-4 border-t border-gray-100 dark:border-gray-800 space-y-2 pb-4">
          <UButton block color="primary" :disabled="loading || backtestLoading" :loading="backtestLoading" @click="$emit('runBacktest')">{{ t('portfolio.runBacktest') }}</UButton>
          <UButton block color="gray" variant="soft" :disabled="paperRunDisabled" @click="$emit('runPaperStep')">{{ t('portfolio.runPaperDirect') }}</UButton>
        </div>
      </div>

      <div v-else-if="activeTab === 'paper' && (!collapsed || mode === 'desktop')" class="space-y-3 pb-4">
        <div class="text-xs text-gray-500">
          {{ t('portfolio.paperRunCreateHint') }}
        </div>

        <div>
          <label class="text-xs text-gray-500">{{ t('strategy.runName') }}</label>
          <UInput v-model="proxyPaperRunForm.name" class="w-full" />
        </div>

        <div>
          <label class="text-xs text-gray-500">{{ t('strategy.offsetSeconds') }}</label>
          <UInput v-model="proxyPaperRunForm.offset_seconds" type="number" class="w-full" />
        </div>

        <div class="flex items-center justify-between gap-2">
          <UCheckbox v-model="proxyPaperRunForm.auto_resume" :label="t('strategy.autoResume')" />
        </div>

        <div class="space-y-2 pt-2 border-t border-gray-100 dark:border-gray-800">
          <div class="flex items-center justify-between gap-2">
            <UCheckbox v-model="proxyPaperRunForm.notify_enabled" :label="t('strategy.notify')" />
          </div>

          <div v-if="proxyPaperRunForm.notify_enabled" class="space-y-2 pl-2 border-l-2 border-gray-200 dark:border-gray-700">
            <div>
              <label class="text-xs text-gray-500">{{ t('strategy.notifyMode') }}</label>
              <USelectMenu v-model="paperNotifyMode" :options="notifyModeOptions" value-attribute="value" option-attribute="label" class="w-full" />
            </div>

            <div v-if="paperNotifyShowDingtalk" class="space-y-2">
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.dingtalkWebhook') }}</label>
                <UInput v-model="proxyPaperRunForm.dingtalk_webhook" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.dingtalkSecret') }}</label>
                <UInput v-model="proxyPaperRunForm.dingtalk_secret" class="w-full" />
              </div>
            </div>

            <div v-if="paperNotifyShowSms" class="space-y-2">
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.smsPhone') }}</label>
                <UInput v-model="proxyPaperRunForm.sms_phone" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.smsToken') }}</label>
                <UInput v-model="proxyPaperRunForm.sms_token" class="w-full" />
              </div>
            </div>
          </div>
        </div>

        <div class="pt-2 space-y-2">
          <div class="flex items-center justify-between gap-2">
            <UCheckbox v-model="proxyPaperRunForm.start" :label="t('strategy.startImmediately')" />
          </div>
          <UButton block color="primary" :disabled="loading" @click="$emit('createPaperRun')">{{ t('strategy.createRun') }}</UButton>
        </div>
      </div>

      <!-- Strategy Tab Configs -->
      <div v-if="activeTab === 'strategy' && (!collapsed || mode === 'desktop')" class="space-y-3">
          <!-- Preset -->
          <div>
            <div class="flex items-center gap-1 mb-2">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.strategyPresets') }}</label>
            </div>
            <USelectMenu v-model="selectedPreset" :options="presets" value-attribute="value" option-attribute="label" @change="applyPreset" class="w-full" />
          </div>

          <!-- Pool -->
          <div>
            <div class="flex items-center gap-1 mb-2">
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.scope') }}</label>
            </div>
            <USelectMenu v-model="proxyStrategyForm.pool_id" :options="poolOptions" value-attribute="value" option-attribute="label" class="w-full" />
          </div>

          <!-- Index Code -->
          <div>
             <div class="flex items-center gap-1 mb-2">
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.indexCode') }}</label>
             </div>
             <USelectMenu v-model="computedIndexCode" :searchable="searchIndices" :searchable-placeholder="t('common.typeToSearch')" :loading="searchingIndices" :options="indexOptions" value-attribute="value" option-attribute="label" class="w-full" />
          </div>

          <!-- Model -->
          <div>
             <div class="flex items-center gap-1 mb-2">
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.model') }}</label>
             </div>
             <USelectMenu v-model="proxyStrategyForm.model" :options="['xgboost', 'lightgbm', 'mlp', 'logistic_regression']" class="w-full" />
          </div>

          <!-- Frequency -->
          <div>
             <div class="flex items-center gap-1 mb-2">
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('sidebar.frequency') }}</label>
             </div>
             <USelectMenu 
                v-model="proxyStrategyForm.frequency" 
                :options="strategyFrequencyOptions"
                value-attribute="value"
                option-attribute="label"
                class="w-full"
             />
          </div>

          <!-- Min Accuracy -->
          <div>
             <div class="flex items-center gap-1 mb-2">
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.minAccuracy') }}</label>
             </div>
             <UInput type="number" v-model="proxyStrategyForm.min_accuracy" step="0.05" min="0" max="1" class="w-full" />
          </div>

          <!-- Data Length -->
          <div>
             <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('app.dataLength') }}</label>
             <UInput type="number" v-model="proxyStrategyForm.data_length_years" step="0.1" min="0.1" max="10" class="w-full" />
          </div>

          <!-- Advanced Toggle -->
          <div class="pt-2">
             <UButton size="xs" variant="ghost" color="gray" block @click="showAdvancedStrategy = !showAdvancedStrategy" :icon="showAdvancedStrategy ? 'i-heroicons-chevron-up' : 'i-heroicons-chevron-down'">
                {{ showAdvancedStrategy ? t('sidebar.collapseAdvanced') : t('sidebar.expandAdvanced') }}
             </UButton>
          </div>

          <!-- Advanced Configs -->
          <div v-if="showAdvancedStrategy" class="space-y-3 pt-2 border-t border-gray-100 dark:border-gray-800 bg-gray-50/50 dark:bg-gray-800/50 p-2 rounded">
              <div>
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.portfolioTopN') }}</label>
                <UInput type="number" v-model="proxyStrategyForm.portfolio_top_n" step="1" min="1" max="100" class="w-full" />
              </div>

              <div>
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.holdingPeriod') }}</label>
                <UInput type="number" v-model="proxyStrategyForm.holding_period" step="1" min="1" max="1000" class="w-full" />
              </div>

              <div class="flex items-center gap-2">
                 <UCheckbox :model-value="Number(proxyStrategyForm.amp_whitelist_days || 0) > 0" :label="t('strategy.ampWhitelistEnable')" @update:model-value="toggleAmpWhitelist" />
              </div>

              <div v-if="Number(proxyStrategyForm.amp_whitelist_days || 0) > 0" class="space-y-2 pl-2 border-l-2 border-gray-200 dark:border-gray-700">
                <div>
                  <label class="text-xs text-gray-500">{{ t('strategy.ampWhitelistDays') }}</label>
                  <UInput type="number" v-model="proxyStrategyForm.amp_whitelist_days" size="2xs" step="1" min="1" max="365" class="w-full" />
                </div>
                <div>
                  <label class="text-xs text-gray-500">{{ t('strategy.ampWhitelistTopFrac') }}</label>
                  <UInput type="number" v-model="proxyStrategyForm.amp_whitelist_top_frac" size="2xs" step="0.05" min="0.05" max="1.0" class="w-full" />
                </div>
              </div>

              <div class="flex items-center gap-2">
                 <UCheckbox v-model="proxyStrategyForm.use_atr_label" :label="t('strategy.useAtrLabel')" />
              </div>

              <div v-if="proxyStrategyForm.use_atr_label" class="space-y-2 pl-2 border-l-2 border-gray-200 dark:border-gray-700">
                <div>
                  <label class="text-xs text-gray-500">{{ t('strategy.atrPeriod') }}</label>
                  <UInput type="number" v-model="proxyStrategyForm.atr_period" size="2xs" step="1" min="1" max="200" class="w-full" />
                </div>
                <div>
                  <label class="text-xs text-gray-500">{{ t('strategy.atrMult') }}</label>
                  <UInput type="number" v-model="proxyStrategyForm.atr_mult" size="2xs" step="0.1" min="0" max="100" class="w-full" />
                </div>
              </div>
              
              <div>
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">{{ t('strategy.profitLookahead') }}</label>
                <UInput type="number" v-model="proxyStrategyForm.profit_lookahead" step="1" min="0" max="250" class="w-full" />
              </div>
          </div>

          <UButton block color="primary" :loading="loading" @click="$emit('runStrategy')" icon="i-heroicons-play">
            {{ t('strategy.run') }}
          </UButton>
      </div>



    </div>

    <UModal v-model="backtestSaveOpen">
      <UCard :ui="{ body: { padding: 'p-4' } }">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">{{ t('portfolio.saveConfig') }}</div>
        <div class="space-y-3">
          <UInput v-model="backtestSaveName" :placeholder="t('portfolio.configNamePlaceholder')" />
          <div class="flex justify-end gap-2">
            <UButton size="sm" color="gray" variant="ghost" :disabled="backtestSaveLoading" @click="backtestSaveOpen = false">
              {{ t('portfolio.close') }}
            </UButton>
            <UButton size="sm" color="primary" :loading="backtestSaveLoading" @click="saveBacktestPreset">
              {{ t('portfolio.save') }}
            </UButton>
          </div>
        </div>
      </UCard>
    </UModal>

    <UModal v-model="backtestLoadOpen">
      <UCard :ui="{ body: { padding: 'p-4' } }">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">{{ t('portfolio.loadConfig') }}</div>
        <div class="space-y-3">
          <USelectMenu v-model="backtestLoadId" :options="backtestPresetOptions" value-attribute="value" option-attribute="label" class="w-full" />
          <div class="flex justify-end gap-2">
            <UButton size="sm" color="gray" variant="ghost" :disabled="backtestLoadLoading" @click="backtestLoadOpen = false">
              {{ t('portfolio.close') }}
            </UButton>
            <UButton size="sm" color="primary" :loading="backtestLoadLoading" @click="applyBacktestPreset">
              {{ t('portfolio.apply') }}
            </UButton>
          </div>
        </div>
      </UCard>
    </UModal>

    <UModal v-model="backtestExportOpen">
      <UCard :ui="{ body: { padding: 'p-4' } }">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">{{ t('portfolio.exportConfig') }}</div>
        <textarea v-model="backtestExportText" readonly class="w-full h-64 p-2 text-xs font-mono border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900"></textarea>
        <div class="flex justify-end gap-2 mt-3">
          <UButton size="sm" color="gray" variant="ghost" @click="backtestExportOpen = false">
            {{ t('portfolio.close') }}
          </UButton>
        </div>
      </UCard>
    </UModal>

    <UModal v-model="backtestImportOpen">
      <UCard :ui="{ body: { padding: 'p-4' } }">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">{{ t('portfolio.importConfig') }}</div>
        <textarea v-model="backtestImportText" class="w-full h-64 p-2 text-xs font-mono border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-900"></textarea>
        <div class="flex justify-end gap-2 mt-3">
          <UButton size="sm" color="gray" variant="ghost" @click="backtestImportOpen = false">
            {{ t('portfolio.close') }}
          </UButton>
          <UButton size="sm" color="primary" :loading="backtestImportLoading" @click="applyBacktestImport">
            {{ t('portfolio.apply') }}
          </UButton>
        </div>
      </UCard>
    </UModal>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import axios from 'axios'
import { MemoryBook, MemoryChevronDown, MemoryChevronLeft } from '@pictogrammers/memory'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()
const toast = useToast()

// Define the structure of the help documentation
const tocStructure = [
  {
    id: 'intro',
    labelKey: 'help.intro.title',
  },
  {
    id: 'chan',
    labelKey: 'help.chan.title',
    children: [
      { id: 'chan-fractal', labelKey: 'help.chan.fractal.title' },
      { id: 'chan-bi', labelKey: 'help.chan.bi.title' },
      { id: 'chan-seg', labelKey: 'help.chan.seg.title' },
      { id: 'chan-pivot', labelKey: 'help.chan.pivot.title' }
    ]
  },
  {
    id: 'bs',
    labelKey: 'help.bs.title',
    children: [
      { id: 'bs-type', labelKey: 'help.bs.type.title' },
      { id: 'bs-visual', labelKey: 'help.bs.visual.title' }
    ]
  },
  {
    id: 'ai',
    labelKey: 'help.ai.title'
  },
  {
    id: 'ui',
    labelKey: 'help.ui.title',
    children: [
      { id: 'ui-sidebar', labelKey: 'help.ui.sidebar.title' },
      { id: 'ui-chart', labelKey: 'help.ui.chart.title' }
    ]
  },
  {
    id: 'strategy',
    labelKey: 'help.strategy.title',
    children: [
      { id: 'strategy-basic', labelKey: 'help.strategy.basic.title' },
      { id: 'strategy-advanced', labelKey: 'help.strategy.advanced.title' },
      { id: 'strategy-presets', labelKey: 'help.strategy.presets.title' },
      { id: 'strategy-examples', labelKey: 'help.strategy.examples.title' }
    ]
  },
  {
    id: 'pretrain',
    labelKey: 'help.pretrain.title'
  }
]

const jumpTo = (id) => {
  if (typeof document === 'undefined') return
  const container = document.getElementById('help-container')
  const el = document.getElementById(id)
  if (!container || !el) return

  const containerRect = container.getBoundingClientRect().top
  const elementRect = el.getBoundingClientRect().top
  const offset = elementRect - containerRect + container.scrollTop - 20

  container.scrollTo({
    top: offset,
    behavior: 'smooth'
  })
}

const yearUnit = computed(() => t('common.yearShort'))
const strategyFrequencyOptions = computed(() => [
  { label: t('periods.1d'), value: '1d' },
  { label: t('periods.60m'), value: '60m' },
  { label: t('periods.30m'), value: '30m' },
  { label: t('periods.5m'), value: '5m' }
])
const paperFrequencyOptions = computed(() => [
  { label: t('periods.60m'), value: '60m' },
  { label: t('periods.30m'), value: '30m' },
  { label: t('periods.15m'), value: '15m' },
  { label: t('periods.5m'), value: '5m' },
  { label: t('periods.1m'), value: '1m' }
])
const riskModeOptions = computed(() => [
  { label: t('strategy.riskModeDrop'), value: 'drop' },
  { label: t('strategy.riskModeAtr'), value: 'atr' },
  { label: t('strategy.riskModeBoth'), value: 'both' }
])
const sellModeOptions = computed(() => [
  { label: t('strategy.sellModeBaseline'), value: 'baseline' },
  { label: t('strategy.sellModeDelayed'), value: 'delayed' },
  { label: t('strategy.sellModeGapRoll'), value: 't1_gap_roll' }
])
const riskFailModeOptions = computed(() => [
  { label: t('strategy.failModePass'), value: 'pass' },
  { label: t('strategy.failModeReduce'), value: 'reduce' },
  { label: t('strategy.failModeFlat'), value: 'flat' }
])
const paperRollingSampleModeOptions = computed(() => [
  { label: t('strategy.rollingModeEveryBar'), value: 'every_bar' },
  { label: t('strategy.rollingModeBsp'), value: 'bsp' }
])

const notifyModeOptions = computed(() => [
  { label: t('strategy.notifyModeDingtalk'), value: 'dingtalk' },
  { label: t('strategy.notifyModeSms'), value: 'sms' },
  { label: t('strategy.notifyModeBoth'), value: 'both' }
])

const paperNotifyMode = computed({
  get: () => String(proxyPaperRunForm.value?.notify_mode || 'dingtalk'),
  set: (val) => {
    const v = String(val || 'dingtalk')
    proxyPaperRunForm.value = { ...(proxyPaperRunForm.value || {}), notify_mode: v }
  }
})

const paperNotifyShowDingtalk = computed(() => {
  if (!proxyPaperRunForm.value?.notify_enabled) return false
  const mode = String(proxyPaperRunForm.value?.notify_mode || 'dingtalk')
  return mode === 'dingtalk' || mode === 'both'
})

const paperNotifyShowSms = computed(() => {
  if (!proxyPaperRunForm.value?.notify_enabled) return false
  const mode = String(proxyPaperRunForm.value?.notify_mode || 'dingtalk')
  return mode === 'sms' || mode === 'both'
})

const props = defineProps({
  activeTab: { type: String, default: 'analysis' },
  code: String,
  autype: { type: String, default: 'qfq' },
  triggerStep: Boolean,
  biStrict: Boolean,
  rankTransformEnabled: { type: Boolean, default: true },
  onlyFeatures: { type: String, default: '' },
  loading: Boolean,
  backtestLoading: { type: Boolean, default: false },
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
  enableRollingLookback: { type: Boolean, default: true },
  
  // Strategy Props
  strategyForm: { type: Object, default: () => ({}) },
  strategyChanConfig: { type: Object, default: () => ({}) },

  paperConfig: { type: Object, default: () => ({}) },
  backtestSimId: { type: String, default: 'default' },
  paperParams: { type: Object, default: () => ({}) },
  paperRunForm: { type: Object, default: () => ({}) },
  
  // Pretrain Props
  pretrainConfig: { type: Object, default: () => ({}) },
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
  'update:code', 'update:autype', 'update:triggerStep', 'update:biStrict', 'update:rankTransformEnabled', 'update:onlyFeatures', 'update:blend', 
  'update:dataSrc', 'update:model', 'update:intradayFreq', 'update:dataLengthYears',
  'update:pretrainedChoice', 'update:intradayBlend', 'update:enableRollingLookback',
  'update:strategyForm', 'update:strategyChanConfig', 'update:pretrainConfig',
  'update:paperConfig', 'update:backtestSimId', 'update:paperParams', 'update:paperRunForm',
  'analyze', 'toggle', 'refreshPretrained', 'refresh', 'runStrategy', 'runPretrain',
  'runPaperStep',
  'runBacktest',
  'createPaperRun'
])

const proxyCode = computed({
  get: () => props.code,
  set: (val) => emit('update:code', val)
})

const proxyAutype = computed({
  get: () => props.autype,
  set: (val) => emit('update:autype', val)
})

const proxyTriggerStep = computed({
  get: () => props.triggerStep,
  set: (val) => emit('update:triggerStep', val)
})

const proxyBiStrict = computed({
  get: () => props.biStrict,
  set: (val) => emit('update:biStrict', val)
})

const proxyRankTransformEnabled = computed({
  get: () => props.rankTransformEnabled,
  set: (val) => emit('update:rankTransformEnabled', val)
})

const proxyOnlyFeatures = computed({
  get: () => props.onlyFeatures,
  set: (val) => emit('update:onlyFeatures', val)
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

const proxyEnableRollingLookback = computed({
  get: () => props.enableRollingLookback,
  set: (val) => emit('update:enableRollingLookback', val)
})

const proxyStrategyForm = computed({
  get: () => props.strategyForm,
  set: (val) => emit('update:strategyForm', val)
})

const proxyStrategyChanConfig = computed({
  get: () => props.strategyChanConfig,
  set: (val) => emit('update:strategyChanConfig', val)
})

const proxyPaperConfig = computed({
  get: () => props.paperConfig,
  set: (val) => emit('update:paperConfig', val)
})

const proxyBacktestSimId = computed({
  get: () => props.backtestSimId,
  set: (val) => emit('update:backtestSimId', val)
})

const proxyPaperParams = computed({
  get: () => props.paperParams,
  set: (val) => emit('update:paperParams', val)
})

const proxyPaperRunForm = computed({
  get: () => props.paperRunForm,
  set: (val) => emit('update:paperRunForm', val)
})

const paperDecisionTimeVisible = computed(() => {
  const mode = String(proxyPaperParams.value?.sell_mode || 'baseline')
  return ['delayed', 't1_gap_roll'].includes(mode)
})

const paperGapRollVisible = computed(() => {
  const mode = String(proxyPaperParams.value?.sell_mode || 'baseline')
  return mode === 't1_gap_roll'
})

const paperSellModeHint = computed(() => {
  const mode = String(proxyPaperParams.value?.sell_mode || 'baseline')
  if (mode === 'delayed') return t('strategy.sellModeHintDelayed')
  if (mode === 't1_gap_roll') return t('strategy.sellModeHintGapRoll')
  return t('strategy.sellModeHintBaseline')
})

const t1DecisionTimeError = computed(() => {
  if (!paperDecisionTimeVisible.value) return ''
  const raw = String(proxyPaperParams.value?.t1_decision_time || '').trim()
  if (!raw) return t('strategy.t1DecisionTimeInvalid')
  const ok = /^([01]\d|2[0-3]):([0-5]\d)$/.test(raw)
  return ok ? '' : t('strategy.t1DecisionTimeInvalid')
})

const t1GapThresholdError = computed(() => {
  if (!paperGapRollVisible.value) return ''
  const strong = Number(proxyPaperParams.value?.t1_gap_strong_threshold)
  const veryStrong = Number(proxyPaperParams.value?.t1_gap_very_strong_threshold)
  if (!Number.isFinite(strong) || !Number.isFinite(veryStrong)) return ''
  return veryStrong < strong ? t('strategy.t1GapThresholdInvalid') : ''
})

const paperRunDisabled = computed(() => {
  if (props.activeTab !== 'backtest') return false
  return !!t1DecisionTimeError.value || !!t1GapThresholdError.value
})

const backtestSaveOpen = ref(false)
const backtestSaveName = ref('')
const backtestSaveLoading = ref(false)
const backtestLoadOpen = ref(false)
const backtestLoadId = ref(null)
const backtestLoadLoading = ref(false)
const backtestExportOpen = ref(false)
const backtestExportText = ref('')
const backtestImportOpen = ref(false)
const backtestImportText = ref('')
const backtestImportLoading = ref(false)
const backtestPresetsLoading = ref(false)
const backtestPresetItems = ref([])

const backtestPresetOptions = computed(() => {
  const items = Array.isArray(backtestPresetItems.value) ? backtestPresetItems.value : []
  return items.map(x => ({
    label: String(x?.name || ''),
    value: Number(x?.id || 0)
  })).filter(x => Number.isFinite(x.value) && x.value > 0 && x.label)
})

const openBacktestSave = () => {
  backtestSaveName.value = ''
  backtestSaveOpen.value = true
}

const openBacktestLoad = async () => {
  await fetchBacktestPresets()
  if (!backtestPresetOptions.value.length) {
    toast.add({ title: t('portfolio.noSavedConfigs'), color: 'gray' })
    return
  }
  backtestLoadId.value = backtestPresetOptions.value[0]?.value || null
  backtestLoadOpen.value = true
}

const openBacktestExport = () => {
  const cfg = proxyPaperConfig.value || {}
  const prm = proxyPaperParams.value || {}
  backtestExportText.value = JSON.stringify({ version: 1, paper_config: cfg, paper_params: prm }, null, 2)
  backtestExportOpen.value = true
}

const openBacktestImport = () => {
  backtestImportText.value = ''
  backtestImportOpen.value = true
}

const fetchBacktestPresets = async () => {
  if (backtestPresetsLoading.value) return
  backtestPresetsLoading.value = true
  try {
    const res = await axios.get('/api/backtest/presets', { params: { limit: 200 } })
    backtestPresetItems.value = Array.isArray(res.data?.items) ? res.data.items : []
  } catch (e) {
    toast.add({ title: t('portfolio.loadFailed'), description: String(e?.response?.data?.detail || e?.message || e), color: 'red' })
  } finally {
    backtestPresetsLoading.value = false
  }
}

const saveBacktestPreset = async () => {
  const nm = String(backtestSaveName.value || '').trim()
  if (!nm) {
    toast.add({ title: t('portfolio.nameRequired'), color: 'red' })
    return
  }
  backtestSaveLoading.value = true
  try {
    const payload = {
      name: nm,
      paper_config: JSON.parse(JSON.stringify(proxyPaperConfig.value || {})),
      paper_params: JSON.parse(JSON.stringify(proxyPaperParams.value || {})),
    }
    await axios.post('/api/backtest/presets', payload)
    toast.add({ title: t('portfolio.saved'), color: 'green' })
    backtestSaveOpen.value = false
    await fetchBacktestPresets()
  } catch (e) {
    toast.add({ title: t('portfolio.saveFailed'), description: String(e?.response?.data?.detail || e?.message || e), color: 'red' })
  } finally {
    backtestSaveLoading.value = false
  }
}

const applyBacktestPreset = async () => {
  const pid = Number(backtestLoadId.value || 0)
  if (!Number.isFinite(pid) || pid <= 0) return
  backtestLoadLoading.value = true
  try {
    const res = await axios.get(`/api/backtest/presets/${encodeURIComponent(String(pid))}`)
    const item = res.data?.item || {}
    const cfg = item.paper_config && typeof item.paper_config === 'object' ? item.paper_config : {}
    const prm = item.paper_params && typeof item.paper_params === 'object' ? item.paper_params : {}
    proxyPaperConfig.value = { ...(proxyPaperConfig.value || {}), ...cfg }
    proxyPaperParams.value = { ...(proxyPaperParams.value || {}), ...prm }
    toast.add({ title: t('portfolio.loaded'), color: 'green' })
    backtestLoadOpen.value = false
  } catch (e) {
    toast.add({ title: t('portfolio.loadFailed'), description: String(e?.response?.data?.detail || e?.message || e), color: 'red' })
  } finally {
    backtestLoadLoading.value = false
  }
}

const applyBacktestImport = async () => {
  backtestImportLoading.value = true
  try {
    const raw = String(backtestImportText.value || '').trim()
    const obj = raw ? JSON.parse(raw) : {}
    const cfg = (obj.paper_config || obj.paperConfig) && typeof (obj.paper_config || obj.paperConfig) === 'object' ? (obj.paper_config || obj.paperConfig) : {}
    const prm = (obj.paper_params || obj.paperParams) && typeof (obj.paper_params || obj.paperParams) === 'object' ? (obj.paper_params || obj.paperParams) : {}
    proxyPaperConfig.value = { ...(proxyPaperConfig.value || {}), ...cfg }
    proxyPaperParams.value = { ...(proxyPaperParams.value || {}), ...prm }
    toast.add({ title: t('portfolio.loaded'), color: 'green' })
    backtestImportOpen.value = false
  } catch (e) {
    toast.add({ title: t('portfolio.invalidJson'), description: String(e?.message || e), color: 'red' })
  } finally {
    backtestImportLoading.value = false
  }
}

const generateSimId = () => {
  let sid = ''
  try {
    sid = typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : ''
  } catch (e) {
    sid = ''
  }
  if (!sid) {
    sid = `${Date.now()}-${Math.random().toString(16).slice(2)}`
  }
  proxyBacktestSimId.value = sid
}

const autoProfitThreshold = computed({
  get: () => {
    const cur = proxyStrategyForm.value || {}
    return cur.profit_threshold === null
  },
  set: (val) => {
    const cur = proxyStrategyForm.value || {}
    if (val) {
      proxyStrategyForm.value = { ...cur, profit_threshold: null }
      return
    }
    proxyStrategyForm.value = { ...cur, profit_threshold: 0.01 }
  }
})

const proxyPretrainConfig = computed({
  get: () => props.pretrainConfig,
  set: (val) => emit('update:pretrainConfig', val)
})

watch(
  () => props.autype,
  (val) => {
    const nextAutype = String(val || '').trim() || 'qfq'

    const curPretrain = proxyPretrainConfig.value || {}
    if (curPretrain.autype !== nextAutype) {
      proxyPretrainConfig.value = { ...curPretrain, autype: nextAutype }
    }

    const curStrategy = proxyStrategyForm.value || {}
    if (curStrategy.autype !== nextAutype) {
      proxyStrategyForm.value = { ...curStrategy, autype: nextAutype }
    }
  },
  { immediate: true }
)

const showAdvancedAnalysis = ref(false)
const showAdvancedPaper = ref(false)

const autypeOptions = computed(() => [
  { label: t('sidebar.autypeQfq'), value: 'qfq' },
  { label: t('sidebar.autypeHfq'), value: 'hfq' }
])
const showAdvancedStrategy = ref(false)
const selectedPreset = ref('custom')

const toggleAmpWhitelist = (enabled) => {
  const cur = proxyStrategyForm.value || {}
  if (enabled) {
    const rawDays = Number(cur.amp_whitelist_days)
    const nextDays = Number.isFinite(rawDays) && rawDays > 0 ? Math.round(rawDays) : 5
    const rawFrac = Number(cur.amp_whitelist_top_frac)
    const nextFrac = Number.isFinite(rawFrac) && rawFrac > 0 ? rawFrac : 0.3
    proxyStrategyForm.value = { ...cur, amp_whitelist_days: nextDays, amp_whitelist_top_frac: nextFrac }
    return
  }
  proxyStrategyForm.value = { ...cur, amp_whitelist_days: 0 }
}

const presets = computed(() => [
  { label: t('sidebar.presetCustom'), value: 'custom', config: {} },
  {
    label: t('sidebar.presetDynamicThreshold'),
    value: 'dynamic_threshold',
    config: {
        min_accuracy: 0.0,
        bi_strict: true,
        model: 'xgboost',
        require_signal: true,
        signal_direction: 'buy',
        signal_lookback: 5,
        min_signal_score: 0.6,
        amp_whitelist_days: 20,
        amp_whitelist_top_frac: 0.3,
        use_atr_label: true,
        atr_period: 14,
        atr_mult: 1.0,
        high_vol_atr_pct_min: null,
        frequency: '30m',
        profit_lookahead: 3,
        portfolio_top_n: 2,
        holding_period: 3,
        profit_threshold: 0.0
    }
  }
])

const applyPreset = () => {
    if (selectedPreset.value === 'custom') return
    const p = (presets.value || []).find(x => x.value === selectedPreset.value)
    if (p && p.config) {
        if (p.config.min_accuracy !== undefined) proxyStrategyForm.value.min_accuracy = p.config.min_accuracy
        if (p.config.min_recent_accuracy !== undefined) proxyStrategyForm.value.min_recent_accuracy = p.config.min_recent_accuracy
        if (p.config.recent_accuracy_years !== undefined) proxyStrategyForm.value.recent_accuracy_years = p.config.recent_accuracy_years
        if (p.config.min_signal_score !== undefined) proxyStrategyForm.value.min_signal_score = p.config.min_signal_score
        if (p.config.min_bsp_count !== undefined) proxyStrategyForm.value.min_bsp_count = p.config.min_bsp_count
        if (p.config.min_test_count !== undefined) proxyStrategyForm.value.min_test_count = p.config.min_test_count
        if (p.config.high_vol_atr_pct_min !== undefined) proxyStrategyForm.value.high_vol_atr_pct_min = p.config.high_vol_atr_pct_min
        if (p.config.amp_whitelist_days !== undefined) proxyStrategyForm.value.amp_whitelist_days = p.config.amp_whitelist_days
        if (p.config.amp_whitelist_top_frac !== undefined) proxyStrategyForm.value.amp_whitelist_top_frac = p.config.amp_whitelist_top_frac
        if (p.config.use_atr_label !== undefined) proxyStrategyForm.value.use_atr_label = p.config.use_atr_label
        if (p.config.atr_period !== undefined) proxyStrategyForm.value.atr_period = p.config.atr_period
        if (p.config.atr_mult !== undefined) proxyStrategyForm.value.atr_mult = p.config.atr_mult
        if (p.config.portfolio_top_n !== undefined) proxyStrategyForm.value.portfolio_top_n = p.config.portfolio_top_n
        if (p.config.holding_period !== undefined) proxyStrategyForm.value.holding_period = p.config.holding_period
        if (p.config.profit_threshold !== undefined) proxyStrategyForm.value.profit_threshold = p.config.profit_threshold
        if (p.config.auto_profit_quantile !== undefined) proxyStrategyForm.value.auto_profit_quantile = p.config.auto_profit_quantile
        if (p.config.profit_lookahead !== undefined) proxyStrategyForm.value.profit_lookahead = p.config.profit_lookahead
        if (p.config.model !== undefined) proxyStrategyForm.value.model = p.config.model
        if (p.config.data_length_years !== undefined) proxyStrategyForm.value.data_length_years = p.config.data_length_years
        if (p.config.enable_rolling_lookback !== undefined) proxyStrategyForm.value.enable_rolling_lookback = p.config.enable_rolling_lookback
        if (p.config.bi_strict !== undefined) proxyStrategyChanConfig.value.bi_strict = p.config.bi_strict
        if (p.config.require_signal !== undefined) proxyStrategyChanConfig.value.require_signal = p.config.require_signal
        if (p.config.signal_direction !== undefined) proxyStrategyChanConfig.value.signal_direction = p.config.signal_direction
        if (p.config.signal_lookback !== undefined) proxyStrategyChanConfig.value.signal_lookback = p.config.signal_lookback
        if (p.config.bs_type !== undefined) proxyStrategyChanConfig.value.bs_type = p.config.bs_type
        if (p.config.macd_algo !== undefined) proxyStrategyChanConfig.value.macd_algo = p.config.macd_algo
        if (p.config.min_zs_cnt !== undefined) proxyStrategyChanConfig.value.min_zs_cnt = p.config.min_zs_cnt
        if (p.config.bsp2_follow_1 !== undefined) proxyStrategyChanConfig.value.bsp2_follow_1 = p.config.bsp2_follow_1
        if (p.config.bsp3_follow_1 !== undefined) proxyStrategyChanConfig.value.bsp3_follow_1 = p.config.bsp3_follow_1
        if (p.config.gap_as_kl !== undefined) proxyStrategyChanConfig.value.gap_as_kl = p.config.gap_as_kl
        if (p.config.bi_allow_sub_peak !== undefined) proxyStrategyChanConfig.value.bi_allow_sub_peak = p.config.bi_allow_sub_peak
    }
}

// Fetch pools logic moved from StrategyPanel? 
// No, let's just hardcode HS300 for now or fetch it if needed.
// StrategyPanel does fetchPools. Let's just use a simple list or assume parent handles it.
// Actually, for simplicity, let's use a hardcoded list or fetch it here.
const poolOptions = ref([{label: 'HS300', value: 'hs300'}])

const poolMembersLoading = ref(false)
const poolMetaLabel = ref('')

const indexOptions = ref([])
const INDEX_OPTIONS_CACHE_KEY = 'mlchan_index_options_cache_v1'
const INDEX_OPTIONS_CACHE_MAX = 200

const loadIndexOptionsCache = () => {
  try {
    if (typeof window === 'undefined') return []
    const raw = localStorage.getItem(INDEX_OPTIONS_CACHE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const cleaned = parsed
      .map(x => ({
        value: String(x?.value || x?.code || '').trim(),
        label: String(x?.label || '').trim(),
        name: String(x?.name || '').trim()
      }))
      .filter(x => x.value)
      .map(x => ({
        value: x.value,
        name: x.name || x.value,
        label: x.label || `${x.name || x.value} (${x.value})`
      }))
    return cleaned.slice(0, INDEX_OPTIONS_CACHE_MAX)
  } catch (e) {
    return []
  }
}

const persistIndexOptionsCache = (items) => {
  try {
    if (typeof window === 'undefined') return
    const arr = (items || [])
      .map(x => ({
        value: String(x?.value || '').trim(),
        label: String(x?.label || '').trim(),
        name: String(x?.name || '').trim()
      }))
      .filter(x => x.value)
    localStorage.setItem(INDEX_OPTIONS_CACHE_KEY, JSON.stringify(arr.slice(0, INDEX_OPTIONS_CACHE_MAX)))
  } catch (e) {
  }
}

const ensureIndexOption = (code) => {
  const cur = String(code || '').trim()
  if (!cur) return
  if ((indexOptions.value || []).find(x => x.value === cur)) return
  indexOptions.value = [{ label: `${cur} (${cur})`, value: cur, name: cur }, ...(indexOptions.value || [])].slice(0, INDEX_OPTIONS_CACHE_MAX)
  persistIndexOptionsCache(indexOptions.value)
}

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
  { label: t('app.modelOptions.mlp'), value: 'mlp' },
  { label: t('app.modelOptions.logistic_regression'), value: 'logistic_regression' }
])

const rankModeOptions = computed(() => [
  { label: t('pretrain.rankModeSample'), value: 'sample' },
  { label: t('pretrain.rankModeCrossSection'), value: 'cross_section' }
])

const rollingSampleModeOptions = computed(() => [
  { label: t('pretrain.rollingModeBsp'), value: 'bsp' },
  { label: t('pretrain.rollingModeEveryBar'), value: 'every_bar' }
])

const fillCodesFromPool = async () => {
    if (!proxyPretrainConfig.value.selectedPoolId) return
    poolMembersLoading.value = true
    poolMetaLabel.value = ''
    try {
        const res = await axios.get(`/api/stock_pools/${encodeURIComponent(proxyPretrainConfig.value.selectedPoolId)}/members`, {
            params: { limit: 10000 }
        })
        const codes = res.data?.codes || []
        if (codes.length > 0) {
            if (proxyPretrainConfig.value.overwriteCodes) {
                proxyPretrainConfig.value.codesText = codes.join(',')
            } else {
                const existing = proxyPretrainConfig.value.codesText ? proxyPretrainConfig.value.codesText.split(',').map(s => s.trim()).filter(Boolean) : []
                const combined = Array.from(new Set([...existing, ...codes]))
                proxyPretrainConfig.value.codesText = combined.join(',')
            }
            poolMetaLabel.value = t('pretrain.poolLoaded').replace('{n}', String(codes.length))
        } else {
            poolMetaLabel.value = t('pretrain.poolEmpty')
        }
    } catch (e) {
        console.error(e)
        poolMetaLabel.value = t('pretrain.poolLoadFailed')
    } finally {
        poolMembersLoading.value = false
    }
}

onMounted(async () => {
    // We can fetch pools here too if we want dynamic list
    // But for now let's keep it simple.
    // If StrategyPanel fetches it, maybe app.vue should fetch and pass down?
    // Let's self-fetch for independence
    try {
        const res = await axios.get('/api/stock_pools')
        if (res.data && res.data.items) {
            poolOptions.value = res.data.items.map(p => ({
                label: `${p.pool_name} (${p.stock_count})`,
                value: p.pool_id,
                name: p.pool_name
            }))
            if (!poolOptions.value.find(x => x.value === 'index:IDX399673')) {
              poolOptions.value.unshift({ label: '创业板50 (50)', value: 'index:IDX399673', name: '创业板50' })
            }
            // Default to first option if not set
            if (!proxyStrategyForm.value.pool_id && poolOptions.value.length > 0) {
                proxyStrategyForm.value.pool_id = poolOptions.value[0].value
            }
            if (!proxyPaperConfig.value?.pool_id) {
              proxyPaperConfig.value.pool_id = poolOptions.value.find(x => x.value === 'index:IDX399673')?.value || poolOptions.value[0].value
            }
        }
    } catch (e) {
        // ignore
    }

    // Load persisted index code
    const savedIndex = localStorage.getItem('mlchan_sidebar_index_code')
    // We must use savedIndex directly for the initial query because the computed prop update 
    // might not have propagated back from parent yet.
    if (savedIndex) {
        computedIndexCode.value = savedIndex
    }

    const cached = loadIndexOptionsCache()
    if (cached.length > 0) {
        indexOptions.value = cached
        ensureIndexOption(computedIndexCode.value)
        ensureIndexOption(proxyPaperParams.value?.risk_index_code)
        return
    }

    try {
        const currentCode = savedIndex || computedIndexCode.value || '399006'
        if (indexOptions.value.length === 0) {
            searchingIndices.value = true
        }

        const res = await axios.get('/api/indices', { params: { limit: 50, keyword: currentCode } })
        if (res.data && res.data.items) {
            const items = res.data.items.map(c => {
                const code = typeof c === 'string' ? c : (c.code || '')
                const name = typeof c === 'string' ? c : (c.name || code)
                return { label: `${name} (${code})`, value: code, name: name }
            })
            indexOptions.value = items
            persistIndexOptionsCache(items)

            const curVal = String(computedIndexCode.value || '').trim()
            if (curVal && !items.find(x => x.value === curVal)) {
                const curDigits = curVal.replace(/\D/g, '')
                const match = (curDigits ? items.find(x => String(x.value || '').replace(/\D/g, '') === curDigits) : null)
                    || (curDigits ? items.find(x => String(x.value || '').replace(/\D/g, '').endsWith(curDigits)) : null)
                    || items[0]
                if (match && match.value) {
                    computedIndexCode.value = match.value
                }
            }

            if (!currentCode && !computedIndexCode.value && items.length > 0) {
                 computedIndexCode.value = items[0].value
            }
        }
    } catch (e) {
        // ignore
    } finally {
        searchingIndices.value = false
    }
})

const searchingIndices = ref(false)

const computedIndexCode = computed({
    get: () => proxyStrategyForm.value.index_code,
    set: (val) => {
        const newForm = { ...proxyStrategyForm.value, index_code: val }
        emit('update:strategyForm', newForm)
    }
})

const searchIndices = async (query) => {
    if (!query) return indexOptions.value
    searchingIndices.value = true
    try {
        const res = await axios.get('/api/indices', { params: { limit: 100, keyword: query } })
        if (res.data && res.data.items) {
            const items = res.data.items.map(c => {
                const code = typeof c === 'string' ? c : (c.code || '')
                const name = typeof c === 'string' ? c : (c.name || code)
                return { label: `${name} (${code})`, value: code, name: name }
            })
            const merged = [...items, ...(indexOptions.value || [])]
            const seen = new Set()
            const uniq = []
            for (const it of merged) {
              const v = String(it?.value || '').trim()
              if (!v || seen.has(v)) continue
              seen.add(v)
              uniq.push(it)
            }
            indexOptions.value = uniq.slice(0, INDEX_OPTIONS_CACHE_MAX)
            persistIndexOptionsCache(indexOptions.value)
            return items
        }
        return []
    } catch (e) {
        return []
    } finally {
        searchingIndices.value = false
    }
}

watch(() => proxyStrategyForm.value.index_code, (val) => {
    if (val) localStorage.setItem('mlchan_sidebar_index_code', val)
    ensureIndexOption(val)
}, { deep: true })

watch(() => proxyPaperParams.value?.risk_index_code, (val) => {
    ensureIndexOption(val)
}, { deep: true })


watch(() => proxyPretrainConfig.value.selectedPoolId, (newId) => {
    const pool = poolOptions.value.find(p => p.value === newId)
    if (pool) {
        proxyPretrainConfig.value.poolName = pool.name
    } else {
        proxyPretrainConfig.value.poolName = ''
    }
})

</script>
