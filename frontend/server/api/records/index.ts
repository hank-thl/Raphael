export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  if (event.method !== 'GET' && event.method !== 'POST') {
    throw createError({ statusCode: 405 })
  }
  const config = useRuntimeConfig(event)
  const target = new URL('/records', config.backendBaseUrl)
  if (event.method === 'GET') {
    const query = getQuery(event)
    for (const key of ['limit', 'cursor']) {
      const value = query[key]
      if (value !== undefined) {
        if (typeof value !== 'string') throw createError({ statusCode: 400 })
        target.searchParams.set(key, value)
      }
    }
  }
  return proxyRequest(event, target.toString())
})
