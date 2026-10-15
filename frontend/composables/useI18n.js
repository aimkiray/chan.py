import { ref } from 'vue'
import { translations } from '../locales/translations'

const currentLang = ref('zh')

// Intl.DateTimeFormat is expensive to construct; cache one per (locale, tz, shape)
// so per-row formatTime() calls in tables don't rebuild it every time.
const fmtCache = new Map()

export function useI18n() {
  const resolveTimeZone = () => {
    let tz = ''
    try {
      tz = String(Intl?.DateTimeFormat?.().resolvedOptions?.().timeZone || '')
    } catch (e) {
      tz = ''
    }
    if (currentLang.value === 'zh') {
      // If timezone is missing, UTC, or if the browser offset is 0 (UTC/GMT), default to Shanghai for Chinese users
      if (!tz || tz === 'UTC' || tz === 'Etc/UTC' || new Date().getTimezoneOffset() === 0) {
        return 'Asia/Shanghai'
      }
    }
    return tz || undefined
  }

  const formatTime = (iso, { withYear = false, withSeconds = false } = {}) => {
    const raw = String(iso || '')
    if (!raw) return ''
    const d = new Date(raw)
    if (Number.isNaN(d.getTime())) return raw

    const timeZone = resolveTimeZone()
    const locale = currentLang.value === 'zh' ? 'zh-CN' : undefined

    const cacheKey = `${locale || 'default'}|${timeZone || 'default'}|${withYear ? 1 : 0}${withSeconds ? 1 : 0}`
    let fmt = fmtCache.get(cacheKey)
    if (!fmt) {
      fmt = new Intl.DateTimeFormat(locale, {
        timeZone,
        year: withYear ? 'numeric' : undefined,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: withSeconds ? '2-digit' : undefined,
        hour12: false
      })
      fmtCache.set(cacheKey, fmt)
    }
    const parts = fmt.formatToParts(d)
    const map = {}
    for (const p of parts) {
      if (!p?.type) continue
      map[p.type] = p.value
    }

    const year = map.year
    const month = map.month
    const day = map.day
    const hour = map.hour
    const minute = map.minute
    const second = map.second

    if (!month || !day || !hour || !minute) return fmt.format(d)

    const date = withYear && year ? `${year}-${month}-${day}` : `${month}-${day}`
    const time = withSeconds && second ? `${hour}:${minute}:${second}` : `${hour}:${minute}`
    return `${date} ${time}`
  }

  const resolveKey = (key, lang) => {
    let value = translations[lang]
    for (const k of key.split('.')) {
      if (value && typeof value === 'object' && k in value) {
        value = value[k]
      } else {
        return undefined
      }
    }
    return value
  }

  // Whether a key exists in the current language or the zh fallback.
  const has = (key) => resolveKey(key, currentLang.value) !== undefined || resolveKey(key, 'zh') !== undefined

  // Contract: returns the translation for the current language; if the key is
  // missing there, falls back to zh; if missing in both, returns the key
  // itself (a bare key visible in the UI means the key must be added —
  // `t(...) || fallback` is NOT a reliable guard, use has() instead).
  const t = (key, params) => {
    let value = resolveKey(key, currentLang.value)
    if (value === undefined && currentLang.value !== 'zh') {
      value = resolveKey(key, 'zh')
    }
    if (value === undefined) return key
    if (typeof value !== 'string') return value
    if (!params || typeof params !== 'object') return value
    return value.replace(/\{(\w+)\}/g, (match, name) => {
      if (!(name in params)) return match
      const raw = params[name]
      const v = raw && typeof raw === 'object' && 'value' in raw ? raw.value : raw
      if (v === null || v === undefined) return ''
      return String(v)
    })
  }

  return {
    currentLang,
    formatTime,
    has,
    t
  }
}
