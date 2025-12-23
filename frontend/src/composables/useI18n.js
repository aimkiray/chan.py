import { ref, computed } from 'vue'
import { translations } from '../locales/translations'

const currentLang = ref('zh')

export function useI18n() {
  const t = (key) => {
    const keys = key.split('.')
    let value = translations[currentLang.value]
    
    for (const k of keys) {
      if (value && value[k]) {
        value = value[k]
      } else {
        return key
      }
    }
    return value
  }

  const setLang = (lang) => {
    currentLang.value = lang
    localStorage.setItem('lastLang', lang)
  }

  return {
    currentLang,
    t,
    setLang
  }
}
