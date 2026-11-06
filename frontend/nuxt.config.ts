import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2025-11-01',
  devtools: { enabled: false },
  css: ['~/assets/css/main.css'],
  vite: { plugins: [tailwindcss()] },
  typescript: { strict: true },
  runtimeConfig: {
    backendBaseUrl: 'http://127.0.0.1:8000',
  },
  app: {
    head: {
      htmlAttrs: { lang: 'zh-Hant' },
      title: 'Raphael｜個人記憶助手',
      meta: [{ name: 'description', content: 'Raphael 個人紀錄與記憶助手' }],
    },
  },
})
