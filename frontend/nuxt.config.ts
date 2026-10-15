// https://nuxt.com/docs/api/configuration/nuxt-config
import { defineNuxtConfig } from 'nuxt/config'

export default defineNuxtConfig({
  modules: ['@nuxt/ui'],
  ssr: false,
  devtools: { enabled: false },
  experimental: { appManifest: false },

  devServer: {
    host: '127.0.0.1',
    port: 3000
  },

  routeRules: {
    '/api/**': { proxy: 'http://localhost:8001/api/**' }
  },

  compatibilityDate: '2025-01-06'
})
