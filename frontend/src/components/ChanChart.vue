<template>
  <div class="relative w-full h-full">
    <div class="chart-container" ref="chartDom"></div>
    <el-popover placement="bottom-end" :width="320" trigger="hover">
      <template #reference>
        <div class="absolute top-[10px] right-[10px] z-10 cursor-help opacity-60 hover:opacity-100 transition-opacity" :title="t('chart.legend')">
            <svg width="24" height="24" viewBox="0 0 24 24" class="text-gray-500"><path :d="MemoryAlertCircle" /></svg>
        </div>
      </template>
      <div class="text-sm">
        <h4 class="font-bold mb-2 text-gray-800 border-b pb-1">{{ t('chart.legend') }}</h4>
        <div class="space-y-2">
            <div><span class="font-semibold text-gray-700">{{ t('chart.kline') }}</span> <span class="text-gray-600" v-html="t('chart.klineDesc')"></span></div>
            <div><span class="font-semibold text-gray-700">{{ t('chart.bi') }}</span> <span class="text-gray-600" v-html="t('chart.biDesc')"></span></div>
            <div><span class="font-semibold text-gray-700">{{ t('chart.seg') }}</span> <span class="text-gray-600" v-html="t('chart.segDesc')"></span></div>
            <div><span class="font-semibold text-gray-700">{{ t('chart.center') }}</span> <span class="text-gray-600" v-html="t('chart.centerDesc')"></span></div>
            <div><span class="font-semibold text-gray-700">{{ t('chart.ma') }}</span> <span class="text-gray-600">{{ t('chart.maDesc') }}</span></div>
        </div>
      </div>
    </el-popover>
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
  if (chartDom.value) {
    const resizeObserver = new ResizeObserver(entries => {
      for (let entry of entries) {
        if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
          if (!myChart) {
            myChart = echarts.init(chartDom.value)
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
  if (!myChart) {
      // Try init again if dimensions are ready
      if (chartDom.value && chartDom.value.clientWidth > 0) {
          myChart = echarts.init(chartDom.value)
      } else {
          return // Still not ready
      }
  }
  
  const dates = data.dates
  const klines = data.klines // [open, close, low, high]
  const volumes = data.volumes
  
  // 1. Bi Series Data
  // Convert Bi list to [[index, value], [index, value], ...]
  // Since Bi is continuous, we can just link endpoints.
  // But wait, Bi might have gaps? No, Bi connects locally.
  // We need to sort by index just in case.
  
  // Construct Bi path
  // Note: Backend returns start/end for each Bi.
  // We can just take all start points + last end point.
  // Or just iterate and build the path.
  const biData = []
  if (data.bi && data.bi.length > 0) {
      data.bi.forEach(b => {
          biData.push([b.start_coord[0], b.start_coord[1]])
          biData.push([b.end_coord[0], b.end_coord[1]])
      })
      // Remove duplicates if any (end of prev == start of next usually)
      // Actually ECharts handles it fine.
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
  const graphicElements = []
  if (data.zs) {
      data.zs.forEach(z => {
          // Convert index to pixel coordinate is hard in 'graphic' without 'convertToPixel'.
          // Better use 'custom' series or 'markArea'.
          // 'markArea' is easiest but limited styling.
          // Let's use 'custom' series for ZS.
      })
  }
  
  // Alternative for ZS: Custom Series
  const renderZSItem = (params, api) => {
      const start = api.coord([api.value(0), api.value(1)]) // x_start, y_low
      const end = api.coord([api.value(2), api.value(3)])   // x_end, y_high
      const width = end[0] - start[0]
      const height = end[1] - start[1] // y_high - y_low (but screen coords y is inverted)
      
      // ECharts Y axis: value increases upwards, but pixel y increases downwards.
      // api.coord returns pixel coords.
      
      return {
          type: 'rect',
          shape: {
              x: start[0],
              y: end[1], // top-left y (which is the high value's pixel y)
              width: width,
              height: start[1] - end[1] // low_y_pixel - high_y_pixel (since low y value gives higher pixel val)
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

  // 5. Means
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
          name: 'Bi',
          type: 'line',
          data: biData,
          symbol: 'none',
          lineStyle: {
              color: 'black',
              width: 1,
              type: 'solid'
          },
          connectNulls: true // Important if we used nulls, but we use points
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

  const option = {
      tooltip: {
          trigger: 'axis',
          confine: true,
          axisPointer: {
              type: 'cross'
          }
      },
      legend: {
          data: ['K-Line', 'Bi', 'Seg', 'Center', ...Object.keys(data.means || {}).map(k => `MA${k}`)],
          bottom: 0,
          left: 'center',
          padding: 5,
          itemGap: 10,
          type: 'scroll', // Allow scrolling if too many items
          tooltip: {
            show: true,
            formatter: function (name) {
                if (typeof name !== 'string') return name
                const descriptions = {
                    'K-Line': t('chart.tooltip.kline'),
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
      grid: {
        left: '2%',
        right: '2%',
        bottom: '80px',
        top: '5%',
        containLabel: true
    },
      xAxis: {
          type: 'category',
          data: dates,
          scale: true,
          boundaryGap: false,
          axisLine: { onZero: false },
          splitLine: { show: false },
          min: 'dataMin',
          max: 'dataMax'
      },
      yAxis: {
          scale: true,
          splitArea: {
              show: true
          }
      },
      dataZoom: [
          {
              type: 'inside',
              start: 80,
              end: 100
          },
          {
              show: true,
              type: 'slider',
              bottom: '40px',
              height: 20,
              start: 80,
              end: 100
          }
      ],
      series: series
  }

  myChart.setOption(option, true)
}

defineExpose({ renderChart })
</script>

<style scoped>
.chart-container {
  width: 100%;
  height: 100%;
}
</style>
