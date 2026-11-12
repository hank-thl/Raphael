import { normalizeLocale } from '~/utils/locale'

export default defineNuxtPlugin({
  name: 'raphael:locale',
  dependsOn: ['i18n:plugin'],
  async setup(nuxt) {
    // Initialize before route middleware and pages, on both server and client.
    const savedLocale = useCookie<string>('raphael_locale', {
      maxAge: 60 * 60 * 24 * 365, sameSite: 'lax', path: '/',
    })
    await nuxt.$i18n.setLocale(normalizeLocale(savedLocale.value))
    nuxt.hook('i18n:localeSwitched', ({ newLocale }) => {
      savedLocale.value = normalizeLocale(newLocale)
    })
  },
})
