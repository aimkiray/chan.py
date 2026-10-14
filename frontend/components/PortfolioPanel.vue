<template>
  <div class="h-full flex flex-col gap-4">
    <div v-if="!fixedMode" class="flex items-center gap-2 border-b border-gray-200 dark:border-gray-800 px-4 pt-4 pb-2">
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
      <div class="w-1/3 flex flex-col gap-2 pl-4 py-4">
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
          <div class="flex justify-between items-center mb-2 pt-4 pr-4">
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
    <div v-else-if="mode === 'paper'" class="flex-1 min-h-0 flex flex-col overflow-y-auto p-6">
        <div v-if="lastBacktestToShow" class="mb-4">
          <div class="flex items-center justify-between gap-2 mb-2">
            <div class="text-xs text-gray-500">
              上一次回测结果
              <span v-if="lastBacktestToShow?.meta?.time" class="text-gray-400">· {{ lastBacktestToShow.meta.time }}</span>
              <span v-if="lastBacktestToShow?.meta?.kind" class="text-gray-400">· {{ lastBacktestToShow.meta.kind }}</span>
            </div>
            <UButton size="xs" color="gray" variant="soft" @click="showLastBacktest = !showLastBacktest">
              {{ showLastBacktest ? '收起' : '展开' }}
            </UButton>
          </div>
          <div v-if="showLastBacktest">
            <div class="grid grid-cols-4 gap-4">
              <UCard>
                <div class="text-xs text-gray-500">{{ t('portfolio.totalReturn') }}</div>
                <div class="mt-1 font-mono text-sm" :class="getColor(lastBacktestToShow.metrics.total_return)">
                  {{ (Number(lastBacktestToShow.metrics.total_return || 0) * 100).toFixed(2) }}%
                </div>
              </UCard>
              <UCard>
                <div class="text-xs text-gray-500">{{ t('portfolio.sharpe') }}</div>
                <div class="mt-1 font-mono text-sm">{{ Number(lastBacktestToShow.metrics.sharpe_ratio || 0).toFixed(2) }}</div>
              </UCard>
              <UCard>
                <div class="text-xs text-gray-500">{{ t('portfolio.maxDD') }}</div>
                <div class="mt-1 font-mono text-sm text-red-500">{{ (Number(lastBacktestToShow.metrics.max_drawdown || 0) * 100).toFixed(2) }}%</div>
              </UCard>
              <UCard>
                <div class="text-xs text-gray-500">{{ t('portfolio.finalEquity') }}</div>
                <div class="mt-1 font-mono text-sm">{{ Number(lastBacktestToShow.metrics.final_equity || 1).toFixed(4) }}</div>
              </UCard>
            </div>

            <div v-if="lastBacktestTradeRows.length" class="mt-3">
              <UCard>
                <template #header>
                  <div class="text-xs text-gray-500">最近交易（回测）</div>
                </template>
                <UTable :rows="lastBacktestTradeRows" :columns="lastBacktestTradeColumns" class="w-full" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
              </UCard>
            </div>
          </div>
        </div>

        <div class="mb-2 flex flex-wrap items-center gap-2 text-xs text-gray-500">
          <span>ClickHouse</span>
          <span :class="clickhouseHealth?.ok ? 'text-green-600 dark:text-green-400' : 'text-amber-600 dark:text-amber-400'">
            {{ clickhouseHealth?.ok ? 'OK' : 'Unavailable' }}
          </span>
          <span v-if="clickhouseHealth" class="font-mono">
            {{ clickhouseHealth.host || '-' }}:{{ clickhouseHealth.port || '-' }}/{{ clickhouseHealth.database || '-' }}
          </span>
          <span v-if="clickhouseHealth?.last_error" class="text-gray-400 break-all">{{ clickhouseHealth.last_error }}</span>
          <UButton size="xs" color="gray" variant="ghost" :loading="clickhouseHealthLoading" @click="loadClickHouseHealth">刷新</UButton>
        </div>

        <div v-if="stepError" class="mb-2 text-xs text-red-500 break-all">{{ stepError }}</div>
        <div v-if="stepResult" class="mb-2 text-xs text-gray-500 break-all">
          {{ t('strategy.stepMeta', { day: stepResult.day, ts: stepResult.ts }) }} · {{ formatTs(stepResult.ts) }} · model={{ stepResult.model_key || '-' }} · rank={{ stepResult.rank_mode || '-' }} · {{ t('strategy.marketStatus') }}={{ stepResult.market_open ? t('strategy.marketOpen') : t('strategy.marketClosed') }}
        </div>
        <div v-if="stepResult?.risk" class="mb-4 text-xs text-gray-500 break-all">
          {{ t('strategy.risk') }}={{ stepResult.risk.level || '-' }} · topN={{ stepResult.risk.effective_top_n ?? '-' }} · gap={{ stepResult.risk.gap_open_pct === null || stepResult.risk.gap_open_pct === undefined ? '-' : formatPct(stepResult.risk.gap_open_pct) }} · d1={{ stepResult.risk.ret1d === null || stepResult.risk.ret1d === undefined ? '-' : formatPct(stepResult.risk.ret1d) }} · atr={{ stepResult.risk.atr_pct === null || stepResult.risk.atr_pct === undefined ? '-' : formatPct(stepResult.risk.atr_pct) }} · in={{ stepResult.risk.intraday?.change === null || stepResult.risk.intraday?.change === undefined ? '-' : formatPct(stepResult.risk.intraday.change) }} · trend={{ stepResult.risk.intraday?.trend || '-' }}<span v-if="stepResult.risk.reason" class="text-gray-400"> · reason={{ stepResult.risk.reason }}</span>
        </div>

        <div v-if="shouldShowNoPickReason" class="mb-4">
          <UCard>
            <template #header>
              <div class="text-xs text-gray-500">本次未选出符合条件的股票（原因）</div>
            </template>
            <div class="text-xs text-gray-600 dark:text-gray-300 space-y-1">
              <div v-for="(line, idx) in noPickReasonLines" :key="idx" class="font-mono break-all">{{ line }}</div>
              <div v-if="!noPickReasonLines.length" class="text-gray-400">-</div>
            </div>
            <div v-if="noPickTop10.length" class="mt-3">
              <div class="text-xs text-gray-500 mb-1">Top10 候选</div>
              <div v-for="c in noPickTop10" :key="c.code" class="text-xs font-mono">
                {{ c.rank }}. {{ c.code }} · {{ Number(c.prob).toFixed(4) }} · {{ c.pass_min_prob ? 'pass' : 'fail' }}
                <span v-if="c.action" class="text-gray-500"> · {{ c.action }}</span>
              </div>
            </div>
          </UCard>
        </div>

        <div>
            <div class="flex items-center justify-between gap-2 mb-4">
              <div class="text-xs text-gray-500 flex items-center gap-1 min-w-0">
                <span>{{ t('strategy.autoRun') }}</span>
                <span class="text-gray-400">·</span>
                <span class="font-mono truncate">{{ t('portfolio.simSession') }}: {{ activeSimId }}</span>
                <UTooltip :text="t('portfolio.simSessionHint')" :popper="{ placement: 'top' }">
                  <UIcon name="i-heroicons-exclamation-circle" class="w-4 h-4 text-gray-400 hover:text-gray-600 cursor-help shrink-0" />
                </UTooltip>
              </div>
              <UButton size="xs" color="gray" variant="soft" :loading="runsLoading" @click="loadRuns">{{ t('strategy.refresh') }}</UButton>
            </div>
            <div v-if="runsError" class="mb-2 text-xs text-red-500 break-all">{{ runsError }}</div>
            <div class="mt-6 space-y-2">
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
                  <UButton size="xs" color="gray" variant="soft" @click="openRunParams(r)">{{ t('strategy.viewParams') }}</UButton>
                  <UButton size="xs" color="gray" variant="soft" @click="goBacktestWithParams(r)">{{ t('strategy.goBacktestFill') }}</UButton>
                  <UButton size="xs" color="emerald" variant="soft" :loading="runningOnceRunId === String(r.run_id||'')" @click="runOnce(r)">{{ t('strategy.runOnce') }}</UButton>
                  <UButton v-if="String(r.status||'') !== 'running'" size="xs" color="primary" variant="soft" @click="startRun(r.run_id)">{{ t('strategy.startRun') }}</UButton>
                  <UButton v-else size="xs" color="orange" variant="soft" @click="stopRun(r.run_id)">{{ t('strategy.stopRun') }}</UButton>
                  <UButton size="xs" color="red" variant="soft" @click="deleteRun(r.run_id)">{{ t('strategy.delete') }}</UButton>
                </div>
              </div>
              <div v-if="!liveRuns.length" class="text-xs text-gray-400">{{ t('portfolio.noPaperRunsHint') }}</div>
            </div>
        </div>

        <div v-if="portfolioSummary" class="mt-6 grid grid-cols-4 gap-4">
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

        <div v-if="stepResult" class="mt-6 grid grid-cols-3 gap-4">
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

        <div v-if="stepResult?.selection_debug" class="mt-4">
          <div class="flex items-center justify-between gap-2 mb-2">
            <div class="text-xs text-gray-500">选股调试</div>
            <UButton size="xs" color="gray" variant="soft" @click="showSelectionDebug = !showSelectionDebug">
              {{ showSelectionDebug ? '收起' : '展开' }}
            </UButton>
          </div>
          <UCard v-if="showSelectionDebug">
            <div class="text-xs text-gray-500 break-all">
              day={{ selectionDebug?.signal_day || '-' }} · mode={{ selectionDebug?.signal_day_mode || '-' }} · minProb={{ selectionDebug?.min_prob ?? '-' }} · topN={{ selectionDebug?.top_n ?? '-' }} · effTopN={{ selectionDebug?.effective_top_n ?? '-' }} · scanned={{ selectionDebug?.counts?.scanned ?? '-' }} · today={{ selectionDebug?.counts?.todays_raw ?? '-' }} · pass={{ selectionDebug?.counts?.todays_pass_min_prob ?? '-' }}
              <span v-if="selectionDebug?.decision_delayed"> · delayed={{ selectionDebug?.decision_time || '-' }}</span>
            </div>

            <div v-if="selectionDebugCandidateAudit.length" class="mt-3">
              <div class="text-xs text-gray-500 mb-1">候选审计</div>
              <div class="overflow-x-auto border border-gray-200 dark:border-gray-800 rounded">
                <table class="w-full text-xs">
                  <thead class="bg-gray-50 dark:bg-gray-900 text-gray-500">
                    <tr>
                      <th class="px-2 py-1 text-left font-medium">Rank</th>
                      <th class="px-2 py-1 text-left font-medium">Code</th>
                      <th class="px-2 py-1 text-right font-medium">Prob</th>
                      <th class="px-2 py-1 text-left font-medium">Status</th>
                      <th class="px-2 py-1 text-left font-medium">Reason</th>
                      <th class="px-2 py-1 text-left font-medium">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="c in selectionDebugCandidateAudit" :key="`${c.rank}-${c.code}`" class="border-t border-gray-100 dark:border-gray-800">
                      <td class="px-2 py-1 font-mono">{{ c.rank ?? '-' }}</td>
                      <td class="px-2 py-1 font-mono">{{ c.code || '-' }}</td>
                      <td class="px-2 py-1 text-right font-mono">{{ formatNumber(c.prob, 4) }}</td>
                      <td class="px-2 py-1">
                        <span :class="candidateStatusClass(c.status)">{{ c.status || '-' }}</span>
                      </td>
                      <td class="px-2 py-1 font-mono text-gray-500">{{ c.filter_reason || '-' }}</td>
                      <td class="px-2 py-1 font-mono">{{ c.action || '-' }}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="mt-3 grid grid-cols-2 gap-4">
              <div>
                <div class="text-xs text-gray-500 mb-1">Top候选</div>
                <div v-for="c in selectionDebugCandidatesTop" :key="c.code" class="text-xs font-mono">
                  {{ c.rank }}. {{ c.code }} · {{ Number(c.prob).toFixed(4) }} · {{ c.pass_min_prob ? 'pass' : 'fail' }}
                  <span v-if="c.selected" class="text-green-600"> · selected</span>
                  <span v-if="c.action" class="text-gray-500"> · {{ c.action }}</span>
                </div>
                <div v-if="!selectionDebugCandidatesTop.length" class="text-xs text-gray-400">-</div>
              </div>
              <div>
                <div class="text-xs text-gray-500 mb-1">低于minProb(最高若干)</div>
                <div v-for="c in selectionDebugCandidatesBelow" :key="c.code" class="text-xs font-mono">
                  {{ c.rank }}. {{ c.code }} · {{ Number(c.prob).toFixed(4) }}
                </div>
                <div v-if="!selectionDebugCandidatesBelow.length" class="text-xs text-gray-400">-</div>
              </div>
            </div>

            <div v-if="selectionDebugSellReasonItems.length" class="mt-3">
              <div class="text-xs text-gray-500 mb-1">计划卖出原因</div>
              <div v-for="[k, v] in selectionDebugSellReasonItems" :key="k" class="text-xs font-mono">
                {{ k }} · {{ v }}
              </div>
            </div>

            <div v-if="selectionDebugBuyReasonItems.length" class="mt-3">
              <div class="text-xs text-gray-500 mb-1">计划买入原因</div>
              <div v-for="[k, v] in selectionDebugBuyReasonItems" :key="k" class="text-xs font-mono">
                {{ k }} · {{ v }}
              </div>
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

    </div>

    <!-- Backtest Mode -->
    <div v-else class="flex-1 min-h-0 flex flex-col overflow-y-auto p-6">
      <div v-if="currentBacktestResult" class="flex flex-col gap-6">
        <div class="grid grid-cols-4 gap-4">
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.totalReturn') }}</div>
            <div class="text-xl font-bold" :class="getColor(currentBacktestResult.metrics.total_return)">
              {{ (currentBacktestResult.metrics.total_return * 100).toFixed(2) }}%
            </div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.sharpe') }}</div>
            <div class="text-xl font-bold">{{ currentBacktestResult.metrics.sharpe_ratio.toFixed(2) }}</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.maxDD') }}</div>
            <div class="text-xl font-bold text-red-500">{{ (currentBacktestResult.metrics.max_drawdown * 100).toFixed(2) }}%</div>
          </UCard>
          <UCard>
            <div class="text-xs text-gray-500">{{ t('portfolio.finalEquity') }}</div>
            <div class="text-xl font-bold">{{ currentBacktestResult.metrics.final_equity.toFixed(2) }}</div>
          </UCard>
        </div>

        <!-- Simple Chart Placeholder -->
        <UCard>
           <template #header>{{ t('portfolio.equityCurve') }}</template>
           <div class="h-[300px]" style="width: 100%; min-width: 0;" ref="chartContainer"></div>
        </UCard>

        <div class="flex flex-col gap-4">
          <UCard v-for="tb in backtestTradeTables" :key="tb.key">
            <template #header>
              <div class="flex items-center justify-between gap-2">
                <div class="min-w-0 truncate">{{ tb.title }}</div>
                <UButton
                  v-if="tb.snapshot_date"
                  size="xs"
                  color="gray"
                  variant="soft"
                  @click.stop="toggleBacktestMembers(tb)"
                >
                  {{ backtestMembersButtonText(tb) }}
                </UButton>
              </div>
              <div v-if="isBacktestMembersOpen(tb)" class="mt-2">
                <div class="max-h-40 overflow-auto">
                  <div class="text-xs text-gray-500 break-all font-mono">
                    {{ (backtestMembersCodes(tb) || []).join(' ') }}
                  </div>
                </div>
              </div>
            </template>
            <div class="max-h-[420px] overflow-auto">
              <UTable :rows="tb.rows" :columns="backtestTradeColumns" class="w-full" :ui="{ th: { base: 'sticky top-0 bg-gray-50 dark:bg-gray-900 z-10' } }" />
            </div>
          </UCard>
        </div>

        <div class="flex flex-col gap-2">
          <UCheckbox v-model="backtestConfirmed" :label="t('portfolio.confirmBacktest')" />
          <UButton
            block
            color="primary"
            :disabled="!backtestConfirmed"
            :loading="paperRunFromBacktestLoading"
            @click="openRunPaperFromBacktest"
          >
            {{ t('portfolio.runPaperFromBacktest') }}
          </UButton>
          <div class="text-xs text-gray-500">{{ t('portfolio.goPaperToView') }}</div>
        </div>
      </div>
      <div v-else class="flex-1 flex items-center justify-center text-gray-400">
        {{ t('portfolio.runBacktest') }}
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

    <UModal v-model="paperRunFromBacktestConfirmOpen">
      <UCard :ui="{ body: { padding: 'p-4' } }">
        <div class="text-sm font-medium text-gray-700 dark:text-gray-200 mb-3">{{ t('portfolio.runPaperFromBacktest') }}</div>
        <div class="space-y-2">
          <div class="flex items-center justify-between gap-2">
            <UCheckbox v-model="paperRunFromBacktestTrainOnFly" :label="t('strategy.trainOnFly')" />
          </div>
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="paperRunFromBacktestConfirmOpen = false">{{ t('app.cancel') }}</UButton>
            <UButton color="primary" :disabled="!backtestConfirmed" :loading="paperRunFromBacktestLoading" @click="confirmRunPaperFromBacktest">
              {{ t('portfolio.runPaperFromBacktest') }}
            </UButton>
          </div>
        </template>
      </UCard>
    </UModal>

    <UModal v-model="showRunParamsModal">
      <UCard>
        <template #header>{{ t('strategy.viewParams') }}</template>
        <div v-if="runParamsError" class="mb-2 text-xs text-red-500 break-all">{{ runParamsError }}</div>
        <div class="max-h-[70vh] overflow-auto">
          <div v-if="runParamsLoading" class="text-xs text-gray-400">{{ t('strategy.running') }}</div>
          <pre v-else class="text-xs font-mono whitespace-pre break-all">{{ runParamsText }}</pre>
        </div>
        <template #footer>
          <div class="flex justify-end gap-2">
            <UButton color="gray" variant="ghost" @click="showRunParamsModal = false">{{ t('portfolio.close') }}</UButton>
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
  backtestSimId: { type: String, default: 'default' },
  paperParams: { type: Object, default: () => ({}) },
  paperRunForm: { type: Object, default: () => ({}) }
})
const emit = defineEmits(['selectSimId', 'applyRunParams', 'openPaper', 'openBacktestFill'])

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

const showRunParamsModal = ref(false)
const runParamsLoading = ref(false)
const runParamsError = ref('')
const runParamsItem = ref(null)
const runParamsText = computed(() => {
  const it = runParamsItem.value
  if (!it) return '-'
  try {
    return JSON.stringify(it, null, 2)
  } catch (e) {
    return String(it)
  }
})

// Backtest
const backtestKind = ref('wolfpack')
const backtestConfig = ref({
  start_date: defaultStartDate,
  end_date: defaultEndDate,
  model_key: ''
})
const backtestLoading = ref(false)
const vectorBacktestResult = ref(null)
const wolfpackBacktestResult = ref(null)
const backtestConfirmed = ref(false)
const lastBacktestPaperConfig = ref(null)
const lastBacktestPaperParams = ref(null)
const paperRunFromBacktestLoading = ref(false)
const paperRunFromBacktestConfirmOpen = ref(false)
const paperRunFromBacktestTrainOnFly = ref(false)

const currentBacktestResult = computed(() => {
  return backtestKind.value === 'vector' ? vectorBacktestResult.value : wolfpackBacktestResult.value
})

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
  data_src: 'clickhouse',
  autype: 'qfq',
  enable_rolling_lookback: true,
  holding_period: 3,
  min_prob: 0.25,
  trade_cost: 0.0002,
  walk_forward_max_folds: 25,
  rolling_sample_mode: 'bsp',
  rolling_stride: 1,
  portfolio_top_n: 2,
  index_filter_enabled: true,
  index_code: '399006',
  index_ma_window: 20,
  stop_loss: -0.03,
  enforce_t1: true,
  sell_mode: 'baseline',
  t1_decision_time: '10:00',
  t1_gap_strong_threshold: 0.03,
  t1_gap_weak_threshold: -0.02,
  t1_gap_very_strong_threshold: 0.05,
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
  risk_intraday_data_fail_mode: 'reduce'
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
  { label: t('app.dataSrcOptions.baostock'), value: 'baostock' }
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
const riskFailModeOptions = computed(() => ([
  { label: t('strategy.failModePass'), value: 'pass' },
  { label: t('strategy.failModeReduce'), value: 'reduce' },
  { label: t('strategy.failModeFlat'), value: 'flat' }
]))
const rollingSampleModeOptions = computed(() => ([
  { label: t('strategy.rollingModeEveryBar'), value: 'every_bar' },
  { label: t('strategy.rollingModeBsp'), value: 'bsp' }
]))
const sellModeOptions = computed(() => ([
  { label: t('strategy.sellModeBaseline'), value: 'baseline' },
  { label: t('strategy.sellModeDelayed'), value: 'delayed' },
  { label: t('strategy.sellModeGapRoll'), value: 't1_gap_roll' }
]))

const poolOptions = ref([{ label: '创业板50 (50)', value: 'index:IDX399673' }])
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

onMounted(async () => {
    fetchPortfolios()
    
    const savedIndex = localStorage.getItem('mlchan_wolfpack_index_code')
    if (savedIndex) {
        wolfpackConfig.value.index_code = savedIndex
    }

    const cached = loadIndexOptionsCache()
    if (cached.length > 0) {
        indexOptions.value = cached
        ensureIndexOption(wolfpackConfig.value.index_code)
        return
    }

    try {
        const cur = savedIndex || String(wolfpackConfig.value.index_code || '').trim() || '399006'
        
        if (indexOptions.value.length === 0) {
            searchingIndices.value = true
        }
        
        const res = await axios.get('/api/indices', { params: { limit: 50, keyword: cur } })
        if (res.data && res.data.items) {
            const items = res.data.items.map(c => {
                const code = typeof c === 'string' ? c : (c.code || '')
                const name = typeof c === 'string' ? c : (c.name || code)
                return { label: `${name} (${code})`, value: code, name: name }
            })
            indexOptions.value = items
            persistIndexOptionsCache(items)
            const curVal = String(cur || '').trim()
            if (curVal && !items.find(x => x.value === curVal)) {
                const curDigits = curVal.replace(/\D/g, '')
                const match = (curDigits ? items.find(x => String(x.value || '').replace(/\D/g, '') === curDigits) : null)
                    || (curDigits ? items.find(x => String(x.value || '').replace(/\D/g, '').endsWith(curDigits)) : null)
                if (match && match.value) {
                    wolfpackConfig.value.index_code = match.value
                }
            }

            const hasCur = !!(wolfpackConfig.value.index_code && indexOptions.value.find(x => x.value === wolfpackConfig.value.index_code))
            if (!hasCur && indexOptions.value.length > 0) {
                if (!wolfpackConfig.value.index_code) {
                    wolfpackConfig.value.index_code = items[0].value
                }
            }
        }
    } catch (e) {
    } finally {
        searchingIndices.value = false
    }
})

const searchingIndices = ref(false)
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

watch(() => wolfpackConfig.value.index_code, (val) => {
    if (val) localStorage.setItem('mlchan_wolfpack_index_code', val)
    ensureIndexOption(val)
}, { deep: true })

const backtestKindOptions = computed(() => ([
  { label: t('portfolio.wolfpackBacktest'), value: 'wolfpack' },
  { label: t('portfolio.simpleBacktest'), value: 'vector' }
]))

const paperInitialized = ref(false)
const stepLoading = ref(false)
const stepError = ref('')
const stepResult = ref(null)
const showSelectionDebug = ref(false)
const clickhouseHealth = ref(null)
const clickhouseHealthLoading = ref(false)

watch(stepResult, () => {
  const sr = stepResult.value
  const noTargets = !Array.isArray(sr?.targets) || sr.targets.length === 0
  const noBuys = !Array.isArray(sr?.buy) || sr.buy.length === 0
  showSelectionDebug.value = !!sr && noTargets && noBuys
})

const selectionDebug = computed(() => {
  const sr = stepResult.value
  if (!sr || typeof sr !== 'object') return null
  const sd = sr.selection_debug
  return sd && typeof sd === 'object' ? sd : null
})

const selectionDebugCandidatesTop = computed(() => {
  const arr = selectionDebug.value?.candidates_top
  return Array.isArray(arr) ? arr : []
})

const selectionDebugCandidatesBelow = computed(() => {
  const arr = selectionDebug.value?.candidates_below_min_prob
  return Array.isArray(arr) ? arr : []
})

const selectionDebugCandidateAudit = computed(() => {
  const arr = selectionDebug.value?.candidate_audit
  return Array.isArray(arr) ? arr : []
})

const loadClickHouseHealth = async () => {
  clickhouseHealthLoading.value = true
  try {
    const res = await axios.get('/api/health/clickhouse')
    clickhouseHealth.value = res.data || null
  } catch (e) {
    clickhouseHealth.value = {
      ok: false,
      last_error: String(e?.response?.data?.detail || e?.message || e)
    }
  } finally {
    clickhouseHealthLoading.value = false
  }
}

const selectionDebugSellReasonItems = computed(() => {
  const obj = selectionDebug.value?.sell_reason_by_code
  if (!obj || typeof obj !== 'object') return []
  try {
    return Object.entries(obj).sort((a, b) => String(a[0]).localeCompare(String(b[0])))
  } catch (e) {
    return Object.entries(obj)
  }
})

const selectionDebugBuyReasonItems = computed(() => {
  const obj = selectionDebug.value?.buy_reason_by_code
  if (!obj || typeof obj !== 'object') return []
  try {
    return Object.entries(obj).sort((a, b) => String(a[0]).localeCompare(String(b[0])))
  } catch (e) {
    return Object.entries(obj)
  }
})

const showLastBacktest = ref(false)
const lastBacktestPersisted = ref(null)

const lastBacktestToShow = computed(() => {
  const cur = currentBacktestResult.value
  if (cur && cur.metrics && cur.equity_curve) {
    return {
      meta: { time: formatIsoDate(new Date()), kind: backtestKind.value || '' },
      metrics: cur.metrics,
      trade_logs: cur.trade_logs || []
    }
  }
  const p = lastBacktestPersisted.value
  if (!p || typeof p !== 'object') return null
  if (!p.metrics || typeof p.metrics !== 'object') return null
  return p
})

const lastBacktestTradeRows = computed(() => {
  const logs = lastBacktestToShow.value?.trade_logs
  const arr = Array.isArray(logs) ? logs : []
  const rows = []
  const tail = arr.slice(Math.max(0, arr.length - 20))
  for (const it of tail) {
    if (!it || typeof it !== 'object') continue
    rows.push({
      time: String(it.time || it.day || ''),
      side: String(it.side || ''),
      code: String(it.code || ''),
      price: it.price,
      prob: it.prob,
      pnl_pct: it.pnl_pct,
      reason: it.reason
    })
  }
  return rows
})

const lastBacktestTradeColumns = computed(() => ([
  { key: 'time', label: t('strategy.timeHuman') },
  { key: 'side', label: t('strategy.side') },
  { key: 'code', label: t('sidebar.stockCode') },
  { key: 'price', label: t('strategy.price') },
  { key: 'prob', label: 'prob' },
  { key: 'pnl_pct', label: t('strategy.positionReturn') },
  { key: 'reason', label: t('strategy.reason') }
]))

const shouldShowNoPickReason = computed(() => {
  const sr = stepResult.value
  if (!sr || typeof sr !== 'object') return false
  const noTargets = !Array.isArray(sr.targets) || sr.targets.length === 0
  const noBuys = !Array.isArray(sr.buy) || sr.buy.length === 0
  return noTargets && noBuys
})

const noPickReasonLines = computed(() => {
  const sr = stepResult.value
  const sd = selectionDebug.value
  const out = []
  if (!sr || !sd) return out

  const minProb = sd.min_prob
  const scanned = sd?.counts?.scanned
  const todaysRaw = sd?.counts?.todays_raw
  const pass = sd?.counts?.todays_pass_min_prob
  const riskLevel = sd?.risk_level
  const riskReason = sr?.risk?.reason

  if (Number.isFinite(Number(scanned))) out.push(`scanned=${Number(scanned)}`)
  if (Number.isFinite(Number(todaysRaw))) out.push(`todays_raw=${Number(todaysRaw)}`)
  if (Number.isFinite(Number(pass))) out.push(`todays_pass_min_prob=${Number(pass)} (minProb=${minProb ?? '-'})`)
  if (riskLevel) out.push(`risk_level=${riskLevel}`)
  if (riskReason) out.push(`risk_reason=${riskReason}`)

  if (Number(scanned) === 0) out.push('可能原因：股票池为空 / 数据源无数据 / 扫描阶段失败')
  else if (Number(todaysRaw) === 0) out.push('可能原因：今日（或最近可用交易日）没有产生信号')
  else if (Number(pass) === 0) out.push('可能原因：候选全部低于 minProb 门槛')
  else if (String(riskLevel) === 'flat') out.push('可能原因：风控为 flat（清仓/不新开仓）')

  return out
})

const noPickTop10 = computed(() => {
  const arr = selectionDebug.value?.candidates_top
  const all = Array.isArray(arr) ? arr : []
  return all.slice(0, 10)
})

const runsLoading = ref(false)
const runsError = ref('')
const liveRuns = ref([])
const selectedRunId = ref('')
const runningOnceRunId = ref('')

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
  { key: 'stock_name', label: t('portfolio.stockName') },
  { key: 'volume', label: t('portfolio.volume') },
  { key: 'lots_text', label: t('portfolio.lots') },
  { key: 'bars_held', label: t('strategy.barsHeld') },
  { key: 'entry_time_text', label: t('strategy.entryTime') },
  { key: 'entry_price_text', label: t('strategy.entryPrice') },
  { key: 'current_price_text', label: t('strategy.currentPrice') },
  { key: 'market_value_text', label: t('portfolio.marketValue') },
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

const formatBacktestTradeRow = (x, idx) => {
  const side = String(x?.side || '').toLowerCase()
  const timeText = String(x?.time || '').trim()
  const useClose = !!x?.use_close
  const code = String(x?.code || x?.stock_code || '').trim()
  const px = Number(x?.price)
  const sh = Number(x?.shares)
  const pnlPct = x?.pnl_pct === null || x?.pnl_pct === undefined ? null : Number(x?.pnl_pct)
  const reason = String(x?.reason || '').trim()
  const tradeTs = Number(x?.trade_ts || 0)
  const timeByTs = Number.isFinite(tradeTs) && tradeTs > 0 ? formatTs(tradeTs) : ''
  const timeBase = timeText || timeByTs || formatDay(x?.day)
  let suffix = ''
  const m = typeof timeBase === 'string' ? timeBase.match(/(\d{2}:\d{2})$/) : null
  const hhmm = m?.[1] || ''
  if (hhmm) {
    if (hhmm === '09:30') suffix = '(开盘)'
    else if (hhmm === '15:00') suffix = '(收盘)'
  } else {
    suffix = useClose ? '(收盘)' : '(开盘)'
  }
  const timeLabel = timeBase === '-' ? '-' : (suffix ? `${timeBase} ${suffix}` : timeBase)
  return {
    id: idx + 1,
    day_text: timeLabel,
    side_text: formatSide(side),
    stock_code: code || '-',
    price_text: Number.isFinite(px) ? px.toFixed(4) : '-',
    shares_text: Number.isFinite(sh) ? sh.toFixed(4) : '-',
    pnl_pct_text: pnlPct !== null && Number.isFinite(pnlPct) ? formatPct(pnlPct) : '-',
    reason_text: reason || '-'
  }
}

const backtestTradeRows = computed(() => {
  const logs = currentBacktestResult.value?.trade_logs
  const items = Array.isArray(logs) ? logs : []
  return items.map((x, idx) => formatBacktestTradeRow(x, idx))
})

const resolveSnapshotIndexForDay = (snapDays, dayKey) => {
  const dk = Number(dayKey || 0)
  if (!Number.isFinite(dk) || dk <= 0) return -1
  const days = Array.isArray(snapDays) ? snapDays : []
  if (!days.length) return -1
  let lo = 0
  let hi = days.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (Number(days[mid]) <= dk) lo = mid + 1
    else hi = mid
  }
  return lo - 1
}

const backtestTradeTables = computed(() => {
  const logs = currentBacktestResult.value?.trade_logs
  const items = Array.isArray(logs) ? logs : []
  const prm = currentBacktestResult.value?.params || {}
  const pit = !!prm.pool_point_in_time
  const rawSnaps = Array.isArray(prm.pool_snapshots) ? prm.pool_snapshots : []
  const snaps = rawSnaps
    .map(s => ({
      day: Number(s?.day || 0),
      snapshot_date: String(s?.snapshot_date || '').trim() || formatDay(s?.day),
      stock_count: Number(s?.stock_count || 0)
    }))
    .filter(s => Number.isFinite(s.day) && s.day > 0)
    .sort((a, b) => a.day - b.day)

  if (!pit || snaps.length <= 1) {
    return [{
      key: 'all',
      title: t('strategy.tradeHistory'),
      snapshot_date: null,
      rows: items.map((x, idx) => formatBacktestTradeRow(x, idx))
    }]
  }

  const snapDays = snaps.map(s => s.day)
  const grouped = new Map()
  for (const x of items) {
    const dk = Number(x?.day || 0)
    const idx = resolveSnapshotIndexForDay(snapDays, dk)
    const key = idx >= 0 ? String(snapDays[idx]) : 'unknown'
    if (!grouped.has(key)) grouped.set(key, [])
    grouped.get(key).push(x)
  }

  const out = []
  for (let i = 0; i < snaps.length; i++) {
    const snap = snaps[i]
    const key = String(snap.day)
    const rowsRaw = grouped.get(key) || []
    if (!rowsRaw.length) continue
    const next = snaps[i + 1]
    const title = next?.snapshot_date
      ? `${snap.snapshot_date} (${snap.stock_count || 0}) ~ ${next.snapshot_date} 前`
      : `${snap.snapshot_date} (${snap.stock_count || 0})`
    out.push({
      key,
      title,
      snapshot_date: snap.snapshot_date,
      rows: rowsRaw.map((x, idx) => formatBacktestTradeRow(x, idx))
    })
  }

  const unknownRows = grouped.get('unknown') || []
  if (unknownRows.length) {
    out.push({
      key: 'unknown',
      title: t('strategy.tradeHistory'),
      snapshot_date: null,
      rows: unknownRows.map((x, idx) => formatBacktestTradeRow(x, idx))
    })
  }

  return out.length ? out : [{
    key: 'all',
    title: t('strategy.tradeHistory'),
    snapshot_date: null,
    rows: items.map((x, idx) => formatBacktestTradeRow(x, idx))
  }]
})

const backtestMembersOpen = ref({})

watch(currentBacktestResult, () => {
  backtestMembersOpen.value = {}
}, { deep: false })

const backtestSnapshotMembersByDate = computed(() => {
  const prm = currentBacktestResult.value?.params || {}
  const raw = prm?.pool_snapshot_members
  const arr = Array.isArray(raw) ? raw : []
  const out = {}
  for (const it of arr) {
    const day = Number(it?.day || 0)
    const d0 = String(it?.snapshot_date || '').trim()
    const snapDate = d0 || (Number.isFinite(day) && day > 0 ? formatDay(day) : '')
    if (!snapDate) continue
    const rawCodes = it?.codes
    const codes = Array.isArray(rawCodes)
      ? rawCodes.map(x => String(x || '').trim()).filter(Boolean)
      : []
    out[snapDate] = codes
  }
  return out
})

const isBacktestMembersOpen = (tb) => {
  const k = String(tb?.key || '').trim()
  if (!k) return false
  return !!backtestMembersOpen.value[k]
}

const backtestMembersCodes = (tb) => {
  const snapDate = String(tb?.snapshot_date || '').trim()
  if (!snapDate) return []
  const m = backtestSnapshotMembersByDate.value || {}
  const codes = m[snapDate]
  return Array.isArray(codes) ? codes : []
}

const toggleBacktestMembers = (tb) => {
  const k = String(tb?.key || '').trim()
  if (!k) return
  const open = isBacktestMembersOpen(tb)
  backtestMembersOpen.value = { ...(backtestMembersOpen.value || {}), [k]: !open }
}

const backtestMembersButtonText = (tb) => {
  const codes = backtestMembersCodes(tb)
  if (codes.length) return `成分股(${codes.length})`
  return isBacktestMembersOpen(tb) ? '收起成分股' : '成分股(0)'
}

const backtestTradeColumns = computed(() => ([
  { key: 'day_text', label: t('strategy.timeHuman') },
  { key: 'side_text', label: t('strategy.side') },
  { key: 'stock_code', label: t('sidebar.stockCode') },
  { key: 'price_text', label: t('strategy.price') },
  { key: 'shares_text', label: t('portfolio.volume') },
  { key: 'pnl_pct_text', label: t('strategy.positionReturn') },
  { key: 'reason_text', label: t('strategy.reason') }
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
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
      timeZone: 'Asia/Shanghai'
    }).format(dt).replace(/\//g, '-')
  } catch (e) {
    return String(ts)
  }
}

const formatPct = (v) => {
  const n = Number(v || 0)
  if (!Number.isFinite(n)) return '-'
  return `${(n * 100).toFixed(2)}%`
}

const formatNumber = (v, digits = 4) => {
  const n = Number(v || 0)
  if (!Number.isFinite(n)) return '-'
  return n.toFixed(digits)
}

const candidateStatusClass = (status) => {
  const raw = String(status || '').toLowerCase()
  if (raw === 'accepted') return 'text-green-600 dark:text-green-400 font-mono'
  if (raw.includes('market') || raw.includes('risk')) return 'text-amber-600 dark:text-amber-400 font-mono'
  if (raw.includes('low') || raw.includes('not_topn') || raw.includes('missing')) return 'text-gray-500 font-mono'
  return 'font-mono'
}

const formatSide = (s) => {
  const raw = String(s || '').toLowerCase()
  if (raw === 'buy') return t('common.buy')
  if (raw === 'sell') return t('common.sell')
  return raw || '-'
}

const formatDay = (day) => {
  const s = String(day ?? '').trim()
  if (!s) return '-'
  if (/^\d{8}$/.test(s)) return `${s.slice(0, 4)}-${s.slice(4, 6)}-${s.slice(6, 8)}`
  return s
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

const stripParallelParams = (obj) => {
  const out = { ...(obj && typeof obj === 'object' ? obj : {}) }
  delete out.max_workers
  delete out.maxWorkers
  return out
}

const buildWolfpackBacktestPayload = (paperCfg, paperPrm) => {
  const cfg = paperCfg && typeof paperCfg === 'object' ? paperCfg : {}
  const prm = paperPrm && typeof paperPrm === 'object' ? paperPrm : {}

  const idxCode = String(prm.index_code || cfg.index_code || '').trim()
  const riskIdxCode = String(prm.risk_index_code || '').trim()
  const idxMa = Number(prm.index_ma_window ?? cfg.index_ma_window)
  const startDate = String(cfg.start_date || '').trim()
  const endDate = String(cfg.end_date || '').trim()
  const payload = {
    pool_id: cfg.pool_id,
    point_in_time_pool: prm.point_in_time_pool !== false,
    frequency: cfg.frequency,
    data_src: cfg.data_src,
    autype: cfg.autype,
    enable_rolling_lookback: true,
    holding_period: prm.holding_period,
    min_prob: prm.min_prob,
    trade_cost: prm.trade_cost,
    walk_forward_max_folds: prm.walk_forward_max_folds,
    rolling_sample_mode: prm.rolling_sample_mode,
    rolling_stride: prm.rolling_stride,
    portfolio_top_n: prm.top_n,
    sell_mode: prm.sell_mode,
    t1_decision_time: prm.t1_decision_time,
    t1_gap_strong_threshold: prm.t1_gap_strong_threshold,
    t1_gap_weak_threshold: prm.t1_gap_weak_threshold,
    t1_gap_very_strong_threshold: prm.t1_gap_very_strong_threshold,
    risk_filter_enabled: prm.risk_filter_enabled,
    risk_index_code: riskIdxCode,
    risk_mode: prm.risk_mode,
    risk_drop_threshold: prm.risk_drop_threshold,
    risk_flat_drop_threshold: prm.risk_flat_drop_threshold,
    risk_gap_down_threshold: prm.risk_gap_down_threshold,
    risk_atr_window: prm.risk_atr_window,
    risk_atr_pct_threshold: prm.risk_atr_pct_threshold,
    risk_flat_atr_pct_threshold: prm.risk_flat_atr_pct_threshold,
    risk_intraday_enabled: prm.risk_intraday_enabled,
    risk_intraday_bear_threshold: prm.risk_intraday_bear_threshold,
    risk_intraday_bull_threshold: prm.risk_intraday_bull_threshold,
    risk_trend_ma_window: prm.risk_trend_ma_window,
    risk_data_fail_mode: prm.risk_data_fail_mode,
    risk_intraday_data_fail_mode: prm.risk_intraday_data_fail_mode,
    stop_loss: prm.stop_loss,
    index_filter_enabled: prm.index_filter_enabled,
    enforce_t1: prm.enforce_t1,
    model: prm.model,
    pretrained_model_key: prm.pretrained_model_key,
    rank_mode: prm.rank_mode,
    lookback_days: prm.lookback_days,
    only_features: prm.only_features,
  }

  if (startDate) payload.start_date = startDate
  if (endDate) payload.end_date = endDate
  if (idxCode) payload.index_code = idxCode
  if (Number.isFinite(idxMa) && idxMa > 0) payload.index_ma_window = Math.trunc(idxMa)

  return payload
}

const runWolfpackBacktest = async () => {
  wolfpackLoading.value = true
  try {
    const cfg = buildWolfpackBacktestPayload(props.paperConfig, props.paperParams)
    if (cfg.start_date && cfg.end_date && String(cfg.start_date) > String(cfg.end_date)) {
      alert(t('portfolio.dateRangeInvalid'))
      return
    }
    const res = await axios.post('/api/backtest/wolfpack', cfg)
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
    const tradeLogs = Array.isArray(pf.trade_logs) ? pf.trade_logs : []
    wolfpackBacktestResult.value = {
      params: data.params || {},
      metrics: {
        total_return: Number(pf.total_return ?? (lastEquity - 1)),
        sharpe_ratio: Number(pf.sharpe ?? 0),
        max_drawdown: Number(pf.max_drawdown ?? 0),
        final_equity: Number.isFinite(lastEquity) ? lastEquity : 1
      },
      equity_curve: curve,
      trade_logs: tradeLogs
    }
    try {
      if (typeof window !== 'undefined' && window.localStorage) {
        const snap = {
          meta: { time: formatIsoDate(new Date()), kind: 'wolfpack' },
          metrics: wolfpackBacktestResult.value.metrics,
          trade_logs: (wolfpackBacktestResult.value.trade_logs || []).slice(Math.max(0, (wolfpackBacktestResult.value.trade_logs || []).length - 300))
        }
        window.localStorage.setItem('mlchan_last_backtest_snapshot', JSON.stringify(snap))
        lastBacktestPersisted.value = snap
        showLastBacktest.value = true
      }
    } catch (e) {
    }
    lastBacktestPaperConfig.value = { ...(props.paperConfig || {}) }
    lastBacktestPaperParams.value = { ...(props.paperParams || {}) }
    backtestConfirmed.value = false
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

const resolveRunSimId = (baseSimId) => {
  const baseSid = String(baseSimId || '').trim() || 'default'
  if (baseSid !== 'default') return baseSid
  return `sim_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

const buildLiveParams = (paperCfg, paperPrm, sid) => {
  const cfg = stripParallelParams(paperCfg && typeof paperCfg === 'object' ? paperCfg : {})
  const prm = stripParallelParams(paperPrm && typeof paperPrm === 'object' ? paperPrm : {})
  const nextPrm = stripParallelParams({ ...prm })
  return stripParallelParams({
    ...stripParallelParams(cfg),
    ...stripParallelParams(nextPrm),
    sim_id: sid,
    portfolio_id: activePortfolioId.value,
    enable_rolling_lookback: true,
    require_pretrained_model: true,
    train_on_fly: false,
    rolling_sample_mode: String(nextPrm.rolling_sample_mode || 'bsp') || 'bsp'
  })
}

const openRunPaperFromBacktest = () => {
  const snapPrm = lastBacktestPaperParams.value || props.paperParams || {}
  paperRunFromBacktestTrainOnFly.value = !!snapPrm?.train_on_fly
  paperRunFromBacktestConfirmOpen.value = true
}

const confirmRunPaperFromBacktest = async () => {
  paperRunFromBacktestConfirmOpen.value = false
  await runPaperFromBacktest({ train_on_fly: paperRunFromBacktestTrainOnFly.value })
}

const runPaperFromBacktest = async ({ train_on_fly } = {}) => {
  if (!currentBacktestResult.value) return
  if (!backtestConfirmed.value) return
  paperRunFromBacktestLoading.value = true
  try {
    const snapCfg = lastBacktestPaperConfig.value || props.paperConfig || {}
    const snapPrm = lastBacktestPaperParams.value || props.paperParams || {}

    const sid = resolveRunSimId(props.backtestSimId)
    const params = buildLiveParams(snapCfg, snapPrm, sid)
    if (train_on_fly !== undefined) params.train_on_fly = !!train_on_fly
    const notifyEnabled = !!props.paperRunForm?.notify_enabled
    const notifyMode = String(props.paperRunForm?.notify_mode || 'dingtalk')
    const enableDingtalk = notifyEnabled && (notifyMode === 'dingtalk' || notifyMode === 'both')
    const enableSms = notifyEnabled && (notifyMode === 'sms' || notifyMode === 'both')
    const payload = {
      name: String(props.paperRunForm?.name || '').trim(),
      sim_id: sid,
      portfolio_id: activePortfolioId.value,
      params,
      offset_seconds: Number(props.paperRunForm?.offset_seconds || 0),
      auto_resume: !!props.paperRunForm?.auto_resume,
      notify_enabled: notifyEnabled,
      notify_kind: enableDingtalk ? 'dingtalk' : '',
      notify_webhook: enableDingtalk ? String(props.paperRunForm?.dingtalk_webhook || '').trim() : '',
      notify_secret: enableDingtalk ? String(props.paperRunForm?.dingtalk_secret || '').trim() : '',
      sms_enabled: enableSms,
      sms_phone: enableSms ? String(props.paperRunForm?.sms_phone || '').trim() : '',
      sms_token: enableSms ? String(props.paperRunForm?.sms_token || '').trim() : '',
      start: !!props.paperRunForm?.start
    }
    const res = await axios.post('/api/live/runs', payload)
    emit('selectSimId', sid)
    emit('applyRunParams', params)
    emit('openPaper', { sim_id: sid, run_id: String(res.data?.item?.run_id || '').trim() })
  } catch (e) {
    alert(String(e?.response?.data?.detail || e?.message || e))
  } finally {
    paperRunFromBacktestLoading.value = false
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
      const mv = Number(p?.market_value || (curPx * Number(p?.volume || 0)))
      const vol = Number(p?.volume || 0)
      const lots = Number.isFinite(vol) ? (vol / 100) : 0
      const lotsText = Number.isFinite(lots) ? (Number.isInteger(lots) ? String(lots) : lots.toFixed(2)) : '-'
      return {
        ...p,
        entry_time_text: formatTs(entry),
        entry_price_text: Number.isFinite(entryPx) ? entryPx.toFixed(4) : '-',
        current_price_text: Number.isFinite(curPx) ? curPx.toFixed(4) : '-',
        market_value_text: Number.isFinite(mv) ? mv.toFixed(2) : '-',
        lots_text: lotsText,
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
    const paperParams = stripParallelParams({ ...(props.paperParams || {}) })
    const payload = stripParallelParams({
      ...stripParallelParams(props.paperConfig || {}),
      ...stripParallelParams(paperParams),
      sim_id: activeSimId.value,
      portfolio_id: activePortfolioId.value,
      enable_rolling_lookback: true,
      require_pretrained_model: true,
      train_on_fly: false,
      rolling_sample_mode: String(paperParams.rolling_sample_mode || 'bsp') || 'bsp'
    })
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
    if (mode.value === 'paper' && !String(selectedRunId.value || '').trim() && liveRuns.value.length) {
      const getRunTs = (r) => {
        const candidates = [
          Number(r?.last_step_ts),
          Number(r?.updated_ts),
          Number(r?.created_ts),
          Number(r?.next_exec_ts)
        ].filter(n => Number.isFinite(n))
        return candidates.length ? Math.max(...candidates) : 0
      }
      const sorted = [...liveRuns.value].sort((a, b) => {
        const dt = getRunTs(b) - getRunTs(a)
        if (dt) return dt
        return String(b?.run_id || '').localeCompare(String(a?.run_id || ''))
      })
      const pick = sorted[0] || null
      const rid = String(pick?.run_id || '').trim()
      const sid = String(pick?.sim_id || '').trim()
      if (rid && sid) {
        preserveSelectedRunIdOnce = true
        localSimId.value = sid
        emit('selectSimId', sid)
        selectedRunId.value = rid
        stepLogPage.value = 1
        tradePage.value = 1
        await loadPositions()
        await loadStepLogs()
        await loadEquity()
      }
    }
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
    const hadRunning = (liveRuns.value || []).some(r => String(r?.status || '').toLowerCase() === 'running')
    const cfg = props.paperConfig || {}
    const baseSid = String(activeSimId.value || '').trim() || 'default'
    const sid = baseSid === 'default'
      ? `sim_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
      : baseSid
    const paperParams = stripParallelParams({ ...(props.paperParams || {}) })
    const params = stripParallelParams({
      ...stripParallelParams(props.paperConfig || {}),
      ...stripParallelParams(paperParams),
      sim_id: sid,
      portfolio_id: activePortfolioId.value,
      enable_rolling_lookback: true,
      require_pretrained_model: true,
      train_on_fly: false,
      rolling_sample_mode: String(paperParams.rolling_sample_mode || 'bsp') || 'bsp'
    })
    const notifyEnabled = !!props.paperRunForm?.notify_enabled
    const notifyMode = String(props.paperRunForm?.notify_mode || 'dingtalk')
    const enableDingtalk = notifyEnabled && (notifyMode === 'dingtalk' || notifyMode === 'both')
    const enableSms = notifyEnabled && (notifyMode === 'sms' || notifyMode === 'both')
    const payload = {
      name: String(props.paperRunForm?.name || '').trim(),
      sim_id: sid,
      portfolio_id: activePortfolioId.value,
      params,
      offset_seconds: Number(props.paperRunForm?.offset_seconds || 0),
      auto_resume: !!props.paperRunForm?.auto_resume,
      notify_enabled: notifyEnabled,
      notify_kind: enableDingtalk ? 'dingtalk' : '',
      notify_webhook: enableDingtalk ? String(props.paperRunForm?.dingtalk_webhook || '').trim() : '',
      notify_secret: enableDingtalk ? String(props.paperRunForm?.dingtalk_secret || '').trim() : '',
      sms_enabled: enableSms,
      sms_phone: enableSms ? String(props.paperRunForm?.sms_phone || '').trim() : '',
      sms_token: enableSms ? String(props.paperRunForm?.sms_token || '').trim() : '',
      start: !!props.paperRunForm?.start
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
    if (!!props.paperRunForm?.start && !hadRunning) {
      setTimeout(() => {
        loadRuns()
        loadPositions()
        loadStepLogs()
        loadEquity()
      }, 1500)
    }
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

const runOnce = async (run) => {
  const rid = String(run?.run_id || '').trim()
  if (!rid) return
  stepError.value = ''
  runsError.value = ''
  runningOnceRunId.value = rid
  try {
    const sid = String(run?.sim_id || '').trim()
    if (sid) {
      localSimId.value = sid
      emit('selectSimId', sid)
    }
    selectedRunId.value = rid
    stepLogPage.value = 1
    tradePage.value = 1

    const res = await axios.post(`/api/live/runs/${rid}/step_once`)
    stepResult.value = res.data?.result || null

    await loadRuns()
    await loadPositions()
    await loadStepLogs()
    await loadEquity()
  } catch (e) {
    const msg = String(e?.response?.data?.detail || e?.message || e)
    stepError.value = msg
    runsError.value = msg
  } finally {
    runningOnceRunId.value = ''
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

const selectRunSim = async (run) => {
  const sid = String(run?.sim_id || '').trim()
  const rid = String(run?.run_id || '').trim()
  if (!sid && !rid) return
  localSimId.value = sid
  emit('selectSimId', sid)
  selectedRunId.value = rid
  stepLogPage.value = 1
  tradePage.value = 1
  await loadPositions()
  await loadStepLogs()
  await loadEquity()
}

const openRunParams = async (run) => {
  const runId = String(run?.run_id || '').trim()
  if (!runId) return
  showRunParamsModal.value = true
  runParamsError.value = ''
  runParamsLoading.value = true
  runParamsItem.value = null
  try {
    const res = await axios.get(`/api/live/runs/${runId}`)
    runParamsItem.value = res.data?.item || null
  } catch (e) {
    runParamsError.value = String(e?.response?.data?.detail || e?.message || e)
  } finally {
    runParamsLoading.value = false
  }
}

const goBacktestWithParams = async (run) => {
  const runId = String(run?.run_id || '').trim()
  if (!runId) return
  try {
    const res = await axios.get(`/api/live/runs/${runId}`)
    const params = res.data?.item?.params || null
    if (params && typeof params === 'object') emit('openBacktestFill', params)
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

const renderEquityChart = (retry = 0) => {
  if (mode.value !== 'paper') return
  const el = equityChartContainer.value
  if (!el) return

  // Check dimensions to prevent ECharts init error
  if (el.clientWidth === 0 || el.clientHeight === 0) {
     if (retry < 3) {
        setTimeout(() => renderEquityChart(retry + 1), 100)
     }
     return
  }

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

const renderChart = (retry = 0) => {
    if (!chartContainer.value || !currentBacktestResult.value) return
    if (chartContainer.value.clientWidth === 0 || chartContainer.value.clientHeight === 0) {
      if (retry < 3) {
         setTimeout(() => renderChart(retry + 1), 100)
      }
      return
    }

    if (chartInstance) chartInstance.dispose()
    
    chartInstance = echarts.init(chartContainer.value)
    const curve = currentBacktestResult.value.equity_curve
    
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
  wolfpackLoading,
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
  },
  loadRuns: async () => {
    mode.value = 'paper'
    await loadRuns()
  },
  createRun: async () => {
    mode.value = 'paper'
    await createRun()
  },
  runBacktest: async () => {
    mode.value = 'backtest'
    await runWolfpackBacktest()
  }
})

onMounted(() => {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      const raw = window.localStorage.getItem('mlchan_last_backtest_snapshot')
      if (raw) {
        const obj = JSON.parse(raw)
        if (obj && typeof obj === 'object') lastBacktestPersisted.value = obj
      }
    }
  } catch (e) {
  }
  fetchPortfolios()
  // Fetch stock pool options
  axios.get('/api/stock_pools').then(res => {
    if (res.data && res.data.items) {
      poolOptions.value = res.data.items.map(p => ({
        label: `${p.pool_name} (${p.stock_count})`,
        value: p.pool_id
      }))
      if (!poolOptions.value.find(x => x.value === 'index:IDX399673')) {
        poolOptions.value.unshift({ label: '创业板50 (50)', value: 'index:IDX399673' })
      }
    }
  }).catch(() => {})
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
        loadClickHouseHealth()
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
          loadClickHouseHealth()
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
