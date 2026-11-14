export default defineEventHandler(async (event) => {
  setResponseHeader(event, 'Cache-Control', 'no-store')
  const target = new URL('/memories', useRuntimeConfig(event).backendBaseUrl)
  for (const key of ['cursor', 'limit', 'topic', 'unclassified']) {
    const value = getQuery(event)[key]
    if (value !== undefined) {
      if (typeof value !== 'string') throw createError({ statusCode: 400 })
      target.searchParams.set(key, value)
    }
  }
  return proxyRequest(event, target.toString())
})
