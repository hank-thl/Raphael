export default defineNuxtRouteMiddleware(async () => {
  const auth = useAuth()
  try {
    await auth.check()
  } catch (error: unknown) {
    const status = (error as { statusCode?: number }).statusCode
    if (status === 401 || status === 403) return navigateTo('/login')
    throw createError({ statusCode: 503, statusMessage: 'Service Unavailable', message: useNuxtApp().$i18n.t('errors.authService') })
  }
})
