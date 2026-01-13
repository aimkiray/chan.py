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

        <!-- Force Refresh -->
        <div class="flex items-center justify-between gap-2">
          <UCheckbox v-model="proxyPretrainConfig.forceRefresh" :label="t('pretrain.forceRefresh')" />
        </div>
        
        <UButton block color="primary" :loading="loading" @click="$emit('runPretrain')">
          {{ t('pretrain.run') }}
        </UButton>
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

          <!-- Model -->
          <div>
             <div class="flex items-center gap-1 mb-2">
                <label class="block text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.model') }}</label>
             </div>
             <USelectMenu v-model="proxyStrategyForm.model" :options="['xgboost', 'lightgbm', 'mlp']" class="w-full" />
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
  </div>
</template>

<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import axios from 'axios'
import { MemoryBook, MemoryChevronDown, MemoryChevronLeft } from '@pictogrammers/memory'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()

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

const props = defineProps({
  activeTab: { type: String, default: 'analysis' },
  code: String,
  autype: { type: String, default: 'qfq' },
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
  enableRollingLookback: { type: Boolean, default: true },
  
  // Strategy Props
  strategyForm: { type: Object, default: () => ({}) },
  strategyChanConfig: { type: Object, default: () => ({}) },
  
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
  'update:code', 'update:autype', 'update:triggerStep', 'update:biStrict', 'update:blend', 
  'update:dataSrc', 'update:model', 'update:intradayFreq', 'update:dataLengthYears',
  'update:pretrainedChoice', 'update:intradayBlend', 'update:enableRollingLookback',
  'update:strategyForm', 'update:strategyChanConfig', 'update:pretrainConfig',
  'analyze', 'toggle', 'refreshPretrained', 'refresh', 'runStrategy', 'runPretrain'
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
    try {
        // We can fetch pools here too if we want dynamic list
        // But for now let's keep it simple.
        // If StrategyPanel fetches it, maybe app.vue should fetch and pass down?
        // Let's self-fetch for independence
        const res = await axios.get('/api/stock_pools')
        if (res.data && res.data.items) {
            poolOptions.value = res.data.items.map(p => ({
                label: `${p.pool_name} (${p.stock_count})`,
                value: p.pool_id,
                name: p.pool_name
            }))
            // Default to first option if not set
            if (!proxyStrategyForm.value.pool_id && poolOptions.value.length > 0) {
                proxyStrategyForm.value.pool_id = poolOptions.value[0].value
            }
        }
    } catch (e) {
        // ignore
    }
})

watch(() => proxyPretrainConfig.value.selectedPoolId, (newId) => {
    const pool = poolOptions.value.find(p => p.value === newId)
    if (pool) {
        proxyPretrainConfig.value.poolName = pool.name
    } else {
        proxyPretrainConfig.value.poolName = ''
    }
})

</script>
