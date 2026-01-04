import { ref, computed } from 'vue'
import { translations } from '../locales/translations'

const currentLang = ref('zh')

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

    const options = {
      timeZone,
      year: withYear ? 'numeric' : undefined,
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: withSeconds ? '2-digit' : undefined,
      hour12: false
    }

    const fmt = new Intl.DateTimeFormat(locale, options)
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

  const t = (key, params) => {
    const keys = key.split('.')
    let value = translations[currentLang.value]
    
    for (const k of keys) {
      if (value && value[k]) {
        value = value[k]
      } else {
        return key
      }
    }
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

  const setLang = (lang) => {
    currentLang.value = lang
    localStorage.setItem('lastLang', lang)
  }

  return {
    currentLang,
    formatTime,
    t,
    setLang
  }
}
