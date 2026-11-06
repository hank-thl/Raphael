export default defineEventHandler(async () => {
  const config = useRuntimeConfig()

  try {
    const response = await $fetch<{ status: string }>('/health', {
      baseURL: config.backendBaseUrl,
      timeout: 3000,
      retry: 0,
    })
    if (response.status !== 'ok') {
      throw new Error('Unexpected health response')
    }
    return { status: 'ok' as const }
  } catch {
    throw createError({ statusCode: 503, statusMessage: 'Backend unavailable' })
  }
})
