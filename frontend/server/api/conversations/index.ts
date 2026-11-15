export default defineEventHandler(async event => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  if (!['GET', 'POST'].includes(event.method)) throw createError({ statusCode: 405 })
  const target = new URL('/conversations', useRuntimeConfig(event).backendBaseUrl)
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
