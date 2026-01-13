<template>
  <div class="relative w-full h-full">
    <div class="chart-container" ref="chartDom"></div>
    <UPopover mode="hover" :popper="{ placement: 'bottom-end' }">
      <div class="absolute top-[10px] right-[10px] z-10 cursor-help opacity-60 hover:opacity-100 transition-opacity" :title="t('chart.legend')">
          <svg width="24" height="24" viewBox="0 0 24 24" class="text-gray-500"><path :d="MemoryAlertCircle" /></svg>
      </div>

      <template #panel>
        <div class="text-sm p-4 w-80">
          <h4 class="font-bold mb-2 text-gray-800 border-b pb-1 dark:text-gray-200 dark:border-gray-700">{{ t('chart.legend') }}</h4>
          <div class="space-y-2">
              <div><span class="font-semibold text-gray-700 dark:text-gray-300">{{ t('chart.kline') }}</span> <span class="text-gray-600 dark:text-gray-400" v-html="t('chart.klineDesc')"></span></div>
              <div><span class="font-semibold text-gray-700 dark:text-gray-300">{{ t('chart.bi') }}</span> <span class="text-gray-600 dark:text-gray-400" v-html="t('chart.biDesc')"></span></div>
              <div><span class="font-semibold text-gray-700 dark:text-gray-300">{{ t('chart.seg') }}</span> <span class="text-gray-600 dark:text-gray-400" v-html="t('chart.segDesc')"></span></div>
              <div><span class="font-semibold text-gray-700 dark:text-gray-300">{{ t('chart.center') }}</span> <span class="text-gray-600 dark:text-gray-400" v-html="t('chart.centerDesc')"></span></div>
              <div><span class="font-semibold text-gray-700 dark:text-gray-300">{{ t('chart.buy') }}</span> <span class="text-gray-600 dark:text-gray-400" v-html="t('chart.buyDesc')"></span></div>
              <div><span class="font-semibold text-gray-700 dark:text-gray-300">{{ t('chart.sell') }}</span> <span class="text-gray-600 dark:text-gray-400" v-html="t('chart.sellDesc')"></span></div>
          </div>
        </div>
      </template>
    </UPopover>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'
import { MemoryAlertCircle } from '@pictogrammers/memory'
import { useI18n } from '../composables/useI18n'

const { t, currentLang } = useI18n()
const chartDom = ref(null)
let myChart = null
let lastData = null

const props = defineProps({
  loading: Boolean
})

watch(() => props.loading, (val) => {
  if (myChart) {
    if (val) myChart.showLoading()
    else myChart.hideLoading()
  }
})

watch(currentLang, () => {
  if (lastData) renderChart(lastData)
})

onMounted(() => {
    console.log('ChanChart mounted')
    if (chartDom.value) {
      const resizeObserver = new ResizeObserver(entries => {
        for (let entry of entries) {
          console.log('ResizeObserver:', entry.contentRect.width, entry.contentRect.height)
          if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
            if (!myChart) {
              console.log('Initializing chart from ResizeObserver')
              myChart = echarts.init(chartDom.value)
              if (props.loading) {
                myChart.showLoading()
              }
              if (lastData) {
                  console.log('Rendering pending data from ResizeObserver')
                  renderChart(lastData)
              }
            } else {
              myChart.resize()
            }
          }
        }
      })
      resizeObserver.observe(chartDom.value)
    }
  })
  
  onUnmounted(() => {
    // window.removeEventListener('resize', resizeChart) // No longer needed with ResizeObserver
    if (myChart) myChart.dispose()
  })
  
  const resizeChart = () => {
    if (myChart) myChart.resize()
  }
  
  const renderChart = (data) => {
    console.log('renderChart called', data)
    lastData = data
    
    if (!myChart) {
        // Try init again if dimensions are ready
        if (chartDom.value && chartDom.value.clientWidth > 0) {
            console.log('Initializing chart from renderChart', chartDom.value.clientWidth, chartDom.value.clientHeight)
            myChart = echarts.init(chartDom.value)
            if (props.loading) {
              myChart.showLoading()
            }
        } else {
            console.warn('Chart container not ready or has 0 dimensions')
            return // Still not ready
        }
    } else {
        console.log('Chart already initialized')
    }
    
    const dates = data.dates
  const klines = data.klines // [open, close, low, high]
  const volumes = data.volumes

  // Calculate start percentage for dataZoom to show max 300 bars
  const totalBars = dates.length
  const maxBars = 200
  let startPercent = 0
  if (totalBars > maxBars) {
      startPercent = (1 - maxBars / totalBars) * 100
  }
  
  // 1. Bi Series Data
  // Convert Bi list to [[index, value], [index, value], ...]
  // Since Bi is continuous, we can just link endpoints.
  // But wait, Bi might have gaps? No, Bi connects locally.
  // We need to sort by index just in case.
  
  // Construct Bi path
  // Note: Backend returns start/end for each Bi.
  // We can just take all start points + last end point.
  // Or just iterate and build the path.
  // Since we are iterating, we can just push start and end.
  // But consecutive Bi share points, so we might have duplicates.
  // ECharts handles it fine.
  const biData = []
  if (data.bi && data.bi.length > 0) {
      data.bi.forEach(b => {
          biData.push([b.start_coord[0], b.start_coord[1]])
          biData.push([b.end_coord[0], b.end_coord[1]])
      })
  }
  
  // 2. Seg Series Data
  const segData = []
  if (data.seg && data.seg.length > 0) {
      data.seg.forEach(s => {
          segData.push([s.start_coord[0], s.start_coord[1]])
          segData.push([s.end_coord[0], s.end_coord[1]])
      })
  }
  
  // 3. ZS (Centers) - Use graphic rects
  // Alternative for ZS: Custom Series
  const renderZSItem = (params, api) => {
      const start = api.coord([api.value(0), api.value(1)]) // x_start, y_low
      const end = api.coord([api.value(2), api.value(3)])   // x_end, y_high
      const width = end[0] - start[0]
      // y_high - y_low (but screen coords y is inverted)
      // so height should be start[1] - end[1] if start is low (bigger y) and end is high (smaller y)
      // wait, api.coord maps data space to pixel space.
      // y axis: 0 at top, H at bottom.
      // value y: low < high.
      // pixel y: low > high.
      
      return {
          type: 'rect',
          shape: {
              x: start[0],
              y: end[1], // top-left y (which is the high value's pixel y)
              width: width,
              height: start[1] - end[1] // low_y_pixel - high_y_pixel
          },
          style: {
              fill: 'rgba(255, 165, 0, 0.2)',
              stroke: 'orange',
              lineWidth: 1
          }
      }
  }
  
  const zsData = []
  if (data.zs) {
      data.zs.forEach(z => {
          zsData.push({
              value: [z.start_coord[0], z.start_coord[1], z.end_coord[0], z.end_coord[1]]
              // itemStyle moved to renderItem's style return
          })
      })
  }
  
  // 4. BSP (Buy Sell Points) -> MarkPoint
  const bspData = []
  if (data.bsp) {
      data.bsp.forEach(b => {
          bspData.push({
              coord: [b.coord[0], b.coord[1]],
              value: b.desc,
              itemStyle: {
                  color: b.is_buy ? '#ef4444' : '#10b981'
              },
              label: {
                  offset: [0, b.is_buy ? 10 : -10]
              }
          })
      })
  }

  // 5. Volume Series
  // Calculate median volume for outlier detection
  const sortedVols = [...volumes].sort((a, b) => a - b)
  const medianVol = sortedVols[Math.floor(sortedVols.length / 2)] || 0
  // Cap at 20x median to prevent extreme outliers (e.g. bad data or extreme spikes) from squashing the chart
  const volCap = medianVol > 0 ? medianVol * 20 : Number.MAX_VALUE

  // Volume color: Red (up) or Green (down) based on close >= open
  const volumeBars = []
  klines.forEach((item, idx) => {
      const open = item[0]
      const close = item[1]
      let vol = volumes[idx]
      
      // Apply cap for rendering (tooltip still uses raw volumes[idx])
      if (vol > volCap) {
          vol = volCap
      }

      volumeBars.push({
          value: vol,
          itemStyle: { color: close >= open ? '#ef4444' : '#10b981' }
      })
  })

  // 6. Means
  const series = [
      {
          name: 'K-Line',
          type: 'candlestick',
          data: klines,
          itemStyle: {
              color: '#ef4444',
              color0: '#10b981',
              borderColor: '#ef4444',
              borderColor0: '#10b981'
          }
      },
      {
          name: 'Volume',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          large: false,
          progressive: 0,
          progressiveThreshold: 0,
          data: volumeBars
      },
      {
          name: 'Bi',
          type: 'line',
          data: biData,
          symbol: 'none',
          lineStyle: {
              color: 'black',
              width: 1,
              type: 'solid'
          },
          connectNulls: true 
      },
      {
          name: 'Seg',
          type: 'line',
          data: segData,
          symbol: 'none',
          lineStyle: {
              color: 'green',
              width: 2
          }
      },
      {
          name: 'Center',
          type: 'custom',
          renderItem: renderZSItem,
          data: zsData,
          z: 1
      }
  ]
  
  // Add BSP to K-Line series markPoint
  series[0].markPoint = {
      data: bspData,
      symbolSize: 30
  }
  
  // Add Means
  const colors = ['#f59e0b', '#3b82f6', '#8b5cf6', '#ec4899']
  if (data.means) {
      let colorIdx = 0
      for (const [window, values] of Object.entries(data.means)) {
          series.push({
              name: `MA${window}`,
              type: 'line',
              data: values,
              smooth: true,
              symbol: 'none',
              lineStyle: {
                  width: 1,
                  color: colors[colorIdx % colors.length]
              }
          })
          colorIdx++
      }
  }

  const formatBigNumber = (v) => {
      const n = Number(v)
      if (!Number.isFinite(n)) return ''
      if (n >= 100000000) return (n / 100000000).toFixed(2) + '亿'
      if (n >= 10000) return (n / 10000).toFixed(2) + '万'
      return n.toFixed(2)
  }

  const option = {
      axisPointer: {
        link: { xAxisIndex: 'all' }
      },
      tooltip: {
          trigger: 'axis',
          confine: true,
          axisPointer: {
              type: 'cross',
              label: {
                formatter: (p) => {
                  const n = Number(p?.value)
                  return Number.isFinite(n) ? n.toFixed(2) : String(p?.value ?? '')
                }
              }
          },
          formatter: (params) => {
            const list = Array.isArray(params) ? params : [params].filter(Boolean)
            if (!list.length) return ''

            const k = list.find((p) => p?.seriesName === 'K-Line')
            const idx = Number.isFinite(k?.dataIndex) ? k.dataIndex : -1
            const dateLabel = String(list[0]?.axisValueLabel || list[0]?.axisValue || '')

            const fmt2 = (v) => {
              const n = Number(v)
              return Number.isFinite(n) ? n.toFixed(2) : '--'
            }

            let open = null
            let close = null
            let low = null
            let high = null
            
            if (Array.isArray(k?.data)) {
                if (k.data.length === 4) {
                  open = k.data[0]
                  close = k.data[1]
                  low = k.data[2]
                  high = k.data[3]
                } else if (k.data.length > 4) {
                  // ECharts sometimes includes the axis value at index 0
                  open = k.data[1]
                  close = k.data[2]
                  low = k.data[3]
                  high = k.data[4]
                }
            }

            let pctText = '--'
            if (idx > 0 && Array.isArray(klines?.[idx - 1]) && klines[idx - 1].length >= 2) {
              const prevClose = Number(klines[idx - 1][1])
              const curClose = Number(close)
              if (Number.isFinite(prevClose) && prevClose !== 0 && Number.isFinite(curClose)) {
                const pct = ((curClose - prevClose) / prevClose) * 100
                const sign = pct > 0 ? '+' : ''
                pctText = `${sign}${pct.toFixed(2)}%`
              }
            }

            const rows = []
            rows.push(`<div class="font-semibold">${dateLabel}</div>`)
            rows.push(`<div class="text-xs opacity-80">${t('chart.candleTooltip.changePct')}: ${pctText}</div>`)
            
            // Collect OHLC and MA data first
            const ohlcItems = [
                { label: t('chart.candleTooltip.open'), val: open, marker: k?.marker || '' },
                { label: t('chart.candleTooltip.high'), val: high, marker: '' },
                { label: t('chart.candleTooltip.low'), val: low, marker: '' },
                { label: t('chart.candleTooltip.close'), val: close, marker: '' }
            ]

            const maRows = (list || [])
              .filter((p) => typeof p?.seriesName === 'string' && p.seriesName.startsWith('MA'))
              .slice()
              .sort((a, b) => Number(String(a.seriesName).slice(2)) - Number(String(b.seriesName).slice(2)))

            const maItems = maRows.map((p) => ({
                label: p.seriesName,
                val: p.data,
                marker: p.marker || ''
            }))

            // Add Volume
            // Prefer using raw data to avoid summing up stacked series or getting 0 from the wrong stack
            let volVal = undefined
            if (idx >= 0 && volumes?.[idx] !== undefined) {
               volVal = volumes[idx]
            } else {
               // Fallback: sum up all 'Volume' series values (since we split them into Up/Down, one is 0 and one is value)
               const volParams = list.filter((p) => p?.seriesName === 'Volume')
               if (volParams.length > 0) {
                   volVal = volParams.reduce((sum, p) => sum + (Number(p.value) || 0), 0)
               }
            }
            
            // let volMarker = vol?.marker || '' // Marker is tricky with 2 series, just omit or use generic
            let volMarker = '<span style="display:inline-block;margin-right:4px;border-radius:10px;width:10px;height:10px;background-color:#5470c6;"></span>' // Default blue-ish or just omit
            // Actually, we can try to find the active series marker
            const activeVol = list.find(p => p?.seriesName === 'Volume' && p?.value > 0)
            if (activeVol) volMarker = activeVol.marker

            if (volVal !== undefined && volVal !== null) {
                maItems.push({
                    label: t('chart.tooltip.volume') || 'Volume',
                    val: volVal,
                    displayVal: formatBigNumber(volVal),
                    marker: volMarker
                })
            }

            const allItems = [...ohlcItems, ...maItems]

            let gridHtml = `<div class="mt-1" style="display:grid;grid-template-columns:14px auto auto;column-gap:8px;row-gap:2px;align-items:center;">`
            
            allItems.forEach(item => {
                const display = item.displayVal || fmt2(item.val)
                gridHtml += `<div>${item.marker}</div>` + 
                            `<div style="text-align:left;">${item.label}</div>` + 
                            `<div style="text-align:right;font-family:monospace;font-weight:bold;">${display}</div>`
            })
            
            gridHtml += `</div>`
            rows.push(gridHtml)

            return rows.join('')
          }
      },
      legend: {
          data: ['K-Line', 'Volume', 'Bi', 'Seg', 'Center', ...Object.keys(data.means || {}).map(k => `MA${k}`)],
          bottom: 5,
          left: 'center',
          padding: 5,
          itemGap: 10,
          type: 'scroll', 
          tooltip: {
            show: true,
            formatter: function (name) {
                if (typeof name !== 'string') return name
                const descriptions = {
                    'K-Line': t('chart.tooltip.kline'),
                    'Volume': t('chart.tooltip.volume') || 'Volume',
                    'Bi': t('chart.tooltip.bi'),
                    'Seg': t('chart.tooltip.seg'),
                    'Center': t('chart.tooltip.center')
                }
                if (descriptions[name]) return descriptions[name]
                if (name.startsWith('MA')) {
                   return t('chart.tooltip.ma').replace('{name}', name).replace('{n}', name.substring(2))
                }
                return name
            }
          }
      },
      grid: [
        {
          left: 50,
          right: 50,
          top: 30,
          height: '65%',
          containLabel: false
        },
        {
          left: 50,
          right: 50,
          top: '77%',
          height: '5%',
          containLabel: false
        }
      ],
      xAxis: [
        {
          type: 'category',
          data: dates,
          scale: true,
          boundaryGap: true,
          axisLine: { onZero: false, lineStyle: { color: '#888' } },
          axisLabel: { show: false }, // Hide labels for top chart
          splitLine: { show: false },
          min: 'dataMin',
          max: 'dataMax',
          gridIndex: 0
        },
        {
          type: 'category',
          data: dates,
          scale: true,
          boundaryGap: true,
          axisLine: { onZero: false, lineStyle: { color: '#888' } },
          axisTick: { alignWithLabel: true },
          axisLabel: { color: '#666' },
          splitLine: { show: false },
          min: 'dataMin',
          max: 'dataMax',
          gridIndex: 1
        }
      ],
      yAxis: [
        {
          scale: true,
          gridIndex: 0,
          axisLine: { lineStyle: { color: '#888' } },
          axisLabel: {
            color: '#666',
            formatter: (v) => {
              const n = Number(v)
              return Number.isFinite(n) ? n.toFixed(2) : String(v)
            }
          },
          splitArea: { show: true }
        },
        {
          scale: true,
          gridIndex: 1,
          axisLine: { lineStyle: { color: '#888' } },
          axisLabel: { show: false },
          axisTick: { show: false },
          splitLine: { show: false }
        }
      ],
      dataZoom: [
          {
              type: 'inside',
              xAxisIndex: [0, 1],
              start: startPercent,
              end: 100
          },
          {
              show: true,
              xAxisIndex: [0, 1],
              type: 'slider',
              bottom: 60,
              height: 20,
              left: 50,
              right: 50,
              showDetail: false,
              start: startPercent,
              end: 100
          }
      ],
      series: series,
      animation: false,
      backgroundColor: 'transparent'
  }

  myChart.setOption(option, true)
  lastData = data
}

defineExpose({
  updateChart: renderChart
})
</script>

<style scoped>
.chart-container {
  width: 100%;
  height: 100%;
}
</style>
