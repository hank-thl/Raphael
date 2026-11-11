import tailwindcss from '@tailwindcss/vite'

export default defineNuxtConfig({
  compatibilityDate: '2025-11-01',
  devtools: { enabled: false },
  css: ['~/assets/css/main.css'],
  vite: { plugins: [tailwindcss()] },
  typescript: { strict: true },
  routeRules: {
    '/admin/**': { headers: { 'cache-control': 'no-store' } },
    '/login': { headers: { 'cache-control': 'no-store' } },
    '/chat': { headers: { 'cache-control': 'no-store' } },
    '/records': { headers: { 'cache-control': 'no-store' } },
  },
  runtimeConfig: {
    backendBaseUrl: 'http://127.0.0.1:8000',
    public: {
      firebase: { apiKey: '', authDomain: '', projectId: '', appId: '' },
    },
  },
  app: {
    head: {
      htmlAttrs: { lang: 'zh-Hant' },
      title: 'Raphael｜個人記憶助手',
      meta: [
        { name: 'description', content: 'Raphael 個人紀錄與記憶助手' },
        { name: 'theme-color', content: '#304d40' },
        { name: 'apple-mobile-web-app-title', content: 'Raphael' },
      ],
      link: [
        { rel: 'icon', href: '/favicon.ico', sizes: '16x16 32x32 48x48' },
        { rel: 'icon', type: 'image/png', sizes: '32x32', href: '/favicon-32x32.png' },
        { rel: 'icon', type: 'image/svg+xml', sizes: 'any', href: '/favicon.svg' },
        { rel: 'apple-touch-icon', sizes: '180x180', href: '/apple-touch-icon.png' },
        { rel: 'manifest', href: '/site.webmanifest' },
      ],
    },
  },
})
