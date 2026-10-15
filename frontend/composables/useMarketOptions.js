import { ref } from 'vue'
import axios from 'axios'

/**
 * Shared option-list helpers used by SidebarContent and PortfolioPanel.
 *
 * Each consuming component gets its own reactive option lists, while the
 * localStorage caches are shared through the keys below so both panels reuse
 * the same persisted data instead of duplicating the fetch/cache code.
 *
 * Also exports the small param/format utilities (stripParallelParams, formatTs)
 * that were duplicated across live/backtest panels.
 */

export const STOCK_POOLS_CACHE_KEY = 'mlchan_stock_pools_cache_v1'
export const STOCK_POOLS_CACHE_MAX = 200
export const INDEX_OPTIONS_CACHE_KEY = 'mlchan_index_options_cache_v1'
export const INDEX_OPTIONS_CACHE_MAX = 200
export const DEFAULT_POOL_ID = 'index:IDX399673'
export const DEFAULT_POOL_OPTION = { label: '创业板50 (50)', value: DEFAULT_POOL_ID, name: '创业板50' }

// Remove client-only parallel tuning knobs before posting to backend APIs.
export const stripParallelParams = (obj) => {
  const out = { ...(obj && typeof obj === 'object' ? obj : {}) }
  delete out.max_workers
  delete out.maxWorkers
  return out
}

// Format a unix-seconds timestamp for display (fixed zh-CN / Asia/Shanghai,
// matching the previous inline implementations).
export const formatTs = (ts) => {
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

const readOptionCache = (key, max) => {
  try {
    if (typeof window === 'undefined') return []
    const raw = localStorage.getItem(key)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const cleaned = parsed
      .map(x => ({
        value: String(x?.value || x?.code || x?.pool_id || '').trim(),
        label: String(x?.label || '').trim(),
        name: String(x?.name || '').trim()
      }))
      .filter(x => x.value)
      .map(x => ({
        value: x.value,
        name: x.name || x.value,
        label: x.label || `${x.name || x.value} (${x.value})`
      }))
    return cleaned.slice(0, max)
  } catch (e) {
    return []
  }
}

const writeOptionCache = (key, max, items) => {
  try {
    if (typeof window === 'undefined') return
    const arr = (items || [])
      .map(x => ({
        value: String(x?.value || '').trim(),
        label: String(x?.label || '').trim(),
        name: String(x?.name || '').trim()
      }))
      .filter(x => x.value)
    localStorage.setItem(key, JSON.stringify(arr.slice(0, max)))
  } catch (e) {
    // storage may be unavailable (private mode etc.) — ignore
  }
}

const mergeUniqueOptions = (items, existing, max) => {
  const merged = [...(items || []), ...(existing || [])]
  const seen = new Set()
  const uniq = []
  for (const it of merged) {
    const v = String(it?.value || '').trim()
    if (!v || seen.has(v)) continue
    seen.add(v)
    uniq.push(it)
  }
  return uniq.slice(0, max)
}

const mapPoolItem = (p) => ({
  label: `${p.pool_name} (${p.stock_count})`,
  value: p.pool_id,
  name: p.pool_name
})

const mapIndexItem = (c) => {
  const code = typeof c === 'string' ? c : (c.code || '')
  const name = typeof c === 'string' ? c : (c.name || code)
  return { label: `${name} (${code})`, value: code, name: name }
}

export const useStockPoolOptions = () => {
  const poolOptions = ref([])
  const searchingStockPools = ref(false)
  const stockPoolLoadFailed = ref(false)

  const persistStockPoolsCache = (items) => writeOptionCache(STOCK_POOLS_CACHE_KEY, STOCK_POOLS_CACHE_MAX, items)

  const ensureStockPoolOption = (poolId) => {
    const cur = String(poolId || '').trim()
    if (!cur) return
    if ((poolOptions.value || []).find(x => x.value === cur)) return
    poolOptions.value = [{ label: `${cur} (${cur})`, value: cur, name: cur }, ...(poolOptions.value || [])].slice(0, STOCK_POOLS_CACHE_MAX)
    persistStockPoolsCache(poolOptions.value)
  }

  const searchStockPools = async (query) => {
    const q = String(query || '').trim()
    if (!q) return poolOptions.value
    searchingStockPools.value = true
    try {
      const res = await axios.get('/api/stock_pools', { params: { limit: 100, keyword: q } })
      const items = (res.data?.items || []).map(mapPoolItem)
      poolOptions.value = mergeUniqueOptions(items, poolOptions.value, STOCK_POOLS_CACHE_MAX)
      persistStockPoolsCache(poolOptions.value)
      stockPoolLoadFailed.value = false
      return items
    } catch (e) {
      console.error('[useMarketOptions] searchStockPools failed:', e?.response?.data?.detail || e?.message || e)
      return []
    } finally {
      searchingStockPools.value = false
    }
  }

  // Initial load: applies the cached list immediately (if any), then refreshes
  // from the API. Returns { ok } so callers can distinguish "no data" from a
  // failed request and offer a retry.
  const loadStockPools = async () => {
    const cached = readOptionCache(STOCK_POOLS_CACHE_KEY, STOCK_POOLS_CACHE_MAX)
    if (cached.length > 0) {
      poolOptions.value = cached
    }
    if (!poolOptions.value.length) {
      searchingStockPools.value = true
    }
    try {
      const res = await axios.get('/api/stock_pools')
      if (res.data && Array.isArray(res.data.items)) {
        poolOptions.value = res.data.items.map(mapPoolItem)
        if (!poolOptions.value.find(x => x.value === DEFAULT_POOL_ID)) {
          poolOptions.value.unshift({ ...DEFAULT_POOL_OPTION })
        }
        persistStockPoolsCache(poolOptions.value)
      }
      stockPoolLoadFailed.value = false
      return { ok: true, items: poolOptions.value }
    } catch (e) {
      console.error('[useMarketOptions] loadStockPools failed:', e?.response?.data?.detail || e?.message || e)
      stockPoolLoadFailed.value = true
      return { ok: false, items: [], error: e }
    } finally {
      searchingStockPools.value = false
    }
  }

  return {
    poolOptions,
    searchingStockPools,
    stockPoolLoadFailed,
    searchStockPools,
    ensureStockPoolOption,
    loadStockPools
  }
}

export const useIndexOptions = () => {
  const indexOptions = ref([])
  const searchingIndices = ref(false)
  const indexLoadFailed = ref(false)

  const persistIndexOptionsCache = (items) => writeOptionCache(INDEX_OPTIONS_CACHE_KEY, INDEX_OPTIONS_CACHE_MAX, items)

  const ensureIndexOption = (code) => {
    const cur = String(code || '').trim()
    if (!cur) return
    if ((indexOptions.value || []).find(x => x.value === cur)) return
    indexOptions.value = [{ label: `${cur} (${cur})`, value: cur, name: cur }, ...(indexOptions.value || [])].slice(0, INDEX_OPTIONS_CACHE_MAX)
    persistIndexOptionsCache(indexOptions.value)
  }

  const searchIndices = async (query) => {
    if (!query) return indexOptions.value
    searchingIndices.value = true
    try {
      const res = await axios.get('/api/indices', { params: { limit: 100, keyword: query } })
      if (res.data && res.data.items) {
        const items = res.data.items.map(mapIndexItem)
        indexOptions.value = mergeUniqueOptions(items, indexOptions.value, INDEX_OPTIONS_CACHE_MAX)
        persistIndexOptionsCache(indexOptions.value)
        return items
      }
      return []
    } catch (e) {
      console.error('[useMarketOptions] searchIndices failed:', e?.response?.data?.detail || e?.message || e)
      return []
    } finally {
      searchingIndices.value = false
    }
  }

  // Initial load: serves the cache when present (no request), otherwise fetches
  // a small page around `keyword`. Returns { items, fromCache } — on failure
  // items is [] and indexLoadFailed is set so callers can show a retry entry.
  const loadIndexOptions = async ({ keyword = '', limit = 50 } = {}) => {
    const cached = readOptionCache(INDEX_OPTIONS_CACHE_KEY, INDEX_OPTIONS_CACHE_MAX)
    if (cached.length > 0) {
      indexOptions.value = cached
      indexLoadFailed.value = false
      return { items: cached, fromCache: true }
    }
    searchingIndices.value = true
    try {
      const res = await axios.get('/api/indices', { params: { limit, keyword } })
      if (res.data && res.data.items) {
        const items = res.data.items.map(mapIndexItem)
        indexOptions.value = items
        persistIndexOptionsCache(items)
        indexLoadFailed.value = false
        return { items, fromCache: false }
      }
      indexLoadFailed.value = false
      return { items: [], fromCache: false }
    } catch (e) {
      console.error('[useMarketOptions] loadIndexOptions failed:', e?.response?.data?.detail || e?.message || e)
      indexLoadFailed.value = true
      return { items: [], fromCache: false, error: e }
    } finally {
      searchingIndices.value = false
    }
  }

  return {
    indexOptions,
    searchingIndices,
    indexLoadFailed,
    searchIndices,
    ensureIndexOption,
    loadIndexOptions
  }
}
