// https://nuxt.com/docs/api/configuration/nuxt-config
import { defineNuxtConfig } from 'nuxt/config'

// Backend base URL used for the /api/** dev proxy.
// Override via env when the backend runs on a different host/port:
//   NUXT_API_PROXY=http://192.168.x.x:8001 npm run dev
const apiProxyBase = process.env.NUXT_API_PROXY || 'http://localhost:8001'

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
    '/api/**': { proxy: `${apiProxyBase}/api/**` }
  },

  compatibilityDate: '2025-01-06'
})
