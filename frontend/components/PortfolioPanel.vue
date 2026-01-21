<template>
  <div class="h-full flex flex-col gap-4 p-2">
    <div v-if="!fixedMode" class="flex items-center gap-2 border-b border-gray-200 dark:border-gray-800 pb-2">
      <UButton
        size="sm"
        :variant="mode === 'portfolio' ? 'solid' : 'ghost'"
        :color="mode === 'portfolio' ? 'primary' : 'gray'"
        @click="mode = 'portfolio'"
      >
        {{ t('portfolio.myPortfolios') }}
      </UButton>
      <UButton
        size="sm"
        :variant="mode === 'backtest' ? 'solid' : 'ghost'"
        :color="mode === 'backtest' ? 'primary' : 'gray'"
        @click="mode = 'backtest'"
      >
        {{ t('portfolio.backtest') }}
      </UButton>
      <UButton
        size="sm"
        :variant="mode === 'paper' ? 'solid' : 'ghost'"
        :color="mode === 'paper' ? 'primary' : 'gray'"
        @click="mode = 'paper'"
      >
        {{ t('strategy.paperTrade') }}
      </UButton>
    </div>

    <!-- Portfolio Mode -->
    <div v-if="mode === 'portfolio'" class="flex-1 flex gap-4 min-h-0">
      <!-- Left: List -->
      <div class="w-1/3 flex flex-col gap-2">
        <div class="flex justify-between items-center">
          <h3 class="font-bold text-gray-700 dark:text-gray-200">{{ t('portfolio.myPortfolios') }}</h3>
          <UButton size="xs" icon="i-heroicons-plus" @click="showCreateModal = true">{{ t('portfolio.create') }}</UButton>
        </div>
        <div class="flex-1 overflow-y-auto border border-gray-200 dark:border-gray-800 rounded">
          <div
            v-for="p in portfolios"
            :key="p.id"
            class="p-3 border-b border-gray-100 dark:border-gray-800 cursor-pointer hover:bg-gray-50 dark:hover:bg-gray-800"
            :class="{ 'bg-blue-50 dark:bg-blue-900/20': selectedPortfolioId === p.id }"
            @click="selectPortfolio(p.id)"
          >
            <div class="font-medium">{{ p.name }}</div>
            <div class="text-xs text-gray-500">{{ p.description }}</div>
          </div>
        </div>
      </div>

      <!-- Right: Detail -->
      <div class="flex-1 flex flex-col gap-2">
        <div v-if="selectedPortfolio" class="h-full flex flex-col">
          <div class="flex justify-between items-center mb-2">
            <div>
              <h3 class="font-bold text-lg">{{ selectedPortfolio.name }}</h3>
              <div class="text-xs text-gray-500">{{ selectedPortfolio.description }}</div>
            </div>
            <UButton size="xs" color="red" variant="ghost" @click="deletePortfolio(selectedPortfolio.id)">{{ t('app.delete') }}</UButton>
          </div>
          
          <div class="flex-1 border border-gray-200 dark:border-gray-800 rounded overflow-hidden flex flex-col">
            <div class="p-2 bg-gray-50 dark:bg-gray-950 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center">
              <span class="font-semibold">{{ t('portfolio.positions') }}</span>
              <UButton size="xs" icon="i-heroicons-plus" @click="showAddPosModal = true">{{ t('portfolio.addPos') }}</UButton>
            </div>
            <div class="flex-1 overflow-auto">
              <table class="w-full text-sm text-left">
                <thead class="text-xs text-gray-700 uppercase bg-gray-50 dark:bg-gray-700 dark:text-gray-400 sticky top-0">
                  <tr>
                    <th class="px-3 py-2">{{ t('portfolio.code') }}</th>
                    <th class="px-3 py-2">{{ t('portfolio.stockName') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.volume') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.avgPrice') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.currentPrice') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.marketValue') }}</th>
                    <th class="px-3 py-2 text-right">{{ t('portfolio.pnl') }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="pos in (selectedPortfolio.positions || [])" :key="pos.code" class="border-b dark:border-gray-700">
                    <td class="px-3 py-2 font-mono">{{ pos.code }}</td>
                    <td class="px-3 py-2">{{ pos.name }}</td>
                    <td class="px-3 py-2 text-right">{{ pos.volume }}</td>
                    <td class="px-3 py-2 text-right">{{ pos.avg_price.toFixed(2) }}</td>
                    <td class="px-3 py-2 text-right">{{ pos.current_price?.toFixed(2) || '-' }}</td>
                    <td class="px-3 py-2 text-right">{{ ((pos.current_price || pos.avg_price) * pos.volume).toFixed(2) }}</td>
                    <td class="px-3 py-2 text-right" :class="getPnLColor(pos)">
                      {{ getPnL(pos) }}
                    </td>
                  </tr>
                  <tr v-if="!selectedPortfolio.positions?.length">
                    <td colspan="7" class="px-3 py-4 text-center text-gray-500">{{ t('portfolio.noPositions') }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
        <div v-else class="flex-1 flex items-center justify-center text-gray-400">
          {{ t('pretrain.selectToView') }}
        </div>
      </div>
    </div>

    <!-- Paper Trade Mode -->
    <div v-else-if="mode === 'paper'" class="flex-1 min-h-0 flex flex-col">
      <UCard :ui="{ body: { base: 'flex-1 min-h-0 flex flex-col overflow-y-auto', padding: 'p-3' } }" class="flex-1 min-h-0 h-full shadow-none border border-gray-200 dark:border-gray-800 flex flex-col overflow-hidden">
        <div v-if="stepError" class="mt-2 text-xs text-red-500 break-all">{{ stepError }}</div>
        <div v-if="stepResult" class="mt-2 text-xs text-gray-500 break-all">
          {{ t('strategy.stepMeta', { day: stepResult.day, ts: stepResult.ts }) }} · {{ formatTs(stepResult.ts) }} · model={{ stepResult.model_key || '-' }} · rank={{ stepResult.rank_mode || '-' }} · {{ t('strategy.marketStatus') }}={{ stepResult.market_open ? t('strategy.marketOpen') : t('strategy.marketClosed') }}
        </div>
        <div v-if="stepResult?.risk" class="mt-1 text-xs text-gray-500 break-all">
          {{ t('strategy.risk') }}={{ stepResult.risk.level || '-' }} · topN={{ stepResult.risk.effective_top_n ?? '-' }} · gap={{ stepResult.risk.gap_open_pct === null || stepResult.risk.gap_open_pct === undefined ? '-' : formatPct(stepResult.risk.gap_open_pct) }} · d1={{ stepResult.risk.ret1d === null || stepResult.risk.ret1d === undefined ? '-' : formatPct(stepResult.risk.ret1d) }} · atr={{ stepResult.risk.atr_pct === null || stepResult.risk.atr_pct === undefined ? '-' : formatPct(stepResult.risk.atr_pct) }} · in={{ stepResult.risk.intraday?.change === null || stepResult.risk.intraday?.change === undefined ? '-' : formatPct(stepResult.risk.intraday.change) }} · trend={{ stepResult.risk.intraday?.trend || '-' }}
        </div>

        <div class="mt-3">
          <UCard>
            <div class="flex items-center justify-between gap-2">
              <div class="text-xs text-gray-500">{{ t('strategy.autoRun') }} · sim={{ activeSimId }}</div>
              <UButton size="xs" color="gray" variant="soft" :loading="runsLoading" @click="loadRuns">{{ t('strategy.refresh') }}</UButton>
            </div>
            <div v-if="runsError" class="mt-2 text-xs text-red-500 break-all">{{ runsError }}</div>
            <div class="mt-2 grid grid-cols-2 gap-2">
              <UInput v-model="newRunName" size="xs" :placeholder="t('strategy.runName')" />
              <UInput v-model="newRunOffsetSeconds" size="xs" type="number" />
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-3">
              <UCheckbox v-model="newRunAutoResume" :label="t('strategy.autoResume')" />
              <UCheckbox v-model="newRunNotifyEnabled" :label="t('strategy.notify')" />
              <UCheckbox v-model="newRunStart" :label="t('strategy.startImmediately')" />
              <UButton size="xs" color="primary" :loading="runsLoading" @click="createRun">{{ t('strategy.createRun') }}</UButton>
            </div>
            <div class="mt-2 space-y-2">
              <div v-for="r in liveRuns" :key="r.run_id" class="flex items-center justify-between gap-2 text-xs border-b border-gray-100 dark:border-gray-800 pb-2">
                <div class="min-w-0">
                  <div class="font-mono truncate">{{ r.name || r.run_id }}</div>
                  <div class="text-gray-500 font-mono truncate">
                    sim={{ r.sim_id }} · {{ r.status }} · next={{ formatTs(r.next_exec_ts) }} · last={{ formatTs(r.last_step_ts) }}
                  </div>
                  <div v-if="r.error" class="text-red-500 break-all">{{ r.error }}</div>
                </div>
                <div class="flex items-center gap-1 shrink-0">
                  <UButton size="xs" color="gray" variant="soft" @click="selectRunSim(r)">{{ t('strategy.view') }}</UButton>
                  <UButton size="xs" color="gray" variant="soft" @click="applyRunParams(r)">{{ t('strategy.apply') }}</UButton>
                  <UButton v-if="String(r.status||'') !== 'running'" size="xs" color="primary" variant="soft" @click="startRun(r.run_id)">{{ t('strategy.startRun') }}</UButton>
                  <UButton v-else size="xs" color="orange" variant="soft" @click="stopRun(r.run_id)">{{ t('strategy.stopRun') }}</UButton>
                  <UButton size="xs" color="red" variant="soft" @click="deleteRun(r.run_id)">{{ t('strategy.delete') }}</UButton>
                </div>
              </div>
              <div v-if="!liveRuns.length" class="text-xs text-gray-400">-</div>
            </div>
          </UCard>
        </div>

        <div v-if="portfolioSummary" class="mt-3 grid grid-cols-4 gap-3">
          <UCard>
            <div class="text-xs text-gray-500">{{ t('strategy.totalPnlPct') }}</div>
            <div class="mt-1 font-mono text-sm">{{ formatPct(portfolioSummary.total_pnl_pct) }}</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('strategy.totalPnl') }}</div>
            <div class="mt-1 font-mono text-sm">{{ formatNumber(portfolioSummary.total_pnl) }}</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('strategy.totalMarketValue') }}</div>
            <div class="mt-1 font-mono text-sm">{{ formatNumber(portfolioSummary.total_market_value) }}</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('strategy.totalCost') }}</div>
            <div class="mt-1 font-mono text-sm">{{ formatNumber(portfolioSummary.total_cost) }}</div>
          </UCard>
        </div>

        <div v-if="stepResult" class="mt-3 grid grid-cols-3 gap-3">
          <UCard>
            <div class="text-xs text-gray-500">{{ t('strategy.targets') }}</div>
            <div class="mt-1 text-xs break-all">
              <div v-for="tgt in (stepResult.targets || [])" :key="tgt.code" class="font-mono">
                {{ tgt.code }} · {{ Number(tgt.prob).toFixed(4) }} · {{ formatTs(tgt.ts) }}
              </div>
              <div v-if="!(stepResult.targets || []).length" class="text-gray-400">-</div>
            </div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('strategy.sellList') }}</div>
            <div class="mt-1 text-xs break-all">
              <div v-for="c in (stepResult.sell || [])" :key="c" class="font-mono">
                {{ c }}<span v-if="stepResult.market_open === false" class="text-gray-500"> · {{ t('strategy.waitingSell') }}</span>
              </div>
              <div v-if="!(stepResult.sell || []).length" class="text-gray-400">-</div>
            </div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('strategy.buyList') }}</div>
            <div class="mt-1 text-xs break-all">
              <div v-for="c in (stepResult.buy || [])" :key="c" class="font-mono">
                {{ c }}
                <span v-if="targetByCode[c]?.ts" class="text-gray-500"> · {{ formatTs(targetByCode[c].ts) }}</span>
                <span v-if="stepResult.market_open === false" class="text-gray-500"> · {{ t('strategy.waitingBuy') }}</span>
              </div>
              <div v-if="!(stepResult.buy || []).length" class="text-gray-400">-</div>
            </div>
          </UCard>
        </div>

        <div class="mt-3 space-y-3">
          <div>
            <div class="text-xs text-gray-500 mb-2">{{ t('strategy.positions') }}</div>
            <UTable :rows="positions" :columns="positionColumns" class="w-full" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
          </div>

          <div>
            <div class="text-xs text-gray-500 mb-2">{{ t('strategy.pendingOrders') }}</div>
            <UTable :rows="pendingOrders" :columns="orderColumns" class="w-full" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
          </div>

          <div>
            <div class="flex items-center justify-between gap-2 mb-2">
              <div class="text-xs text-gray-500">{{ t('strategy.tradeHistory') }}</div>
              <div v-if="tradeTotalPages > 1" class="text-xs text-gray-500">
                {{ t('strategy.pageInfo', { page: tradePage, pages: tradeTotalPages, total: tradeTotal }) }}
              </div>
            </div>
            <UTable :rows="tradeHistory" :columns="tradeColumns" class="w-full" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
            <div v-if="tradeTotalPages > 1" class="mt-2 flex items-center justify-end gap-2 border-t border-gray-100 dark:border-gray-800 pt-2">
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="tradePage <= 1"
                @click="tradePage = 1"
              >
                &lt;&lt;
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="tradePage <= 1"
                @click="tradePage = Math.max(1, tradePage - 1)"
              >
                {{ t('strategy.prevPage') }}
              </UButton>
              <div class="flex items-center gap-1">
                <UInput
                  v-model="tradePageInput"
                  size="xs"
                  class="w-12 text-center"
                  @keydown.enter="jumpToTradePage"
                />
              </div>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="tradePage >= tradeTotalPages"
                @click="tradePage = Math.min(tradeTotalPages, tradePage + 1)"
              >
                {{ t('strategy.nextPage') }}
              </UButton>
              <UButton
                size="xs"
                color="gray"
                variant="soft"
                :disabled="tradePage >= tradeTotalPages"
                @click="tradePage = tradeTotalPages"
              >
                &gt;&gt;
              </UButton>
            </div>
          </div>

          <div>
            <div class="flex items-center justify-between gap-2 mb-2">
              <div class="text-xs text-gray-500">{{ t('strategy.stepLog') }}</div>
              <div class="text-xs text-gray-500">
                {{ stepLogTotal ? t('strategy.pageInfo', { page: stepLogPage, pages: Math.max(1, Math.ceil(stepLogTotal / stepLogPageSize)), total: stepLogTotal }) : '' }}
              </div>
            </div>
            <div v-if="stepsError" class="mb-2 text-xs text-red-500 break-all">{{ stepsError }}</div>
            <UTable :rows="stepLogRows" :columns="stepLogColumns" class="w-full" :loading="stepsLoading" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
            <div class="mt-2 flex items-center justify-end gap-2">
              <UButton size="xs" color="gray" variant="soft" :disabled="stepLogPage <= 1" @click="stepLogPage = Math.max(1, stepLogPage - 1); loadStepLogs()">{{ t('strategy.prevPage') }}</UButton>
              <UButton size="xs" color="gray" variant="soft" :disabled="stepLogPage >= Math.max(1, Math.ceil(stepLogTotal / stepLogPageSize))" @click="stepLogPage = Math.min(Math.max(1, Math.ceil(stepLogTotal / stepLogPageSize)), stepLogPage + 1); loadStepLogs()">{{ t('strategy.nextPage') }}</UButton>
            </div>
          </div>

          <div>
            <div class="flex items-center justify-between gap-2 mb-2">
              <div class="text-xs text-gray-500">{{ t('strategy.equityCurve') }}</div>
              <UButton size="xs" color="gray" variant="soft" :loading="equityLoading" @click="loadEquity">{{ t('strategy.refresh') }}</UButton>
            </div>
            <div v-if="equityError" class="mb-2 text-xs text-red-500 break-all">{{ equityError }}</div>
            <UTable :rows="equityRows" :columns="equityColumns" class="w-full" :loading="equityLoading" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
            <div ref="equityChartContainer" class="mt-3 w-full h-72" />
          </div>
        </div>
      </UCard>
    </div>

    <!-- Backtest Mode -->
    <div v-else class="flex-1 flex flex-col gap-4 overflow-y-auto">
      <div class="flex items-center gap-2">
        <UButton
          size="xs"
          :variant="backtestKind === 'vector' ? 'solid' : 'ghost'"
          :color="backtestKind === 'vector' ? 'primary' : 'gray'"
          @click="backtestKind = 'vector'"
        >
          {{ t('portfolio.simpleBacktest') }}
        </UButton>
        <UButton
          size="xs"
          :variant="backtestKind === 'wolfpack' ? 'solid' : 'ghost'"
          :color="backtestKind === 'wolfpack' ? 'primary' : 'gray'"
          @click="backtestKind = 'wolfpack'"
        >
          {{ t('portfolio.wolfpackBacktest') }}
        </UButton>
      </div>

      <UCard v-if="backtestKind === 'vector'">
        <template #header>
          <div class="font-bold">{{ t('portfolio.simpleBacktest') }}</div>
        </template>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.startDate') }}</label>
            <UInput type="date" v-model="backtestConfig.start_date" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.endDate') }}</label>
            <UInput type="date" v-model="backtestConfig.end_date" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.modelKey') }}</label>
            <UInput v-model="backtestConfig.model_key" :placeholder="t('portfolio.autoPlaceholder')" />
          </div>
          <div class="flex items-end">
            <UButton block color="primary" :loading="backtestLoading" @click="runBacktest">{{ t('portfolio.runBacktest') }}</UButton>
          </div>
        </div>
      </UCard>

      <UCard v-else>
        <template #header>
          <div class="font-bold">{{ t('portfolio.wolfpackBacktest') }}</div>
        </template>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.startDate') }}</label>
            <UInput type="date" v-model="wolfpackConfig.start_date" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('portfolio.endDate') }}</label>
            <UInput type="date" v-model="wolfpackConfig.end_date" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.poolId') }}</label>
            <UInput v-model="wolfpackConfig.pool_id" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('app.periodSelect') }}</label>
            <USelectMenu v-model="wolfpackConfig.frequency" :options="wolfpackFrequencyOptions" value-attribute="value" option-attribute="label" class="w-full" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('app.dataSrc') }}</label>
            <USelectMenu v-model="wolfpackConfig.data_src" :options="wolfpackDataSrcOptions" value-attribute="value" option-attribute="label" class="w-full" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('sidebar.autype') }}</label>
            <USelectMenu v-model="wolfpackConfig.autype" :options="wolfpackAutypeOptions" value-attribute="value" option-attribute="label" class="w-full" />
          </div>

          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.topN') }}</label>
            <UInput type="number" v-model="wolfpackConfig.portfolio_top_n" min="0" step="1" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.holdingBars') }}</label>
            <UInput type="number" v-model="wolfpackConfig.holding_period" min="1" step="1" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.minProb') }}</label>
            <UInput type="number" v-model="wolfpackConfig.min_prob" min="0" max="1" step="0.01" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.tradeCost') }}</label>
            <UInput type="number" v-model="wolfpackConfig.trade_cost" step="0.0001" />
          </div>

          <div class="flex items-center gap-4">
            <UCheckbox v-model="wolfpackConfig.enforce_t1" :label="t('strategy.enforceT1')" />
            <UCheckbox v-model="wolfpackConfig.index_filter_enabled" :label="t('strategy.indexFilterEnabled')" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.stopLoss') }}</label>
            <UInput type="number" v-model="wolfpackConfig.stop_loss" step="0.005" />
          </div>

          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.indexCode') }}</label>
            <UInput v-model="wolfpackConfig.index_code" />
          </div>
          <div>
            <label class="block text-sm font-medium mb-1">{{ t('strategy.indexMaWindow') }}</label>
            <UInput type="number" v-model="wolfpackConfig.index_ma_window" min="2" step="1" />
          </div>

          <div class="col-span-2 pt-2 border-t border-gray-100 dark:border-gray-800">
            <div class="flex items-center justify-between">
              <div class="text-sm font-medium text-gray-700 dark:text-gray-300">{{ t('strategy.risk') }}</div>
              <UCheckbox v-model="wolfpackConfig.risk_filter_enabled" :label="t('strategy.riskEnabled')" />
            </div>
            <div class="grid grid-cols-2 gap-2 mt-2">
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskMode') }}</label>
                <USelectMenu v-model="wolfpackConfig.risk_mode" :options="riskModeOptions" value-attribute="value" option-attribute="label" class="w-full" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskGapDownThreshold') }}</label>
                <UInput v-model="wolfpackConfig.risk_gap_down_threshold" type="number" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskDropThreshold') }}</label>
                <UInput v-model="wolfpackConfig.risk_drop_threshold" type="number" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskFlatDropThreshold') }}</label>
                <UInput v-model="wolfpackConfig.risk_flat_drop_threshold" type="number" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskAtrWindow') }}</label>
                <UInput v-model="wolfpackConfig.risk_atr_window" type="number" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskAtrPctThreshold') }}</label>
                <UInput v-model="wolfpackConfig.risk_atr_pct_threshold" type="number" />
              </div>
              <div>
                <label class="text-xs text-gray-500">{{ t('strategy.riskFlatAtrPctThreshold') }}</label>
                <UInput v-model="wolfpackConfig.risk_flat_atr_pct_threshold" type="number" />
              </div>
            </div>
          </div>

          <div class="col-span-2 flex items-end">
            <UButton block color="primary" :loading="wolfpackLoading" @click="runWolfpackBacktest">{{ t('portfolio.runBacktest') }}</UButton>
          </div>
        </div>
      </UCard>

      <div v-if="backtestResult" class="flex flex-col gap-4">
        <div class="grid grid-cols-4 gap-4">
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.totalReturn') }}</div>
            <div class="text-xl font-bold" :class="getColor(backtestResult.metrics.total_return)">
              {{ (backtestResult.metrics.total_return * 100).toFixed(2) }}%
            </div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.sharpe') }}</div>
            <div class="text-xl font-bold">{{ backtestResult.metrics.sharpe_ratio.toFixed(2) }}</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.maxDD') }}</div>
            <div class="text-xl font-bold text-red-500">{{ (backtestResult.metrics.max_drawdown * 100).toFixed(2) }}%</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.finalEquity') }}</div>
            <div class="text-xl font-bold">{{ backtestResult.metrics.final_equity.toFixed(2) }}</div>
          </UCard>
        </div>

        <!-- Simple Chart Placeholder -->
        <UCard>
           <template #header>{{ t('portfolio.equityCurve') }}</template>
           <div class="h-[300px] w-full" ref="chartContainer"></div>
        </UCard>
      </div>
    </div>

    <!-- Modals -->
    <UModal v-model="showCreateModal">
      <UCard>
        <template #header>{{ t('portfolio.create') }}</template>
        <div class="space-y-4">
          <UInput v-model="newPortfolio.name" :placeholder="t('portfolio.name')" />
          <UInput v-model="newPortfolio.description" :placeholder="t('portfolio.desc')" />
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="showCreateModal = false">{{ t('portfolio.cancel') }}</UButton>
            <UButton color="primary" @click="createPortfolio">{{ t('portfolio.create') }}</UButton>
          </div>
        </template>
      </UCard>
    </UModal>

    <UModal v-model="showAddPosModal">
      <UCard>
        <template #header>{{ t('portfolio.addPos') }}</template>
        <div class="space-y-4">
          <UInput v-model="newPosition.code" :placeholder="t('portfolio.code')" />
          <UInput v-model="newPosition.name" :placeholder="t('portfolio.stockName')" />
          <UInput type="number" v-model="newPosition.volume" :placeholder="t('portfolio.volume')" />
          <UInput type="number" v-model="newPosition.price" :placeholder="t('portfolio.price')" />
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="showAddPosModal = false">{{ t('portfolio.cancel') }}</UButton>
            <UButton color="primary" @click="addPosition">{{ t('portfolio.add') }}</UButton>
          </div>
        </template>
      </UCard>
    </UModal>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick, watch, computed } from 'vue'
import axios from 'axios'
import * as echarts from 'echarts'
import { useI18n } from '../composables/useI18n'

const { t } = useI18n()

const formatIsoDate = (d) => {
  try {
    const dt = d instanceof Date ? d : new Date(d)
    if (!Number.isFinite(dt.getTime())) return ''
    const y = dt.getFullYear()
    const m = String(dt.getMonth() + 1).padStart(2, '0')
    const day = String(dt.getDate()).padStart(2, '0')
    return `${y}-${m}-${day}`
  } catch (e) {
    return ''
  }
}

const defaultEndDate = formatIsoDate(new Date())
const defaultStartDate = (() => {
  const d = new Date()
  d.setFullYear(d.getFullYear() - 3)
  return formatIsoDate(d)
})()

const props = defineProps({
  initialMode: { type: String, default: 'portfolio' },
  fixedMode: { type: String, default: '' },
  paperConfig: { type: Object, default: () => ({}) },
  paperParams: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['selectSimId', 'applyRunParams'])

const fixedMode = computed(() => String(props.fixedMode || '').trim())
const mode = ref(String((fixedMode.value || props.initialMode || 'portfolio')).trim() || 'portfolio')
const portfolios = ref([])
const selectedPortfolioId = ref(null)
const selectedPortfolio = ref(null)
const activePortfolioId = computed(() => {
  const v = selectedPortfolioId.value
  if (v === null || v === undefined) return undefined
  const n = Number(v)
  if (!Number.isFinite(n)) return undefined
  return Math.trunc(n)
})

watch(
  fixedMode,
  (m) => {
    if (!m) return
    if (mode.value !== m) mode.value = m
  },
  { immediate: true }
)

watch(
  mode,
  (m) => {
    const locked = fixedMode.value
    if (locked && m !== locked) mode.value = locked
  },
  { immediate: false }
)

const showCreateModal = ref(false)
const newPortfolio = ref({ name: '', description: '' })

const showAddPosModal = ref(false)
const newPosition = ref({ code: '', name: '', volume: 0, price: 0 })

// Backtest
const backtestKind = ref('vector')
const backtestConfig = ref({
  start_date: defaultStartDate,
  end_date: defaultEndDate,
  model_key: ''
})
const backtestLoading = ref(false)
const backtestResult = ref(null)
const chartContainer = ref(null)
const equityChartContainer = ref(null)
let chartInstance = null
let equityChartInstance = null
let liveRefreshTimer = null

const wolfpackConfig = ref({
  pool_id: 'index:IDX399673',
  start_date: defaultStartDate,
  end_date: defaultEndDate,
  frequency: '30m',
  data_src: 'akshare',
  autype: 'qfq',
  enable_rolling_lookback: true,
  holding_period: 3,
  min_prob: 0.25,
  trade_cost: 0.0002,
  portfolio_top_n: 2,
  index_filter_enabled: true,
  index_code: '399006',
  index_ma_window: 20,
  stop_loss: -0.03,
  enforce_t1: true,
  risk_filter_enabled: true,
  risk_mode: 'both',
  risk_drop_threshold: -0.03,
  risk_flat_drop_threshold: -0.05,
  risk_atr_window: 14,
  risk_atr_pct_threshold: 0.04,
  risk_flat_atr_pct_threshold: 0.06,
  risk_gap_down_threshold: -0.015,
  risk_intraday_enabled: true,
  risk_intraday_bear_threshold: -0.015,
  risk_intraday_bull_threshold: -0.03,
  risk_trend_ma_window: 20,
  risk_data_fail_mode: 'reduce',
  risk_intraday_data_fail_mode: 'reduce',
  max_workers: 8
})
const wolfpackLoading = ref(false)

const wolfpackFrequencyOptions = computed(() => ([
  { label: t('periods.30m'), value: '30m' },
  { label: t('periods.60m'), value: '60m' },
  { label: t('periods.15m'), value: '15m' },
  { label: t('periods.5m'), value: '5m' },
  { label: t('periods.1m'), value: '1m' },
  { label: t('periods.1d'), value: '1d' }
]))
const wolfpackDataSrcOptions = computed(() => ([
  { label: t('app.dataSrcOptions.clickhouse'), value: 'clickhouse' },
  { label: t('app.dataSrcOptions.baostock'), value: 'baostock' },
  { label: t('app.dataSrcOptions.akshare'), value: 'akshare' }
]))
const wolfpackAutypeOptions = computed(() => ([
  { label: t('sidebar.autypeQfq'), value: 'qfq' },
  { label: t('sidebar.autypeHfq'), value: 'hfq' },
  { label: 'None', value: 'none' }
]))
const riskModeOptions = computed(() => ([
  { label: t('strategy.riskModeDrop'), value: 'drop' },
  { label: t('strategy.riskModeAtr'), value: 'atr' },
  { label: t('strategy.riskModeBoth'), value: 'both' }
]))

const paperInitialized = ref(false)
const stepLoading = ref(false)
const stepError = ref('')
const stepResult = ref(null)

const runsLoading = ref(false)
const runsError = ref('')
const liveRuns = ref([])
const newRunName = ref('')
const newRunOffsetSeconds = ref(5)
const newRunAutoResume = ref(true)
const newRunNotifyEnabled = ref(false)
const newRunStart = ref(true)
const selectedRunId = ref('')

const stepsLoading = ref(false)
const stepsError = ref('')
const stepLogs = ref([])
const stepLogPage = ref(1)
const stepLogPageSize = ref(10)
const stepLogTotal = ref(0)

const equityLoading = ref(false)
const equityError = ref('')
const equityPoints = ref([])

const propSimId = computed(() => {
  const cfg = props.paperConfig || {}
  return String(cfg.sim_id || '').trim() || 'default'
})

const localSimId = ref('')
const activeSimId = computed(() => String(localSimId.value || propSimId.value || '').trim() || 'default')

watch(
  propSimId,
  (sid) => {
    const s = String(sid || '').trim()
    if (!s) return
    if (String(localSimId.value || '').trim() === s) localSimId.value = ''
  },
  { immediate: true }
)

let preserveSelectedRunIdOnce = false

const stepLogRows = computed(() => {
  return (stepLogs.value || []).map(x => {
    const side = String(x?.side || '').toLowerCase()
    const st = String(x?.status || '').toLowerCase()
    const rawReason = String(x?.reason || '')
    const reasonText = rawReason === 'T+1' ? t('strategy.reasonT1') : rawReason
    const typeText = side === 'buy'
      ? t('common.buy')
      : (side === 'sell' ? t('common.sell') : '-')
    const statusText = st === 'executed'
      ? t('strategy.executed')
      : (st === 'failed' ? t('strategy.failed') : t('strategy.unexecuted'))
    return {
      ...x,
      step_ts_text: formatTs(x?.step_ts),
      type_text: typeText,
      status_text: statusText,
      reason_text: reasonText || '-'
    }
  })
})

const stepLogColumns = computed(() => ([
  { key: 'step_ts_text', label: t('strategy.timeHuman') },
  { key: 'stock_code', label: t('sidebar.stockCode') },
  { key: 'type_text', label: t('strategy.tradeType') },
  { key: 'status_text', label: t('strategy.status') },
  { key: 'reason_text', label: t('strategy.reason') }
]))

const equityRows = computed(() => {
  return (equityPoints.value || []).map(x => ({
    ...x,
    ts_text: formatTs(x?.ts),
    equity_text: Number.isFinite(Number(x?.equity)) ? Number(x.equity).toFixed(6) : '-'
  }))
})

const equityColumns = computed(() => ([
  { key: 'ts_text', label: t('strategy.timeHuman') },
  { key: 'equity_text', label: t('strategy.equity') },
  { key: 'code', label: t('sidebar.stockCode') },
  { key: 'side', label: t('strategy.side') },
  { key: 'price', label: t('strategy.price') }
]))
const positionsLoading = ref(false)
const resettingPositions = ref(false)
const positions = ref([])
const portfolioSummary = ref(null)
const pendingOrders = ref([])
const tradeHistory = ref([])
const tradePageSize = ref(50)
const tradePage = ref(1)
const tradePageInput = ref('1')
const tradeTotal = ref(0)

const tradeTotalPages = computed(() => {
  const total = Number(tradeTotal.value || 0)
  const ps = Number(tradePageSize.value || 50)
  if (!Number.isFinite(total) || total <= 0) return 1
  if (!Number.isFinite(ps) || ps <= 0) return 1
  return Math.max(1, Math.ceil(total / ps))
})

const positionColumns = computed(() => ([
  { key: 'stock_code', label: t('sidebar.stockCode') },
  { key: 'bars_held', label: t('strategy.barsHeld') },
  { key: 'entry_time_text', label: t('strategy.entryTime') },
  { key: 'entry_price_text', label: t('strategy.entryPrice') },
  { key: 'current_price_text', label: t('strategy.currentPrice') },
  { key: 'pnl_pct_text', label: t('strategy.positionReturn') }
]))

const orderColumns = computed(() => ([
  { key: 'requested_time_text', label: t('strategy.timeHuman') },
  { key: 'effective_time_text', label: t('strategy.effectiveTime') },
  { key: 'side_text', label: t('strategy.side') },
  { key: 'stock_code', label: t('sidebar.stockCode') },
  { key: 'status_text', label: t('strategy.status') },
  { key: 'price_text', label: t('strategy.price') },
  { key: 'reason_text', label: t('strategy.reason') }
]))

const tradeColumns = computed(() => ([
  { key: 'trade_time_text', label: t('strategy.timeHuman') },
  { key: 'side_text', label: t('strategy.side') },
  { key: 'stock_code', label: t('sidebar.stockCode') },
  { key: 'status_text', label: t('strategy.status') },
  { key: 'price_text', label: t('strategy.price') }
]))

const targetByCode = computed(() => {
  const m = {}
  for (const x of (stepResult.value?.targets || [])) {
    if (x?.code) m[String(x.code)] = x
  }
  return m
})

const formatTs = (ts) => {
  const n = Number(ts || 0)
  if (!Number.isFinite(n) || n <= 0) return '-'
  try {
    const dt = new Date(n * 1000)
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    }).format(dt)
  } catch (e) {
    return String(ts)
  }
}

const formatPct = (v) => {
  const n = Number(v || 0)
  if (!Number.isFinite(n)) return '-'
  return `${(n * 100).toFixed(2)}%`
}

const formatNumber = (v) => {
  const n = Number(v || 0)
  if (!Number.isFinite(n)) return '-'
  return n.toFixed(4)
}

const formatSide = (s) => {
  const raw = String(s || '').toLowerCase()
  if (raw === 'buy') return t('common.buy')
  if (raw === 'sell') return t('common.sell')
  return raw || '-'
}

const formatLiveStatus = (status, side) => {
  const st = String(status || '').toLowerCase()
  if (st === 'pending') {
    const sd = String(side || '').toLowerCase()
    if (sd === 'buy') return t('strategy.waitingBuy')
    if (sd === 'sell') return t('strategy.waitingSell')
    return t('strategy.pending')
  }
  if (st === 'executed') return t('strategy.executed')
  if (st === 'blocked') return t('strategy.blocked')
  if (st) return st
  return '-'
}

const formatOrderReason = (reason) => {
  const raw = String(reason || '').trim()
  if (!raw) return '-'
  if (raw === 'T+1') return t('strategy.reasonT1')
  const key = `strategy.marketSession.${raw}`
  const v = t(key)
  return v === key ? raw : v
}

const jumpToTradePage = () => {
  const raw = String(tradePageInput.value || '').trim()
  const n = Number(raw)
  if (!Number.isFinite(n)) {
    tradePageInput.value = String(tradePage.value)
    return
  }
  const p = Math.max(1, Math.min(tradeTotalPages.value, Math.floor(n)))
  tradePage.value = p
}

const fetchPortfolios = async () => {
  try {
    const res = await axios.get('/api/portfolios')
    portfolios.value = res.data
  } catch (e) {
    console.error(e)
  }
}

const selectPortfolio = async (id) => {
  selectedPortfolioId.value = id
  try {
    const res = await axios.get(`/api/portfolios/${id}`)
    selectedPortfolio.value = res.data
  } catch (e) {
    console.error(e)
  }
}

const createPortfolio = async () => {
  try {
    await axios.post('/api/portfolios', newPortfolio.value)
    showCreateModal.value = false
    newPortfolio.value = { name: '', description: '' }
    fetchPortfolios()
  } catch (e) {
    alert(t('portfolio.createFailed'))
  }
}

const deletePortfolio = async (id) => {
  if (!confirm(t('portfolio.deleteConfirm'))) return
  try {
    await axios.delete(`/api/portfolios/${id}`)
    if (selectedPortfolioId.value === id) {
      selectedPortfolioId.value = null
      selectedPortfolio.value = null
    }
    fetchPortfolios()
  } catch (e) {
    alert(t('portfolio.deleteFailed'))
  }
}

const addPosition = async () => {
  if (!selectedPortfolioId.value) return
  try {
    await axios.post(`/api/portfolios/${selectedPortfolioId.value}/positions`, {
      code: newPosition.value.code,
      name: newPosition.value.name,
      volume: Number(newPosition.value.volume),
      price: Number(newPosition.value.price)
    })
    showAddPosModal.value = false
    newPosition.value = { code: '', name: '', volume: 0, price: 0 }
    selectPortfolio(selectedPortfolioId.value)
  } catch (e) {
    alert(t('portfolio.addFailed'))
  }
}

const getPnL = (pos) => {
  if (!pos.current_price) return '-'
  const val = (pos.current_price - pos.avg_price) * pos.volume
  return val.toFixed(2)
}

const getPnLColor = (pos) => {
  if (!pos.current_price) return ''
  const val = pos.current_price - pos.avg_price
  return val > 0 ? 'text-red-500' : (val < 0 ? 'text-green-500' : '')
}

const getColor = (val) => {
  return val > 0 ? 'text-red-500' : (val < 0 ? 'text-green-500' : '')
}

const runBacktest = async () => {
  backtestLoading.value = true
  try {
    const res = await axios.post('/api/backtest/vector', backtestConfig.value)
    if (res.data.error) {
        alert(res.data.error)
        return
    }
    backtestResult.value = res.data
    nextTick(() => {
        renderChart()
    })
  } catch (e) {
    alert(t('portfolio.backtestFailed'))
    console.error(e)
  } finally {
    backtestLoading.value = false
  }
}

const runWolfpackBacktest = async () => {
  wolfpackLoading.value = true
  try {
    const res = await axios.post('/api/backtest/wolfpack', wolfpackConfig.value)
    const data = res.data || {}
    if (String(data.status || '') !== 'success') {
      alert(String(data.detail || data.error || t('portfolio.backtestFailed')))
      return
    }
    const pf = data.portfolio || {}
    const curveRaw = Array.isArray(pf.equity_curve) ? pf.equity_curve : []
    const curve = curveRaw.map((x) => ({
      time: String(x?.day ?? ''),
      equity: Number(x?.equity ?? 0)
    }))
    const lastEquity = curve.length ? Number(curve[curve.length - 1]?.equity) : 1
    backtestResult.value = {
      metrics: {
        total_return: Number(pf.total_return ?? (lastEquity - 1)),
        sharpe_ratio: Number(pf.sharpe ?? 0),
        max_drawdown: Number(pf.max_drawdown ?? 0),
        final_equity: Number.isFinite(lastEquity) ? lastEquity : 1
      },
      equity_curve: curve
    }
    nextTick(() => {
      renderChart()
    })
  } catch (e) {
    alert(t('portfolio.backtestFailed'))
    console.error(e)
  } finally {
    wolfpackLoading.value = false
  }
}

const loadTrades = async () => {
  try {
    const cfg = props.paperConfig || {}
    const res = await axios.get('/api/live/trades', { params: { page: tradePage.value, page_size: tradePageSize.value, sim_id: activeSimId.value, portfolio_id: activePortfolioId.value } })

    const items = Array.isArray(res.data?.items) ? res.data.items : []
    const totalRaw = Number(res.data?.total)
    tradeTotal.value = Number.isFinite(totalRaw) ? totalRaw : items.length

    const serverPage = Number(res.data?.page)
    if (Number.isFinite(serverPage) && serverPage > 0 && serverPage !== tradePage.value) {
      tradePage.value = Math.max(1, Math.min(tradeTotalPages.value, Math.floor(serverPage)))
    }

    tradeHistory.value = items.map(tr => {
      const px = Number(tr?.price || 0)
      return {
        ...tr,
        trade_time_text: formatTs(tr?.trade_ts),
        side_text: formatSide(tr?.side),
        status_text: formatLiveStatus(tr?.status, tr?.side),
        price_text: Number.isFinite(px) ? px.toFixed(4) : '-'
      }
    })
  } catch (e) {
  } finally {
    tradePageInput.value = String(tradePage.value)
  }
}

const loadPositions = async () => {
  positionsLoading.value = true
  try {
    const cfg = props.paperConfig || {}
    const [posRes, orderRes] = await Promise.all([
      axios.get('/api/live/positions_report', { params: { frequency: cfg.frequency || '30m', data_src: cfg.data_src || 'clickhouse', autype: cfg.autype || 'qfq', sim_id: activeSimId.value, portfolio_id: activePortfolioId.value } }),
      axios.get('/api/live/orders', { params: { status: '', limit: 500, sim_id: activeSimId.value, portfolio_id: activePortfolioId.value } }),
    ])

    const posItems = Array.isArray(posRes.data?.items) ? posRes.data.items : []
    positions.value = posItems.map(p => {
      const entry = Number(p?.entry_time || 0)
      const entryPx = Number(p?.entry_price || 0)
      const curPx = Number(p?.current_price || 0)
      const pnlPct = Number(p?.pnl_pct || 0)
      return {
        ...p,
        entry_time_text: formatTs(entry),
        entry_price_text: Number.isFinite(entryPx) ? entryPx.toFixed(4) : '-',
        current_price_text: Number.isFinite(curPx) ? curPx.toFixed(4) : '-',
        pnl_pct_text: formatPct(pnlPct)
      }
    })
    portfolioSummary.value = posRes.data?.summary || null

    const orders = Array.isArray(orderRes.data?.items) ? orderRes.data.items : []
    pendingOrders.value = orders
      .filter(o => ['pending', 'blocked'].includes(String(o?.status || '').toLowerCase()))
      .map(o => {
        const px = Number(o?.price || 0)
        return {
          ...o,
          requested_time_text: formatTs(o?.requested_ts),
          effective_time_text: formatTs(o?.execute_after_ts),
          side_text: formatSide(o?.side),
          status_text: formatLiveStatus(o?.status, o?.side),
          price_text: Number.isFinite(px) ? px.toFixed(4) : '-',
          reason_text: formatOrderReason(o?.reason)
        }
      })

    await loadTrades()
  } catch (e) {
  } finally {
    positionsLoading.value = false
  }
}

const resetPositions = async () => {
  resettingPositions.value = true
  stepError.value = ''
  try {
    await axios.post('/api/live/positions/reset', null, { params: { sim_id: activeSimId.value, portfolio_id: activePortfolioId.value } })
    await loadPositions()
  } catch (e) {
    stepError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    resettingPositions.value = false
  }
}

const runPaperStep = async () => {
  stepError.value = ''
  stepResult.value = null
  stepLoading.value = true
  try {
    const payload = {
      ...(props.paperConfig || {}),
      ...(props.paperParams || {}),
      sim_id: activeSimId.value,
      portfolio_id: activePortfolioId.value,
      enable_rolling_lookback: true
    }
    const res = await axios.post('/api/live/paper_trade_step', payload)
    stepResult.value = res.data || null
    if (Array.isArray(stepResult.value?.pending_orders)) {
      pendingOrders.value = (stepResult.value.pending_orders || [])
        .filter(o => ['pending', 'blocked'].includes(String(o?.status || '').toLowerCase()))
        .map(o => {
          const px = Number(o?.price || 0)
          return {
            ...o,
            requested_time_text: formatTs(o?.requested_ts),
            effective_time_text: formatTs(o?.execute_after_ts),
            side_text: formatSide(o?.side),
            status_text: formatLiveStatus(o?.status, o?.side),
            price_text: Number.isFinite(px) ? px.toFixed(4) : '-',
            reason_text: formatOrderReason(o?.reason)
          }
        })
    }
    tradePage.value = 1
    await loadPositions()
  } catch (e) {
    stepError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    stepLoading.value = false
  }
}

const initPaper = async () => {
  if (paperInitialized.value) return
  paperInitialized.value = true
  await loadPositions()
}

const loadRuns = async () => {
  runsError.value = ''
  runsLoading.value = true
  try {
    const res = await axios.get('/api/live/runs', { params: { limit: 200, portfolio_id: activePortfolioId.value } })
    liveRuns.value = Array.isArray(res.data?.items) ? res.data.items : []
  } catch (e) {
    runsError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    runsLoading.value = false
  }
}

const createRun = async () => {
  runsError.value = ''
  runsLoading.value = true
  try {
    const cfg = props.paperConfig || {}
    const baseSid = String(activeSimId.value || '').trim() || 'default'
    const sid = baseSid === 'default'
      ? `sim_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
      : baseSid
    const params = {
      ...(props.paperConfig || {}),
      ...(props.paperParams || {}),
      sim_id: sid,
      portfolio_id: activePortfolioId.value,
      enable_rolling_lookback: true
    }
    const payload = {
      name: String(newRunName.value || '').trim(),
      sim_id: sid,
      portfolio_id: activePortfolioId.value,
      params,
      offset_seconds: Number(newRunOffsetSeconds.value || 0),
      auto_resume: !!newRunAutoResume.value,
      notify_enabled: !!newRunNotifyEnabled.value,
      start: !!newRunStart.value
    }
    const res = await axios.post('/api/live/runs', payload)
    preserveSelectedRunIdOnce = true
    localSimId.value = sid
    emit('selectSimId', sid)
    selectedRunId.value = String(res.data?.item?.run_id || '').trim()
    stepLogPage.value = 1
    tradePage.value = 1
    await loadRuns()
    await loadPositions()
    await loadStepLogs()
    await loadEquity()
  } catch (e) {
    runsError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    runsLoading.value = false
  }
}

const startRun = async (runId) => {
  try {
    await axios.post(`/api/live/runs/${runId}/start`)
    await loadRuns()
  } catch (e) {
    runsError.value = String(e?.response?.data?.detail || e?.message || e)
  }
}

const stopRun = async (runId) => {
  try {
    await axios.post(`/api/live/runs/${runId}/stop`)
    await loadRuns()
  } catch (e) {
    runsError.value = String(e?.response?.data?.detail || e?.message || e)
  }
}

const deleteRun = async (runId) => {
  const rid = String(runId || '').trim()
  if (!rid) return
  const wasSelected = String(selectedRunId.value || '') === rid
  let simId = ''
  try {
    const found = (liveRuns.value || []).find(r => String(r?.run_id || '') === rid)
    simId = String(found?.sim_id || '').trim()
    if (!simId) {
      try {
        const res = await axios.get(`/api/live/runs/${rid}`)
        simId = String(res.data?.item?.sim_id || '').trim()
      } catch (e) {
      }
    }

    await axios.delete(`/api/live/runs/${rid}`)
    if (wasSelected) selectedRunId.value = ''
    await loadRuns()

    if (simId) {
      const hasRunsForSim = (liveRuns.value || []).some(r => String(r?.sim_id || '').trim() === simId)
      if (!hasRunsForSim) {
        await axios.post('/api/live/steps/reset', null, { params: { sim_id: simId, portfolio_id: activePortfolioId.value } })
        await axios.post('/api/live/positions/reset', null, { params: { sim_id: simId, portfolio_id: activePortfolioId.value } })
      }

      if (String(activeSimId.value || '') === simId) {
        stepLogPage.value = 1
        tradePage.value = 1
        await loadPositions()
        await loadStepLogs()
        await loadEquity()
      }
    } else if (wasSelected) {
      stepLogPage.value = 1
      await loadStepLogs()
      await loadEquity()
    }
  } catch (e) {
    runsError.value = String(e?.response?.data?.detail || e?.message || e)
  }
}

const selectRunSim = (run) => {
  const sid = String(run?.sim_id || '').trim()
  if (!sid) return
  localSimId.value = sid
  emit('selectSimId', sid)
}

const applyRunParams = async (run) => {
  const runId = String(run?.run_id || '').trim()
  if (!runId) return
  try {
    const res = await axios.get(`/api/live/runs/${runId}`)
    const params = res.data?.item?.params || null
    if (params && typeof params === 'object') emit('applyRunParams', params)
    const sid = String(res.data?.item?.sim_id || '').trim()
    if (sid) {
      preserveSelectedRunIdOnce = true
      localSimId.value = sid
      emit('selectSimId', sid)
    }
    selectedRunId.value = runId
    stepLogPage.value = 1
    tradePage.value = 1
    await loadPositions()
    await loadStepLogs()
    await loadEquity()
  } catch (e) {
    runsError.value = String(e?.response?.data?.detail || e?.message || e)
  }
}

const loadStepLogs = async () => {
  stepsError.value = ''
  stepsLoading.value = true
  try {
    const res = await axios.get('/api/live/steps/stocks', { params: { sim_id: activeSimId.value, run_id: selectedRunId.value || undefined, portfolio_id: activePortfolioId.value, page: stepLogPage.value, page_size: stepLogPageSize.value } })
    stepLogs.value = Array.isArray(res.data?.items) ? res.data.items : []
    stepLogTotal.value = Number(res.data?.total || 0)
  } catch (e) {
    stepsError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    stepsLoading.value = false
  }
}

const loadEquity = async () => {
  equityError.value = ''
  equityLoading.value = true
  try {
    const res = await axios.get('/api/live/equity_curve', { params: { sim_id: activeSimId.value, run_id: selectedRunId.value || undefined, portfolio_id: activePortfolioId.value, limit: 2000 } })
    equityPoints.value = Array.isArray(res.data?.points) ? res.data.points : []
    nextTick(() => renderEquityChart())
  } catch (e) {
    equityError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    equityLoading.value = false
  }
}

const renderEquityChart = () => {
  if (mode.value !== 'paper') return
  const el = equityChartContainer.value
  if (!el) return

  const points = Array.isArray(equityPoints.value) ? equityPoints.value : []
  const xs = []
  const ys = []
  for (const p of points) {
    const ts = Number(p?.ts)
    const eq = Number(p?.equity)
    if (!Number.isFinite(ts) || !Number.isFinite(eq)) continue
    xs.push(formatTs(ts))
    ys.push(eq)
  }

  if (!equityChartInstance) equityChartInstance = echarts.init(el)

  const isDark = typeof document !== 'undefined' && document.documentElement?.classList?.contains('dark')
  const axisLabelColor = isDark ? '#d1d5db' : '#4b5563'
  const splitLineColor = isDark ? '#374151' : '#e5e7eb'

  const option = xs.length
    ? {
        grid: { left: 40, right: 18, top: 18, bottom: 34 },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category',
          data: xs,
          axisLabel: { color: axisLabelColor, hideOverlap: true }
        },
        yAxis: {
          type: 'value',
          scale: true,
          axisLabel: { color: axisLabelColor },
          splitLine: { lineStyle: { color: splitLineColor } }
        },
        dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 6 }],
        series: [
          {
            data: ys,
            type: 'line',
            smooth: true,
            showSymbol: false,
            lineStyle: { color: '#3b82f6', width: 2 },
            areaStyle: {
              color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                { offset: 0, color: 'rgba(59,130,246,0.35)' },
                { offset: 1, color: 'rgba(59,130,246,0.0)' }
              ])
            }
          }
        ]
      }
    : {
        grid: { left: 10, right: 10, top: 10, bottom: 10 },
        xAxis: { show: false, type: 'category', data: [] },
        yAxis: { show: false, type: 'value' },
        series: [],
        graphic: [
          {
            type: 'text',
            left: 'center',
            top: 'middle',
            style: {
              text: '-',
              fill: axisLabelColor,
              fontSize: 14
            }
          }
        ]
      }

  equityChartInstance.setOption(option, { notMerge: true })
}

const renderChart = () => {
    if (!chartContainer.value || !backtestResult.value) return
    if (chartInstance) chartInstance.dispose()
    
    chartInstance = echarts.init(chartContainer.value)
    const curve = backtestResult.value.equity_curve
    
    const option = {
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: curve.map(x => x.time) },
        yAxis: { type: 'value', scale: true },
        series: [{
            data: curve.map(x => x.equity),
            type: 'line',
            smooth: true,
            showSymbol: false,
            lineStyle: { color: '#ef4444' },
            areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{offset: 0, color: 'rgba(239,68,68,0.5)'}, {offset: 1, color: 'rgba(239,68,68,0.0)'}]) }
        }]
    }
    chartInstance.setOption(option)
}

const handleResize = () => {
  if (chartInstance) chartInstance.resize()
  if (equityChartInstance) equityChartInstance.resize()
}

// Expose methods for parent
defineExpose({
  refresh: fetchPortfolios,
  openAddPosition: (code, name) => {
    mode.value = 'portfolio'
    newPosition.value.code = code
    newPosition.value.name = name || ''
    showAddPosModal.value = true
  },
  runPaperStep: async () => {
    mode.value = 'paper'
    await runPaperStep()
  },
  loadPositions: async () => {
    mode.value = 'paper'
    await loadPositions()
  },
  resetPositions: async () => {
    mode.value = 'paper'
    await resetPositions()
  }
})

onMounted(() => {
  fetchPortfolios()
  if (typeof window !== 'undefined') {
    window.addEventListener('resize', handleResize, { passive: true })
  }
  watch(
    tradePage,
    () => {
      if (mode.value !== 'paper') return
      loadTrades()
    },
    { immediate: false }
  )
  watch(
    () => activeSimId.value,
    () => {
      if (mode.value !== 'paper') return
      stepLogPage.value = 1
      tradePage.value = 1
      if (!preserveSelectedRunIdOnce) selectedRunId.value = ''
      preserveSelectedRunIdOnce = false
      loadPositions()
      loadRuns()
      loadStepLogs()
      loadEquity()
    },
    { immediate: false }
  )
  watch(
    () => activePortfolioId.value,
    () => {
      if (mode.value !== 'paper') return
      stepLogPage.value = 1
      tradePage.value = 1
      selectedRunId.value = ''
      loadPositions()
      loadRuns()
      loadStepLogs()
      loadEquity()
    },
    { immediate: false }
  )
  watch(
    mode,
    (m) => {
      if (m === 'paper') {
        initPaper()
        loadRuns()
        loadStepLogs()
        loadEquity()
        nextTick(() => renderEquityChart())
        if (liveRefreshTimer) clearInterval(liveRefreshTimer)
        liveRefreshTimer = setInterval(() => {
          loadRuns()
          loadPositions()
          loadStepLogs()
          loadEquity()
        }, 15000)
      } else {
        if (liveRefreshTimer) clearInterval(liveRefreshTimer)
        liveRefreshTimer = null
        if (equityChartInstance) {
          equityChartInstance.dispose()
          equityChartInstance = null
        }
      }
    },
    { immediate: true }
  )
})

onBeforeUnmount(() => {
  if (liveRefreshTimer) clearInterval(liveRefreshTimer)
  liveRefreshTimer = null
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', handleResize)
  }
  if (chartInstance) {
    chartInstance.dispose()
    chartInstance = null
  }
  if (equityChartInstance) {
    equityChartInstance.dispose()
    equityChartInstance = null
  }
})
</script>
